from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

from app.adapters.base import AlgorithmAdapter
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

        self._last_agents = {code: agent for code, agent in agents.items()}
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
        returned = [
            code for code, override in self._overrides.items()
            if override.mode == "RETURNED"
        ]
        blocking = [
            code for code, override in self._overrides.items()
            if override.mode != "RETURNED"
        ]
        frame.metrics["singleDeviceControlEnabled"] = True
        frame.metrics["operatorControlledDeviceCount"] = len(states)
        frame.metrics["deviceControlStates"] = states
        frame.metrics["returningDeviceCount"] = len(returning)
        frame.metrics["returnedDeviceCount"] = len(returned)
        frame.metrics["returningDeviceCodes"] = sorted(returning)
        frame.metrics["returnedDeviceCodes"] = sorted(returned)
        frame.metrics["activeMissionDeviceCount"] = len(agents) - len(returned)
        if blocking:
            # A group completion cannot be authoritative while a required
            # member is still travelling home or waiting for an operator
            # decision.  A device that has reached home is a settled task
            # withdrawal and no longer blocks the mission terminal state.
            frame.terminalStatus = None
            frame.metrics["controlOverrideActive"] = True
            frame.metrics["completionBlocker"] = (
                "WAITING_FOR_RETURN" if returning else "OPERATOR_OVERRIDE"
            )
            frame.metrics["completionBlockerDeviceCodes"] = sorted(blocking)
        elif returned:
            frame.metrics["controlOverrideActive"] = False
            frame.metrics["completionBlocker"] = "NONE"
            if self._remaining_force_is_insufficient(frame, returned):
                # A withdrawal is a recoverable degraded condition, not an
                # immediate mission failure. Keep the runner alive so the
                # operator can rejoin a returned device or choose another
                # course of action.
                frame.terminalStatus = None
                frame.metrics["completionBlocker"] = "INSUFFICIENT_ACTIVE_FORCE"
                frame.metrics["missionDegradedReason"] = "INSUFFICIENT_ACTIVE_FORCE"
        return frame

    def _remaining_force_is_insufficient(
        self,
        frame: RuntimeFrame,
        returned: list[str],
    ) -> bool:
        """Reject a false success when permanent withdrawals break quorum."""
        active_agents = [agent for agent in frame.agents if agent.code.upper() not in returned]
        if self.code.startswith("GB_SFLA_CS"):
            configured = self.config.get("minimumActiveMissionDeviceCount", 5)
            try:
                required = int(configured)
            except (TypeError, ValueError):
                required = 5
            if required <= 0:
                required = 5
            required = min(required, len(frame.agents))
            frame.metrics["requiredActiveMissionDeviceCount"] = required
            frame.metrics["activeMissionDeviceDeficit"] = max(
                0, required - len(active_agents)
            )
            return len(active_agents) < required

        active_types = {agent.type.upper() for agent in active_agents}
        frame.metrics["requiredActiveDeviceTypes"] = ["UAV", "USV"]
        missing = sorted({"UAV", "USV"} - active_types)
        frame.metrics["missingActiveDeviceTypes"] = missing
        frame.metrics["activeMissionDeviceDeficit"] = len(missing)
        return bool(missing)

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
