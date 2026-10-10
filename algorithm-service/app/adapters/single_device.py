from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict

from app.adapters.base import AlgorithmAdapter
from app.adapters.return_infrastructure import ParkingSlot, validate_return_infrastructure
from app.schemas import AgentFrame, RuntimeFrame


@dataclass
class _DeviceOverride:
    mode: str
    x: float
    y: float
    z: float
    heading: float
    rejoin_steps: int = 0
    return_stage: str | None = None
    parking_slot: ParkingSlot | None = None
    route: list[tuple[str, tuple[float, float, float], float]] = field(default_factory=list)


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
        self._return_infrastructure = validate_return_infrastructure(self.config)
        self._parking_slots = {
            slot.deviceCode: slot for slot in self._return_infrastructure.slots
        } if self._return_infrastructure else {}
        self._infrastructure_fleet_validated = False
        # One FIFO owner per physical north/south berth channel. An owner that
        # is held/stopped keeps its reservation until it safely clears the lane.
        self._berth_channel_queues: dict[float | str, list[str]] = {}
        if self._return_infrastructure:
            for code in self._parking_slots:
                self._return_speed(code)

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
        # A landing command must reach an actual helipad, never freeze in midair.
        if command == "UAV_LAND":
            if not self._return_infrastructure or code not in self._parking_slots:
                raise ValueError("LANDING_SITE_UNAVAILABLE: 未配置原场景停机坪，不能执行降落")
            command = "UAV_RETURN"
        if command not in self.SUPPORTED_COMMANDS:
            raise ValueError("unsupported single-device command")
        if command.startswith("UAV_") and not code.startswith("UAV-"):
            raise ValueError("UAV command requires a UAV target")
        if command.startswith("USV_") and not code.startswith("USV-"):
            raise ValueError("USV command requires a USV target")
        current = self._last_agents.get(code)
        if current is None:
            raise ValueError("unknown device code")
        if (command in {"UAV_RETURN", "USV_RETURN"}
                and self._return_infrastructure
                and self._return_infrastructure.version == "scene-existing-v1"
                and code not in self._parking_slots):
            kind = "停机坪" if code.startswith("UAV-") else "岸边泊位"
            raise ValueError(f"RETURN_CAPACITY_EXCEEDED: 原场景仅有 3 个已验证{kind}，{code} 未分配返航位置")

        if command in {"UAV_RESUME", "USV_RESUME"}:
            if code not in self._overrides:
                return "ALGORITHM"
            override = self._overrides[code]
            if override.mode == "REJOINING":
                return override.mode
            override.mode = "REJOINING"
            override.rejoin_steps = 20
            override.route.clear()
            override.return_stage = None
            slot = self._parking_slots.get(code)
            if slot is not None:
                override.parking_slot = slot
                speed = self._return_speed(code)
                if slot.kind == "HELIPAD" and self._needs_helipad_departure(override, slot):
                    override.route = [("TAKING_OFF", (override.x, override.y, slot.approachUpM), min(speed, 2.0))]
                elif slot.kind == "BERTH" and self._uses_existing_scene:
                    override.route = self._existing_berth_departure_route(override, slot, speed)
                elif slot.kind == "BERTH" and override.y < slot.approachNorthM + 8.0:
                    override.route = [
                        ("UNDOCKING", (slot.approachEastM, override.y, 0.0), min(speed, 0.6)),
                        ("EXITING", (slot.approachEastM, slot.approachNorthM + 8.0, 0.0), speed),
                    ]
                if override.route:
                    override.return_stage = override.route[0][0]
                    if slot.kind == "BERTH" and not self._claim_berth_channel(code, slot):
                        override.return_stage = "WAITING_FOR_CHANNEL"
                elif slot.kind == "BERTH":
                    # A cancelled inbound request already north of the guarded
                    # entrance needs no departure reservation.
                    self._release_berth_channel(code)
            return override.mode

        if command in {"UAV_RETURN", "USV_RETURN"}:
            previous = self._overrides.get(code)
            if previous is not None and previous.mode in {"RETURNING", "RETURNED"}:
                return previous.mode
            mode = "RETURNING"
        elif command == "USV_STOP":
            mode = "STOPPED"
        else:
            mode = "HOLDING"
        override = _DeviceOverride(
            mode=mode,
            x=float(current.x),
            y=float(current.y),
            z=float(current.z),
            heading=float(current.heading),
        )
        if mode == "RETURNING" and code in self._parking_slots:
            slot = self._parking_slots[code]
            override.parking_slot = slot
            speed = self._return_speed(code)
            if slot.kind == "HELIPAD":
                safe_height = max(override.z, slot.approachUpM)
                override.route = [
                    ("ASCENDING", (override.x, override.y, safe_height), min(speed, 2.0)),
                    ("CRUISING", (slot.eastM, slot.northM, safe_height), speed),
                    ("DESCENDING", slot.position, min(speed, 1.0)),
                ]
            else:
                if abs(override.z - self._return_infrastructure.sea_level) > 1e-6 or (not self._uses_existing_scene and override.y <= self._return_infrastructure.shore.northMaxM):
                    raise ValueError("USV return requires a current pose in validated water at sea level")
                if self._uses_existing_scene:
                    override.route = [
                        ("APPROACHING", (slot.approachEastM, slot.approachNorthM, slot.approachUpM), speed),
                        ("APPROACHING", self._berth_slow_docking_point(slot), min(speed, 1.5)),
                        ("DOCKING", slot.position, min(speed, 0.6)),
                    ]
                else:
                    override.route = [
                        ("APPROACHING", (slot.approachEastM, slot.approachNorthM, 0.0), speed),
                        ("CHANNEL", (slot.approachEastM, slot.northM, 0.0), min(speed, 1.5)),
                        ("DOCKING", slot.position, min(speed, 0.6)),
                    ]
            override.return_stage = override.route[0][0]
            if slot.kind == "BERTH" and not self._claim_berth_channel(code, slot):
                override.return_stage = "WAITING_FOR_CHANNEL"
        elif mode in {"HOLDING", "STOPPED"}:
            slot = self._parking_slots.get(code)
            if slot is not None and slot.kind == "BERTH":
                queue = self._berth_channel_queues.get(self._berth_channel_key(slot), [])
                if queue and queue[0] == code:
                    override.parking_slot = slot
                    override.return_stage = "HOLDING_CHANNEL"
                else:
                    # Cancelling a waiter does not leave a stale queue entry.
                    self._release_berth_channel(code)
        self._overrides[code] = override
        return mode

    def step(self) -> RuntimeFrame:
        frame = self.base.step()
        frame.algorithmCode = self.code
        agents = {agent.code.upper(): agent for agent in frame.agents}
        if self._return_infrastructure and not self._infrastructure_fleet_validated:
            existing_scene = self._return_infrastructure.version == "scene-existing-v1"
            if ((not set(self._parking_slots).issubset(agents)) if existing_scene
                    else set(agents) != set(self._parking_slots)):
                raise ValueError("returnInfrastructure slots must match the complete prepared fleet")
            for code, agent in agents.items():
                slot = self._parking_slots.get(code)
                expected_type = ("UAV" if slot.kind == "HELIPAD" else "USV") if slot else code[:3]
                if agent.type.upper() != expected_type or not all(math.isfinite(float(value)) for value in (agent.x, agent.y, agent.z)):
                    raise ValueError("returnInfrastructure requires matching device types and finite initial poses")
                if expected_type == "USV" and (abs(agent.z - self._return_infrastructure.sea_level) > 1e-6 or (not existing_scene and agent.y <= self._return_infrastructure.shore.northMaxM)):
                    raise ValueError("returnInfrastructure initial USVs must be in northern water at sea level" if not existing_scene
                                     else "returnInfrastructure initial USVs must remain at sea level")
            self._infrastructure_fleet_validated = True
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
                slot = override.parking_slot
                target = (*slot.position, slot.headingDeg) if slot else self._home[code]
                arrived = self._advance_route(code, override) if slot else self._move_towards(code, override, target[:3])
                if arrived:
                    override.mode = "RETURNED"
                    override.heading = target[3]
                    if slot:
                        override.return_stage = "PARKED"
                        self._release_berth_channel(code)
            elif override.mode == "REJOINING":
                if override.route:
                    if self._advance_route(code, override):
                        self._release_berth_channel(code)
                else:
                    target = (float(agent.x), float(agent.y), float(agent.z))
                    if override.parking_slot:
                        # Fixed-shore departures may be far from the fleet.
                        # Rejoin only on a speed-limited intercept of its live
                        # target, never by accelerating a fixed 20-frame blend.
                        override.return_stage = "REJOINING"
                        if self._move_towards(code, override, target, self._return_speed(code)):
                            completed_rejoins.append(code)
                    else:
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
            self._release_berth_channel(code)
            agents[code].status = "ACTIVE"

        self._last_agents = {code: agent for code, agent in agents.items()}
        states = {
            code: {
                "controlAuthority": (
                    "RETURNED" if override.mode == "RETURNED" else "OPERATOR"
                ),
                "motionState": override.mode,
                "returnedAtHome": override.mode == "RETURNED",
                **({"returnStage": override.return_stage,
                    "parkingSlot": override.parking_slot.deviceCode,
                    "parkingKind": override.parking_slot.kind} if override.parking_slot else {}),
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
        if self._return_infrastructure:
            frame.metrics["returnInfrastructure"] = self._return_infrastructure.metadata()
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

    @staticmethod
    def _needs_helipad_departure(override: _DeviceOverride, slot: ParkingSlot) -> bool:
        # A reserved parking slot does not mean the aircraft is parked there.
        # Require its actual pose to be in the pad's vertical departure column.
        # This also covers an interrupted landing/takeoff followed by HOLD/STOP,
        # whose mode no longer carries the original departure history.
        # The small tolerance accommodates pose rounding, not a new flight zone.
        above_pad = math.hypot(override.x - slot.eastM, override.y - slot.northM) <= 0.5
        return above_pad and slot.upM - 0.05 <= override.z < slot.approachUpM

    def _move_towards(
        self,
        code: str,
        override: _DeviceOverride,
        target: tuple[float, float, float],
        speed_limit: float | None = None,
    ) -> bool:
        dx = target[0] - override.x
        dy = target[1] - override.y
        dz = target[2] - override.z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        speed_key = "uavSpeedMps" if code.startswith("UAV-") else "usvSpeedMps"
        default_speed = 5.0 if code.startswith("UAV-") else 3.0
        max_step = max(0.05, float(self.config.get(speed_key, default_speed)) * 0.1) if speed_limit is None else speed_limit * 0.1
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

    def _return_speed(self, code: str) -> float:
        uav = code.startswith("UAV-")
        speed = float(self.config.get("uavSpeedMps" if uav else "usvSpeedMps", 5.0 if uav else 3.0))
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("return infrastructure requires a finite positive device speed")
        return min(15.0 if uav else 4.0, speed)

    def _advance_route(self, code: str, override: _DeviceOverride) -> bool:
        if not override.route:
            return True
        slot = override.parking_slot
        if slot and slot.kind == "BERTH" and not self._claim_berth_channel(code, slot):
            override.return_stage = "WAITING_FOR_CHANNEL"
            return False
        stage, target, speed = override.route[0]
        override.return_stage = stage
        arrived = self._move_towards(code, override, target, speed)
        if arrived:
            override.route.pop(0)
        return not override.route

    @property
    def _uses_existing_scene(self) -> bool:
        return bool(self._return_infrastructure and self._return_infrastructure.version == "scene-existing-v1")

    def _berth_channel_key(self, slot: ParkingSlot) -> float | str:
        # The three native corridors are separately verified and never share
        # a lane merely because their offshore entrances have the same east.
        return f"native:{slot.deviceCode}" if self._uses_existing_scene else round(slot.approachEastM, 6)

    @staticmethod
    def _berth_slow_docking_point(slot: ParkingSlot) -> tuple[float, float, float]:
        dx, dy = slot.eastM - slot.approachEastM, slot.northM - slot.approachNorthM
        distance = math.hypot(dx, dy)
        return slot.eastM - dx * 8.0 / distance, slot.northM - dy * 8.0 / distance, slot.upM

    def _existing_berth_departure_route(
        self, override: _DeviceOverride, slot: ParkingSlot, speed: float,
    ) -> list[tuple[str, tuple[float, float, float], float]]:
        dx, dy = slot.eastM - slot.approachEastM, slot.northM - slot.approachNorthM
        length = math.hypot(dx, dy)
        along = ((override.x - slot.approachEastM) * dx + (override.y - slot.approachNorthM) * dy) / length
        across = abs((override.x - slot.approachEastM) * dy - (override.y - slot.approachNorthM) * dx) / length
        if along <= 1e-6 or across > 6.0:
            # No part of the verified 12m-wide dock corridor was entered; an
            # interrupted offshore approach can rejoin the live target directly.
            return []
        route = []
        if along > length - 8.0:
            route.append(("UNDOCKING", self._berth_slow_docking_point(slot), min(speed, 0.6)))
        route.append(("EXITING", (slot.approachEastM, slot.approachNorthM, slot.approachUpM), min(speed, 1.5)))
        return route

    def _claim_berth_channel(self, code: str, slot: ParkingSlot) -> bool:
        queue = self._berth_channel_queues.setdefault(self._berth_channel_key(slot), [])
        if code not in queue:
            queue.append(code)
        return queue[0] == code

    def _release_berth_channel(self, code: str) -> None:
        for key, queue in list(self._berth_channel_queues.items()):
            if code in queue:
                queue.remove(code)
            if not queue:
                self._berth_channel_queues.pop(key, None)

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
