from __future__ import annotations

import contextlib
import importlib
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from app.adapters.base import AlgorithmAdapter
from app.capture import (
    FormationSlot,
    assess_capture,
    assess_containment,
    build_formation_slots,
    maximum_capture_gap_deg,
)
from app.navigation import TASK_CENTER_SCENE_MAP, SafePoint, SceneSafetyFilter
from app.schemas import AgentFrame, RuntimeFrame, TargetFrame


class CaptureAdapter(AlgorithmAdapter):
    code = "GB_SFLA_CS"

    def __init__(self, run_id: int, config: Dict[str, object] | None = None) -> None:
        super().__init__(run_id, config)
        fleet_size = max(1, min(128, int(self.config.get("uavCount", 3)))) + max(1, min(128, int(self.config.get("usvCount", 3))))
        self.required_pursuit_distance = 80.0 if fleet_size < 20 else 100.0 if fleet_size < 40 else 120.0
        matplotlib_cache = Path(tempfile.gettempdir()) / "uav-usv-matplotlib"
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))
        with contextlib.redirect_stdout(sys.stderr):
            source = importlib.import_module("app.vendor.gb_sfla_cs_source")
            source.SEED = int(self.config.get("seed", 42))
            # The vendor module seeds only at import time. Re-seed for every
            # standalone run so the same UI seed is reproducible regardless
            # of which fleet sizes were simulated earlier in the process.
            np.random.seed(source.SEED)
            if hasattr(source, "random"):
                source.random.seed(source.SEED)
            # Counts are driven by the scenario device list.  The algorithm
            # core deliberately has no 16/100 cut-off; UI/protocol limits are
            # a separate compatibility concern and must not silently discard
            # agents that the mission already contains.
            source.UAV_COUNT = max(1, min(128, int(self.config.get("uavCount", 3))))
            source.USV_COUNT = max(1, min(128, int(self.config.get("usvCount", 3))))
            source.TARGET_COUNT = max(1, min(1, int(self.config.get("targetCount", 1))))
            # A configured fleet is one containment team. Do not declare the
            # target captured after only the first six arrive and strand the
            # remaining devices outside the ring.
            fleet_size = source.UAV_COUNT + source.USV_COUNT
            # Fewer than three points cannot geometrically surround a target.
            # Such fleets remain valid intercept/tracking runs but can never
            # report a false encirclement success.
            self.required_capture_agents = fleet_size if fleet_size >= 3 else fleet_size + 1
            # The adapter validates against the target's post-motion position.
            # Disable the vendor core's pre-motion irreversible latch; when it
            # fires during pursuit it repeatedly prints success and rebuilds
            # stale guard state, which both obscures logs and hurts WebGL
            # smoothness.
            source.MIN_CAPTURE_AGENTS = fleet_size + 1
            # The Task Center maps three native units to one scene metre.
            # Surface craft finish on a 22 m ring; UAVs finish on a 30 m
            # triangle at 26 m altitude.  The two layers remain readable in
            # both the fixed 2-D frame and Unity's fixed experiment camera.
            # Include the outer staging corridor and the UAV altitude in the
            # planner's 3-D capture sphere. The old 135-unit sphere clipped a
            # 120-unit aerial staging circle to 112 units horizontally, so an
            # aircraft could never reach the very corridor it was told to use.
            self.usv_slot_specs = build_formation_slots(
                source.USV_COUNT,
                kind="USV",
                phase=-math.pi / 2.0,
                minimum_radius=22.0,
                minimum_spacing=13.8,
            )
            # Keep the two rendered fleets in separate radial bands.  The
            # former fixed 30 m UAV ring sat only 8 m outside the 22 m USV
            # ring; from 6+6 upward several air/surface slots shared the same
            # bearing and the presentation safety layer correctly pushed one
            # of them out of its assigned slot. Keep four additional scene
            # metres beyond rendered hull clearance so interpolation, heading
            # changes and high-count traffic do not consume the guard band.
            usv_outer_radius = max(slot.radius for slot in self.usv_slot_specs)
            # Keep a wider vertical/horizontal safety annulus between UAV and
            # USV rings.  At 9+9 the previous 14 m gap let the global solver
            # push one UAV through the inner USV ring, making the scene look
            # open on one side even though the aggregate hull was closed.
            uav_minimum_radius = max(46.0, usv_outer_radius + 24.0)
            # Keep 30+ aircraft on multiple readable rings instead of letting
            # a large starting radius make one extremely dense circle.
            uav_minimum_spacing = max(
                8.4,
                2.0 * uav_minimum_radius * math.sin(math.pi / 24.0)
                if source.UAV_COUNT > 24
                else 8.4,
            )
            self.uav_slot_specs = build_formation_slots(
                source.UAV_COUNT,
                kind="UAV",
                # Stagger the aerial layer by half of one aerial slot.  The
                # previous max(3, count) special case put a 2+2 fleet on two
                # overlapping diagonals and created a 120-degree opening even
                # after every craft reached its assigned pose.
                phase=-math.pi / 2.0 + math.pi / max(2, source.UAV_COUNT),
                minimum_radius=uav_minimum_radius,
                minimum_spacing=uav_minimum_spacing,
                altitude=26.0,
            )
            self.usv_formation_radius_scene = self.usv_slot_specs[0].radius
            outer_radius = max(
                [slot.radius for slot in self.usv_slot_specs + self.uav_slot_specs],
                default=30.0,
            )
            self.outer_formation_radius = outer_radius
            # Keep the algorithm origin centred for large concentric fleets.
            # Staging traffic receives an additional 35 scene metres outside
            # the outer formation ring.
            # The old 150-unit half arena left only 50 scene metres per side,
            # so a target starting near the fleet was captured before a chase
            # could become visible. Reserve a complete 80-120 m escape run,
            # staging space and a final ring inside the algorithm arena.
            self.internal_center = max(
                (self.required_pursuit_distance + 80.0) * 3.0,
                (outer_radius + 45.0) * 3.0,
            )
            source.ARENA_SIZE_XY = self.internal_center * 2.0
            source.ARENA_SIZE_Z = 180
            source.CAPTURE_RADIUS = (outer_radius + 40.0) * 3.0
            source.CAPTURE_FORMATION_USV_RADIUS = self.usv_formation_radius_scene * 3.0
            source.CAPTURE_FORMATION_UAV_RADIUS = 90
            source.CAPTURE_FORMATION_UAV_ALTITUDE = 75
            source.CAPTURE_FORMATION_PHASE = -math.pi / 2
            source.CAPTURE_FORMATION_USV_SLOT_SPECS = [
                (slot.radius * 3.0, slot.angle, 0.0, slot.ring)
                for slot in self.usv_slot_specs
            ]
            source.CAPTURE_FORMATION_UAV_SLOT_SPECS = [
                (slot.radius * 3.0, slot.angle, (slot.altitude - 9.0) * 4.4, slot.ring)
                for slot in self.uav_slot_specs
            ]
            source.TARGET_RUN_NUM = 70
            # The vendor defaults are algorithm-scale values, not render-safe
            # world speeds.  At 10 frames/s they previously produced 13-22
            # Unity units/s and looked like teleportation.  These limits keep
            # a visible transit phase before the fleet closes the ring.
            configured_uav_speed = min(15.0, max(0.1, float(self.config.get("uavSpeedMps", 5.0))))
            configured_usv_speed = min(4.0, max(0.1, float(self.config.get("usvSpeedMps", 3.0))))
            # Preserve the vendor model's native-unit calibration while making
            # the two speed controls authoritative.  The algorithm slows again
            # during final formation; these are transit limits, not a constant
            # animation multiplier.
            source.V_MAX_UAV = 2.0 * configured_uav_speed / 5.0
            # The UI value is patrol cruise. During an escape chase a surface
            # interceptor may accelerate, up to the existing 4 m/s physical
            # limit, so a 1.5-2.8 m/s target remains catchable.
            self.usv_pursuit_mps = min(4.0, max(3.2, configured_usv_speed))
            source.V_MAX_USV = 0.9 * self.usv_pursuit_mps
            # Direct capture is an escape/chase experiment.  Historical
            # callers may still send targetBehavior=STATIC, but honouring it
            # made the pursuit-distance gate impossible to finish and brought
            # back the exact "enemy only spins in place" failure reported in
            # the WebGL view.  The target therefore always starts with a
            # visible, surface-speed-calibrated escape run.
            # Enemy surface motion is independent from the friendly cruise
            # slider.  Every run starts with a deterministic 1.5-2.2 m/s
            # escape speed, while still leaving a useful speed advantage for
            # the 3-4 m/s friendly USV fleet.
            speed_seed = (source.SEED * 1103515245 + 12345) & 0xFFFF
            self.target_cruise_mps = 1.5 + speed_seed / 65535.0 * 0.7
            self.target_max_mps = 2.8
            # Target motion is executed in scene metres below. Letting the
            # vendor core also move it applied two unrelated motion models and
            # produced frequent heading changes with almost no net travel.
            source.TARGET_SPEED = 0
            source.TARGET_IS_STATIC = 1 if source.TARGET_SPEED == 0 else 0
            source.UAV_Z_MIN, source.UAV_Z_MAX = 18, 54
            # The multi-target coordinator runs one isolated one-target
            # solver per capture group. The vendor module stores these values
            # as globals, so retain the exact profile owned by this instance
            # and reactivate it before every step.
            self._vendor_runtime_config = {
                name: getattr(source, name)
                for name in (
                    "UAV_COUNT", "USV_COUNT", "TARGET_COUNT",
                    "MIN_CAPTURE_AGENTS", "ARENA_SIZE_XY", "ARENA_SIZE_Z",
                    "CAPTURE_RADIUS", "CAPTURE_FORMATION_USV_RADIUS",
                    "CAPTURE_FORMATION_UAV_RADIUS",
                    "CAPTURE_FORMATION_UAV_ALTITUDE",
                    "CAPTURE_FORMATION_PHASE",
                    "CAPTURE_FORMATION_USV_SLOT_SPECS",
                    "CAPTURE_FORMATION_UAV_SLOT_SPECS", "TARGET_RUN_NUM",
                    "V_MAX_UAV", "V_MAX_USV", "TARGET_SPEED",
                    "TARGET_IS_STATIC", "UAV_Z_MIN", "UAV_Z_MAX",
                )
            }
            self.source = source
            self.env = source.SwarmEnv3D()
            self._reset_positions()
            self._initial_frame_pending = True
        scene_extent = max(
            145.0,
            self.internal_center / 3.0 - 12.0,
            # The spawn contract may already shift the target 90 m away from
            # a compact fleet. Reserve another full pursuit plus the final
            # ring on either side so a boundary turn is a tactical choice,
            # not something forced immediately after START.
            outer_radius + self.required_pursuit_distance * 2.0 + 70.0,
        )
        self.safety = SceneSafetyFilter({
            "bounds": [-scene_extent, scene_extent, -scene_extent, scene_extent],
            "obstacles": list(TASK_CENTER_SCENE_MAP.get("obstacles", [])),
        })
        # Keep enough open water around the target for the complete outer
        # ring.  Previously the target used almost the entire rectangular
        # arena for its 80 m run, then stopped where a 30 m ring could no
        # longer fit.  The Unity terrain makes that abstract edge look like a
        # shoreline, which exposed the bug as a boat parked against land.
        self.operational_inset = (
            self.outer_formation_radius
            + self.safety.radius_for("TARGET")
            + 8.0
        )
        self.previous_scene: Dict[str, Tuple[float, float, float]] = {}
        self.avoidance_count = 0
        self.captured_at_sequence: int | None = None
        self.settling_started_at_sequence: int | None = None
        self.containment_candidate_at_sequence: int | None = None
        self.formation_ready_at_sequence: int | None = None
        self.external_containment_authority = bool(
            self.config.get("externalContainmentAuthority", False)
        )
        self.executed_containment_ready = False
        self.executed_containment_confidence = 0.0
        self.executed_containment_started_at: int | None = None
        self.executed_max_gap_deg = 360.0
        self.executed_allowed_gap_deg = 0.0
        self.presentation_slot_assignments: Dict[str, int] = {}
        self.last_usv_angular_error_deg = 180.0
        target = self._to_scene(self.env.targets[0, :3], "TARGET")
        self.target_start_scene = (target[0], target[1])
        self.target_travelled_distance = 0.0
        self.peer_target_positions: List[Tuple[float, float, float]] = []
        self.target_escape_direction = self._choose_target_escape_direction(target)
        # Construction may be followed by either PREVIEW or an immediate
        # mission start.  Seed a real cruise vector now so the first emitted
        # frame never shows a stationary hostile simply because active=True
        # was already the adapter default.
        self.target_velocity = (
            self.target_escape_direction[0] * self.target_cruise_mps,
            self.target_escape_direction[1] * self.target_cruise_mps,
        )
        self.target_behavior_state = "ESCAPE"
        self.target_speed_reason = "ESCAPE_CRUISE"
        self.mission_stage = "ESCAPE"
        self.last_containment_confidence = 0.0
        self.capture_hold_frames = max(10, int(self.config.get("captureHoldFrames", 20)))
        self.display_progress = 0.0
        self.progress_best_sequence = 0
        self.replan_count = 0
        self.last_replan_sequence = 0
        self.stalled_frames = 0
        self.last_capture_blocker = "PREVIEW_NOT_STARTED"
        self.preview_frame = 0
        self._mission_start_pose_pending = False
        self._mission_has_started = False
        self.preview_centers: Dict[str, Tuple[float, float, float]] = {}
        uav_no = usv_no = 0
        for raw in self.env.agents:
            kind = "UAV" if int(raw[6]) == 0 else "USV"
            if kind == "UAV":
                uav_no += 1
                code = f"UAV-{uav_no:03d}"
            else:
                usv_no += 1
                code = f"USV-{usv_no:03d}"
            self.preview_centers[code] = self._to_scene(raw[:3], kind)
        self.initial_mean_distance = float(np.mean([
            math.hypot(self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")[0] - target[0],
                       self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")[1] - target[1])
            for raw in self.env.agents
        ]))

    def set_mission_active(self, active: bool) -> None:
        was_active = self.mission_active
        super().set_mission_active(active)
        if active and not was_active and not self._mission_has_started:
            # PREVIEW has its own continuous loiter/shore-avoidance activity.
            # Mission metrics must describe only the run that starts when the
            # operator presses Start, otherwise the counter can already be in
            # the thousands before pursuit begins. A later PAUSE -> RESUME
            # transition must preserve the current mission progress.
            self._mission_has_started = True
            self.avoidance_count = 0
            target = self.previous_scene.get(
                "TARGET",
                self._to_scene(self.env.targets[0, :3], "TARGET"),
            )
            self.target_start_scene = (target[0], target[1])
            self.target_travelled_distance = 0.0
            self.target_escape_direction = self._choose_target_escape_direction(target)
            # START changes intent, not the preview's physical velocity or bow.
            resume_heading = getattr(self, "target_motion_heading", float(self.env.targets[0, 4]))
            resume_speed = math.hypot(*self.target_velocity)
            self.target_velocity = (
                math.cos(resume_heading) * resume_speed,
                math.sin(resume_heading) * resume_speed,
            )
            self.target_behavior_state = "ESCAPE"
            self.target_speed_reason = "ESCAPE_CRUISE"
            self.last_containment_confidence = 0.0
            self.captured_at_sequence = None
            self.settling_started_at_sequence = None
            self.containment_candidate_at_sequence = None
            self.formation_ready_at_sequence = None
            self.executed_containment_ready = False
            self.executed_containment_confidence = 0.0
            self.executed_containment_started_at = None
            self.executed_max_gap_deg = 360.0
            self.executed_allowed_gap_deg = 0.0
            self.presentation_slot_assignments.clear()
            self.last_usv_angular_error_deg = 180.0
            self.display_progress = 0.0
            self.progress_best_sequence = self.sequence
            self.replan_count = 0
            self.last_replan_sequence = self.sequence
            self.stalled_frames = 0
            self.last_capture_blocker = "PURSUIT_DISTANCE"
            self._mission_start_pose_pending = True
            self.initial_mean_distance = float(np.mean([
                math.hypot(
                    self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")[0] - target[0],
                    self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")[1] - target[1],
                )
                for raw in self.env.agents
            ]))

    def confirm_executed_containment(self) -> None:
        """Latch capture only after an outer coordinator proves executed poses."""
        if not self.mission_active or self.captured_at_sequence is not None:
            return
        self.captured_at_sequence = self.sequence
        guard_ids = {int(raw[7]) for raw in self.env.agents}
        self.env.guarding_agents[0] = guard_ids
        self.env.permanently_captured.add(0)
        self.target_behavior_state = "CAPTURED"
        self.target_velocity = (0.0, 0.0)

    def set_executed_containment_feedback(
        self,
        *,
        ready: bool,
        confidence: float,
        max_gap_deg: float,
        allowed_gap_deg: float,
    ) -> None:
        """Accept authoritative geometry from the multi-target coordinator."""
        self.executed_containment_confidence = max(0.0, min(1.0, confidence))
        self.executed_max_gap_deg = max(0.0, max_gap_deg)
        self.executed_allowed_gap_deg = max(0.0, allowed_gap_deg)
        if ready and not self.executed_containment_ready:
            self.executed_containment_started_at = self.sequence
        elif not ready:
            self.executed_containment_started_at = None
        self.executed_containment_ready = ready

    def revoke_executed_containment(self) -> None:
        """Release a previously latched capture when executed geometry opens."""
        if self.captured_at_sequence is None:
            return
        self.captured_at_sequence = None
        self.env.permanently_captured.discard(0)
        self.env.guarding_agents.pop(0, None)
        self.target_behavior_state = "BREAKOUT"
        # The regular controller accelerates from the held pose; no burst
        # vector or heading jump merely because the capture latch opened.
        self.target_velocity = (0.0, 0.0)

    def _activate_vendor_runtime_config(self) -> None:
        for name, value in self._vendor_runtime_config.items():
            setattr(self.source, name, value)

    def _choose_target_escape_direction(
        self,
        target: Tuple[float, float, float],
    ) -> Tuple[float, float]:
        """Pick a long, persistent open-water corridor away from the fleet."""
        scene_agents = [
            self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")
            for raw in self.env.agents
        ]
        if scene_agents:
            center_x = sum(item[0] for item in scene_agents) / len(scene_agents)
            center_y = sum(item[1] for item in scene_agents) / len(scene_agents)
        else:
            center_x, center_y = target[0] - 1.0, target[1]
        away_x, away_y = target[0] - center_x, target[1] - center_y
        away_length = math.hypot(away_x, away_y) or 1.0
        away_x, away_y = away_x / away_length, away_y / away_length
        left, right, bottom, top = self.safety.bounds
        inset = float(getattr(self, "operational_inset", 16.0))

        def clearance(dx: float, dy: float) -> float:
            values: List[float] = []
            if dx > 1e-6:
                values.append((right - inset - target[0]) / dx)
            elif dx < -1e-6:
                values.append((left + inset - target[0]) / dx)
            if dy > 1e-6:
                values.append((top - inset - target[1]) / dy)
            elif dy < -1e-6:
                values.append((bottom + inset - target[1]) / dy)
            return max(0.0, min((value for value in values if value >= 0.0), default=0.0))

        candidates = [
            (math.cos(2.0 * math.pi * index / 32.0), math.sin(2.0 * math.pi * index / 32.0))
            for index in range(32)
        ]
        # Clearance is only a secondary objective.  The old unconstrained
        # score could prefer the longest water corridor even when it pointed
        # straight through the pursuing fleet.  Keep every selected corridor
        # in the away half-plane, then optimise the available water inside it.
        aligned = [
            direction for direction in candidates
            if direction[0] * away_x + direction[1] * away_y >= 0.35
        ]
        if not aligned:
            aligned = [
                direction for direction in candidates
                if direction[0] * away_x + direction[1] * away_y >= -1e-6
            ]
        return max(
            aligned or candidates,
            key=lambda direction: clearance(*direction) + 18.0 * (
                direction[0] * away_x + direction[1] * away_y
            ),
        )

    def _operational_clearance(self, x: float, y: float) -> float:
        left, right, bottom, top = self.safety.bounds
        inset = self.operational_inset
        return min(
            x - (left + inset),
            (right - inset) - x,
            y - (bottom + inset),
            (top - inset) - y,
        )

    @staticmethod
    def _largest_gap_direction(
        target: Tuple[float, float, float],
        agents: List[Tuple[float, float, float]],
    ) -> Tuple[float, float] | None:
        if len(agents) < 2:
            return None
        angles = sorted(
            math.atan2(item[1] - target[1], item[0] - target[0]) % (2.0 * math.pi)
            for item in agents
        )
        gaps = [
            ((angles[(index + 1) % len(angles)] - angles[index]) % (2.0 * math.pi), angles[index])
            for index in range(len(angles))
        ]
        gap, start = max(gaps)
        midpoint = start + gap / 2.0
        return math.cos(midpoint), math.sin(midpoint)

    def _choose_boundary_aware_direction(
        self,
        target: Tuple[float, float, float],
        desired: Tuple[float, float],
        agents: List[Tuple[float, float, float]],
    ) -> Tuple[Tuple[float, float], bool]:
        """Choose a moving open-water course before the target hits an edge."""
        lookahead = 22.0
        predicted_x = target[0] + desired[0] * lookahead
        predicted_y = target[1] + desired[1] * lookahead
        near_edge = (
            self._operational_clearance(target[0], target[1]) < 12.0
            or self._operational_clearance(predicted_x, predicted_y) < 0.0
        )
        if not near_edge:
            return desired, False

        if agents:
            center_x = sum(item[0] for item in agents) / len(agents)
            center_y = sum(item[1] for item in agents) / len(agents)
            away_x, away_y = target[0] - center_x, target[1] - center_y
            length = math.hypot(away_x, away_y) or 1.0
            away_x, away_y = away_x / length, away_y / length
        else:
            away_x, away_y = desired
        vx, vy = self.target_velocity
        velocity_length = math.hypot(vx, vy)
        momentum = (vx / velocity_length, vy / velocity_length) if velocity_length > 1e-5 else desired
        candidates = [
            (math.cos(2.0 * math.pi * index / 48.0), math.sin(2.0 * math.pi * index / 48.0))
            for index in range(48)
        ]

        def score(direction: Tuple[float, float]) -> float:
            next_x = target[0] + direction[0] * lookahead
            next_y = target[1] + direction[1] * lookahead
            clearance = self._operational_clearance(next_x, next_y)
            return (
                clearance * 3.0
                + (direction[0] * away_x + direction[1] * away_y) * 14.0
                + (direction[0] * momentum[0] + direction[1] * momentum[1]) * 7.0
                + (direction[0] * desired[0] + direction[1] * desired[1]) * 3.0
            )

        return max(candidates, key=score), True

    def _preview_agent_proposal(
        self,
        code: str,
        kind: str,
        index: int,
    ) -> Tuple[float, float, float]:
        center = self.preview_centers[code]
        radius = (5.0 if kind == "USV" else 6.5) + (index % 3) * 1.2
        direction = 1.0 if index % 2 == 0 else -1.0
        angle = index * 2.399963 + direction * self.preview_frame * (0.006 if kind == "USV" else 0.009)
        return (
            center[0] + math.cos(angle) * radius,
            center[1] + math.sin(angle) * radius,
            center[2],
        )

    def _rotate_capture_slots(self) -> None:
        delta = math.pi / max(8, len(self.usv_slot_specs) + len(self.uav_slot_specs))
        self.usv_slot_specs = [
            FormationSlot(slot.radius, slot.angle + delta, slot.altitude, slot.ring)
            for slot in self.usv_slot_specs
        ]
        self.uav_slot_specs = [
            FormationSlot(slot.radius, slot.angle + delta, slot.altitude, slot.ring)
            for slot in self.uav_slot_specs
        ]
        self.source.CAPTURE_FORMATION_USV_SLOT_SPECS = [
            (slot.radius * 3.0, slot.angle, 0.0, slot.ring)
            for slot in self.usv_slot_specs
        ]
        self.source.CAPTURE_FORMATION_UAV_SLOT_SPECS = [
            (slot.radius * 3.0, slot.angle, (slot.altitude - 9.0) * 4.4, slot.ring)
            for slot in self.uav_slot_specs
        ]
        self.env.algorithm.formation_slot_assignment.clear()

    def _align_capture_slots_to_open_water(self, target: Tuple[float, float, float]) -> None:
        """Rotate the complete formation away from shoreline/obstacle conflicts."""
        best_delta = 0.0
        best_penalty = float("inf")
        for candidate in range(24):
            delta = candidate * 2.0 * math.pi / 24.0
            penalty = 0.0
            for kind, specs in (("USV", self.usv_slot_specs), ("UAV", self.uav_slot_specs)):
                for slot in specs:
                    rotated = FormationSlot(slot.radius, slot.angle + delta, slot.altitude, slot.ring)
                    point = rotated.point(target)
                    safe = self.safety.constrain(point, point, kind, (), 0.0)
                    penalty += math.hypot(safe.x - point[0], safe.y - point[1])
            if penalty < best_penalty:
                best_penalty = penalty
                best_delta = delta
        if abs(best_delta) <= 1e-9:
            return
        self.usv_slot_specs = [
            FormationSlot(slot.radius, slot.angle + best_delta, slot.altitude, slot.ring)
            for slot in self.usv_slot_specs
        ]
        self.uav_slot_specs = [
            FormationSlot(slot.radius, slot.angle + best_delta, slot.altitude, slot.ring)
            for slot in self.uav_slot_specs
        ]

    def _assign_presentation_slots(
        self,
        target: Tuple[float, float, float],
    ) -> None:
        """Assign slots without asking craft to exchange sides through the ring.

        A nearest-slot greedy assignment is locally cheap but can reverse the
        cyclic order of two neighbours. Their straight paths then intersect
        the target keep-out circle (or each other), and the swept-path safety
        guard correctly holds both craft forever. Assign the nearest radial
        cohort to each physical ring and preserve the cohort's angular order.
        Only the cyclic offset is optimised, so every final route is reachable
        without cutting through the containment centre.
        """
        center_x, center_y, _ = target
        for kind, specs, type_value in (
            ("UAV", self.uav_slot_specs, 0),
            ("USV", self.usv_slot_specs, 1),
        ):
            agents: List[Tuple[str, Tuple[float, float, float]]] = []
            ordinal = 0
            for raw in self.env.agents:
                if int(raw[6]) != type_value:
                    continue
                ordinal += 1
                code = f"{kind}-{ordinal:03d}"
                current = self.previous_scene.get(code, self._to_scene(raw[:3], kind))
                agents.append((code, current))
            agents.sort(key=lambda item: math.hypot(item[1][0] - center_x, item[1][1] - center_y))

            specs_by_ring: Dict[int, List[int]] = {}
            for slot_index, slot in enumerate(specs):
                specs_by_ring.setdefault(slot.ring, []).append(slot_index)
            cursor = 0
            for ring_index in sorted(specs_by_ring, key=lambda value: specs[specs_by_ring[value][0]].radius):
                slot_indices = sorted(specs_by_ring[ring_index], key=lambda value: specs[value].angle)
                cohort = agents[cursor:cursor + len(slot_indices)]
                cursor += len(slot_indices)
                cohort.sort(key=lambda item: math.atan2(item[1][1] - center_y, item[1][0] - center_x))
                if not cohort:
                    continue
                best_offset = 0
                best_cost = float("inf")
                for offset in range(len(slot_indices)):
                    cost = 0.0
                    for position, (_, current) in enumerate(cohort):
                        slot_index = slot_indices[(position + offset) % len(slot_indices)]
                        point = specs[slot_index].point(target)
                        cost += math.hypot(current[0] - point[0], current[1] - point[1])
                    if cost < best_cost:
                        best_cost, best_offset = cost, offset
                for position, (code, _) in enumerate(cohort):
                    self.presentation_slot_assignments[code] = slot_indices[
                        (position + best_offset) % len(slot_indices)
                    ]

    @staticmethod
    def _settling_proposal(
        current: Tuple[float, float, float],
        slot: FormationSlot,
        target: Tuple[float, float, float],
        step: float,
    ) -> Tuple[float, float, float]:
        """Reach a ring slot by a radial/arc route that never crosses its centre."""
        center_x, center_y, _ = target
        dx, dy = current[0] - center_x, current[1] - center_y
        radius = math.hypot(dx, dy)
        if radius < 1e-6:
            angle, radius = slot.angle, 1e-6
        else:
            angle = math.atan2(dy, dx)
        angle_error = (slot.angle - angle + math.pi) % (2.0 * math.pi) - math.pi

        # First clear the target hull. While changing bearing, stay on or just
        # outside the assigned ring. This prevents a chord from cutting across
        # the enemy and gives neighbouring craft a consistent traffic lane.
        # Surface craft use the inside lane while changing bearing; the UAV
        # ring is outside them, so an outside USV lane can create an avoidable
        # air/surface deadlock. Aircraft use the outer lane. Both return to
        # their exact configured radius once the bearing is aligned.
        lane_offset = 2.0 if slot.altitude > 0.0 else -2.5
        transit_radius = slot.radius + (lane_offset if abs(angle_error) > 0.10 else 0.0)
        if abs(angle_error) > 0.025:
            # A moving centre continually perturbs radius. The former
            # radial-first branch therefore froze bearing correction for some
            # fleet sizes and left a permanent angular opening. Correct radius
            # and bearing together, with a conservative arc budget that still
            # keeps the path outside the target hull.
            angular_step = min(
                abs(angle_error),
                step * 1.05 / max(8.0, max(radius, transit_radius)),
            )
            next_angle = angle + math.copysign(angular_step, angle_error)
            next_radius = radius + max(
                -step * 0.68,
                min(step * 0.68, transit_radius - radius),
            )
        else:
            next_angle = slot.angle
            next_radius = radius + max(-step, min(step, slot.radius - radius))
        return (
            center_x + math.cos(next_angle) * next_radius,
            center_y + math.sin(next_angle) * next_radius,
            slot.altitude,
        )

    def _pursuit_proposal(
        self,
        current: Tuple[float, float, float],
        target: Tuple[float, float, float],
        kind: str,
        ordinal: int,
        count: int,
    ) -> Tuple[float, float, float]:
        """Generate a visibly closing, bounded-lead interception step."""
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        distance = math.hypot(dx, dy)
        if distance < 1e-6:
            return current

        target_vx, target_vy = self.target_velocity
        target_speed = math.hypot(target_vx, target_vy)
        configured = float(self.config.get(
            "uavSpeedMps" if kind == "UAV" else "usvSpeedMps",
            5.0 if kind == "UAV" else 3.0,
        ))
        pursuit_speed = (
            min(15.0, max(configured, target_speed + 1.8))
            if kind == "UAV"
            else min(4.0, max(self.usv_pursuit_mps, target_speed + 0.65))
        )
        lead_time = min(
            2.8,
            max(0.45, distance / max(1.0, pursuit_speed + target_speed)),
        )
        lead_x = target_vx * lead_time
        lead_y = target_vy * lead_time
        lead_length = math.hypot(lead_x, lead_y)
        maximum_lead = distance * 0.22
        if lead_length > maximum_lead > 0.0:
            lead_x *= maximum_lead / lead_length
            lead_y *= maximum_lead / lead_length

        velocity_length = target_speed or 1.0
        normal_x = (
            -target_vy / velocity_length
            if target_speed > 1e-6 else -dy / distance
        )
        normal_y = (
            target_vx / velocity_length
            if target_speed > 1e-6 else dx / distance
        )
        centered = ordinal - (max(1, count) - 1) * 0.5
        lane_spacing = 3.2 if kind == "USV" else 4.5
        lane_offset = centered * lane_spacing * min(1.0, distance / 45.0)
        aim_x = target[0] + lead_x + normal_x * lane_offset
        aim_y = target[1] + lead_y + normal_y * lane_offset
        aim_dx = aim_x - current[0]
        aim_dy = aim_y - current[1]
        aim_distance = math.hypot(aim_dx, aim_dy)
        step = pursuit_speed * 0.1
        if aim_distance <= step or aim_distance < 1e-9:
            next_x, next_y = aim_x, aim_y
        else:
            next_x = current[0] + aim_dx * step / aim_distance
            next_y = current[1] + aim_dy * step / aim_distance
        return next_x, next_y, current[2]

    def _advance_capture_target(
        self,
        previous: Tuple[float, float, float],
        preview: bool = False,
    ) -> SafePoint:
        pursuit = preview or self.target_travelled_distance < self.required_pursuit_distance
        desired_x, desired_y = self.target_escape_direction
        scene_agents = [
            self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")
            for raw in self.env.agents
        ]
        if scene_agents:
            nearest = min(
                scene_agents,
                key=lambda item: math.hypot(previous[0] - item[0], previous[1] - item[1]),
            )
            avoid_x, avoid_y = previous[0] - nearest[0], previous[1] - nearest[1]
            avoid_length = math.hypot(avoid_x, avoid_y) or 1.0
            avoid_x, avoid_y = avoid_x / avoid_length, avoid_y / avoid_length
            bias = 0.16 if preview else 0.10 if pursuit else 0.34
            desired_x += avoid_x * bias
            desired_y += avoid_y * bias
        if self.peer_target_positions:
            # Independent child solvers share one ocean.  Without an explicit
            # separation bias their escape corridors can collapse toward the
            # same open-water lane; the global collision pass then pushes one
            # group's USV far outside its ring. Keep hostile targets apart
            # before the groups begin final closure.
            peer = min(
                self.peer_target_positions,
                key=lambda item: math.hypot(previous[0] - item[0], previous[1] - item[1]),
            )
            peer_dx, peer_dy = previous[0] - peer[0], previous[1] - peer[1]
            peer_distance = math.hypot(peer_dx, peer_dy)
            required_peer_clearance = self.outer_formation_radius * 2.0 + 28.0
            if peer_distance < required_peer_clearance:
                peer_length = peer_distance or 1.0
                repulsion = min(
                    1.15,
                    (required_peer_clearance - peer_distance) / max(45.0, required_peer_clearance * 0.55),
                )
                desired_x += peer_dx / peer_length * repulsion
                desired_y += peer_dy / peer_length * repulsion
        local_gap_agents = [
            item for item in scene_agents
            if math.hypot(previous[0] - item[0], previous[1] - item[1])
            <= max(92.0, self.outer_formation_radius * 2.2)
        ]
        gap_direction = self._largest_gap_direction(previous, local_gap_agents)
        if not preview and gap_direction is not None:
            # A target under pressure should exploit the largest opening,
            # instead of circling around whichever single craft is nearest.
            # Use it as soon as a meaningful local interception line exists;
            # waiting for the legacy pursuit-distance gate made the hostile
            # appear unaware of an obvious opening during most of the chase.
            gap_weight = min(
                0.78,
                0.38 + len(local_gap_agents) / max(3.0, len(scene_agents)) * 0.34,
            )
            desired_x = desired_x * (1.0 - gap_weight) + gap_direction[0] * gap_weight
            desired_y = desired_y * (1.0 - gap_weight) + gap_direction[1] * gap_weight
        desired_length = math.hypot(desired_x, desired_y) or 1.0
        desired_x, desired_y = desired_x / desired_length, desired_y / desired_length
        # Receding-horizon escape choice: test several feasible headings
        # before committing.  This prevents a target from blindly following a
        # persistent vector into the coast and lets it exploit a moving gap in
        # the interception line without random frame-to-frame zig-zagging.
        base_angle = math.atan2(desired_y, desired_x)
        best_direction = (desired_x, desired_y)
        best_score = -math.inf
        for offset_deg in (-105, -75, -50, -30, -15, 0, 15, 30, 50, 75, 105):
            angle = base_angle + math.radians(offset_deg)
            candidate = (math.cos(angle), math.sin(angle))
            lookahead = 32.0
            predicted = (
                previous[0] + candidate[0] * lookahead,
                previous[1] + candidate[1] * lookahead,
                0.0,
            )
            safe_prediction = self.safety.constrain(previous, predicted, "TARGET", (), 0.0)
            if safe_prediction.adjusted or math.hypot(
                safe_prediction.x - predicted[0], safe_prediction.y - predicted[1]
            ) > 0.5:
                continue
            pursuer_clearance = min((
                math.hypot(predicted[0] - item[0], predicted[1] - item[1])
                for item in scene_agents
            ), default=80.0)
            peer_clearance = min((
                math.hypot(predicted[0] - item[0], predicted[1] - item[1])
                for item in self.peer_target_positions
            ), default=80.0)
            corridor = self._operational_clearance(predicted[0], predicted[1])
            continuity = candidate[0] * self.target_escape_direction[0] + candidate[1] * self.target_escape_direction[1]
            score = min(70.0, pursuer_clearance) + min(55.0, peer_clearance) * 1.35
            required_peer_clearance = self.outer_formation_radius * 2.0 + 28.0
            if peer_clearance < required_peer_clearance:
                score -= (required_peer_clearance - peer_clearance) * 3.2
            score += min(35.0, corridor) * 1.4 + continuity * 10.0
            score -= abs(offset_deg) * 0.035
            if score > best_score:
                best_score = score
                best_direction = candidate
        # Heading hysteresis keeps the selected evasive route physically
        # steerable while still allowing a decisive coast-avoidance turn.
        desired_x = self.target_escape_direction[0] * 0.68 + best_direction[0] * 0.32
        desired_y = self.target_escape_direction[1] * 0.68 + best_direction[1] * 0.32
        desired_length = math.hypot(desired_x, desired_y) or 1.0
        desired_x, desired_y = desired_x / desired_length, desired_y / desired_length
        (desired_x, desired_y), coast_avoid = self._choose_boundary_aware_direction(
            previous,
            (desired_x, desired_y),
            scene_agents,
        )
        if self.peer_target_positions:
            peer = min(
                self.peer_target_positions,
                key=lambda item: math.hypot(previous[0] - item[0], previous[1] - item[1]),
            )
            peer_dx, peer_dy = previous[0] - peer[0], previous[1] - peer[1]
            peer_distance = math.hypot(peer_dx, peer_dy)
            required_peer_clearance = self.outer_formation_radius * 2.0 + 28.0
            if peer_distance < required_peer_clearance:
                peer_length = peer_distance or 1.0
                pressure = min(
                    0.88,
                    0.42 + (required_peer_clearance - peer_distance) / max(1.0, required_peer_clearance),
                )
                desired_x = desired_x * (1.0 - pressure) + peer_dx / peer_length * pressure
                desired_y = desired_y * (1.0 - pressure) + peer_dy / peer_length * pressure
                desired_length = math.hypot(desired_x, desired_y) or 1.0
                desired_x, desired_y = desired_x / desired_length, desired_y / desired_length
        self.target_escape_direction = (desired_x, desired_y)
        nearest_pursuer_distance = min((
            math.hypot(previous[0] - item[0], previous[1] - item[1])
            for item in scene_agents
        ), default=math.inf)
        authoritative_containment = (
            self.executed_containment_ready
            if self.external_containment_authority
            else self.containment_candidate_at_sequence is not None
        )
        if preview:
            speed = min(1.4, self.target_cruise_mps * 0.65)
            self.target_behavior_state = "CRUISE"
            self.target_speed_reason = "PREVIEW_CRUISE"
        elif not authoritative_containment:
            # Travel distance and a nearby interceptor are not capture.  The
            # target keeps at least its seeded cruise speed until the executed
            # formation has become a genuinely closed ring.  Pressure may
            # cause a bounded burst, including while turning along a coast.
            pressure = 0.0 if math.isinf(nearest_pursuer_distance) else max(
                0.0,
                min(1.0, (72.0 - nearest_pursuer_distance) / 48.0),
            )
            # Burst while there is a usable escape sector. Once coverage is
            # already high, return to (never below) cruise speed so faster
            # interceptors can physically close the final angular gap.
            escape_room = max(
                0.0,
                min(1.0, (0.78 - self.last_containment_confidence) / 0.43),
            )
            pressure *= escape_room
            speed = min(
                self.target_max_mps,
                self.target_cruise_mps + pressure * 0.6,
            )
            if gap_direction is not None and pressure > 0.08:
                self.target_behavior_state = "BREAKOUT"
                self.target_speed_reason = "GAP_BREAKOUT"
            else:
                self.target_behavior_state = "COAST_AVOID" if coast_avoid else "ESCAPE"
                self.target_speed_reason = "COAST_AVOID" if coast_avoid else "ESCAPE_CRUISE"
        else:
            started_at = (
                self.executed_containment_started_at
                if self.external_containment_authority
                else self.containment_candidate_at_sequence
            )
            held = max(0, self.sequence - (started_at or self.sequence))
            reduction = min(0.94, held / max(1, self.capture_hold_frames) * 0.94)
            speed = max(0.08, self.target_cruise_mps * (1.0 - reduction))
            self.target_behavior_state = "CONTAINED"
            self.target_speed_reason = "EXECUTED_CONTAINMENT_DECEL"
        # Single bow-first motion integrator. Safety evaluates proposals but
        # never substitutes a lateral position or an unrelated velocity.
        current_speed = math.hypot(*self.target_velocity)
        heading = getattr(self, "target_motion_heading", float(self.env.targets[0, 4]))
        desired_heading = math.atan2(desired_y, desired_x)
        delta = (desired_heading-heading+math.pi) % math.tau-math.pi
        heading += max(-math.radians(7), min(math.radians(7), delta))
        self.target_motion_heading = heading
        next_speed = current_speed + max(-0.10, min(0.08, speed-current_speed))
        next_speed = min(self.target_max_mps, max(0.0, next_speed))
        vx, vy = math.cos(heading)*next_speed, math.sin(heading)*next_speed
        proposed = (previous[0]+vx*.1, previous[1]+vy*.1, 0.0)
        checked = self.safety.constrain(previous, proposed, "TARGET", (), 0.0)
        valid = (
            math.hypot(checked.x-proposed[0], checked.y-proposed[1]) < 1e-5
            and self._operational_clearance(proposed[0], proposed[1]) >= min(
                0.0, self._operational_clearance(previous[0], previous[1]),
            )-1e-5
        )
        if valid:
            safe = SafePoint(*proposed, False)
        else:
            safe = SafePoint(*previous, True)
            vx = vy = 0.0
            self.target_speed_reason = "COAST_BRAKING"
            self.target_escape_direction = self._choose_target_escape_direction(previous)
        self.target_velocity = (vx, vy)
        if not preview:
            self.target_travelled_distance += math.hypot(safe.x-previous[0], safe.y-previous[1])
        return safe

    def _reset_positions(self) -> None:
        # Build a layout matching the configured fleet size. The previous
        # six-row fixture worked for 3+3 only and caused a broadcasting error
        # as soon as the algorithm created a larger agent array.
        def grid_positions(
            count: int,
            x_min: float,
            x_max: float,
            z_min: float,
            z_step: float,
        ) -> List[List[float]]:
            columns = max(1, int(math.ceil(math.sqrt(count))))
            rows = max(1, int(math.ceil(count / columns)))
            positions: List[List[float]] = []
            for index in range(count):
                row, column = divmod(index, columns)
                x = x_min if columns == 1 else x_min + (x_max - x_min) * column / (columns - 1)
                # Keep the generated fleet inside the Task Center safety
                # bounds after _to_scene() converts native coordinates by
                # (value - 150) / 3.  The old 25..275 range became
                # -41.67..41.67 and pinned edge UAVs to the boundary.
                y_min = self.internal_center - 105.0
                y_max = self.internal_center + 105.0
                y = self.internal_center if rows == 1 else y_min + (y_max - y_min) * row / (rows - 1)
                positions.append([x, y, z_min + (index % 4) * z_step])
            return positions

        positions = np.asarray(
            grid_positions(self.source.UAV_COUNT, self.internal_center - 150.0, self.internal_center, 45.0, 8.0)
            + grid_positions(self.source.USV_COUNT, self.internal_center, self.internal_center + 150.0, 0.0, 0.0),
            dtype=float,
        )
        initial_poses = self.initial_pose_map()
        for index in range(self.source.UAV_COUNT + self.source.USV_COUNT):
            kind = "UAV" if index < self.source.UAV_COUNT else "USV"
            number = index + 1 if kind == "UAV" else index - self.source.UAV_COUNT + 1
            code = f"{kind}-{number:03d}"
            pose = initial_poses.get(code)
            if pose is None:
                continue
            east, north, up = self.initial_pose_to_local(pose)
            positions[index, 0] = east * 3.0 + self.internal_center
            positions[index, 1] = north * 3.0 + self.internal_center
            if kind == "UAV":
                positions[index, 2] = max(18.0, (up - 9.0) * 4.4)
        self.env.agents[:, :3] = positions
        self.env.agents[:, 3] = 0
        target_pose = (
            initial_poses.get("TARGET-001")
            or initial_poses.get("TARGET")
        )
        if target_pose is not None:
            east, north, _ = self.initial_pose_to_local(target_pose)
            target_x = east * 3.0 + self.internal_center
            target_y = north * 3.0 + self.internal_center
        else:
            target_x, target_y = self.internal_center + 30.0, self.internal_center
        self.env.targets[0, :5] = np.asarray(
            [target_x, target_y, 0, self.source.TARGET_SPEED, math.pi / 2]
        )
        # Enforce the scenario contract inside the algorithm as well as in the
        # front end. A stale WebGL/default layout must never place the target
        # inside an almost-finished containment formation.
        minimum_spawn_distance = max(
            90.0,
            float(self.config.get("threatMinDistanceM", 90.0)),
        )
        target_scene = self._to_scene(self.env.targets[0, :3], "TARGET")
        scene_agents = [
            self._to_scene(raw[:3], "UAV" if int(raw[6]) == 0 else "USV")
            for raw in self.env.agents
        ]
        nearest = min(
            (math.hypot(item[0] - target_scene[0], item[1] - target_scene[1]) for item in scene_agents),
            default=minimum_spawn_distance,
        )
        if nearest < minimum_spawn_distance:
            center_x = sum(item[0] for item in scene_agents) / max(1, len(scene_agents))
            center_y = sum(item[1] for item in scene_agents) / max(1, len(scene_agents))
            away_x, away_y = target_scene[0] - center_x, target_scene[1] - center_y
            length = math.hypot(away_x, away_y)
            if length < 1e-6:
                away_x, away_y, length = 1.0, 0.0, 1.0
            shift = minimum_spawn_distance - nearest + 8.0
            target_scene = (
                target_scene[0] + away_x / length * shift,
                target_scene[1] + away_y / length * shift,
                0.0,
            )
            self.env.targets[0, :3] = self._to_internal(target_scene, "TARGET")

    def _to_scene(self, position: np.ndarray, kind: str) -> Tuple[float, float, float]:
        # One uniform horizontal scale keeps the algorithm's circular capture
        # geometry circular in both Task Center 2-D and Unity 3-D.
        x = (float(position[0]) - self.internal_center) / 3.0
        y = (float(position[1]) - self.internal_center) / 3.0
        z = 0.0 if kind != "UAV" else 9.0 + float(position[2]) / 4.4
        return x, y, z

    def _to_internal(self, position: Tuple[float, float, float], kind: str) -> np.ndarray:
        x, y, z = position
        return np.asarray([x * 3.0 + self.internal_center, y * 3.0 + self.internal_center, 0.0 if kind != "UAV" else max(18.0, (z - 9.0) * 4.4)])

    @staticmethod
    def _ring_angular_error(angles: List[float]) -> float:
        """Largest gap error from an evenly distributed containment ring."""
        if len(angles) <= 1:
            return 0.0
        ordered = sorted(angle % (2.0 * math.pi) for angle in angles)
        gaps = [
            (ordered[(index + 1) % len(ordered)] - ordered[index])
            % (2.0 * math.pi)
            for index in range(len(ordered))
        ]
        ideal = 2.0 * math.pi / len(ordered)
        return max(abs(gap - ideal) for gap in gaps)

    @staticmethod
    def _ring_max_gap_deg(angles: List[float]) -> float:
        if len(angles) <= 1:
            return 360.0
        ordered = sorted(angle % (2.0 * math.pi) for angle in angles)
        return math.degrees(max(
            (ordered[(index + 1) % len(ordered)] - ordered[index])
            % (2.0 * math.pi)
            for index in range(len(ordered))
        ))

    def step(self) -> RuntimeFrame:
        self._activate_vendor_runtime_config()
        self.sequence += 1
        initial_frame = self._initial_frame_pending
        self._initial_frame_pending = False
        mission_start_frame = self._mission_start_pose_pending
        self._mission_start_pose_pending = False
        initial_snapshot = initial_frame and bool(self.initial_pose_map())
        preview = not self.mission_active
        if preview and not initial_frame:
            self.preview_frame += 1
        if not initial_frame and not preview:
            with contextlib.redirect_stdout(sys.stderr):
                self.env.step()

        target_scene = self.previous_scene.get(
            "TARGET",
            self._to_scene(self.env.targets[0, :3], "TARGET"),
        ) if mission_start_frame else self._to_scene(self.env.targets[0, :3], "TARGET")
        if initial_frame or mission_start_frame:
            safe_target = SafePoint(*target_scene, False)
        else:
            target_previous = self.previous_scene.get("TARGET", target_scene)
            safe_target = self._advance_capture_target(target_previous, preview=preview)
        self.previous_scene["TARGET"] = (safe_target.x, safe_target.y, safe_target.z)
        # Keep the vendor's internal cruise state one frame ahead when START
        # follows PREVIEW. The first emitted mission frame must reuse the
        # preview pose, while the following frame should resume the same
        # escape corridor instead of restarting its turn from that pose.
        if not mission_start_frame:
            self.env.targets[0, :3] = self._to_internal((safe_target.x, safe_target.y, 0.0), "TARGET")
        if hasattr(self, "target_motion_heading"):
            self.env.targets[0, 4] = self.target_motion_heading
        elif math.hypot(*self.target_velocity) > 1e-5:
            self.env.targets[0, 4] = math.atan2(self.target_velocity[1], self.target_velocity[0])

        proposals: Dict[str, Tuple[str, Tuple[float, float, float]]] = {}
        rows: Dict[str, Tuple[int, np.ndarray]] = {}
        agents: List[AgentFrame] = []
        formation_settling = (
            not preview
            and self.target_travelled_distance >= self.required_pursuit_distance + 20.0
        )
        if formation_settling and self.settling_started_at_sequence is None:
            # Reaching the visible chase distance only starts slot convergence.
            # It is deliberately not a containment candidate: the latter is
            # latched below only after the executed, collision-safe positions
            # form a real closed annulus around the target.
            self.settling_started_at_sequence = self.sequence
        if formation_settling and not self.presentation_slot_assignments:
            self._align_capture_slots_to_open_water((safe_target.x, safe_target.y, safe_target.z))
            self._assign_presentation_slots((safe_target.x, safe_target.y, safe_target.z))
        uav_no = usv_no = 0
        guided_pursuit = (
            not preview
            and not formation_settling
            and self.target_travelled_distance < self.required_pursuit_distance
        )
        for index, raw in enumerate(self.env.agents):
            kind = "UAV" if int(raw[6]) == 0 else "USV"
            if kind == "UAV":
                uav_no += 1
                code = f"UAV-{uav_no:03d}"
            else:
                usv_no += 1
                code = f"USV-{usv_no:03d}"
            initial_pose = self.initial_pose_map().get(code)
            if initial_snapshot and initial_pose is not None:
                # The first mission frame is a visual hand-off from Unity.
                # Reconstruct it from the supplied pose contract instead of
                # reading the vendor array, which may have been normalized by
                # a previous runtime instance or vendor reset.
                east, north, up = self.initial_pose_to_local(initial_pose)
                proposed_scene = (east, north, up if kind == "UAV" else 0.0)
            elif preview and not initial_frame:
                proposed_scene = self._preview_agent_proposal(code, kind, index)
            elif formation_settling:
                specs = self.uav_slot_specs if kind == "UAV" else self.usv_slot_specs
                slot_index = self.presentation_slot_assignments[code]
                assigned_slot = specs[slot_index]
                current_scene = self.previous_scene.get(code, self._to_scene(raw[:3], kind))
                # Once the chase distance is complete, convergence is the
                # mission objective.  The old sub-metre settle step could
                # leave one aircraft on the target-hull edge for hundreds of
                # frames while the UI already looked “captured”.  Use a
                # bounded but decisive closure step; the safety solver still
                # owns the final collision-safe projection.
                settle_step = 1.35 if kind == "UAV" else 1.05
                proposed_scene = self._settling_proposal(
                    current_scene,
                    assigned_slot,
                    (safe_target.x, safe_target.y, safe_target.z),
                    settle_step,
                )
            elif guided_pursuit:
                current_scene = self.previous_scene.get(
                    code, self._to_scene(raw[:3], kind)
                )
                proposed_scene = self._pursuit_proposal(
                    current_scene,
                    (safe_target.x, safe_target.y, safe_target.z),
                    kind,
                    (uav_no if kind == "UAV" else usv_no) - 1,
                    (
                        len(self.uav_slot_specs)
                        if kind == "UAV" else len(self.usv_slot_specs)
                    ),
                )
            else:
                proposed_scene = self._to_scene(raw[:3], kind)
            proposals[code] = (kind, proposed_scene)
            rows[code] = (index, raw)

        fixed_target = {"TARGET": ("CAPTURE_TARGET", (safe_target.x, safe_target.y, 0.0))}
        if initial_snapshot:
            resolved = {}
        else:
            # Every moving frame, including the final radial/arc slot-settling
            # phase, must pass through the same whole-fleet solver.  The old
            # settling shortcut only projected shore/bounds and allowed two
            # correctly assigned USVs to occupy intersecting rendered hulls
            # while they queued for neighbouring ring slots.
            resolved = self.safety.resolve_group(
                proposals,
                self.previous_scene,
                fixed_target,
                iterations=96 if formation_settling else 48,
                max_steps={
                    "UAV": (
                        min(1.0, max(0.08, float(self.config.get("uavSpeedMps", 5.0)) * 0.20))
                        if formation_settling
                        else min(0.35, max(0.05, float(self.config.get("uavSpeedMps", 5.0)) * 0.1))
                    ),
                    "USV": (
                        min(0.72, max(0.06, float(self.config.get("usvSpeedMps", 3.0)) * 0.28))
                        if formation_settling
                        else min(0.32, max(0.04, float(self.config.get("usvSpeedMps", 3.0)) * 0.18))
                    ),
                },
            )
        for code, (index, raw) in rows.items():
            kind = proposals[code][0]
            previous_scene = self.previous_scene.get(code)
            initial_pose = self.initial_pose_map().get(code)
            if initial_snapshot:
                scene = proposals[code][1]
                adjusted = False
            else:
                safe = resolved[code]
                scene = (safe.x, safe.y, safe.z)
                adjusted = safe.adjusted
            # SceneSafetyFilter already applies the physical per-frame motion
            # limit before solving separation.  A second per-agent clamp here
            # used to undo part of the pair correction and could reintroduce
            # an overlap after the group had been made safe.
            if adjusted:
                self.avoidance_count += 1
            if preview or formation_settling or guided_pursuit or adjusted:
                self.env.agents[index, :3] = self._to_internal(scene, kind)
            self.previous_scene[code] = scene
            if initial_snapshot and initial_pose is not None:
                heading = float(initial_pose.get("headingDeg", 0.0)) % 360.0
                self._stable_headings[code] = heading
            else:
                heading = self.stabilize_heading(
                    code,
                    previous_scene,
                    scene,
                    math.degrees(float(raw[4])) % 360.0,
                    3.0 if kind == "UAV" else 1.5,
                )
            pursuit_complete = self.target_travelled_distance >= self.required_pursuit_distance
            agents.append(AgentFrame(
                code,
                kind,
                *scene,
                heading,
                "PATROL" if preview else "INTERCEPTOR" if not pursuit_complete else "ENCIRCLEMENT",
            ))
        mean_distance = float(np.mean([
            math.hypot(agent.x - safe_target.x, agent.y - safe_target.y)
            for agent in agents
        ]))
        usv_radii = [
            math.hypot(agent.x - safe_target.x, agent.y - safe_target.y)
            for agent in agents
            if agent.type == "USV"
        ]
        uav_radii = [
            math.hypot(agent.x - safe_target.x, agent.y - safe_target.y)
            for agent in agents
            if agent.type == "UAV"
        ]
        usv_angles = [
            math.atan2(agent.y - safe_target.y, agent.x - safe_target.x)
            for agent in agents
            if agent.type == "USV"
        ]
        uav_angles = [
            math.atan2(agent.y - safe_target.y, agent.x - safe_target.x)
            for agent in agents
            if agent.type == "UAV"
        ]
        # Angular uniformity must be assessed per physical ring.  Treating two
        # concentric rings as one list creates apparent duplicate bearings and
        # reports a false SLOT_CONFLICT for larger fleets even when every ring
        # is already closed around the target.
        usv_ring_angles: Dict[int, List[float]] = {}
        uav_ring_angles: Dict[int, List[float]] = {}
        usv_ring_radial_errors: Dict[int, List[float]] = {}
        uav_ring_radial_errors: Dict[int, List[float]] = {}
        assigned_specs = []
        for _, raw in rows.values():
            agent_type = int(raw[6])
            agent_id = int(raw[7])
            record = self.env.algorithm.formation_slot_assignment.get((0, agent_type), {})
            col = record.get("slots", {}).get(agent_id)
            specs = self.usv_slot_specs if agent_type == 1 else self.uav_slot_specs
            code = (
                f"USV-{agent_id - 1999:03d}"
                if agent_type == 1
                else f"UAV-{agent_id - 999:03d}"
            )
            presentation_col = self.presentation_slot_assignments.get(code)
            assigned = (
                specs[int(presentation_col)]
                if presentation_col is not None and 0 <= int(presentation_col) < len(specs)
                else specs[int(col)] if col is not None and 0 <= int(col) < len(specs)
                else specs[min(len(assigned_specs), len(specs) - 1)]
            )
            actual = next((agent for agent in agents if agent.code == code), None)
            actual_angle = (
                math.atan2(actual.y - safe_target.y, actual.x - safe_target.x)
                if actual is not None
                else assigned.angle
            )
            ring_angles = usv_ring_angles if agent_type == 1 else uav_ring_angles
            ring_angles.setdefault(assigned.ring, []).append(actual_angle)
            if actual is not None:
                radial_errors = (
                    usv_ring_radial_errors if agent_type == 1
                    else uav_ring_radial_errors
                )
                radial_errors.setdefault(assigned.ring, []).append(abs(
                    math.hypot(actual.x - safe_target.x, actual.y - safe_target.y)
                    - assigned.radius
                ))
            # Slot ownership determines the required ring/altitude.  Its
            # precise angle is deliberately assessed from the executed pose:
            # whole-ring angular coverage and convex containment are already
            # validated independently by assess_capture. Requiring the exact
            # pre-safety angle as well caused a collision-avoidance sidestep
            # to hold an otherwise closed ring in RUNNING forever.
            assigned_specs.append(FormationSlot(
                assigned.radius,
                actual_angle,
                assigned.altitude,
                assigned.ring,
            ))
        usv_angular_error = max(
            (self._ring_angular_error(angles) for angles in usv_ring_angles.values()),
            default=0.0,
        )
        self.last_usv_angular_error_deg = math.degrees(usv_angular_error)
        uav_angular_error = max(
            (self._ring_angular_error(angles) for angles in uav_ring_angles.values()),
            default=0.0,
        )
        ring_diagnostics: Dict[str, Dict[str, float | int | bool]] = {}
        # A child solver in the 10-12 total-device range owns one of the
        # multi-target sub-formations (typically 5+5 or 6+6).  These compact
        # formations are the most sensitive to a single global-avoidance
        # sidestep, so they use a safe-containment fallback below instead of
        # requiring every craft to remain on its ideal slot.
        compact_capture = 10 <= len(agents) <= 12
        dense_capture = len(agents) >= 24
        ring_geometry_ready = True
        for kind, specs, angles_by_ring, errors_by_ring in (
            ("USV", self.usv_slot_specs, usv_ring_angles, usv_ring_radial_errors),
            ("UAV", self.uav_slot_specs, uav_ring_angles, uav_ring_radial_errors),
        ):
            expected_by_ring: Dict[int, int] = {}
            for slot in specs:
                expected_by_ring[slot.ring] = expected_by_ring.get(slot.ring, 0) + 1
            for ring_index, expected_count in expected_by_ring.items():
                angles = angles_by_ring.get(ring_index, [])
                errors = errors_by_ring.get(ring_index, [])
                max_gap = self._ring_max_gap_deg(angles)
                ideal_gap = 360.0 / max(1, expected_count)
                # Different-size hulls share a collision-safe annulus, so a
                # craft may legitimately shift farther around its own logical
                # ring to clear a neighbour on the adjacent ring. Keep this
                # per-ring check permissive enough for that manoeuvre; the
                # combined hull and convex-containment checks below remain the
                # authoritative no-gap test for the visible fleet as a whole.
                # At 12+12 and above, the global collision envelope can
                # displace one craft by a few metres while the physical ring
                # is still visibly and geometrically closed. Keep the
                # per-ring tolerance adaptive for dense fleets; the whole
                # hull, annulus and 20-frame hold checks remain mandatory.
                gap_limit = maximum_capture_gap_deg(expected_count)
                radial_limit = 8.0 if compact_capture else 7.0 if dense_capture else 5.0
                complete = (
                    len(angles) == expected_count
                    and len(errors) == expected_count
                    and max(errors, default=float("inf")) <= radial_limit
                    and (expected_count < 3 or max_gap <= gap_limit + 0.5)
                )
                ring_geometry_ready = ring_geometry_ready and complete
                ring_diagnostics[f"{kind}-{ring_index}"] = {
                    "expected": expected_count,
                    "actual": len(angles),
                    "maxGapDeg": round(max_gap, 2),
                    "maxAllowedGapDeg": round(gap_limit, 2),
                    "maxRadialErrorM": round(max(errors, default=float("inf")), 2),
                    "ready": complete,
                }
        assessment = assess_capture(
            [(agent.x, agent.y, agent.z) for agent in agents],
            assigned_specs,
            (safe_target.x, safe_target.y, safe_target.z),
            radial_tolerance=3.2,
        )
        pursuit_complete = self.target_travelled_distance >= self.required_pursuit_distance
        actual_radii = [
            math.hypot(agent.x - safe_target.x, agent.y - safe_target.y)
            for agent in agents
        ]
        lower_radius = 13.5
        upper_radius = self.outer_formation_radius + 15.0
        in_band_count = sum(
            lower_radius <= radius <= upper_radius
            for radius in actual_radii
        )
        max_gap_limit_deg = maximum_capture_gap_deg(len(agents))
        # This is the single terminal geometry contract.  The slot solver and
        # the visual annulus checks can provide convergence hints, but neither
        # may independently promote a mission to CAPTURED.
        containment_contract = assess_containment(
            [(agent.x, agent.y, agent.z) for agent in agents],
            (safe_target.x, safe_target.y, safe_target.z),
            required_count=len(agents),
            device_types=[agent.type for agent in agents],
            minimum_type_counts={"UAV": 1, "USV": 1},
            minimum_radius_m=lower_radius,
            maximum_radius_m=upper_radius,
            maximum_radial_spread_m=max(48.0, upper_radius * 0.40),
            minimum_pairwise_separation_m=7.1,
            valid=[agent.status == "ACTIVE" for agent in agents],
        )
        # Use the contract's layer-aware threshold for asymmetric UAV/USV
        # teams. The plain count threshold remains the default for balanced
        # groups and for each individual physical layer.
        max_gap_limit_deg = containment_contract.allowed_gap_deg
        # Real collision avoidance owns the final centimetres of each pose.
        # Treat the configured concentric rings as one safe containment band:
        # every craft must be outside the target hull, none may remain on a
        # distant staging route, and the actual bearings still have to form a
        # closed convex surround. This is stricter visually than allowing a
        # missing boat, but does not require an exact mathematical slot.
        annulus_ready = (
            len(agents) >= 3
            and assessment.target_inside
            and assessment.combined_max_gap_deg <= max_gap_limit_deg + 0.5
            and min(actual_radii, default=0.0) >= 13.5
            and max(actual_radii, default=float("inf")) <= self.outer_formation_radius + 15.0
        )
        degraded_annulus = (
            # Dense formations (8+8 and above) are solved with two physical
            # rings.  Collision avoidance can move the target just outside
            # the vendor hull polygon even though the executed fleet has
            # closed a safe annulus around it.  Treat that bounded condition
            # as a valid degraded containment candidate after one replan.
            len(agents) >= 8
            and pursuit_complete
            and assessment.target_inside
            and assessment.combined_max_gap_deg <= max_gap_limit_deg + 0.5
            and min(actual_radii, default=0.0) >= 13.5
            and max(actual_radii, default=float("inf")) <= self.outer_formation_radius + 18.0
        )
        # A broad convex hull alone is not a convincing visual closure: one
        # aerial craft can be pushed onto the inner side while the remaining
        # fleet surrounds the target.  Require each physical layer to occupy
        # its own annulus before allowing the degraded completion path.
        def layer_ring_ready(radii: List[float], angles: List[float], minimum: float) -> bool:
            if len(radii) < 3:
                return False
            mean_radius = sum(radii) / len(radii)
            spread_limit = 18.0 if compact_capture else 16.0 if dense_capture else 12.0
            gap_limit = maximum_capture_gap_deg(len(angles))
            mean_offset_limit = 16.0 if compact_capture else 14.0 if dense_capture else 10.0
            return (
                min(radii) >= minimum
                and max(radii) <= self.outer_formation_radius + 8.0
                and max(radii) - min(radii) <= spread_limit
                and self._ring_max_gap_deg(angles) <= gap_limit + 0.5
                and abs(mean_radius - min(radii)) <= mean_offset_limit
            )
        physical_ring_ready = (
            layer_ring_ready(usv_radii, usv_angles, 18.0)
            and layer_ring_ready(uav_radii, uav_angles, 28.0)
        )
        degraded_annulus = degraded_annulus and physical_ring_ready
        if degraded_annulus:
            annulus_ready = True
        # For compact multi-target teams, a single craft can be displaced by
        # the global collision envelope even though the executed hull still
        # safely contains the target.  Treat that state as a valid support-ring
        # closure: the overall hull, angular coverage, radial band and hold
        # confirmation remain mandatory; only exact per-slot geometry is
        # relaxed.
        compact_safe_containment = (
            compact_capture
            and pursuit_complete
            and assessment.target_inside
            and assessment.combined_max_gap_deg <= max_gap_limit_deg + 0.5
            and min(actual_radii, default=0.0) >= 13.5
            and max(actual_radii, default=float("inf")) <= self.outer_formation_radius + 18.0
            and physical_ring_ready
        )
        if compact_safe_containment:
            annulus_ready = True
        # Small asymmetric fleets (for example 1 UAV + 5 USV) and compact
        # 4+4 fleets cannot make two independent type-specific rings.  Their
        # executed poses are nevertheless valid when the combined fleet forms
        # one collision-safe closed hull around the target.  Keep this path
        # separate from the broad annulus fallback so a large visual gap can
        # never be promoted to completion.
        joint_safe_containment = (
            len(agents) >= 6
            and pursuit_complete
            and assessment.target_inside
            and assessment.combined_max_gap_deg <= max_gap_limit_deg + 0.5
            and min(actual_radii, default=0.0) >= lower_radius
            and max(actual_radii, default=float("inf")) <= upper_radius
            and in_band_count == len(agents)
        )
        if joint_safe_containment:
            annulus_ready = True
        gap_score = max(
            0.0,
            min(1.0, 1.0 - assessment.combined_max_gap_deg / max(180.0, max_gap_limit_deg)),
        )
        radial_score = in_band_count / max(1, len(agents))
        containment_confidence = min(
            1.0,
            (0.35 if assessment.target_inside else 0.0)
            + gap_score * 0.35
            + radial_score * 0.30,
        )
        self.last_containment_confidence = containment_confidence
        coarse_containment = self.mission_active and pursuit_complete and annulus_ready
        expected_usv_radius = sum(slot.radius for slot in self.usv_slot_specs) / max(1, len(self.usv_slot_specs))
        expected_uav_radius = sum(slot.radius for slot in self.uav_slot_specs) / max(1, len(self.uav_slot_specs))
        usv_mean_radius = sum(usv_radii) / max(1, len(usv_radii))
        uav_mean_radius = sum(uav_radii) / max(1, len(uav_radii))
        # Compact fleets are visually sensitive: three craft must reach their
        # intended radius and four craft must form a genuinely even cross.
        # Larger fleets use the collision-safe annulus and whole-fleet hull,
        # because exact per-type bearings would conflict with multi-ring hull
        # separation even though the target is already surrounded.
        domain_formation_ready = (
            (ring_geometry_ready or degraded_annulus or compact_safe_containment or joint_safe_containment)
            and
            (len(usv_radii) != 3 or abs(usv_mean_radius - expected_usv_radius) <= 1.15)
            and (len(uav_radii) != 3 or abs(uav_mean_radius - expected_uav_radius) <= 1.35)
            and (joint_safe_containment or len(usv_angles) != 4 or math.degrees(usv_angular_error) <= 18.0)
            and (joint_safe_containment or len(uav_angles) != 4 or math.degrees(uav_angular_error) <= 18.0)
        )
        visible_chase_complete = (
            self.target_travelled_distance >= self.required_pursuit_distance + 20.0
        )
        formation_ready = (
            self.mission_active
            # Completion is forbidden during the initial escape run even if
            # a lucky spawn already resembles a ring.  The extra 20 metres is
            # the visually observable chase segment promised by the UI.
            and visible_chase_complete
            # The broad annulus is only a convergence trigger. Completion
            # additionally requires every physical ring to be radially seated
            # and, where geometrically meaningful, angularly closed.
            and annulus_ready
            and domain_formation_ready
            and containment_contract.ready
        )
        if formation_ready and not self.external_containment_authority:
            if self.containment_candidate_at_sequence is None:
                self.containment_candidate_at_sequence = self.sequence
        elif self.captured_at_sequence is None:
            # Candidate continuity is strict. If the executed ring opens, the
            # target resumes breakout and the 20-frame confirmation restarts.
            self.containment_candidate_at_sequence = None
        if formation_ready and self.formation_ready_at_sequence is None:
            self.formation_ready_at_sequence = self.sequence
        elif not formation_ready:
            self.formation_ready_at_sequence = None
        # Once the hold requirement has been satisfied, keep reporting the
        # required number instead of letting the counter grow forever.  The
        # UI uses this value as a confirmation indicator (e.g. 20/20), not as
        # an elapsed-time counter.
        hold_frames = (
            min(
                self.capture_hold_frames,
                self.sequence - self.formation_ready_at_sequence + 1,
            )
            if self.formation_ready_at_sequence is not None
            else 0
        )
        if (
            formation_ready
            and hold_frames >= self.capture_hold_frames
            and self.captured_at_sequence is None
            and not self.external_containment_authority
        ):
            self.captured_at_sequence = self.sequence
            guard_ids = {int(raw[7]) for raw in self.env.agents}
            self.env.guarding_agents[0] = guard_ids
            self.env.permanently_captured.add(0)
            self.target_behavior_state = "CAPTURED"
            self.target_velocity = (0.0, 0.0)
        captured = self.captured_at_sequence is not None
        formation_held = formation_ready or captured
        gap_repair_required = (
            pursuit_complete
            and not formation_ready
            and (
                assessment.combined_max_gap_deg > max_gap_limit_deg + 0.5
                or not ring_geometry_ready
                or containment_contract.blocker in {
                    "ANGULAR_GAP",
                    "SECTOR_COVERAGE",
                    "RADIAL_SPREAD",
                }
            )
        )
        if preview:
            mission_stage = "PREVIEW"
        elif captured:
            mission_stage = "COMPLETED"
        elif not pursuit_complete:
            mission_stage = (
                "ESCAPE"
                if self.target_travelled_distance < self.required_pursuit_distance * 0.35
                else "PURSUIT"
            )
        elif self.mission_stage in {"ESCAPE", "PURSUIT"}:
            # Keep one explicit interception/formation frame after the chase
            # gate. This prevents the coordinator's first gap repair from
            # making the visible state jump directly from PURSUIT to repair.
            mission_stage = "INTERCEPT"
        elif mean_distance > max(42.0, self.outer_formation_radius + 12.0):
            mission_stage = "INTERCEPT"
        elif gap_repair_required:
            mission_stage = "GAP_REPAIR"
        elif formation_ready:
            mission_stage = "STABLE_CONTAINMENT"
        else:
            mission_stage = "ENCIRCLEMENT"
        self.mission_stage = mission_stage
        mission_stage = (
            "PREVIEW"
            if preview
            else "COMPLETED"
            if captured
            else self.mission_stage
        )
        phase = (
            "PREVIEW"
            if preview
            else "CAPTURED"
            if captured
            else "ESCAPE_PURSUIT"
            if not pursuit_complete
            else "INTERCEPTING"
            if mean_distance > max(42.0, self.outer_formation_radius + 12.0)
            else "ENCIRCLEMENT"
        )
        pursuit_progress = min(
            1.0,
            self.target_travelled_distance / max(1.0, self.required_pursuit_distance),
        )
        if preview:
            raw_progress = 0.0
        elif not pursuit_complete:
            raw_progress = pursuit_progress * 0.35
        elif formation_ready:
            raw_progress = 0.90 + 0.10 * min(1.0, hold_frames / self.capture_hold_frames)
        else:
            raw_progress = 0.35 + containment_confidence * 0.55
        if captured:
            self.display_progress = 1.0
        elif not preview:
            # Progress describes the currently valid containment state. Do
            # not keep a stale 99% after global collision repair invalidates
            # the ring; that makes an unfinished target look terminal.
            self.display_progress = min(0.99, max(0.0, raw_progress))
            self.progress_best_sequence = self.sequence

        if preview:
            capture_blocker = "PREVIEW_NOT_STARTED"
        elif not visible_chase_complete:
            capture_blocker = "PURSUIT_DISTANCE"
        elif not assessment.target_inside:
            capture_blocker = "TARGET_OUTSIDE_HULL"
        elif assessment.combined_max_gap_deg > max_gap_limit_deg + 0.5:
            capture_blocker = "ANGULAR_GAP"
        elif min(actual_radii, default=0.0) < lower_radius:
            capture_blocker = "INNER_RADIUS"
        elif max(actual_radii, default=float("inf")) > upper_radius:
            capture_blocker = "OUTER_RADIUS"
        elif not (ring_geometry_ready or compact_safe_containment or joint_safe_containment):
            capture_blocker = "RING_GEOMETRY"
        elif not formation_ready:
            capture_blocker = "SLOT_CONFLICT"
        elif not captured:
            capture_blocker = "HOLD_CONFIRMATION"
        else:
            capture_blocker = "NONE"

        if not preview and not captured and capture_blocker == self.last_capture_blocker:
            self.stalled_frames += 1
        else:
            self.stalled_frames = 0
        self.last_capture_blocker = capture_blocker

        stalled = (
            not preview
            and pursuit_complete
            and not captured
            and phase in {"INTERCEPTING", "ENCIRCLEMENT"}
            and self.stalled_frames >= 80
        )
        if stalled and self.sequence - self.last_replan_sequence >= 80:
            self.replan_count += 1
            self.last_replan_sequence = self.sequence
            self.progress_best_sequence = self.sequence
            self._rotate_capture_slots()
            self.presentation_slot_assignments.clear()
            self.containment_candidate_at_sequence = None
            self.formation_ready_at_sequence = None
            self.stalled_frames = 0
            gap_direction = self._largest_gap_direction(
                (safe_target.x, safe_target.y, safe_target.z),
                [(agent.x, agent.y, agent.z) for agent in agents],
            )
            if gap_direction is not None:
                self.target_escape_direction = gap_direction
        target_heading_deg = (
            math.degrees(math.atan2(self.target_velocity[1], self.target_velocity[0]))
            % 360.0
            if math.hypot(*self.target_velocity) > 1e-6
            else math.degrees(float(self.env.targets[0, 4])) % 360.0
        )
        target_angles = sorted(
            math.atan2(agent.y - safe_target.y, agent.x - safe_target.x)
            % (2.0 * math.pi)
            for agent in agents
        )
        if len(target_angles) >= 2:
            target_gap, target_gap_start = max(
                (
                    (target_angles[(index + 1) % len(target_angles)] - target_angles[index])
                    % (2.0 * math.pi),
                    target_angles[index],
                )
                for index in range(len(target_angles))
            )
            target_gap_bearing_deg = math.degrees(
                (target_gap_start + target_gap * 0.5) % (2.0 * math.pi)
            )
        else:
            target_gap_bearing_deg = target_heading_deg
        nearest_interceptor = min(
            agents,
            key=lambda agent: math.hypot(agent.x - safe_target.x, agent.y - safe_target.y),
            default=None,
        )
        metrics = {
            "progress": round(self.display_progress, 3),
            "captured": captured,
            "formationReady": formation_held,
            "captureAgents": len(agents) if formation_held else in_band_count,
            "requiredCaptureAgents": assessment.required,
            "capability": assessment.capability,
            "targetInsideFormation": assessment.target_inside,
            "combinedMaxGapDeg": round(assessment.combined_max_gap_deg, 2),
            "maxAllowedGapDeg": round(max_gap_limit_deg, 2),
            "maximumSlotError": round(assessment.radial_error, 2),
            "minimumRadiusM": round(min(actual_radii, default=0.0), 2),
            "maximumRadiusM": round(max(actual_radii, default=0.0), 2),
            "allowedRadiusBandM": [round(lower_radius, 2), round(upper_radius, 2)],
            "containmentConfidence": round(containment_confidence, 3),
            "coarseContainment": coarse_containment,
            "compactSupportMode": compact_capture,
            "safeContainmentReady": compact_safe_containment or joint_safe_containment or degraded_annulus or annulus_ready,
            "domainFormationReady": domain_formation_ready,
            "ringGeometryReady": ring_geometry_ready,
            "ringDiagnostics": ring_diagnostics,
            "captureHoldFrames": hold_frames,
            "requiredCaptureHoldFrames": self.capture_hold_frames,
            "captureStage": 3 if captured else 2 if formation_ready or coarse_containment else 1,
            "missionStage": mission_stage,
            "stableContainmentFrames": hold_frames if formation_ready else 0,
            "requiredStableContainmentFrames": self.capture_hold_frames,
            "gapRepairRequired": gap_repair_required,
            "arrivalRatio": round(radial_score, 3),
            "captureBlocker": capture_blocker,
            "containmentContract": {
                "ready": containment_contract.ready,
                "blocker": containment_contract.blocker,
                "targetInside": containment_contract.target_inside,
                "maxGapDeg": containment_contract.max_gap_deg,
                "maxAllowedGapDeg": containment_contract.allowed_gap_deg,
                "sectorCount": containment_contract.sector_count,
                "coveredSectors": containment_contract.covered_sectors,
                "minimumSeparationM": containment_contract.minimum_separation_m,
                "requiredSeparationM": containment_contract.required_separation_m,
                "radialSpreadM": containment_contract.radial_spread_m,
                "uavCount": containment_contract.uav_count,
                "usvCount": containment_contract.usv_count,
                "invalidParticipants": containment_contract.invalid,
                "stationaryParticipants": containment_contract.stationary,
                "detachedParticipants": containment_contract.detached,
            },
            "settlingStarted": self.settling_started_at_sequence is not None,
            "containmentCandidate": self.containment_candidate_at_sequence is not None,
            "stalledFrames": self.stalled_frames,
            "replanCount": self.replan_count,
            "avoidanceCount": self.avoidance_count,
            "totalDistance": round(float(self.env.total_travel_distance), 3),
            "meanDistanceToTarget": round(mean_distance, 3),
            "targetTravelDistanceM": round(self.target_travelled_distance, 3),
            "requiredPursuitDistanceM": round(self.required_pursuit_distance, 3),
            "pursuitProgress": round(pursuit_progress, 3),
            "targetSpeedMps": round(math.hypot(*self.target_velocity), 3),
            "targetBehavior": self.target_behavior_state,
            "targetIntent": self.target_behavior_state,
            "targetIntentConfidence": round(
                min(1.0, 0.45 + self.last_containment_confidence * 0.4),
                3,
            ),
            "targetHeadingDeg": round(target_heading_deg, 2),
            "targetSpeedReason": self.target_speed_reason,
            "gapCenterDeg": round(target_gap_bearing_deg, 2),
            "nearestInterceptorCode": (
                None if nearest_interceptor is None else nearest_interceptor.code
            ),
            "nearestInterceptorDistanceM": (
                None
                if nearest_interceptor is None
                else round(
                    math.hypot(
                        nearest_interceptor.x - safe_target.x,
                        nearest_interceptor.y - safe_target.y,
                    ),
                    2,
                )
            ),
            "operationalBoundaryClearanceM": round(
                self._operational_clearance(safe_target.x, safe_target.y),
                2,
            ),
            "targetNetDisplacementM": round(
                math.hypot(
                    safe_target.x - self.target_start_scene[0],
                    safe_target.y - self.target_start_scene[1],
                ),
                3,
            ),
            "captureRadius": round(max(slot.radius for slot in self.usv_slot_specs + self.uav_slot_specs), 2),
            "usvFormationRadius": round(self.usv_formation_radius_scene, 2),
            "uavFormationRadius": round(self.uav_slot_specs[0].radius, 2),
            "uavFormationAltitude": 26.0,
            "usvFormationRings": 1 + max(slot.ring for slot in self.usv_slot_specs),
            "uavFormationRings": 1 + max(slot.ring for slot in self.uav_slot_specs),
            "usvAngularErrorDeg": round(math.degrees(usv_angular_error), 2),
            "uavAngularErrorDeg": round(math.degrees(uav_angular_error), 2),
            "minimumUsvSpacing": round(
                self.safety.required_separation("USV", "USV"),
                2,
            ),
        }
        return RuntimeFrame(
            self.run_id, self.code, self.sequence, int(time.time() * 1000), phase,
            agents,
            [
                TargetFrame(
                    "TARGET-001",
                    "CAPTURE_TARGET",
                    safe_target.x,
                    safe_target.y,
                    float(
                        self.initial_pose_map()
                        .get("TARGET-001", {})
                        .get("upM", 0.0)
                    ),
                    (
                        float(self.initial_pose_map().get("TARGET-001", {}).get("headingDeg", 0.0)) % 360.0
                        if initial_snapshot
                        else math.degrees(float(self.env.targets[0, 4])) % 360.0
                    ),
                ),
            ],
            metrics,
            # Obstacles stay authoritative in the safety solver but are hidden
            # from the clean Task Center 2-D/3-D presentation.
            obstacles=[],
            terminalStatus=(
                "COMPLETED"
                if self.captured_at_sequence is not None
                else None
            ),
        )
