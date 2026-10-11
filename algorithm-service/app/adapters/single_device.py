from __future__ import annotations

import math
import time
import heapq
from copy import deepcopy
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
    automatic_departure: bool = False


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
        self._start_from_shore = self.config.get("startFromShore") is True
        self._safe_release_mode = (
            self.config.get("shoreDeploymentMode") == "SAFE_RELEASE"
        )
        self._deployment_frame: RuntimeFrame | None = None
        self._deployment_pending: set[str] = set()
        self._deployment_withdrawn: set[str] = set()
        self._deployment_initial_distance: dict[str, float] = {}
        self._deployment_remaining_distance: dict[str, float] = {}
        self._simulation_elapsed_steps = 0
        self._deployment_elapsed_steps = 0
        self._deployment_complete = not self._start_from_shore
        if self._start_from_shore:
            if not self._return_infrastructure:
                raise ValueError("岸边出发需要配置停机坪和泊位")
            for kind, key in (("UAV", "uavCount"), ("USV", "usvCount")):
                expected = {f"{kind}-{i:03d}" for i in range(1, int(self.config.get(key, 3)) + 1)}
                if expected != {code for code in self._parking_slots if code.startswith(kind)}:
                    raise ValueError("岸边出发需要为每台设备分配独立停机坪或泊位，原场景最多各 3 台")
            self.mission_active = False
            self.base.set_mission_active(False)
        # One FIFO owner per physical north/south berth channel. An owner that
        # is held/stopped keeps its reservation until it safely clears the lane.
        self._berth_channel_queues: dict[float | str, list[str]] = {}
        if self._return_infrastructure:
            for code in self._parking_slots:
                self._return_speed(code)

    def set_mission_active(self, active: bool) -> None:
        self.mission_active = bool(active)
        self.base.set_mission_active(active and self._deployment_complete)

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
            if self._start_from_shore and not self._deployment_complete:
                # A completed return withdraws this device from assembly.
                # Rejoining before deployment ends restores that obligation,
                # including its safe departure route, before the planner runs.
                self._deployment_pending.add(code)
                self._deployment_withdrawn.discard(code)
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
                if slot.kind == "HELIPAD" and code in self._deployment_pending:
                    if self._safe_release_mode:
                        target = self._deployment_target(code)
                        safe_height = max(target[2], slot.approachUpM)
                        override.route = []
                        if override.z < safe_height - 0.05:
                            override.route.append((
                                "TAKING_OFF",
                                (override.x, override.y, safe_height),
                                min(speed, 2.0),
                            ))
                        if math.hypot(target[0] - override.x, target[1] - override.y) > 0.05:
                            override.route.append((
                                "DEPARTING",
                                (target[0], target[1], safe_height),
                                speed,
                            ))
                    else:
                        exit_position = self._air_departure_exit(slot)
                        if override.x > exit_position[0] + 0.05:
                            safe_height = max(override.z, slot.approachUpM)
                            override.route = [
                                ("TAKING_OFF", (override.x, override.y, safe_height), min(speed, 2.0)),
                                ("DEPARTING", (exit_position[0], slot.northM, safe_height), speed),
                            ]
                if slot.kind == "BERTH" and code in self._deployment_pending:
                    if self._safe_release_mode:
                        override.route = self._deployment_berth_release_route(
                            code, override, slot, speed,
                        )
                    else:
                        start = override.route[-1][1] if override.route else (override.x, override.y, override.z)
                        override.route.extend(self._deployment_water_route(code, start, speed))
                if override.route:
                    override.return_stage = override.route[0][0]
                    if slot.kind == "BERTH" and not self._claim_berth_channel(code, slot):
                        override.return_stage = "WAITING_FOR_CHANNEL"
                elif slot.kind == "BERTH":
                    # A cancelled inbound request already north of the guarded
                    # entrance needs no departure reservation.
                    self._release_berth_channel(code)
            if code in self._deployment_pending:
                remaining = self._deployment_route_distance(code, override)
                self._deployment_initial_distance[code] = max(
                    self._deployment_initial_distance.get(code, 0.0), remaining,
                )
                self._deployment_remaining_distance[code] = remaining
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
        if self._start_from_shore and self.mission_active:
            # The planner is deliberately frozen until assembly. Its local
            # sequence cannot measure the takeoff and undocking simulation.
            self._simulation_elapsed_steps += 1
            if not self._deployment_complete:
                self._deployment_elapsed_steps += 1
        if self._start_from_shore and not self._deployment_complete:
            if self._deployment_frame is None:
                self._deployment_frame = deepcopy(self.base.step())
                self._deployment_pending = {agent.code.upper() for agent in self._deployment_frame.agents}
                if self._deployment_pending != set(self._parking_slots):
                    raise ValueError("岸边出发泊位必须覆盖全部实际设备")
                for code, slot in self._parking_slots.items():
                    self._overrides[code] = _DeviceOverride(
                        mode="PARKED", x=slot.eastM, y=slot.northM, z=slot.upM,
                        heading=slot.headingDeg, parking_slot=slot, return_stage="PARKED",
                        automatic_departure=True,
                    )
            frame = deepcopy(self._deployment_frame)
        else:
            frame = self.base.step()
        if self._start_from_shore:
            self.sequence += 1
            frame.sequence = self.sequence
            frame.timestamp = int(time.time() * 1000)
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

        if self._start_from_shore and self.mission_active:
            for code, override in self._overrides.items():
                if override.mode == "PARKED":
                    self.control_device(code, "UAV_RESUME" if code.startswith("UAV-") else "USV_RESUME")

        completed_rejoins: list[str] = []
        for code, override in list(self._overrides.items()):
            agent = agents.get(code)
            if agent is None:
                continue
            if self._start_from_shore and not self.mission_active:
                pass  # PREVIEW and PAUSE never advance takeoff or undocking.
            elif override.mode == "RETURNING":
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

                if code in self._deployment_pending:
                    self._deployment_remaining_distance[code] = (
                        0.0 if code in completed_rejoins
                        else self._deployment_route_distance(code, override)
                    )

            if code not in completed_rejoins:
                agent.x = override.x
                agent.y = override.y
                agent.z = override.z
                agent.heading = override.heading
                agent.role = "DEPLOYMENT" if override.automatic_departure else "RETURNED" if override.mode == "RETURNED" else "OPERATOR_CONTROLLED"
                # Initial shore departure is not an operator "rejoin".  Expose
                # the physical stage to Unity so unaffected craft read as
                # take-off/undocking/assembly; keep REJOINING only for a craft
                # explicitly resumed after hover or hold.
                agent.status = (
                    "ASSEMBLING"
                    if override.automatic_departure
                    and override.mode == "REJOINING"
                    and override.return_stage == "REJOINING"
                    else override.return_stage
                    if override.automatic_departure and override.return_stage
                    else override.mode
                )
                agent.assignedTargetCode = ""

        for code in completed_rejoins:
            self._overrides.pop(code, None)
            self._release_berth_channel(code)
            agents[code].status = "ACTIVE"

        if self._start_from_shore and not self._deployment_complete:
            self._deployment_pending.difference_update(completed_rejoins)
            # A settled withdrawal persists through HOLD/STOP at the berth or
            # pad. Only an explicit RESUME restores its assembly obligation.
            self._deployment_withdrawn.update(
                code for code, override in self._overrides.items() if override.mode == "RETURNED"
            )
            self._deployment_pending.difference_update(self._deployment_withdrawn)
            if not self._deployment_pending and self.mission_active:
                self._deployment_complete = True
                self.base.set_mission_active(True)

        self._last_agents = {code: agent for code, agent in agents.items()}
        states = {
            code: {
                "controlAuthority": (
                    "ALGORITHM" if override.automatic_departure else "RETURNED" if override.mode == "RETURNED" else "OPERATOR"
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
        frame.metrics["operatorControlledDeviceCount"] = sum(not item.automatic_departure for item in self._overrides.values())
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
        if self._start_from_shore:
            eligible = set(self._parking_slots) - self._deployment_withdrawn
            fractions = []
            for code in eligible:
                if code not in self._deployment_pending:
                    fractions.append(1.0)
                elif code in self._deployment_initial_distance:
                    fractions.append(max(0.0, min(
                        1.0,
                        1.0 - self._deployment_remaining_distance[code]
                        / max(self._deployment_initial_distance[code], 1e-9),
                    )))
                else:
                    fractions.append(0.0)
            frame.metrics["simulationElapsedSeconds"] = round(self._simulation_elapsed_steps * 0.1, 1)
            frame.metrics["deploymentElapsedSeconds"] = round(self._deployment_elapsed_steps * 0.1, 1)
            frame.metrics["deploymentProgress"] = round(sum(fractions) / len(fractions), 6) if fractions else 0.0
            frame.metrics["deploymentReadyCount"] = len(eligible - self._deployment_pending)
            frame.metrics["deploymentTotalCount"] = len(self._parking_slots)
            frame.metrics["deploymentWithdrawnCount"] = len(self._deployment_withdrawn)
            frame.metrics["deploymentState"] = "COMPLETE" if self._deployment_complete else "DEPLOYING" if self.mission_active else "PARKED"
            frame.metrics["deploymentMode"] = (
                "SAFE_RELEASE" if self._safe_release_mode else "FULL_ASSEMBLY"
            )
            frame.metrics["deployingDeviceCodes"] = sorted(self._deployment_pending)
            frame.metrics["deployingDeviceCount"] = len(self._deployment_pending)
            if not self._deployment_complete:
                frame.phase = "DEPLOYING" if self.mission_active else "PARKED"
                frame.terminalStatus = None
                frame.metrics["completionBlocker"] = "DEPLOYING" if self.mission_active else "PARKED"
                frame.metrics["controlOverrideActive"] = any(not item.automatic_departure for item in self._overrides.values())
        return frame

    def _deployment_route_distance(self, code: str, override: _DeviceOverride) -> float:
        """Measure the remaining planned path, including takeoff and detours.

        Only rejoining motion updates this value: a channel waiter or held
        device earns no progress from time passing. Resuming recalculates the
        route, and a withdrawn device is accounted for separately from arrivals.
        """
        target = next(agent for agent in self._deployment_frame.agents if agent.code.upper() == code)
        points = [
            (override.x, override.y, override.z),
            *(waypoint for _, waypoint, _ in override.route),
            (target.x, target.y, target.z),
        ]
        return sum(math.dist(first, second) for first, second in zip(points, points[1:]))

    def _deployment_target(self, code: str) -> tuple[float, float, float]:
        target = next(
            agent for agent in self._deployment_frame.agents
            if agent.code.upper() == code.upper()
        )
        return float(target.x), float(target.y), float(target.z)

    def _deployment_berth_release_route(
        self,
        code: str,
        override: _DeviceOverride,
        slot: ParkingSlot,
        speed: float,
    ) -> list[tuple[str, tuple[float, float, float], float]]:
        """Leave the rendered hull and slow-docking zone, then hand off.

        SAFE_RELEASE planners start at this nearby verified point.  Travelling
        all the way to the distant legacy approach anchor before starting the
        mission was safe but made the task appear unresponsive for minutes.
        """
        target = self._deployment_target(code)
        dx, dy = target[0] - slot.eastM, target[1] - slot.northM
        length = math.hypot(dx, dy)
        if length <= 0.05:
            return []
        ux, uy = dx / length, dy / length
        along = (override.x - slot.eastM) * ux + (override.y - slot.northM) * uy
        across = abs((override.x - slot.eastM) * uy - (override.y - slot.northM) * ux)
        if across > 6.0 or along >= length - 0.05:
            return []
        slow_distance = min(8.0, length)
        route: list[tuple[str, tuple[float, float, float], float]] = []
        if along < slow_distance - 0.05:
            route.append((
                "UNDOCKING",
                (
                    slot.eastM + ux * slow_distance,
                    slot.northM + uy * slow_distance,
                    target[2],
                ),
                min(speed, 0.6),
            ))
        if length > slow_distance + 0.05:
            route.append(("EXITING", target, min(speed, 1.5)))
        return route

    def _air_departure_exit(self, slot: ParkingSlot) -> tuple[float, float, float]:
        # Stay above the original island until reaching the same offshore
        # boundary as the verified berth approaches, then descend over water.
        east = min(item.approachEastM for item in self._parking_slots.values() if item.kind == "BERTH")
        return east, slot.northM, slot.approachUpM

    def _deployment_water_route(self, code, start, speed):
        """Route around occupied assembly slots before joining the formation.

        Early arrivals remain stationary until the fleet is ready. A direct
        segment to a later slot must not pass through those waiting boats.
        All waypoints stay offshore, beyond the verified berth entrances.
        """
        fleet = self._deployment_frame.agents
        target = next(agent for agent in fleet if agent.code == code)
        end = (target.x, target.y, target.z)
        obstacles = [(agent.x, agent.y) for agent in fleet if agent.type == "USV" and agent.code != code]
        clearance = 8.0

        def clear(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length2 = dx * dx + dy * dy
            for x, y in obstacles:
                t = min(1.0, max(0.0, ((x - a[0]) * dx + (y - a[1]) * dy) / length2)) if length2 else 0.0
                if math.hypot(a[0] + t * dx - x, a[1] + t * dy - y) < clearance - 1e-6:
                    return False
            return True

        if clear(start, end):
            return []
        nodes = [start, end]
        east_limit = max(start[0], end[0])
        for x, y in obstacles:
            for i in range(16):
                angle = i * math.tau / 16
                point = (x + 9 * math.cos(angle), y + 9 * math.sin(angle), end[2])
                if point[0] <= east_limit and clear(point, point):
                    nodes.append(point)
        queue = [(0.0, 0)]
        distance = {0: 0.0}
        previous = {}
        while queue:
            cost, index = heapq.heappop(queue)
            if cost != distance[index]:
                continue
            if index == 1:
                path = []
                while index:
                    path.append(nodes[index])
                    index = previous[index]
                return [("ASSEMBLING", point, speed) for point in reversed(path)]
            for other, point in enumerate(nodes):
                candidate = cost + math.dist(nodes[index], point)
                if candidate < distance.get(other, math.inf) and clear(nodes[index], point):
                    distance[other] = candidate
                    previous[other] = index
                    heapq.heappush(queue, (candidate, other))
        raise ValueError("岸边集结路径受阻，请调整任务集结点")

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
