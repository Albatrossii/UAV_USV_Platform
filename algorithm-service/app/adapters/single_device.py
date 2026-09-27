from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

from app.adapters.base import AlgorithmAdapter
from app.capture.containment_contract import assess_containment
from app.navigation import TASK_CENTER_SCENE_MAP, SceneSafetyFilter
from app.schemas import AgentFrame, RuntimeFrame


@dataclass
class _DeviceOverride:
    mode: str
    x: float
    y: float
    z: float
    heading: float
    rejoin_steps: int = 0


class SingleDeviceControlAdapter(AlgorithmAdapter):
    """Add isolated operator control to an existing authoritative adapter.

    The wrapped algorithm continues to own every non-selected device.  The
    selected device is overridden only in the final authoritative frame and
    rejoins the live algorithm target with a speed-limited transition.
    """

    SUPPORTED_COMMANDS = {
        "UAV_HOVER", "UAV_RETURN", "UAV_RESUME", "UAV_LAND",
        "USV_HOLD", "USV_RETURN", "USV_RESUME", "USV_STOP",
    }

    def __init__(self, base: AlgorithmAdapter, algorithm_code: str) -> None:
        super().__init__(base.run_id, base.config)
        self.base = base
        self.code = algorithm_code
        self.version = f"{base.version}-single-device.1"
        self._overrides: Dict[str, _DeviceOverride] = {}
        self._home: Dict[str, tuple[float, float, float, float]] = {}
        self._last_agents: Dict[str, AgentFrame] = {}
        self._capture_replan_positions: Dict[str, tuple[float, float, float]] = {}
        self._safety = SceneSafetyFilter(TASK_CENTER_SCENE_MAP)

    def set_mission_active(self, active: bool) -> None:
        self.mission_active = bool(active)
        self.base.set_mission_active(active)

    def place_threat(self, x: float, y: float) -> None:
        self.base.place_threat(x, y)

    def activate_capture(self, threat_code: str | None = None) -> str:
        return self.base.activate_capture(threat_code)

    def control_device(self, device_code: str, command_type: str) -> str:
        code = str(device_code).strip().upper().replace("_", "-")
        command = str(command_type).strip().upper()
        if command not in self.SUPPORTED_COMMANDS:
            raise ValueError("unsupported single-device command")
        if command.startswith("UAV_") and not code.startswith("UAV-"):
            raise ValueError("UAV command requires a UAV target")
        if command.startswith("USV_") and not code.startswith("USV-"):
            raise ValueError("USV command requires a USV target")
        current = self._last_agents.get(code)
        if current is None:
            raise ValueError("unknown device code")

        if command in {"UAV_RESUME", "USV_RESUME"}:
            if code not in self._overrides:
                return "ALGORITHM"
            override = self._overrides[code]
            override.mode = "REJOINING"
            override.rejoin_steps = 20
            return override.mode

        if command in {"UAV_RETURN", "USV_RETURN"}:
            mode = "RETURNING"
        elif command in {"UAV_LAND", "USV_STOP"}:
            mode = "STOPPED"
        else:
            mode = "HOLDING"
        self._overrides[code] = _DeviceOverride(
            mode=mode,
            x=float(current.x),
            y=float(current.y),
            z=float(current.z),
            heading=float(current.heading),
        )
        return mode

    def step(self) -> RuntimeFrame:
        frame = self.base.step()
        frame.algorithmCode = self.code
        agents = {agent.code.upper(): agent for agent in frame.agents}
        for code, agent in agents.items():
            self._home.setdefault(
                code,
                (float(agent.x), float(agent.y), float(agent.z), float(agent.heading)),
            )
            self._last_agents.setdefault(code, agent)

        completed_rejoins: list[str] = []
        for code, override in list(self._overrides.items()):
            agent = agents.get(code)
            if agent is None:
                continue
            if override.mode == "RETURNING":
                target = self._home[code]
                arrived = self._move_towards(code, override, target[:3])
                if arrived:
                    override.mode = "RETURNED"
                    override.heading = target[3]
            elif override.mode == "REJOINING":
                target = (float(agent.x), float(agent.y), float(agent.z))
                override.rejoin_steps = max(0, override.rejoin_steps - 1)
                if override.rejoin_steps == 0:
                    completed_rejoins.append(code)
                else:
                    self._blend_towards(override, target, override.rejoin_steps)

            if code not in completed_rejoins:
                agent.x = override.x
                agent.y = override.y
                agent.z = override.z
                agent.heading = override.heading
                agent.role = "RETURNED" if override.mode == "RETURNED" else "OPERATOR_CONTROLLED"
                agent.status = override.mode
                agent.assignedTargetCode = ""

        for code in completed_rejoins:
            self._overrides.pop(code, None)
            agents[code].status = "ACTIVE"

        self._apply_authoritative_safety(frame, agents)
        states = {
            code: {
                "controlAuthority": (
                    "RETURNED" if override.mode == "RETURNED" else "OPERATOR"
                ),
                "motionState": override.mode,
                "returnedAtHome": override.mode == "RETURNED",
            }
            for code, override in sorted(self._overrides.items())
        }
        returning = [
            code for code, override in self._overrides.items()
            if override.mode == "RETURNING"
        ]
        rejoining = [
            code for code, override in self._overrides.items()
            if override.mode == "REJOINING"
        ]
        returned = [
            code for code, override in self._overrides.items()
            if override.mode == "RETURNED"
        ]
        excluded = sorted(self._overrides)
        active_agents = [
            agent for code, agent in agents.items()
            if code not in self._overrides
        ]
        frame.metrics["singleDeviceControlEnabled"] = True
        frame.metrics["operatorControlledDeviceCount"] = len(states)
        frame.metrics["deviceControlStates"] = states
        frame.metrics["returningDeviceCount"] = len(returning)
        frame.metrics["returnedDeviceCount"] = len(returned)
        frame.metrics["returningDeviceCodes"] = sorted(returning)
        frame.metrics["returnedDeviceCodes"] = sorted(returned)
        frame.metrics["rejoiningDeviceCount"] = len(rejoining)
        frame.metrics["rejoiningDeviceCodes"] = sorted(rejoining)
        frame.metrics["activeMissionDeviceCount"] = len(active_agents)
        frame.metrics["activeMissionDeviceCodes"] = sorted(agent.code for agent in active_agents)
        frame.metrics["excludedMissionDeviceCodes"] = excluded
        frame.metrics["controlOverrideActive"] = bool(self._overrides)

        insufficient = self._remaining_force_is_insufficient(frame, active_agents)
        transitional = sorted(returning + rejoining)
        blocker = "NONE"
        if transitional:
            # The task fleet may continue working, but terminal success waits
            # for an in-flight authority transfer to settle.  This prevents a
            # RETURN/REJOIN acknowledgement racing the final mission frame.
            blocker = "WAITING_FOR_RETURN" if returning else "WAITING_FOR_REJOIN"
            frame.metrics["completionBlockerDeviceCodes"] = transitional
        elif insufficient:
            blocker = "INSUFFICIENT_ACTIVE_FORCE"
            frame.metrics["missionDegradedReason"] = blocker
        elif frame.terminalStatus == "COMPLETED" and self.code.startswith("GB_SFLA_CS"):
            # The wrapped legacy solver still advances a hidden ghost for an
            # operator-controlled craft.  Never accept its terminal flag until
            # the *active* physical roster itself encloses the target.
            if not self._active_capture_is_closed(frame, active_agents):
                self._repair_active_capture_formation(frame, active_agents)
                self._apply_authoritative_safety(frame, agents)
                if not self._active_capture_is_closed(frame, active_agents):
                    blocker = "ACTIVE_FORMATION_NOT_CLOSED"

        frame.metrics["completionBlocker"] = blocker
        frame.metrics["missionCapability"] = (
            "DEGRADED" if insufficient else "FULL"
        )
        if blocker != "NONE":
            frame.terminalStatus = None
            if frame.phase.upper() == "COMPLETED":
                frame.phase = (
                    "ENCIRCLEMENT"
                    if self.code.startswith("GB_SFLA_CS")
                    else "GUARD_RECONFIGURATION"
                )
            self._cap_progress(frame)
        self._last_agents = {code: agent for code, agent in agents.items()}
        return frame

    def _remaining_force_is_insufficient(
        self,
        frame: RuntimeFrame,
        active_agents: list[AgentFrame],
    ) -> bool:
        """Apply the structural quorum of the selected cooperative mission."""
        active_types = {agent.type.upper() for agent in active_agents}
        missing = sorted({"UAV", "USV"} - active_types)
        frame.metrics["requiredActiveDeviceTypes"] = ["UAV", "USV"]
        frame.metrics["missingActiveDeviceTypes"] = missing
        if self.code.startswith("GB_SFLA_CS"):
            configured = self.config.get("minimumActiveCaptureDeviceCount", 3)
            try:
                required = int(configured)
            except (TypeError, ValueError):
                required = 3
            required = max(3, required)
            required = min(required, len(frame.agents))
            frame.metrics["requiredActiveMissionDeviceCount"] = required
            frame.metrics["activeMissionDeviceDeficit"] = max(
                0, required - len(active_agents)
            )
            return len(active_agents) < required or bool(missing)

        configured = self.config.get("minimumActiveEscortDeviceCount", 2)
        try:
            required = int(configured)
        except (TypeError, ValueError):
            required = 2
        required = max(2, required)
        required = min(required, len(frame.agents))
        frame.metrics["requiredActiveMissionDeviceCount"] = required
        frame.metrics["activeMissionDeviceDeficit"] = max(
            len(missing), required - len(active_agents), 0
        )
        return len(active_agents) < required or bool(missing)

    @staticmethod
    def _active_capture_is_closed(
        frame: RuntimeFrame,
        active_agents: list[AgentFrame],
    ) -> bool:
        """Validate terminal capture against the actual active roster.

        A target is enclosed when at least three active craft leave no empty
        half-plane around it.  This is the topology used by the capture
        contract; distance/slot convergence remains owned by the base solver.
        """
        target = next((item for item in frame.targets if item.visible), None)
        if target is None or len(active_agents) < 3:
            return False
        assessment = assess_containment(
            [(agent.x, agent.y, agent.z) for agent in active_agents],
            (target.x, target.y, target.z),
            required_count=len(active_agents),
            participating=len(active_agents),
            device_types=[agent.type for agent in active_agents],
            minimum_type_counts={"UAV": 1, "USV": 1},
        )
        frame.metrics["activeFormationMaxGapDeg"] = assessment.max_gap_deg
        frame.metrics["activeFormationAllowedGapDeg"] = assessment.allowed_gap_deg
        frame.metrics["activeFormationTargetInside"] = assessment.target_inside
        frame.metrics["activeFormationBlocker"] = assessment.blocker
        return assessment.ready

    @staticmethod
    def _cap_progress(frame: RuntimeFrame) -> None:
        """Keep a recoverable run visibly non-terminal without decreasing it."""
        for key in ("progress", "missionProgress", "captureProgress"):
            value = frame.metrics.get(key)
            if isinstance(value, (int, float)):
                frame.metrics[key] = min(float(value), 0.99)

    def _apply_authoritative_safety(
        self,
        frame: RuntimeFrame,
        agents: Dict[str, AgentFrame],
    ) -> None:
        """Keep task craft clear of real operator-controlled positions.

        The wrapped solver may still predict a detached craft at its old task
        slot.  This final joint projection uses the displayed controlled pose
        as fixed truth, so every active craft avoids that physical vehicle.
        """
        if not self._overrides:
            return
        active = {
            code: (agent.type, (agent.x, agent.y, agent.z))
            for code, agent in agents.items()
            if code not in self._overrides
        }
        if not active:
            return
        fixed = {
            code: (agents[code].type, (agents[code].x, agents[code].y, agents[code].z))
            for code in self._overrides
            if code in agents
        }
        for target in frame.targets:
            if not target.visible:
                continue
            kind = "THREAT_TARGET" if "THREAT" in target.type.upper() else "ESCORT_TARGET"
            fixed[target.code] = (kind, (target.x, target.y, target.z))
        previous = {
            code: (prior.x, prior.y, prior.z)
            for code, prior in self._last_agents.items()
            if code in active
        }
        resolved = self._safety.resolve_group(
            active,
            fixed=fixed,
        )
        adjusted: list[str] = []
        for code, point in resolved.items():
            agent = agents[code]
            old = previous.get(code)
            agent.x, agent.y, agent.z = point.x, point.y, point.z
            if old is not None:
                agent.heading = self.stabilize_heading(
                    code, old, (point.x, point.y, point.z), agent.heading, 12.0,
                )
            if point.adjusted:
                adjusted.append(code)
        frame.metrics["operatorObstacleAvoidanceCount"] = len(adjusted)
        frame.metrics["operatorObstacleAvoidanceDeviceCodes"] = sorted(adjusted)

    def _repair_active_capture_formation(
        self,
        frame: RuntimeFrame,
        active_agents: list[AgentFrame],
    ) -> None:
        """Close a gap left by devices that exited a completed legacy ring."""
        target = next((item for item in frame.targets if item.visible), None)
        if target is None or len(active_agents) < 3:
            return
        active_codes = {agent.code.upper() for agent in active_agents}
        self._capture_replan_positions = {
            code: point
            for code, point in self._capture_replan_positions.items()
            if code in active_codes
        }
        ordered = sorted(
            active_agents,
            key=lambda agent: (
                math.atan2(agent.y - target.y, agent.x - target.x) % (2.0 * math.pi),
                agent.code,
            ),
        )
        step_angle = 2.0 * math.pi / len(ordered)
        phase = math.atan2(
            ordered[0].y - target.y,
            ordered[0].x - target.x,
        ) + step_angle / 2.0
        moving: list[str] = []
        for index, agent in enumerate(ordered):
            code = agent.code.upper()
            start = self._capture_replan_positions.get(
                code, (float(agent.x), float(agent.y), float(agent.z)),
            )
            radius = max(
                # Rebuild outside the abandoned legacy slots.  Keeping the
                # replacement ring on top of stationary/returned craft can
                # make the safety solver reopen the very gap being repaired.
                34.0 if agent.type.upper() == "UAV" else 38.0,
                math.hypot(start[0] - target.x, start[1] - target.y),
            )
            angle = phase + index * step_angle
            desired = (
                target.x + radius * math.cos(angle),
                target.y + radius * math.sin(angle),
                float(agent.z),
            )
            speed_key = "uavSpeedMps" if agent.type.upper() == "UAV" else "usvSpeedMps"
            default_speed = 5.0 if agent.type.upper() == "UAV" else 3.0
            max_step = max(0.05, float(self.config.get(speed_key, default_speed)) * 0.1)
            dx, dy = desired[0] - start[0], desired[1] - start[1]
            distance = math.hypot(dx, dy)
            if distance <= max_step:
                point = desired
            else:
                scale = max_step / distance
                point = (start[0] + dx * scale, start[1] + dy * scale, start[2])
                moving.append(code)
            self._capture_replan_positions[code] = point
            agent.x, agent.y, agent.z = point
            if distance > 1e-6:
                agent.heading = math.degrees(math.atan2(dy, dx)) % 360.0
        frame.metrics["activeFormationReplanning"] = bool(moving)
        frame.metrics["activeFormationReplanningDeviceCodes"] = sorted(moving)

    def _move_towards(
        self,
        code: str,
        override: _DeviceOverride,
        target: tuple[float, float, float],
    ) -> bool:
        dx = target[0] - override.x
        dy = target[1] - override.y
        dz = target[2] - override.z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        speed_key = "uavSpeedMps" if code.startswith("UAV-") else "usvSpeedMps"
        default_speed = 5.0 if code.startswith("UAV-") else 3.0
        max_step = max(0.05, float(self.config.get(speed_key, default_speed)) * 0.1)
        if distance <= max_step:
            override.x, override.y, override.z = target
            return True
        scale = max_step / distance
        override.x += dx * scale
        override.y += dy * scale
        override.z += dz * scale
        if abs(dx) + abs(dy) > 1e-6:
            override.heading = math.degrees(math.atan2(dy, dx)) % 360.0
        return False

    @staticmethod
    def _blend_towards(
        override: _DeviceOverride,
        target: tuple[float, float, float],
        remaining_steps: int,
    ) -> None:
        dx = target[0] - override.x
        dy = target[1] - override.y
        dz = target[2] - override.z
        scale = 1.0 / max(1, remaining_steps)
        override.x += dx * scale
        override.y += dy * scale
        override.z += dz * scale
        if abs(dx) + abs(dy) > 1e-6:
            override.heading = math.degrees(math.atan2(dy, dx)) % 360.0
