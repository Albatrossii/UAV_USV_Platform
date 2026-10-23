"""Headless bridge for the owner's 2026-10-20 command-driven simulator.

The vendor file is kept intact. Native coordinates stay native; only the frame
boundary converts to metres. Operator/shore poses are fed back before planning.
"""
import math
import time
import numpy as np

from app.adapters.base import AlgorithmAdapter
from app.adapters.shore_target_layout import read_escort_berth_layout
from app.navigation.safety import SceneSafetyFilter
from app.schemas import AgentFrame, RuntimeFrame, TargetFrame
from app.vendor import escort_commands_20261020 as source

PROFILE = "escort-20261020"


class _SceneEscortSimulator(source.EscortGuardSimulator):
    """Evaluate capture only after executing hull-safe scene positions."""

    def _update_gbsflacs_capture_state(self):
        if not self.scene_safety_hook():
            return False
        return super()._update_gbsflacs_capture_state()


class CommandEscortAdapter(AlgorithmAdapter):
    code = "ESCORT_GUARD"
    version = PROFILE
    preset_profile = PROFILE
    # The vendor's smallest ring is 0.45 * 2.6 units. At 4 m/unit even
    # the capture radius lay inside the rendered USV/hostile hull clearance.
    # 14 m/unit leaves room for that ring without changing vendor constants.
    scale = 14.0

    def __init__(self, run_id, config=None):
        super().__init__(run_id, config)
        if any(self.config.get(k, 3) != 3 for k in ("uavCount", "usvCount")):
            raise ValueError("新版指令算法固定为 3 UAV + 3 USV")
        count = self.config.get("threatCount", self.config.get("targetCount", 2))
        if isinstance(count, bool) or count not in (1, 2):
            raise ValueError("新版指令算法只支持 1 或 2 个敌船目标")
        self.sim = _SceneEscortSimulator(seed=int(self.config.get("seed", 42)), target_count=count)
        self.sim.reset()
        self.native_origin = self.sim.own_position.copy()
        self.scene_origin = np.zeros(3)
        origin = self.config.get("fleetOrigin", {})
        sea_level = -float(origin.get("upM", 0.0)) if isinstance(origin, dict) else 0.0
        self.scene_origin[2] = sea_level
        poses = self.initial_pose_map()
        protected = poses.get("PROTECTED-001") or poses.get("ESCORT_TARGET")
        if protected:
            self.scene_origin = np.array(self.initial_pose_to_local(protected))
            self.scene_origin[2] = sea_level
        self.berth_layout = read_escort_berth_layout(self.config)
        if self.berth_layout:
            self._place_shore_encounter()
        self.max_steps = {}
        for p in self.sim.platforms:
            speed = float(self.config.get("uavSpeedMps" if p.kind == "UAV" else "usvSpeedMps", 5.0 if p.kind == "UAV" else 3.0))
            if not math.isfinite(speed) or speed <= 0:
                raise ValueError("设备速度必须为正的有限数值")
            # Runner publishes one native step every 0.1 seconds.
            p.max_speed = min(p.max_speed, speed * 0.1 / (self.scale * self.sim.dt))
            self.max_steps[self.code_for(p.identifier)] = speed * 0.1
            pose = poses.get(self.code_for(p.identifier))
            if pose:
                p.position = self.to_native(self.initial_pose_to_local(pose))
                p.position[2] = max(0.25, p.position[2]) if p.kind == "UAV" else 0
                p.altitude = float(p.position[2])
                p.goal = p.position.copy()
        for t in self.sim.threats:
            pose = poses.get(f"THREAT-{t.threat_id:03d}") or poses.get(f"TARGET-{t.threat_id:03d}")
            if pose and not self.berth_layout:
                t.position = self.to_native(self.initial_pose_to_local(pose))
                t.position[2] = 0
        # Cover the existing scene without clipping authoritative initial poses.
        points = [self.sim.own_position, *(p.position for p in self.sim.platforms), *(t.position for t in self.sim.threats)]
        self.sim.world_x_min = min(float(p[0]) for p in points) - 80
        self.sim.world_x_max = max(float(p[0]) for p in points) + 80
        self.sim.world_y_min = min(float(p[1]) for p in points) - 80
        self.sim.world_y_max = max(float(p[1]) for p in points) + 80
        self.unavailable = set()
        self.mission_active = False
        self.last_command = None
        # Keep the previous target/escort physical speeds when enlarging the
        # tactical geometry; enlargement must not make the ships move faster.
        for name in ("own_max_speed", "cruise_speed", "enemy_approach_speed",
                     "enemy_forming_speed", "enemy_controlled_speed",
                     "random_orbit_min_speed", "random_orbit_max_speed"):
            setattr(self.sim, name, getattr(self.sim, name) * 4.0 / self.scale)
        # The orbit setpoint must slow down with linear motion. Otherwise
        # its tangential velocity exceeds the vessel's speed budget and the
        # pursuit controller cuts across the protected vessel's orbit.
        lower = self.to_scene((self.sim.world_x_min, self.sim.world_y_min, 0))
        upper = self.to_scene((self.sim.world_x_max, self.sim.world_y_max, 0))
        self.safety = SceneSafetyFilter({"bounds": [lower[0], upper[0], lower[1], upper[1]], "obstacles": []})
        self.sim.scene_safety_hook = self._apply_scene_safety
        self._safety_applied = False
        self._safety_adjusted = 0
        self._clearance_satisfied = self._has_clearance(self._scene_bodies())

    def _place_shore_encounter(self):
        """Keep every hostile seaward of the convoy, never behind the base.

        Use the same actual berth/approach axis as departure. This placement
        runs only at initialization; runtime motion is never reprojected into
        a formation or teleported to new spawn coordinates.
        """
        layout = self.berth_layout
        direction = np.array(layout.direction)
        sideways = np.array([-direction[1], direction[0]])
        berth = np.array([layout.east, layout.north])

        def segment_distance(point, start, end):
            delta = np.asarray(end) - start
            fraction = np.clip(np.dot(point - start, delta) / max(np.dot(delta, delta), 1e-9), 0, 1)
            return np.linalg.norm(point - start - fraction * delta)

        left, right, bottom, top = layout.shore_bounds
        for offset in (0, 40, -40, 65, -65, 90, -90):
            candidate = berth + direction * 70 + sideways * offset
            # The original scene's seaward side, outside the pad rectangle
            # and the swept berth approach corridors (including hull margin).
            shore_clearance = (left - candidate[0] if direction[0] < 0 else candidate[0] - right) \
                if abs(direction[0]) >= abs(direction[1]) \
                else (bottom - candidate[1] if direction[1] < 0 else candidate[1] - top)
            if shore_clearance >= 25 and all(
                segment_distance(candidate, np.asarray(start), np.asarray(end)) >= 25
                for start, end in zip(layout.berth_positions, layout.approach_positions)
            ):
                self.scene_origin[:2] = candidate
                break
        else:
            raise ValueError("No clear seaward escort start beside the berth corridors")

        self.sim.forward = direction.copy()
        distance = float(self.config.get("threatMinDistanceM", 170))
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("threatMinDistanceM must be positive and finite")
        # Begin outside the native detection radius. Both targets approach
        # from a forward fan, with enough lateral room for independent rings.
        distance = max(distance, (self.sim.sensor_radius + 4) * self.scale)
        for index, threat in enumerate(self.sim.threats):
            angle = 0 if len(self.sim.threats) == 1 else math.radians(-15 + index * 30)
            vector = (direction * math.cos(angle) + sideways * math.sin(angle)) * distance
            point = self.scene_origin[:2] + vector
            threat.position = self.to_native((point[0], point[1], self.scene_origin[2]))
            threat.spawn_angle = math.atan2(vector[1], vector[0])
            threat.spawn_radius = distance / self.scale
        self.sim.last_message = f"护航船在岸外集结，{len(self.sim.threats)} 艘敌船从远方外海接近"

    def _scene_bodies(self):
        bodies = {self.code_for(p.identifier): (p.kind, self.to_scene(p.position)) for p in self.sim.platforms}
        bodies["PROTECTED-001"] = ("ESCORT_TARGET", self.to_scene(self.sim.own_position))
        bodies.update({f"THREAT-{t.threat_id:03d}": ("THREAT_TARGET", self.to_scene(t.position))
                       for t in self.sim.threats if t.state != "waiting"})
        # The shared water solver uses sea-relative altitude, whereas frames
        # use fleet-local ENU (whose sea level can be nonzero).
        return {code: (kind, (float(v[0]), float(v[1]), float(v[2] - self.scene_origin[2])))
                for code, (kind, v) in bodies.items()}

    def _has_clearance(self, bodies):
        items = list(bodies.values())
        return all(math.hypot(a[1][0] - b[1][0], a[1][1] - b[1][1]) + 1e-7
                   >= self.safety.required_separation(a[0], b[0], a[1][2], b[1][2])
                   for i, a in enumerate(items) for b in items[i + 1:])

    def _apply_scene_safety(self):
        if self._safety_applied:
            return self._clearance_satisfied
        proposed = self._scene_bodies()
        previous = {code: point for code, (_, point) in self._step_previous.items()}
        limits = dict(self.max_steps)
        for code, (_, point) in proposed.items():
            if code not in limits:
                limits[code] = math.dist(previous.get(code, point), point)
            elif proposed[code][0] == "UAV":
                old_z = previous[code][2]
                dz = max(-limits[code], min(limits[code], point[2] - old_z))
                proposed[code] = ("UAV", (point[0], point[1], old_z + dz))
                # The common solver caps horizontal travel; reserve the
                # vertical displacement from the same 3-D speed budget.
                limits[code] = math.sqrt(max(0.0, limits[code] ** 2 - dz ** 2))
        resolved = self.safety.resolve_group(proposed, previous, max_steps=limits)
        executed = {code: (proposed[code][0], (p.x, p.y, p.z)) for code, p in resolved.items()}
        # Numerical projection must never publish a remaining overlap from a
        # previously safe configuration. All moving bodies participate, so
        # holding this whole frame is feasible and cannot teleport a target.
        if not self._has_clearance(executed) and self._has_clearance(self._step_previous):
            executed = self._step_previous
        self._safety_adjusted = sum(math.dist(proposed[c][1], v[1]) > 1e-6 for c, v in executed.items())

        def native(code):
            x, y, z = executed[code][1]
            return self.to_native((x, y, z + self.scene_origin[2]))

        for p in self.sim.platforms:
            p.position = native(self.code_for(p.identifier))
            p.altitude = float(p.position[2]) if p.kind == "UAV" else 0.0
        self.sim.own_position = native("PROTECTED-001")
        for t in self.sim.threats:
            if t.state != "waiting":
                t.position = native(f"THREAT-{t.threat_id:03d}")
                if t.state == "orbiting":
                    # Collision avoidance can hold a ship while the vendor's
                    # virtual orbit angle advances. Feed the executed bearing
                    # back so the next waypoint cannot run ahead and pull the
                    # ship through the inside of the protected orbit.
                    relative = t.position[:2] - self.sim.own_position[:2]
                    t.controlled_angle = math.atan2(relative[1], relative[0])
        self.sim._sync_gbsflacs_environment()
        self._safety_applied = True
        self._clearance_satisfied = self._has_clearance(executed)
        return self._clearance_satisfied

    @staticmethod
    def code_for(identifier):
        return f"{'UAV' if identifier[0] == 'U' else 'USV'}-{int(identifier[1:]):03d}"

    def to_scene(self, point):
        return self.scene_origin + (np.asarray(point) - self.native_origin) * self.scale

    def to_native(self, point):
        return self.native_origin + (np.asarray(point) - self.scene_origin) / self.scale

    def reconcile_execution(self, agents, previous_agents, unavailable_codes=()):
        actual = {a.code: a for a in agents}
        self.unavailable = set(unavailable_codes)
        for p in self.sim.platforms:
            a = actual.get(self.code_for(p.identifier))
            if a:
                p.position = self.to_native((a.x, a.y, a.z))
                p.altitude = float(p.position[2]) if p.kind == "UAV" else 0

    def rejoin_target(self, device_code):
        for p in self.sim.platforms:
            if self.code_for(p.identifier) == device_code:
                return tuple(self.to_scene(p.goal if p.goal is not None else p.position))
        return None

    def preset_definition(self, preset_id, profile):
        if profile != PROFILE or type(preset_id) is not int or not 0 <= preset_id <= 20:
            raise ValueError("PRESET_INVALID")
        words = source.PRESET_CAPTURE_COMMANDS.get(str(preset_id), "cancel").split()
        targets = [word for word in words[1:] if word.startswith("T")]
        if any(int(word[1:]) not in {t.threat_id for t in self.sim.spawned_threats} for word in targets):
            raise ValueError("PRESET_TARGET_MISSING")
        return words

    def execute_preset(self, preset_id, profile):
        self.preset_definition(preset_id, profile)
        # Native formation/capture uses all six participants. Never let hidden
        # operator-controlled craft contribute fictitious capture evidence.
        if self.unavailable:
            raise ValueError("PRESET_DEVICE_OVERRIDE")
        if preset_id == 20:
            if not self.sim.start_gbsflacs_capture():
                raise ValueError("PRESET_CAPTURE_NOT_READY")
        else:
            # The vendor starts the first chase via cancel_to_escort(), which
            # also clears saved capture teams. A device task may replace current
            # motion, but only explicit withdrawal should erase operator groups.
            saved_groups = dict(self.sim.manual_capture_assignments) if 1 <= preset_id <= 12 else None
            try:
                self.sim.execute_command(str(preset_id))
            except ValueError as error:
                raise ValueError("PRESET_REJECTED") from error
            finally:
                if saved_groups is not None:
                    self.sim.manual_capture_assignments = saved_groups
        self.last_command = preset_id

    def step(self):
        self.sequence += 1
        # Pause native tactical progress while an operator owns any craft; the
        # wrapper still performs the requested hover/return/rejoin movements.
        if self.mission_active and not self.unavailable:
            self._step_previous = self._scene_bodies()
            self._safety_applied = False
            self.sim.step()
            self._apply_scene_safety()
        agents = []
        for p in self.sim.platforms:
            code = self.code_for(p.identifier)
            xyz = tuple(float(v) for v in self.to_scene(p.position))
            heading = self.stabilize_heading(code, getattr(self, "previous", {}).get(code), xyz, 0, 4)
            agents.append(AgentFrame(code, p.kind, *xyz, heading, p.role.upper(),
                                     assignedTargetCode=f"THREAT-{p.assigned_threat_id:03d}" if p.assigned_threat_id else ""))
        self.previous = {a.code: (a.x, a.y, a.z) for a in agents}
        targets = [TargetFrame("PROTECTED-001", "ESCORT_TARGET", *map(float, self.to_scene(self.sim.own_position)), state="ESCORTING")]
        targets += [TargetFrame(f"THREAT-{t.threat_id:03d}", "THREAT_TARGET", *map(float, self.to_scene(t.position)),
                                visible=t.state != "waiting", state=t.state.upper()) for t in self.sim.threats]
        previous_targets = getattr(self, "previous_targets", {})
        for target in targets:
            if target.type == "ESCORT_TARGET":
                direction = self.sim.forward
            else:
                own = targets[0]
                direction = (own.x - target.x, own.y - target.y)
            fallback = math.degrees(math.atan2(direction[1], direction[0]))
            target.heading = self.stabilize_heading(
                target.code, previous_targets.get(target.code),
                (target.x, target.y, target.z), fallback, 4)
        self.previous_targets = {t.code: (t.x, t.y, t.z) for t in targets}
        message = self.sim.last_message
        for original, replacement in (("F4/0", "0号指令"), ("19/G", "19号指令"), ("20/J", "20号指令"),
                                      ("N 新建目标", "重新生成场景以新建目标")):
            message = message.replace(original, replacement)
        metrics = {
            "algorithmCommandProfile": PROFILE, "lastPresetId": self.last_command,
            "initialEncounterLayout": "SHORE_FORWARD" if self.berth_layout else "NATIVE",
            "algorithmMessage": message,
            "captureRadiusM": source.GBSFLACS_CAPTURE_RADIUS * self.scale,
            "hullSafetyAdjustedCount": self._safety_adjusted,
            "hullClearanceSatisfied": self._clearance_satisfied,
            "captureComplete": all(t.state == "captured" for t in self.sim.threats),
            "algorithmMode": "CAPTURE" if self.sim.gbsflacs_active or self.sim.capture_success else "PURSUIT" if self.sim.pursuit_assignments else "ESCORT" if self.sim.escort_only else "GUARD",
            "operatorHold": bool(self.unavailable),
            "fixedAssignments": {self.code_for(self.sim.platforms[i].identifier): f"THREAT-{t:03d}" for i, t in self.sim.manual_capture_assignments.items()},
            "deviceAlgorithmStates": {a.code: {"role": a.role, "targetCode": a.assignedTargetCode} for a in agents},
            "threatCount": len(self.sim.threats), "visibleThreatCount": len(self.sim.spawned_threats),
            "capturedThreatCount": sum(t.state == "captured" for t in self.sim.threats),
            "progress": sum(t.state == "captured" for t in self.sim.threats) / len(self.sim.threats),
        }
        return RuntimeFrame(self.run_id, self.code, self.sequence, int(time.time()*1000), self.sim.phase,
                            agents, targets, metrics=metrics)
