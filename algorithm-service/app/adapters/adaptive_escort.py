from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from heapq import heappop, heappush
from typing import Dict, Mapping, Sequence

from app.adapters.base import AlgorithmAdapter
from app.adapters.escort_defense import DEPARTURE_DISTANCE, EscortDefenseCoordinator, inner_slots
from app.adapters.shore_target_layout import read_escort_berth_layout, read_shore_target_layout
from app.capture import (
    FormationSlot,
    RingMember,
    RingSlot,
    assess_canonical_ring,
    assess_containment,
    build_canonical_slots,
    maximum_capture_gap_deg,
)
from app.decision import DecisionAgent, DecisionTarget, DynamicTaskAllocator
from app.navigation import SceneSafetyFilter
from app.scenario import derive_scenario_plan
from app.schemas import AgentFrame, RuntimeFrame, TargetFrame


DT = 0.1
CAPTURE_HOLD_FRAMES = 25
# Rendered target hulls remain clear above 12 m. The motion solver maintains
# an 18 m nominal gap; a slightly lower terminal threshold avoids declaring a
# false attack success from sub-frame projection/rounding at 12.98 m.
BREACH_DISTANCE_M = 28.0
TARGET_SEPARATION_M = 34.0
CONVOY_TARGET_SPACING_M = 42.0
# Keep the square close to both protected hulls while preserving the rendered
# USV/escort-target clearance (5.2 + 7.3 + 1.4 = 13.9 m).
CONVOY_GUARD_MARGIN_M = 15.0
CLOSE_PROTECTION_SLOT_TOLERANCE_M = 8.0
SHORE_MARGIN_M = 28.0
THREAT_DETECTION_M = 155.0
THREAT_AWARENESS_M = 190.0
ESCORT_DEPARTURE_MIN_M = DEPARTURE_DISTANCE
ATTACK_INTENT_HOLD_FRAMES = 10
ESCAPE_INTENT_HOLD_FRAMES = 5
INTERCEPT_DISTANCE_M = 58.0
INTERCEPT_LATERAL_M = 30.0
INTERCEPT_HOLD_FRAMES = 16
URGENT_INTERCEPT_HOLD_FRAMES = 5
URGENT_TTI_SECONDS = 34.0
URGENT_DISTANCE_M = 105.0
COVER_REVERSE_SPEED_MPS = 0.8
COVER_MIN_FRAMES = 40  # Evidence window, never a capture deadline.
CONTAINMENT_STANDOFF_M = 78.0
CONTAINMENT_REPLAN_M = 108.0
POST_CAPTURE_CONVOY_CLEARANCE_M = TARGET_SEPARATION_M + 8.0
# Capture itself requires every protected vessel to be outside the wider
# containment stand-off, so a newly captured target never starts with the
# convoy trapped inside its keep-out circle. Preserve a 52 m centre margin
# throughout post-capture transit; this also keeps the convoy clear of the
# surface craft on the inner containment ring. Allowing a temporary 34 m
# approach made a completed enemy look as if it could still obstruct or ram
# the escort route.
POST_CAPTURE_TRANSIT_CLEARANCE_M = TARGET_SEPARATION_M + 18.0
POST_MISSION_SLOT_TOLERANCE_M = 10.0
POST_MISSION_STABLE_FRAMES = 12
POST_MISSION_RING_AVOIDANCE_M = TARGET_SEPARATION_M + 18.0
POST_MISSION_OUTER_GUARD_GAP_M = 22.0
POST_MISSION_ROUTE_ARRIVAL_M = 8.0
PROTECTED_SAFE_GATE_OFFSET_M = 6.0
# Keep both post-capture phases visible in telemetry/WebGL.  Stable containment
# confirms that the ring is real; safe-gate transit then becomes the explicit
# final navigation contract requested by the operator.
STABLE_CONTAINMENT_DISPLAY_FRAMES = 18
SAFE_GATE_TRANSIT_MIN_FRAMES = 12


def _length(x: float, y: float) -> float:
    return math.hypot(x, y)


def _unit(x: float, y: float, fallback: tuple[float, float] = (1.0, 0.0)) -> tuple[float, float]:
    value = _length(x, y)
    return fallback if value < 1e-8 else (x / value, y / value)


def _clamp_magnitude(x: float, y: float, limit: float) -> tuple[float, float]:
    value = _length(x, y)
    if value <= limit or value < 1e-8:
        return x, y
    return x * limit / value, y * limit / value


def _square_formation_offsets(count: int, spacing: float) -> list[tuple[float, float]]:
    """Return centred, route-aligned slots for a compact protected convoy."""
    columns = max(1, math.ceil(math.sqrt(max(1, count))))
    rows = max(1, math.ceil(max(1, count) / columns))
    raw = [
        (
            (index // columns - (rows - 1) / 2.0) * spacing,
            (index % columns - (columns - 1) / 2.0) * spacing,
        )
        for index in range(max(1, count))
    ]
    mean_x = sum(item[0] for item in raw) / len(raw)
    mean_y = sum(item[1] for item in raw) / len(raw)
    return [(x - mean_x, y - mean_y) for x, y in raw]


@dataclass
class _Vehicle:
    code: str
    kind: str
    x: float
    y: float
    z: float
    role: str
    group_id: str
    protected_index: int
    assigned_threat: int | None = None
    vx: float = 0.0
    vy: float = 0.0
    final_slot_angle: float | None = None


@dataclass
class _Protected:
    code: str
    x: float
    y: float
    heading: float
    destination_x: float
    destination_y: float
    vx: float = 0.0
    vy: float = 0.0
    state: str = "ESCORTING"
    avoidance_side: int = 0


@dataclass
class _Threat:
    code: str
    x: float
    y: float
    heading: float
    protected_index: int
    activate_frame: int
    state: str = "WAITING"
    capture_hold: int = 0
    forced: bool = False
    detected_frame: int | None = None
    vx: float = 0.0
    vy: float = 0.0
    previous_distance: float = math.inf
    capture_phase: float = 0.0
    travelled_distance: float = 0.0
    captured_frame: int | None = None
    capture_started_frame: int | None = None
    capture_stage: int = 0
    capture_arrival_ratio: float = 0.0
    capture_max_gap_deg: float = 360.0
    capture_radial_error: float = math.inf
    capture_start_travel_distance: float = 0.0
    required_pursuit_distance: float = 80.0
    escape_dir_x: float = 1.0
    escape_dir_y: float = 0.0
    intent: str = "UNCLASSIFIED"
    intent_hold_frames: int = 0
    escape_intent_hold_frames: int = 0
    escape_intent_confirmed: bool = False
    auto_capture_reason: str = ""
    attack_start_distance: float = math.inf
    closest_attack_distance: float = math.inf
    intercept_hold_frames: int = 0
    intercept_stage_frames: int = 0
    last_retarget_frame: int = 0
    cruise_speed: float = 1.8
    maximum_speed: float = 2.8
    intercept_attempts: int = 0
    breach_until_frame: int = 0
    gap_filler_code: str = ""
    gap_center_angle: float = 0.0
    mission_stage: str = "GUARDING"
    containment_stage_latched: bool = False
    containment_soft_failure_frames: int = 0
    slowdown_reason: str = "NONE"
    intent_confidence: float = 0.15
    nearest_defender_code: str = ""
    nearest_defender_distance: float = math.inf
    response_dispatched: bool = False
    response_motion_frames: int = 0
    screen_established: bool = False
    cover_slots: dict[str, tuple[float, float]] = field(default_factory=dict)
    cover_origin: tuple[float, float] | None = None
    cover_frames: int = 0
    cover_distance: float = 0.0
    cover_released: bool = False
    cover_verified: bool = False


class AdaptiveEscortAdapter(AlgorithmAdapter):
    """Moving-target escort, guarding and automatic capture controller."""

    code = "ESCORT_GUARD"
    version = "3.0.0"

    def __init__(self, run_id: int, config: Dict[str, object] | None = None) -> None:
        super().__init__(run_id, config)
        self.plan = derive_scenario_plan(
            int(self.config.get("uavCount", 3)), int(self.config.get("usvCount", 3)),
        )
        self.random = random.Random(int(self.config.get("seed", 20260814)))
        self.seed = int(self.config.get("seed", 20260814))
        self.uav_cruise = min(15.0, max(0.2, float(self.config.get("uavSpeedMps", 5.0))))
        self.usv_cruise = min(4.0, max(0.2, float(self.config.get("usvSpeedMps", 3.0))))
        half_width, half_height = self.plan.world_width / 2.0, self.plan.world_height / 2.0
        self.safe_bounds = (
            -half_width + SHORE_MARGIN_M, half_width - SHORE_MARGIN_M,
            -half_height + SHORE_MARGIN_M, half_height - SHORE_MARGIN_M,
        )
        self.safety = SceneSafetyFilter({"bounds": list(self.safe_bounds), "obstacles": []})
        self._berth_layout = read_escort_berth_layout(self.config)
        self.protected = self._create_protected()
        # Generate the original encounter first, then move both target classes
        # together exactly once. Generating threats around an already shifted
        # convoy would change their radius a second time near the water bounds.
        self.threats = self._create_threats()
        if self._berth_layout is not None:
            self._place_escort_at_berth()
        else:
            self._place_targets_nearer_shore()
        protected_center_x = sum(item.x for item in self.protected) / len(self.protected)
        protected_center_y = sum(item.y for item in self.protected) / len(self.protected)
        self._protected_formation_offsets = {
            item.code: (item.x - protected_center_x, item.y - protected_center_y)
            for item in self.protected
        }
        self._protected_start_positions = {
            item.code: (item.x, item.y)
            for item in self.protected
        }
        self.protected_start_x = {item.code: item.x for item in self.protected}
        self.vehicles = self._create_vehicles()
        initial_guards = sorted(
            (item for item in self.vehicles if item.role == "CLOSE_GUARD"),
            key=lambda item: (
                int(item.code.rsplit("-", 1)[-1]),
                0 if item.kind == "USV" else 1,
            ),
        )
        self._convoy_guard_slot_by_code = {
            item.code: position for position, item in enumerate(initial_guards)
        }
        self._initial_convoy_guard_codes = {
            item.code for item in initial_guards
        }
        self._separate_initial_response_craft()
        self._unavailable_codes: set[str] = set()
        self._withdrawn_vehicles: dict[str, _Vehicle] = {}
        self._guard_replacement_pending = False
        self._shore_assembly_required = self.config.get("shoreDeploymentMode") == "SAFE_RELEASE"
        self._shore_assembly_pending = self._shore_assembly_required
        self._escort_assembly_progress = 0.0 if self._shore_assembly_required else 1.0
        self._escort_assembly_completed = not self._shore_assembly_required
        self._escort_assembly_initial_errors: dict[tuple[str, int, int], float] = {}
        if self._shore_assembly_required:
            self._apply_release_poses()
        self.previous = {item.code: (item.x, item.y, item.z) for item in self.vehicles}
        self.avoidance_count = 0
        self.min_protected_threat_distance = math.inf
        self.min_agent_distance = math.inf
        self.min_shore_distance = math.inf
        self._initial_frame_pending = True
        self._terminal_status: str | None = None
        self._terminal_reason = ""
        self._terminal_blocker = "MISSION_IN_PROGRESS"
        self._protected_arrival_ready = False
        self._captured_rings_ready = False
        self._display_progress = 0.0
        self._display_escort_progress = 0.0
        self._capture_slot_reference_errors: dict[tuple, float] = {}
        self._capture_slot_progress: dict[str, float] = {}
        self._reported_mission_stage = "ESCORTING"
        self._final_containment_consolidated = False
        self._ring_slots: dict[int, dict[str, RingSlot]] = {}
        self._ring_best_arrival: dict[int, float] = {}
        self._ring_stalled_frames: dict[int, int] = {}
        self._ring_replans: dict[int, int] = {}
        self._vehicle_stall_frames: dict[str, int] = {}
        self._vehicle_task_error: dict[str, float] = {}
        self._convoy_support_slot_by_code: dict[str, int] = {}
        self._convoy_support_goal_override_by_code: dict[str, tuple[float, float]] = {}
        self._convoy_support_route_by_code: dict[str, list[tuple[float, float]]] = {}
        self._convoy_support_route_cursor_by_code: dict[str, int] = {}
        self._convoy_support_route_replan_frame_by_code: dict[str, int] = {}
        self._convoy_support_swap_pairs: set[tuple[str, str]] = set()
        self._post_watch_threat_by_code: dict[str, int] = {}
        self._post_watch_angle_by_code: dict[str, float] = {}
        self._post_mission_initial_error_by_code: dict[str, float] = {}
        self._post_mission_formation_initialized = False
        self._post_mission_final_replan_done = False
        self._post_mission_best_maximum_error = math.inf
        self._post_mission_stalled_frames = 0
        self._post_mission_slot_replans = 0
        self._convoy_support_ready_frames = 0
        self._stable_containment_ready_frame: int | None = None
        self._safe_gate_transit_started_frame: int | None = None
        self.capture_started_frame: int | None = None
        self._parallel_response_enabled = bool(self.config.get(
            "parallelThreatResponse",
            self.plan.effective_scale >= 15
            and self.plan.realtime_tier == "PHASE_TWO_REALTIME",
        ))
        self._parallel_response_started = False
        self.dynamic_allocator = DynamicTaskAllocator(
            evaluation_interval_frames=max(
                5, int(self.config.get("assignmentEvaluationFrames", 10))
            ),
            minimum_improvement_ratio=float(
                self.config.get("assignmentMinimumImprovement", 0.12)
            ),
            confirmation_cycles=max(
                1, int(self.config.get("assignmentConfirmationCycles", 2))
            ),
            cooldown_frames=max(
                10, int(self.config.get("assignmentCooldownFrames", 30))
            ),
        )
        self.assignment_changes: list[dict[str, object]] = []
        self.assignment_last_result: dict[str, object] = {
            "reason": "INITIAL_RESPONSE_ALLOCATION",
            "improvementRatio": 0.0,
        }
        self._response_origins: dict[str, tuple[float, float, float, float]] = {}
        self._response_progress: dict[str, float] = {}
        self._guard_sector_angle: float | None = None
        self._guard_sector_sequence = -1
        self._tactical_events: list[dict[str, object]] = []
        self.defense = EscortDefenseCoordinator(self)

    def _apply_release_poses(self) -> None:
        """Begin at verified departure endpoints; assemble around the live hull."""
        self._shore_encounter_radii = {
            threat.code: self._distance_to_protected(threat) for threat in self.threats
        }
        poses = self.initial_pose_map()
        for item in self.vehicles:
            pose = poses.get(item.code)
            if pose is None:
                continue
            item.x, item.y, item.z = self.initial_pose_to_local(pose)
            heading = float(pose.get("headingDeg", 0.0)) % 360.0
            speed = float(pose.get("speedMps", 0.0))
            item.vx, item.vy = speed * math.cos(math.radians(heading)), speed * math.sin(math.radians(heading))
            self._stable_headings[item.code] = heading
        # Mission slots and target routes retain their original water bounds.
        # Execution additionally needs the actual inlet from the shore; using
        # the old scenario rectangle would project a released boat 100 m inland.
        self.safety.bounds = (
            min(self.safe_bounds[0], min(v.x for v in self.vehicles) - 10.0),
            max(self.safe_bounds[1], max(v.x for v in self.vehicles) + 10.0),
            min(self.safe_bounds[2], min(v.y for v in self.vehicles) - 10.0),
            max(self.safe_bounds[3], max(v.y for v in self.vehicles) + 10.0),
        )

    def _protection_status(self) -> tuple[int, float]:
        ready, remaining = 0, []
        guards = [v for v in self.vehicles if v.role == "CLOSE_GUARD"]
        for kind in ("UAV", "USV"):
            candidates = [v for v in guards if v.kind == kind and v.code not in self._unavailable_codes]
            errors = []
            for item in candidates:
                position = self._convoy_guard_slot_by_code.get(item.code, 0)
                goal = self._convoy_guard_point(position, max(1, len(guards)))
                errors.append(math.hypot(item.x - goal[0], item.y - goal[1]))
            error = min(errors, default=math.inf)
            remaining.append(error)
            ready += int(error <= CLOSE_PROTECTION_SLOT_TOLERANCE_M)
        return ready, max(remaining, default=0.0)

    def _observe_escort_assembly(self, agents: Mapping[str, AgentFrame]) -> None:
        """Measure initial close-protection approach from executed poses only.

        The preview contains planned release poses, not observations. Establish
        each guard's baseline only on reconciliation, and leave frame generation
        read-only. Progress records real distance closed to the same 8 m gate
        used by protection readiness; elapsed time never contributes.
        """
        if self._escort_assembly_completed:
            return
        guards = [v for v in self.vehicles if v.role == "CLOSE_GUARD"]
        credit = {"UAV": 0.0, "USV": 0.0}
        # Computing a live slot can populate the tactical sector cache. A
        # presentation observation must not select or advance that sector.
        prior_sector = self._guard_sector_angle, self._guard_sector_sequence
        try:
            for item in guards:
                agent = agents.get(item.code)
                if (agent is None or item.code in self._unavailable_codes
                        or agent.status != "ACTIVE"
                        or agent.role in {"DEPLOYMENT", "OPERATOR_CONTROLLED", "RETURNED"}
                        or not all(math.isfinite(value) for value in (agent.x, agent.y, agent.z))):
                    continue
                position = self._convoy_guard_slot_by_code.get(item.code, 0)
                goal = self._convoy_guard_point(position, max(1, len(guards)))
                error = math.hypot(agent.x - goal[0], agent.y - goal[1])
                if not math.isfinite(error):
                    continue
                key = item.code, position, len(guards)
                initial = self._escort_assembly_initial_errors.setdefault(key, error)
                if not self.mission_active:
                    continue
                tolerance = CLOSE_PROTECTION_SLOT_TOLERANCE_M
                contribution = (1.0 if error <= tolerance else max(
                    0.0, min(0.999999, (initial - error) / max(1e-6, initial - tolerance)),
                ))
                credit[item.kind] = max(credit[item.kind], contribution)
        finally:
            self._guard_sector_angle, self._guard_sector_sequence = prior_sector
        if self.mission_active:
            self._escort_assembly_progress = max(
                self._escort_assembly_progress, (credit["UAV"] + credit["USV"]) / 2.0,
            )
            self._escort_assembly_completed = credit["UAV"] == credit["USV"] == 1.0

    def reconcile_execution(
        self,
        agents: Sequence[AgentFrame],
        previous_agents: Mapping[str, AgentFrame],
        unavailable_codes: Sequence[str] = (),
    ) -> None:
        prior_unavailable = self._unavailable_codes
        self._unavailable_codes = set(unavailable_codes)
        by_code = {agent.code: agent for agent in agents}
        all_vehicles = [*self.vehicles, *self._withdrawn_vehicles.values()]
        for item in all_vehicles:
            agent = by_code.get(item.code)
            if agent is None:
                continue
            prior = previous_agents.get(item.code)
            if prior is not None and prior is not agent:
                item.vx, item.vy = (agent.x - prior.x) / DT, (agent.y - prior.y) / DT
            item.x, item.y, item.z = float(agent.x), float(agent.y), float(agent.z)
            self.previous[item.code] = (item.x, item.y, item.z)
            self._stable_headings[item.code] = float(agent.heading) % 360.0
        withdrawn = {code for code, agent in by_code.items()
                     if code in self._unavailable_codes and agent.status == "RETURNED"}
        changed_membership = withdrawn != set(self._withdrawn_vehicles)
        affected_threats: set[int] = set()
        if changed_membership:
            for item in all_vehicles:
                if item.code in withdrawn:
                    if item.assigned_threat is not None:
                        affected_threats.add(item.assigned_threat)
                    self._convoy_guard_slot_by_code.pop(item.code, None)
                    item.assigned_threat = None
                    item.role, item.group_id = "WITHDRAWN", "RETURNED"
                    item.final_slot_angle = None
                    self._withdrawn_vehicles[item.code] = item
                elif item.code in self._withdrawn_vehicles:
                    self._withdrawn_vehicles.pop(item.code)
                    item.role, item.group_id = "RECON", f"RECON-{item.protected_index + 1:03d}"
            # Withdrawn craft remain in every emitted frame, but no planner,
            # guard allocator or geometric completion test can enrol them.
            self.vehicles = sorted((v for v in all_vehicles if v.code not in withdrawn), key=lambda v: v.code)
            affected_threats.update(self._replace_withdrawn_guards())
            self._post_mission_formation_initialized = False
            self._post_mission_final_replan_done = False
            self._convoy_support_ready_frames = 0
        if changed_membership or prior_unavailable - self._unavailable_codes:
            active = [t for t in self.threats if t.forced and t.state not in {"CAPTURED", "SECURED", "ESCAPED"}]
            if active:
                self._rebalance_capture_groups(active)
                affected_threats.update(self.threats.index(t) for t in active)
            for index in affected_threats:
                threat = self.threats[index]
                threat.capture_hold = 0
                threat.containment_stage_latched = False
                if threat.state in {"CAPTURED", "SECURED", "CAPTURE_HOLD"}:
                    threat.state = "ENCIRCLING"
                    threat.captured_frame = None
                self._freeze_even_slot_angles(index)
            self._stable_containment_ready_frame = None
            self._safe_gate_transit_started_frame = None
        blocking = self._unavailable_codes - set(self._withdrawn_vehicles)
        if blocking:
            for index, threat in enumerate(self.threats):
                if any(v.assigned_threat == index and v.code in self._unavailable_codes for v in self.vehicles):
                    threat.capture_hold = 0
        if (blocking or changed_membership) and self._terminal_status == "COMPLETED":
            self._terminal_status = None
            self._terminal_reason = ""
        self._observe_escort_assembly(by_code)

    def _replace_withdrawn_guards(self) -> set[int]:
        """Reserve a real mixed replacement; the convoy waits for its arrival."""
        affected: set[int] = set()
        for kind in ("UAV", "USV"):
            if any(v.kind == kind and v.role == "CLOSE_GUARD" for v in self.vehicles):
                continue
            candidates = [v for v in self.vehicles if v.kind == kind and v.code not in self._unavailable_codes]
            self._guard_replacement_pending = True
            if not candidates:
                continue
            item = min(candidates, key=lambda v: (
                v.assigned_threat is not None,
                math.hypot(v.x - self.protected[v.protected_index].x, v.y - self.protected[v.protected_index].y),
                v.code,
            ))
            if item.assigned_threat is not None:
                affected.add(item.assigned_threat)
            item.assigned_threat = None
            item.final_slot_angle = None
            item.role, item.group_id = "CLOSE_GUARD", f"GUARD-{item.protected_index + 1:03d}"
            self._initial_convoy_guard_codes.add(item.code)
            for screen in self.defense.screens.values():
                if item.code in screen.codes:
                    screen.codes.remove(item.code)
                    screen.slots.pop(item.code, None)
                    screen.origins.pop(item.code, None)
        guards = sorted((v for v in self.vehicles if v.role == "CLOSE_GUARD"), key=lambda v: v.code)
        self._convoy_guard_slot_by_code = {v.code: i for i, v in enumerate(guards)}
        return affected

    def _active_force_requirements(self) -> tuple[int, int, int]:
        # A mixed close-protection pair remains reserved. Each unresolved or
        # contained incident needs at least a three-member mixed geometric ring.
        incidents = [t for t in self.threats if t.state != "ESCAPED"]
        rings = len(incidents)
        uavs = rings + 1
        usvs = rings + 1
        total = 3 * rings + 2
        screening = sum(not t.cover_verified and not t.forced for t in incidents)
        if screening:
            # Preserve the original TWO-USV screen and its 40-frame reverse
            # evidence; lack of force is not fixed by lowering that contract.
            usvs = max(usvs, 2 * screening + 1)
            total = max(total, uavs + usvs)
        return uavs, usvs, total

    def _active_force_sufficient(self) -> bool:
        uavs, usvs, total = self._active_force_requirements()
        available = [v for v in self.vehicles if v.code not in self._unavailable_codes]
        return (sum(v.kind == "UAV" for v in available) >= uavs
                and sum(v.kind == "USV" for v in available) >= usvs
                and len(available) >= total)

    def rejoin_target(self, device_code: str) -> tuple[float, float, float] | None:
        item = next((v for v in self.vehicles if v.code == device_code), self._withdrawn_vehicles.get(device_code))
        return None if item is None else self._desired_position(item)

    def _place_targets_nearer_shore(self) -> None:
        layout = read_shore_target_layout(self.config)
        if layout is None or layout.ratio == 1.0:
            return
        candidates = [
            (item, kind, layout.position(item.x, item.y))
            for items, kind in ((self.protected, "ESCORT_TARGET"), (self.threats, "THREAT_TARGET"))
            for item in items
        ]
        for item, kind, (x, y) in candidates:
            safe = self.safety.constrain((x, y, 0.0), (x, y, 0.0), kind, (), 0.0)
            if not (math.isclose(x, safe.x, abs_tol=1e-7) and math.isclose(y, safe.y, abs_tol=1e-7)):
                raise ValueError(f"targetShoreDistanceRatio places {item.code} outside navigable water")
        for index, (item, kind, point) in enumerate(candidates):
            for other, other_kind, other_point in candidates[index + 1:]:
                required = max(TARGET_SEPARATION_M, self.safety.required_separation(kind, other_kind))
                if math.dist(point, other_point) < required:
                    raise ValueError(f"targetShoreDistanceRatio leaves insufficient target separation: {item.code}/{other.code}")
        for item, _, (x, y) in candidates:
            item.x, item.y = x, y

    def _place_escort_at_berth(self) -> None:
        """Initialize one outward route in the verified berth-side water."""
        layout = self._berth_layout
        assert layout is not None
        ux, uy = layout.direction
        old_x = sum(item.x for item in self.protected) / len(self.protected)
        old_y = sum(item.y for item in self.protected) / len(self.protected)
        offsets = [(item.x - old_x, item.y - old_y) for item in self.protected]
        extent = max((math.hypot(*offset) for offset in offsets), default=0.0)
        start_x, start_y = layout.east + ux * (45.0 + extent), layout.north + uy * (45.0 + extent)
        shore_left, shore_right, shore_bottom, shore_top = layout.shore_bounds
        def corridor_distance(x: float, y: float, start: tuple[float, float], end: tuple[float, float]) -> float:
            dx, dy = end[0] - start[0], end[1] - start[1]
            fraction = max(0.0, min(1.0, ((x - start[0]) * dx + (y - start[1]) * dy) / max(1e-6, dx * dx + dy * dy)))
            return math.hypot(x - start[0] - dx * fraction, y - start[1] - dy * fraction)
        for lateral in (0.0, 18.0, -18.0, 36.0, -36.0, 54.0, -54.0, 72.0, -72.0, 90.0, -90.0):
            candidate_x, candidate_y = start_x - uy * lateral, start_y + ux * lateral
            points = [(candidate_x + dx + gx, candidate_y + dy + gy)
                      for dx, dy in offsets
                      for gx, gy in ((0.0, 0.0), (15.0, -15.0), (-15.0, 15.0), (15.0, 15.0), (-15.0, -15.0))]
            shore_clearance = min(
                (shore_left - x if ux < 0 else x - shore_right)
                if abs(ux) >= abs(uy) else (shore_bottom - y if uy < 0 else y - shore_top)
                for x, y in points
            )
            if shore_clearance < 20.0:
                continue
            if all(corridor_distance(x, y, berth, approach) >= 14.0
                   for x, y in points
                   for berth, approach in zip(layout.berth_positions, layout.approach_positions)):
                start_x, start_y = candidate_x, candidate_y
                break
        else:
            raise ValueError("escort berth start has no clear position beside the departure corridors")
        destination_x, destination_y = start_x + ux * 220.0, start_y + uy * 220.0
        left = min(self.safe_bounds[0], start_x - 90.0 - extent, destination_x - 90.0 - extent)
        right = max(self.safe_bounds[1], start_x + 90.0 + extent, destination_x + 90.0 + extent)
        bottom = min(self.safe_bounds[2], start_y - 90.0 - extent, destination_y - 90.0 - extent)
        top = max(self.safe_bounds[3], start_y + 90.0 + extent, destination_y + 90.0 + extent)
        # The mission rectangle stays on the seaward side of the island.
        # _apply_release_poses separately admits the real pad/berth inlet for
        # fleet execution; it must not turn that inlet into target water.
        if abs(ux) >= abs(uy):
            if ux < 0:
                right = min(right, shore_left - 14.0)
            else:
                left = max(left, shore_right + 14.0)
        elif uy < 0:
            top = min(top, shore_bottom - 14.0)
        else:
            bottom = max(bottom, shore_top + 14.0)
        if left >= right or bottom >= top:
            raise ValueError("escort berth route has no navigable seaward rectangle")
        self.safe_bounds = left, right, bottom, top
        self.safety.bounds = self.safe_bounds
        heading = math.degrees(math.atan2(uy, ux)) % 360.0
        for item, (dx, dy) in zip(self.protected, offsets):
            item.x, item.y = start_x + dx, start_y + dy
            item.destination_x, item.destination_y = destination_x + dx, destination_y + dy
            item.heading = heading
            if (self._project_to_safe_water(item.x, item.y, CONVOY_GUARD_MARGIN_M + 6.0) != (item.x, item.y)
                    or self._project_to_safe_water(item.destination_x, item.destination_y, 20.0) != (item.destination_x, item.destination_y)):
                raise ValueError("escort berth start and destination require clear guard water")
            if any(math.hypot(item.x - x, item.y - y) < TARGET_SEPARATION_M for x, y in layout.berth_positions):
                raise ValueError("escort berth start overlaps a configured berth")
        for index, threat in enumerate(self.threats):
            # Keep encounter distance independently of the older 2/3 target
            # layout. Threats are in open water ahead, never beside the pads.
            lateral = ((index % 3) - (min(3, len(self.threats)) - 1) / 2.0) * 54.0
            forward = 120.0 + (index // 3) * 40.0
            threat.x, threat.y = self._project_to_safe_water(
                start_x + ux * forward - uy * lateral,
                start_y + uy * forward + ux * lateral,
                38.0,
            )
            toward_x, toward_y = _unit(start_x - threat.x, start_y - threat.y)
            threat.vx, threat.vy = toward_x * threat.cruise_speed, toward_y * threat.cruise_speed
            threat.heading = math.degrees(math.atan2(toward_y, toward_x)) % 360.0
            if any(math.hypot(threat.x - item.x, threat.y - item.y) < TARGET_SEPARATION_M for item in self.protected):
                raise ValueError("escort berth encounter overlaps the protected formation")
            if any(math.hypot(threat.x - other.x, threat.y - other.y) < TARGET_SEPARATION_M for other in self.threats[:index]):
                raise ValueError("escort berth encounter has insufficient threat separation")

    def _escort_route_axis(self) -> tuple[float, float]:
        return (1.0, 0.0) if self._berth_layout is None else self._berth_layout.direction

    def _escort_route_station(self, item: _Protected, fraction: float) -> tuple[float, float]:
        if self._berth_layout is None:
            start_x = self.protected_start_x[item.code]
            return start_x + (item.destination_x - PROTECTED_SAFE_GATE_OFFSET_M - start_x) * fraction, item.destination_y
        start_x, start_y = self._protected_start_positions[item.code]
        ux, uy = self._escort_route_axis()
        length = math.hypot(item.destination_x - start_x, item.destination_y - start_y) - PROTECTED_SAFE_GATE_OFFSET_M
        return start_x + ux * length * fraction, start_y + uy * length * fraction

    def _limit_escort_approach(self, item: _Protected, x: float, y: float) -> tuple[float, float]:
        """Keep the existing 96% pre-containment gate along the real route."""
        if self._berth_layout is None:
            limit_x, _ = self._escort_route_station(item, 0.96)
            if x > limit_x:
                x, item.vx = limit_x, min(0.0, item.vx)
            return x, y
        ux, uy = self._escort_route_axis()
        limit_x, limit_y = self._escort_route_station(item, 0.96)
        excess = (x - limit_x) * ux + (y - limit_y) * uy
        if excess > 0.0:
            x, y = x - ux * excess, y - uy * excess
            forward_velocity = max(0.0, item.vx * ux + item.vy * uy)
            item.vx, item.vy = item.vx - ux * forward_velocity, item.vy - uy * forward_velocity
        return x, y

    def _create_protected(self) -> list[_Protected]:
        usable_width = self.safe_bounds[1] - self.safe_bounds[0]
        # Leave room behind and around the convoy for the complete initial
        # 10+10 patrol pattern. Starting only 35 m from the safe-water edge
        # clamped several boats onto the same line and caused a first-frame
        # collision-resolution jump after the speed increase.
        start_x = -min(24.0, usable_width * 0.08)
        offsets = _square_formation_offsets(
            self.plan.protected_count,
            CONVOY_TARGET_SPACING_M,
        )
        destination_x = self.safe_bounds[1] - 42.0
        return [
            _Protected(
                f"PROTECTED-{index + 1:03d}", start_x + offsets[index][0],
                offsets[index][1], 0.0,
                destination_x + offsets[index][0], offsets[index][1],
            ) for index in range(self.plan.protected_count)
        ]

    def _create_threats(self) -> list[_Threat]:
        """Construct feasible encounter geometry before applying speed control."""
        result = []
        target = self.protected[0]
        left, right, bottom, top = self.safe_bounds
        radius = min(target.x-left, right-target.x, target.y-bottom, top-target.y) - 12.0
        radius = min(170.0, radius)
        remaining = self.plan.uav_count + self.plan.usv_count - 4*self.plan.threat_count
        inner_radius = max((_length(*p) for p in inner_slots(max(0, remaining))), default=20)
        self._rendezvous_radius = max(84.0, inner_radius + 62.0)
        count = self.plan.threat_count
        span = min(math.tau, max(0, count - 1) * math.pi * .75)
        phase = self.random.uniform(-.16, .16)
        for index in range(count):
            angle = phase + (math.tau*index/count if span >= math.tau else
                             -span/2 + span*index/max(1,count-1))
            spawn_radius = radius - (index % 3)*2.0
            x, y = target.x+math.cos(angle)*spawn_radius, target.y+math.sin(angle)*spawn_radius
            ux, uy = _unit(target.x-x,target.y-y)
            speed = 1.5+(spawn_radius-(radius-4.0))*.15
            result.append(_Threat(f"THREAT-{index+1:03d}",x,y,
                math.degrees(math.atan2(uy,ux))%360,0,1,"APPROACHING",
                vx=ux*speed,vy=uy*speed,cruise_speed=speed))
        return result

    def _guard_count(self, count: int) -> int:
        desired = (
            4
            if self.plan.protected_count == 1 and self.plan.effective_scale >= 18
            else (2 if count >= self.plan.protected_count * 4 else 1)
            * self.plan.protected_count
        )
        # Parallel realtime capture reserves four craft of each kind per
        # threat. Never improve the close-guard picture by starving a ring.
        capture_reserve = (
            self.plan.threat_count * 4
            if self.plan.realtime_tier == "PHASE_TWO_REALTIME"
            else 0
        )
        available = max(self.plan.protected_count, count - capture_reserve)
        return min(count, max(self.plan.protected_count, min(desired, available)))

    def _create_vehicles(self) -> list[_Vehicle]:
        result: list[_Vehicle] = []
        for kind, count in (("UAV", self.plan.uav_count), ("USV", self.plan.usv_count)):
            guard_total = self._guard_count(count)
            for index in range(count):
                code = f"{kind}-{index + 1:03d}"
                protected_index = index % self.plan.protected_count
                target = self.protected[protected_index]
                is_guard = index < guard_total
                role = "CLOSE_GUARD" if is_guard else "FORMATION_GUARD"
                group = (
                    "CONVOY-GUARD"
                    if is_guard
                    else f"ESCORT-{kind}"
                )
                angle = 2.0 * math.pi * index / max(1, count) + (0.35 if kind == "UAV" else 0.0)
                if kind == "UAV":
                    radius = (55.0 if self.plan.effective_scale >= 10 else 42.0) + (index % 3) * 6.0
                else:
                    radius = 30.0 + (index % 3) * 5.0
                x, y = self._project_to_safe_water(target.x + math.cos(angle) * radius, target.y + math.sin(angle) * radius)
                result.append(_Vehicle(
                    code, kind, x, y,
                    25.0 + (index % 4) * 2.5 if kind == "UAV" else 0.0,
                    role, group, protected_index,
                ))
        guards = sorted(
            (item for item in result if item.role == "CLOSE_GUARD"),
            key=lambda item: (
                int(item.code.rsplit("-", 1)[-1]),
                0 if item.kind == "USV" else 1,
            ),
        )
        for position, guard in enumerate(guards):
            guard.x, guard.y = self._convoy_guard_point(position, len(guards))
        for kind in ("USV", "UAV"):
            formation = sorted(
                (
                    item for item in result
                    if item.kind == kind and item.role == "FORMATION_GUARD"
                ),
                key=lambda item: item.code,
            )
            for position, member in enumerate(formation):
                member.x, member.y = self._formation_guard_point(
                    position, len(formation), kind,
                )
        return result

    def _separate_initial_response_craft(self) -> None:
        """Resolve overlapping per-target patrol rings before frame one.

        Dense single-target scenes can intersect just as easily as the former
        multi-target convoy: independently seeded UAV/USV patrol radii share
        angles and shoreline projection can compress several craft onto one
        water edge. Keep the close-guard square authoritative and globally
        project every free response craft before its first pose is published.
        """
        movable = [item for item in self.vehicles if item.role != "CLOSE_GUARD"]
        if not movable:
            return
        proposals = {
            item.code: (item.kind, (item.x, item.y, item.z))
            for item in movable
        }
        fixed = {
            item.code: (item.kind, (item.x, item.y, item.z))
            for item in self.vehicles
            if item.role == "CLOSE_GUARD"
        }
        fixed.update({
            item.code: ("ESCORT_TARGET", (item.x, item.y, 0.0))
            for item in self.protected
        })
        fixed.update({
            item.code: ("THREAT_TARGET", (item.x, item.y, 0.0))
            for item in self.threats
            if item.state != "WAITING"
        })
        resolved = self.safety.resolve_group(
            proposals,
            fixed=fixed,
            iterations=96,
        )
        for item in movable:
            safe = resolved[item.code]
            item.x, item.y, item.z = safe.x, safe.y, safe.z

    def _convoy_center(self) -> tuple[float, float]:
        return (
            sum(item.x for item in self.protected) / len(self.protected),
            sum(item.y for item in self.protected) / len(self.protected),
        )

    def _convoy_guard_point(self, position: int, count: int) -> tuple[float, float]:
        """Start in a convoy square; hold the safe flank after detection."""
        center_x, center_y = self._convoy_center()
        hazards = [t for t in self.threats
                   if t.detected_frame is not None and t.state not in {"WAITING", "ESCAPED"}]
        if hazards and hasattr(self, "_guard_sector_angle"):
            # Keep the close escort on the safe side of the convoy. Evaluate
            # the whole arc, so a safe centre cannot hide an unsafe end slot.
            radius = max(24.0, count * 6.0)
            spread = math.radians(130.0)
            def point(angle: float, slot: int) -> tuple[float, float]:
                offset = spread * (slot / max(1, count - 1) - 0.5)
                return center_x + radius * math.cos(angle + offset), center_y + radius * math.sin(angle + offset)
            if self._guard_sector_sequence != self.sequence:
                previous = self._guard_sector_angle
                candidates = []
                for sample in range(72):
                    angle = math.tau * sample / 72
                    points = [point(angle, slot) for slot in range(count)]
                    clearance = min(_length(x - t.x, y - t.y) - 42.0
                                    for x, y in points for t in hazards)
                    shore = min(min(x - self.safe_bounds[0], self.safe_bounds[1] - x,
                                    y - self.safe_bounds[2], self.safe_bounds[3] - y)
                                for x, y in points)
                    change = 0.0 if previous is None else abs((angle - previous + math.pi) % math.tau - math.pi)
                    candidates.append((min(clearance, 60.0) + min(shore - 6.0, 0.0) * 8.0 - change * 8.0, angle))
                chosen = max(candidates)[1]
                if previous is not None:
                    delta = (chosen - previous + math.pi) % math.tau - math.pi
                    chosen = previous + max(-0.012, min(0.012, delta))
                self._guard_sector_angle = chosen
                self._guard_sector_sequence = self.sequence
            return self._project_to_safe_water(*point(self._guard_sector_angle, position), 6.0)
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in self._protected_formation_offsets.values()
        )
        half_extent = protected_extent + CONVOY_GUARD_MARGIN_M
        phase = 4.0 * position / max(1, count)
        if phase < 1.0:
            offset_x, offset_y = half_extent, -half_extent + 2.0 * half_extent * phase
        elif phase < 2.0:
            edge = phase - 1.0
            offset_x, offset_y = half_extent - 2.0 * half_extent * edge, half_extent
        elif phase < 3.0:
            edge = phase - 2.0
            offset_x, offset_y = -half_extent, half_extent - 2.0 * half_extent * edge
        else:
            edge = phase - 3.0
            offset_x, offset_y = -half_extent + 2.0 * half_extent * edge, -half_extent
        return self._project_to_safe_water(center_x + offset_x, center_y + offset_y)

    def _convoy_support_point(self, position: int, count: int) -> tuple[float, float]:
        """Place released responders on a second square around the convoy."""
        center_x, center_y = self._convoy_center()
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in self._protected_formation_offsets.values()
        )
        inner_half_extent = protected_extent + CONVOY_GUARD_MARGIN_M
        # The perimeter is 8 * half_extent. Keep neighbouring 30+30 slots at
        # least 16 m apart while leaving a visible lane outside the close guard.
        half_extent = max(
            inner_half_extent + POST_MISSION_OUTER_GUARD_GAP_M,
            max(1, count) * 2.0,
        )
        current_clearances = {
            "LEFT": center_x - self.safe_bounds[0],
            "RIGHT": self.safe_bounds[1] - center_x,
            "BOTTOM": center_y - self.safe_bounds[2],
            "TOP": self.safe_bounds[3] - center_y,
        }
        blocked_edge, blocked_clearance = min(
            current_clearances.items(),
            key=lambda row: (row[1], row[0]),
        )
        if blocked_clearance < half_extent + 2.0 and count > 1:
            # At a corner, independently clamping every slot collapses several
            # reserve craft onto the same point on the second boundary. Shift
            # the support pattern as a whole along the open edge instead.
            # Close guards still retain their protected-target-relative slots.
            if blocked_edge in {"LEFT", "RIGHT"}:
                center_y = max(self.safe_bounds[2] + half_extent + 6.0,
                               min(self.safe_bounds[3] - half_extent - 6.0, center_y))
            else:
                center_x = max(self.safe_bounds[0] + half_extent + 6.0,
                               min(self.safe_bounds[1] - half_extent - 6.0, center_x))
            # The destination is intentionally close to the harbour shoreline.
            # A full outer edge there overlaps the close-guard square and is
            # physically unreachable. Use the other three sides at even spacing;
            # the shoreline closes the fourth side and both corner sentries stay.
            # SceneSafetyFilter reserves the rendered USV footprint (5.2 m)
            # inside its bounds. Keep nominal slots inside the same reachable
            # centreline instead of asking a hull to approach an impossible
            # one-metre shoreline inset.
            open_offset = max(0.0, blocked_clearance - 6.0)
            if blocked_edge == "RIGHT":
                vertices = [
                    (open_offset, half_extent),
                    (-half_extent, half_extent),
                    (-half_extent, -half_extent),
                    (open_offset, -half_extent),
                ]
            elif blocked_edge == "LEFT":
                vertices = [
                    (-open_offset, -half_extent),
                    (half_extent, -half_extent),
                    (half_extent, half_extent),
                    (-open_offset, half_extent),
                ]
            elif blocked_edge == "TOP":
                vertices = [
                    (-half_extent, open_offset),
                    (-half_extent, -half_extent),
                    (half_extent, -half_extent),
                    (half_extent, open_offset),
                ]
            else:
                vertices = [
                    (half_extent, -open_offset),
                    (half_extent, half_extent),
                    (-half_extent, half_extent),
                    (-half_extent, -open_offset),
                ]
            segment_lengths = [
                _length(
                    vertices[index + 1][0] - vertices[index][0],
                    vertices[index + 1][1] - vertices[index][1],
                )
                for index in range(3)
            ]
            perimeter_position = (
                sum(segment_lengths) * position / (count - 1)
            )
            offset_x, offset_y = vertices[-1]
            traversed = 0.0
            for index, segment_length in enumerate(segment_lengths):
                if perimeter_position > traversed + segment_length:
                    traversed += segment_length
                    continue
                ratio = (
                    0.0 if segment_length <= 1e-9
                    else (perimeter_position - traversed) / segment_length
                )
                offset_x = (
                    vertices[index][0]
                    + (vertices[index + 1][0] - vertices[index][0]) * ratio
                )
                offset_y = (
                    vertices[index][1]
                    + (vertices[index + 1][1] - vertices[index][1]) * ratio
                )
                break
            return self._project_to_safe_water(
                center_x + offset_x,
                center_y + offset_y,
                6.0,
            )
        phase = 4.0 * position / max(1, count)
        if phase < 1.0:
            offset_x, offset_y = half_extent, -half_extent + 2.0 * half_extent * phase
        elif phase < 2.0:
            edge = phase - 1.0
            offset_x, offset_y = half_extent - 2.0 * half_extent * edge, half_extent
        elif phase < 3.0:
            edge = phase - 2.0
            offset_x, offset_y = -half_extent, half_extent - 2.0 * half_extent * edge
        else:
            edge = phase - 3.0
            offset_x, offset_y = -half_extent + 2.0 * half_extent * edge, -half_extent
        return self._project_to_safe_water(
            center_x + offset_x,
            center_y + offset_y,
        )

    def _formation_guard_point(
        self,
        position: int,
        count: int,
        kind: str,
    ) -> tuple[float, float]:
        """Return a stable, scalable escort slot around the protected hull.

        Free responders used to orbit continuously as RECON craft.  That made
        the convoy look unprotected even though the craft were nearby.  Keep
        each kind on one or two fixed, interleaved rings until an observed
        hostile intent gives that craft a different job.
        """
        center_x, center_y = self._convoy_center()
        capacity = 14 if kind == "USV" else 20
        ring_count = max(1, math.ceil(max(1, count) / capacity))
        ring = position % ring_count
        slot = position // ring_count
        slots_on_ring = max(1, math.ceil((count - ring) / ring_count))
        base_radius = 32.0 if kind == "USV" else 46.0
        ring_spacing = 18.0 if kind == "USV" else 16.0
        radius = base_radius + ring * ring_spacing
        # Interleave UAV and USV bearings around the protected hull.  With the
        # default 3 + 3 fleet each kind has two formation guards: the old
        # cardinal phases put both UAVs on the flanks while the USVs alone
        # occupied the route axis.  Mirrored half-slot phases give both domains
        # a forward and a rear guard without changing radii or safety spacing.
        phase = (
            (1.0 if kind == "UAV" else -1.0) * math.pi / 4.0
            if slots_on_ring == 2
            else (math.pi / slots_on_ring if kind == "UAV" else 0.0)
        )
        if ring % 2:
            phase += math.pi / max(1, slots_on_ring)
        angle = phase + 2.0 * math.pi * slot / slots_on_ring
        return self._project_to_safe_water(
            center_x + math.cos(angle) * radius,
            center_y + math.sin(angle) * radius,
            6.0,
        )

    def _safe_convoy_support_point(
        self,
        position: int,
        count: int,
    ) -> tuple[float, float]:
        """Keep a convoy slot on its square while clearing captured rings."""
        nominal = self._convoy_support_point(position, count)
        obstacles = self._captured_return_obstacles()
        if not obstacles or all(
            _length(nominal[0] - x, nominal[1] - y) >= radius + 0.5
            for x, y, radius in obstacles
        ):
            return nominal

        candidates: list[tuple[float, float, float]] = []
        for center_x, center_y, radius in obstacles:
            for sample in range(72):
                angle = 2.0 * math.pi * sample / 72.0
                candidate = self._project_to_safe_water(
                    center_x + math.cos(angle) * (radius + 3.0),
                    center_y + math.sin(angle) * (radius + 3.0),
                    6.0,
                )
                if not all(
                    _length(candidate[0] - x, candidate[1] - y) >= other_radius + 0.5
                    for x, y, other_radius in obstacles
                ):
                    continue
                candidates.append((
                    _length(candidate[0] - nominal[0], candidate[1] - nominal[1]),
                    candidate[0],
                    candidate[1],
                ))
        if not candidates:
            return nominal
        _, x, y = min(candidates, key=lambda row: (row[0], row[1], row[2]))
        return x, y

    def _convoy_inner_escape_point(
        self,
        item: _Vehicle,
    ) -> tuple[float, float] | None:
        """Lead a recalled responder out of a moving close-guard square."""
        center_x, center_y = self._convoy_center()
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in self._protected_formation_offsets.values()
        )
        inner_half_extent = protected_extent + CONVOY_GUARD_MARGIN_M
        relative_x = item.x - center_x
        relative_y = item.y - center_y
        if max(abs(relative_x), abs(relative_y)) >= inner_half_extent - 1.0:
            return None
        escape_extent = inner_half_extent + 9.0
        if abs(relative_x) >= abs(relative_y):
            side = 1.0 if relative_x >= 0.0 else -1.0
            return self._project_to_safe_water(
                center_x + side * escape_extent,
                item.y,
                6.0,
            )
        side = 1.0 if relative_y >= 0.0 else -1.0
        return self._project_to_safe_water(
            item.x,
            center_y + side * escape_extent,
            6.0,
        )

    def _convoy_support_members(self) -> list[_Vehicle]:
        return sorted(
            (item for item in self.vehicles if item.role == "CONVOY_SUPPORT"),
            key=lambda item: (
                self._convoy_support_slot_by_code.get(item.code, 10_000),
                int(item.code.rsplit("-", 1)[-1]),
                0 if item.kind == "USV" else 1,
            ),
        )

    def _convoy_reserve_members(self) -> list[_Vehicle]:
        return sorted(
            (item for item in self.vehicles if item.role == "CAPTURE_RESERVE"),
            key=lambda item: (
                int(item.code.rsplit("-", 1)[-1]),
                0 if item.kind == "USV" else 1,
            ),
        )

    def _swap_enclosed_surplus_with_guards(self) -> None:
        """Move an enclosed reserve into the guard line without crossing it."""
        center_x, center_y = self._convoy_center()
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in self._protected_formation_offsets.values()
        )
        inner_half_extent = protected_extent + CONVOY_GUARD_MARGIN_M
        enclosed = sorted(
            (
                item for item in self.vehicles
                if item.role != "CLOSE_GUARD"
                and item.assigned_threat is None
                and max(abs(item.x - center_x), abs(item.y - center_y))
                < inner_half_extent - 1.0
            ),
            key=lambda item: (item.kind, item.code),
        )
        available_guards = [
            item for item in self.vehicles
            if item.role == "CLOSE_GUARD"
        ]
        guard_count = len(available_guards)
        for item in enclosed:
            same_kind = [
                guard for guard in available_guards
                if guard.kind == item.kind
            ]
            if not same_kind:
                continue
            released = min(
                same_kind,
                key=lambda guard: (
                    _length(
                        item.x - self._convoy_guard_point(
                            self._convoy_guard_slot_by_code[guard.code],
                            guard_count,
                        )[0],
                        item.y - self._convoy_guard_point(
                            self._convoy_guard_slot_by_code[guard.code],
                            guard_count,
                        )[1],
                    ),
                    guard.code,
                ),
            )
            slot = self._convoy_guard_slot_by_code.pop(released.code)
            self._convoy_guard_slot_by_code[item.code] = slot
            item.role = "CLOSE_GUARD"
            item.group_id = released.group_id
            item.protected_index = released.protected_index
            released.role = "CAPTURE_RESERVE"
            released.group_id = "POST-MISSION-RELEASE"
            released.final_slot_angle = None
            available_guards.remove(released)

    def _swap_enclosed_surplus_with_containment(self) -> None:
        """Hand a ring slot to a same-kind craft already trapped inside it.

        A completed surface ring is a physical barrier. Asking an unassigned
        USV inside that ring to navigate to an outer convoy slot either makes
        it cross another hull or leaves it permanently stopped by the safety
        resolver. Preserve the ring size and exact slot geometry by swapping
        roles with the nearest same-kind ring member. The released member is
        already on the outside edge and can safely return to the convoy.
        """
        free_surface = sorted(
            (
                item for item in self.vehicles
                if item.kind == "USV"
                and item.assigned_threat is None
                and item.role != "CLOSE_GUARD"
            ),
            key=lambda item: item.code,
        )
        for item in free_surface:
            enclosed_by: list[tuple[float, int, _Threat]] = []
            for threat_index, threat in enumerate(self.threats):
                if threat.state not in {"CAPTURED", "SECURED"}:
                    continue
                members = self._capture_members(threat_index)
                if not members:
                    continue
                ring_radius = max(
                    slot.radius for slot in self._capture_slots(members, threat)
                )
                distance = _length(item.x - threat.x, item.y - threat.y)
                if distance < ring_radius + 8.0:
                    enclosed_by.append((distance, threat_index, threat))
            if not enclosed_by:
                continue

            _, threat_index, threat = min(enclosed_by, key=lambda row: row[0])
            candidates = [
                member for member in self._capture_members(threat_index)
                if member.kind == item.kind
            ]
            if not candidates:
                continue
            released = min(
                candidates,
                key=lambda member: (
                    _length(item.x - member.x, item.y - member.y),
                    member.code,
                ),
            )
            cached = self._ring_slots.get(threat_index, {})
            released_slot = cached.pop(released.code, None)
            if released_slot is not None:
                cached[item.code] = released_slot

            item.assigned_threat = threat_index
            item.protected_index = threat.protected_index
            item.role = "CONTAINMENT"
            item.group_id = released.group_id
            item.final_slot_angle = released.final_slot_angle

            released.assigned_threat = None
            released.role = "CAPTURE_RESERVE"
            released.group_id = "POST-MISSION-RELEASE"
            released.final_slot_angle = None
            if threat.gap_filler_code == released.code:
                threat.gap_filler_code = item.code

    def _swap_enclosed_support_with_guards(self) -> None:
        """Keep a moving convoy from enclosing a recalled support craft."""
        center_x, center_y = self._convoy_center()
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in self._protected_formation_offsets.values()
        )
        inner_half_extent = protected_extent + CONVOY_GUARD_MARGIN_M
        enclosed: list[_Vehicle] = []
        for item in self._convoy_support_members():
            same_kind_guards = [
                guard for guard in self.vehicles
                if guard.role == "CLOSE_GUARD" and guard.kind == item.kind
            ]
            all_guards = [
                guard for guard in self.vehicles
                if guard.role == "CLOSE_GUARD"
            ]
            inside_guard_square = (
                max(abs(item.x - center_x), abs(item.y - center_y))
                < inner_half_extent - 1.0
            )
            pinned_to_guard = any(
                _length(item.x - guard.x, item.y - guard.y) < 16.0
                for guard in all_guards
            )
            if inside_guard_square or pinned_to_guard:
                enclosed.append(item)
        for item in enclosed:
            guards = [
                guard for guard in self.vehicles
                if guard.role == "CLOSE_GUARD" and guard.kind == item.kind
                and tuple(sorted((item.code, guard.code)))
                not in self._convoy_support_swap_pairs
            ]
            if not guards or item.code not in self._convoy_support_slot_by_code:
                continue
            released = min(
                guards,
                key=lambda guard: (
                    _length(item.x - guard.x, item.y - guard.y),
                    guard.code,
                ),
            )
            support_slot = self._convoy_support_slot_by_code.pop(item.code)
            guard_slot = self._convoy_guard_slot_by_code.pop(released.code)

            item.role = "CLOSE_GUARD"
            item.group_id = released.group_id
            item.protected_index = released.protected_index
            item.assigned_threat = None
            self._convoy_guard_slot_by_code[item.code] = guard_slot

            released.role = "CONVOY_SUPPORT"
            released.group_id = "CONVOY-SUPPORT"
            released.assigned_threat = None
            released.final_slot_angle = None
            self._convoy_support_slot_by_code[released.code] = support_slot
            self._convoy_support_swap_pairs.add(
                tuple(sorted((item.code, released.code)))
            )

            self._convoy_support_route_by_code.pop(item.code, None)
            self._convoy_support_route_cursor_by_code.pop(item.code, None)
            self._convoy_support_route_replan_frame_by_code.pop(item.code, None)
            self._convoy_support_goal_override_by_code.pop(item.code, None)
            self._convoy_support_goal_override_by_code.pop(released.code, None)
            support_count = len(self._convoy_support_members())
            goal = self._safe_convoy_support_point(support_slot, support_count)
            route, effective_goal = self._build_convoy_support_route(released, goal)
            self._convoy_support_route_by_code[released.code] = route
            if (
                self._post_mission_final_replan_done
                and _length(effective_goal[0] - goal[0], effective_goal[1] - goal[1]) > 0.05
            ):
                self._convoy_support_goal_override_by_code[released.code] = effective_goal
            self._convoy_support_route_cursor_by_code[released.code] = 0
            self._convoy_support_route_replan_frame_by_code[released.code] = self.sequence
            self._post_mission_initial_error_by_code.pop(item.code, None)
            self._post_mission_initial_error_by_code[released.code] = max(
                POST_MISSION_SLOT_TOLERANCE_M + 1.0,
                _length(released.x - goal[0], released.y - goal[1]),
            )

    def _redeploy_surplus_to_convoy(self) -> None:
        """Recall every released responder into an ordered outer guard square."""
        if not self.threats or not all(
            item.state in {"CAPTURED", "SECURED", "ESCAPED"}
            for item in self.threats
        ):
            return
        if self._post_mission_formation_initialized:
            if not self._post_mission_final_replan_done:
                self._swap_enclosed_support_with_guards()
            return

        self._swap_enclosed_surplus_with_containment()
        self._swap_enclosed_surplus_with_guards()
        free = [
            item for item in self.vehicles
            if item.role != "CLOSE_GUARD" and item.assigned_threat is None
        ]
        support = sorted(free, key=lambda item: (item.kind, item.code))
        for item in support:
            item.role = "CONVOY_SUPPORT"
            item.group_id = "CONVOY-SUPPORT"
            item.final_slot_angle = None
        self._assign_convoy_support_slots(support)
        self._assign_convoy_support_routes(support)

        self._post_mission_formation_initialized = True
        for item in self._post_mission_members():
            desired_x, desired_y = self._post_mission_point(item)
            self._post_mission_initial_error_by_code[item.code] = max(
                POST_MISSION_SLOT_TOLERANCE_M + 1.0,
                _length(item.x - desired_x, item.y - desired_y),
            )

    def _replan_final_convoy_support_formation(self) -> None:
        """Reassign moving support slots once the convoy reaches its gate."""
        if self._post_mission_final_replan_done:
            return
        self._convoy_support_swap_pairs.clear()
        self._swap_enclosed_support_with_guards()
        support = self._convoy_support_members()
        if support:
            self._assign_convoy_support_slots(support)
            self._assign_convoy_support_routes(support, allow_terminal_fallback=True)
        self._post_mission_initial_error_by_code = {}
        for item in self._post_mission_members():
            desired_x, desired_y = self._post_mission_point(item)
            self._post_mission_initial_error_by_code[item.code] = max(
                POST_MISSION_SLOT_TOLERANCE_M + 1.0,
                _length(item.x - desired_x, item.y - desired_y),
            )
        self._convoy_support_ready_frames = 0
        self._post_mission_best_maximum_error = math.inf
        self._post_mission_stalled_frames = 0
        self._post_mission_final_replan_done = True

    def _replan_stalled_convoy_support_formation(self) -> None:
        # If one responder is physically wedged between close guards, path
        # reassignment alone cannot move it. Exchange it with one same-kind
        # guard before rebuilding the remaining outer-slot routes.
        self._swap_enclosed_support_with_guards()
        support = self._convoy_support_members()
        if not support:
            return
        self._assign_convoy_support_slots(support)
        self._assign_convoy_support_routes(support, allow_terminal_fallback=True)
        for item in support:
            desired_x, desired_y = self._post_mission_point(item)
            self._post_mission_initial_error_by_code[item.code] = max(
                POST_MISSION_SLOT_TOLERANCE_M + 1.0,
                _length(item.x - desired_x, item.y - desired_y),
            )
        self._post_mission_best_maximum_error = math.inf
        self._post_mission_stalled_frames = 0
        self._post_mission_slot_replans += 1

    def _post_watch_members(self) -> list[_Vehicle]:
        return sorted(
            (item for item in self.vehicles if item.role == "LOCAL_OVERWATCH"),
            key=lambda item: (
                self._post_watch_threat_by_code.get(item.code, 10_000),
                item.kind,
                item.code,
            ),
        )

    def _post_watch_point(self, item: _Vehicle) -> tuple[float, float]:
        threat_index = self._post_watch_threat_by_code[item.code]
        threat = self.threats[threat_index]
        angle = self._post_watch_angle_by_code[item.code]
        radius = TARGET_SEPARATION_M + (14.0 if item.kind == "USV" else 26.0)
        return self._project_to_safe_water(
            threat.x + math.cos(angle) * radius,
            threat.y + math.sin(angle) * radius,
            12.0,
        )

    def _post_mission_members(self) -> list[_Vehicle]:
        return [*self._convoy_support_members(), *self._post_watch_members()]

    def _post_mission_point(self, item: _Vehicle) -> tuple[float, float]:
        if item.role == "CONVOY_SUPPORT":
            override = self._convoy_support_goal_override_by_code.get(item.code)
            if override is not None:
                return override
            support = self._convoy_support_members()
            return self._safe_convoy_support_point(support.index(item), len(support))
        return self._post_watch_point(item)

    def _post_mission_formation_status(self) -> dict[str, object]:
        members = self._post_mission_members()
        if not members:
            return {
                "ready": True,
                "readyCount": 0,
                "requiredCount": 0,
                "progress": 1.0,
                "maximumErrorM": 0.0,
                "blockerCode": "",
            }
        errors: list[tuple[float, _Vehicle]] = []
        progress_values: list[float] = []
        for item in members:
            desired_x, desired_y = self._post_mission_point(item)
            error = _length(item.x - desired_x, item.y - desired_y)
            errors.append((error, item))
            initial = self._post_mission_initial_error_by_code.get(
                item.code,
                max(POST_MISSION_SLOT_TOLERANCE_M + 1.0, error),
            )
            remaining_range = max(1.0, initial - POST_MISSION_SLOT_TOLERANCE_M)
            progress_values.append(max(
                0.0,
                min(1.0, 1.0 - max(0.0, error - POST_MISSION_SLOT_TOLERANCE_M) / remaining_range),
            ))
        maximum_error, blocker = max(errors, key=lambda row: (row[0], row[1].code))
        ready_count = sum(
            error <= POST_MISSION_SLOT_TOLERANCE_M
            for error, _ in errors
        )
        return {
            "ready": ready_count == len(members),
            "readyCount": ready_count,
            "requiredCount": len(members),
            "progress": sum(progress_values) / len(progress_values),
            "maximumErrorM": maximum_error,
            "blockerCode": "" if ready_count == len(members) else blocker.code,
        }

    def _assign_convoy_support_slots(self, members: Sequence[_Vehicle]) -> None:
        """Bind recalled craft to nearby rear slots without crossing traffic.

        Code-order assignment sent a late responder from the convoy's front to
        the farthest rear slot while nearer craft crossed in the opposite
        direction.  That produced a long collision-avoidance oscillation at
        maximum speed.  Use an exact small-fleet assignment and a deterministic
        nearest-pair fallback for capacity-mode fleets.
        """
        ordered = sorted(members, key=lambda item: item.code)
        count = len(ordered)
        if not count:
            self._convoy_support_slot_by_code = {}
            return
        points = [self._safe_convoy_support_point(index, count) for index in range(count)]
        costs = [
            [
                _length(item.x - point[0], item.y - point[1])
                for point in points
            ]
            for item in ordered
        ]
        if count <= 12:
            @lru_cache(maxsize=None)
            def solve(member_index: int, used_mask: int) -> tuple[float, tuple[int, ...]]:
                if member_index >= count:
                    return 0.0, ()
                best_cost = math.inf
                best_slots: tuple[int, ...] = ()
                for slot_index in range(count):
                    if used_mask & (1 << slot_index):
                        continue
                    remaining_cost, remaining_slots = solve(
                        member_index + 1,
                        used_mask | (1 << slot_index),
                    )
                    candidate_cost = costs[member_index][slot_index] + remaining_cost
                    if candidate_cost < best_cost - 1e-9:
                        best_cost = candidate_cost
                        best_slots = (slot_index, *remaining_slots)
                return best_cost, best_slots

            _, slots = solve(0, 0)
            self._convoy_support_slot_by_code = {
                item.code: slots[index]
                for index, item in enumerate(ordered)
            }
            return

        # Hungarian assignment keeps capacity-mode fleets globally optimal.
        # The old nearest-pair greedy pass could leave the final USV with the
        # diagonally opposite slot, adding more than 100 m to its return leg.
        potentials_left = [0.0] * (count + 1)
        potentials_right = [0.0] * (count + 1)
        matched_left = [0] * (count + 1)
        predecessor = [0] * (count + 1)
        for left in range(1, count + 1):
            matched_left[0] = left
            minimum = [math.inf] * (count + 1)
            used = [False] * (count + 1)
            right = 0
            while True:
                used[right] = True
                current_left = matched_left[right]
                delta = math.inf
                next_right = 0
                for candidate_right in range(1, count + 1):
                    if used[candidate_right]:
                        continue
                    reduced = (
                        costs[current_left - 1][candidate_right - 1]
                        - potentials_left[current_left]
                        - potentials_right[candidate_right]
                    )
                    if reduced < minimum[candidate_right] - 1e-9:
                        minimum[candidate_right] = reduced
                        predecessor[candidate_right] = right
                    if minimum[candidate_right] < delta - 1e-9:
                        delta = minimum[candidate_right]
                        next_right = candidate_right
                for candidate_right in range(count + 1):
                    if used[candidate_right]:
                        potentials_left[matched_left[candidate_right]] += delta
                        potentials_right[candidate_right] -= delta
                    else:
                        minimum[candidate_right] -= delta
                right = next_right
                if matched_left[right] == 0:
                    break
            while True:
                previous_right = predecessor[right]
                matched_left[right] = matched_left[previous_right]
                right = previous_right
                if right == 0:
                    break
        slots_by_member = [0] * count
        for right in range(1, count + 1):
            slots_by_member[matched_left[right] - 1] = right - 1
        self._convoy_support_slot_by_code = {
            item.code: slots_by_member[index]
            for index, item in enumerate(ordered)
        }

    @staticmethod
    def _segment_distance_to_point(
        start: tuple[float, float],
        end: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        dx, dy = end[0] - start[0], end[1] - start[1]
        denominator = dx * dx + dy * dy
        if denominator <= 1e-9:
            return _length(start[0] - point[0], start[1] - point[1])
        ratio = max(0.0, min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / denominator,
        ))
        closest_x = start[0] + dx * ratio
        closest_y = start[1] + dy * ratio
        return _length(closest_x - point[0], closest_y - point[1])

    def _captured_return_obstacles(self) -> list[tuple[float, float, float]]:
        return [
            (threat.x, threat.y, POST_MISSION_RING_AVOIDANCE_M)
            for threat in self.threats
            if threat.state in {"CAPTURED", "SECURED"}
        ]

    def _convoy_return_obstacles(self) -> list[tuple[float, float, float]]:
        """Return rings plus the moving inner convoy as route obstacles."""
        center_x, center_y = self._convoy_center()
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in self._protected_formation_offsets.values()
        )
        inner_radius = protected_extent + CONVOY_GUARD_MARGIN_M + 9.0
        return [
            *self._captured_return_obstacles(),
            (center_x, center_y, inner_radius),
        ]

    def _return_segment_is_clear(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        obstacles: Sequence[tuple[float, float, float]],
    ) -> bool:
        return all(
            self._segment_distance_to_point(start, end, (x, y)) >= radius - 0.25
            for x, y, radius in obstacles
        )

    def _build_convoy_support_route(
        self,
        item: _Vehicle,
        goal: tuple[float, float],
    ) -> tuple[list[tuple[float, float]], tuple[float, float]]:
        """Find waypoints and a reachable terminal point around fixed obstacles.

        A shoreline can join overlapping convoy/ring avoidance discs into a
        closed pocket. In that case the nominal outer-square slot is physically
        unreachable even though the point itself lies just outside both discs.
        Return the closest reachable visibility node as a deformed-but-safe
        terminal slot instead of repeatedly sending one craft into the pocket.
        """
        obstacles = self._convoy_return_obstacles()
        if not obstacles:
            return [], goal

        start = (item.x, item.y)
        escape_prefix: list[tuple[float, float]] = []
        # A responder can already be inside the conservative planning buffer
        # while still outside the occupied 34 m ring. Move radially outward
        # first, so no return route cuts through a completed containment ring.
        for _ in range(len(obstacles) + 1):
            containing = [
                obstacle for obstacle in obstacles
                if _length(start[0] - obstacle[0], start[1] - obstacle[1])
                < obstacle[2] + 0.5
            ]
            if not containing:
                break
            center_x, center_y, radius = max(
                containing,
                key=lambda obstacle: obstacle[2] - _length(
                    start[0] - obstacle[0], start[1] - obstacle[1],
                ),
            )
            start_radius = _length(start[0] - center_x, start[1] - center_y)
            candidates: list[tuple[float, tuple[float, float]]] = []
            for sample in range(40):
                angle = 2.0 * math.pi * sample / 40.0
                escaped = self._project_to_safe_water(
                    center_x + math.cos(angle) * (radius + 8.0),
                    center_y + math.sin(angle) * (radius + 8.0),
                    6.0,
                )
                if _length(escaped[0] - center_x, escaped[1] - center_y) < radius + 0.5:
                    continue
                if self._segment_distance_to_point(
                    start,
                    escaped,
                    (center_x, center_y),
                ) < min(start_radius - 0.25, TARGET_SEPARATION_M + 6.0):
                    continue
                if any(
                    other != (center_x, center_y, radius)
                    and self._segment_distance_to_point(
                        start,
                        escaped,
                        (other[0], other[1]),
                    ) < min(
                        other[2] - 0.25,
                        max(
                            TARGET_SEPARATION_M + 6.0,
                            _length(start[0] - other[0], start[1] - other[1]) - 0.25,
                        ),
                    )
                    for other in containing
                ):
                    continue
                candidates.append((
                    _length(escaped[0] - start[0], escaped[1] - start[1])
                    + 0.18 * _length(escaped[0] - goal[0], escaped[1] - goal[1]),
                    escaped,
                ))
            if not candidates:
                break
            _, escaped = min(candidates, key=lambda row: (row[0], row[1]))
            escape_prefix.append(escaped)
            start = escaped

        if self._return_segment_is_clear(start, goal, obstacles):
            return escape_prefix, goal

        nodes: list[tuple[float, float]] = [start, goal]
        samples_per_ring = 20
        for center_x, center_y, radius in obstacles:
            sample_radius = radius + 8.0
            for sample in range(samples_per_ring):
                angle = 2.0 * math.pi * sample / samples_per_ring
                candidate = self._project_to_safe_water(
                    center_x + math.cos(angle) * sample_radius,
                    center_y + math.sin(angle) * sample_radius,
                    6.0,
                )
                if all(
                    _length(candidate[0] - x, candidate[1] - y) >= other_radius + 0.5
                    for x, y, other_radius in obstacles
                ):
                    nodes.append(candidate)

        adjacency: list[list[tuple[float, int]]] = [[] for _ in nodes]
        for left in range(len(nodes)):
            for right in range(left + 1, len(nodes)):
                if not self._return_segment_is_clear(nodes[left], nodes[right], obstacles):
                    continue
                distance = _length(
                    nodes[left][0] - nodes[right][0],
                    nodes[left][1] - nodes[right][1],
                )
                adjacency[left].append((distance, right))
                adjacency[right].append((distance, left))

        distances = [math.inf] * len(nodes)
        previous = [-1] * len(nodes)
        distances[0] = 0.0
        queue: list[tuple[float, int]] = [(0.0, 0)]
        while queue:
            distance, index = heappop(queue)
            if distance > distances[index] + 1e-9:
                continue
            if index == 1:
                break
            for edge, neighbour in adjacency[index]:
                candidate = distance + edge
                if candidate >= distances[neighbour] - 1e-9:
                    continue
                distances[neighbour] = candidate
                previous[neighbour] = index
                heappush(queue, (candidate, neighbour))

        target_index = 1
        effective_goal = goal
        if not math.isfinite(distances[target_index]):
            reachable = [
                index for index in range(2, len(nodes))
                if math.isfinite(distances[index])
            ]
            if not reachable:
                # The escape point is already outside every conservative
                # obstacle and is the safest attainable holding station.
                return escape_prefix, start
            target_index = min(
                reachable,
                key=lambda index: (
                    _length(nodes[index][0] - goal[0], nodes[index][1] - goal[1]),
                    distances[index],
                    nodes[index][0],
                    nodes[index][1],
                ),
            )
            effective_goal = nodes[target_index]
        indices: list[int] = []
        cursor = target_index
        while cursor >= 0:
            indices.append(cursor)
            cursor = previous[cursor]
        indices.reverse()
        return (
            [*escape_prefix, *(nodes[index] for index in indices[1:-1])],
            effective_goal,
        )

    def _assign_convoy_support_routes(
        self,
        members: Sequence[_Vehicle],
        *,
        allow_terminal_fallback: bool = False,
    ) -> None:
        self._convoy_support_route_by_code = {}
        self._convoy_support_route_cursor_by_code = {}
        self._convoy_support_route_replan_frame_by_code = {}
        self._convoy_support_goal_override_by_code = {}
        count = len(members)
        for item in members:
            slot = self._convoy_support_slot_by_code[item.code]
            goal = self._safe_convoy_support_point(slot, count)
            route, effective_goal = self._build_convoy_support_route(item, goal)
            self._convoy_support_route_by_code[item.code] = route
            if (
                allow_terminal_fallback
                and _length(effective_goal[0] - goal[0], effective_goal[1] - goal[1]) > 0.05
            ):
                self._convoy_support_goal_override_by_code[item.code] = effective_goal
            self._convoy_support_route_cursor_by_code[item.code] = 0
            self._convoy_support_route_replan_frame_by_code[item.code] = self.sequence

    def _convoy_support_route_point(
        self,
        item: _Vehicle,
    ) -> tuple[float, float] | None:
        route = self._convoy_support_route_by_code.get(item.code, [])
        cursor = self._convoy_support_route_cursor_by_code.get(item.code, 0)
        while cursor < len(route) and _length(
            item.x - route[cursor][0],
            item.y - route[cursor][1],
        ) <= POST_MISSION_ROUTE_ARRIVAL_M:
            cursor += 1
        self._convoy_support_route_cursor_by_code[item.code] = cursor
        if cursor < len(route):
            return route[cursor]

        members = self._convoy_support_members()
        if item not in members:
            return None
        if item.code in self._convoy_support_goal_override_by_code:
            # A terminal fallback is a deliberately deformed outer slot chosen
            # from the reachable visibility component. Replanning it back to
            # the sealed nominal pocket would recreate the same oscillation.
            return None
        slot = self._convoy_support_slot_by_code[item.code]
        goal = self._safe_convoy_support_point(slot, len(members))
        obstacles = self._convoy_return_obstacles()
        if not obstacles or self._return_segment_is_clear(
            (item.x, item.y),
            goal,
            obstacles,
        ):
            return None

        last_replan = self._convoy_support_route_replan_frame_by_code.get(
            item.code,
            -10_000,
        )
        if self.sequence - last_replan < 30:
            return None
        route, effective_goal = self._build_convoy_support_route(item, goal)
        if (
            self._post_mission_final_replan_done
            and _length(effective_goal[0] - goal[0], effective_goal[1] - goal[1]) > 0.05
        ):
            self._convoy_support_goal_override_by_code[item.code] = effective_goal
        self._convoy_support_route_by_code[item.code] = route
        self._convoy_support_route_cursor_by_code[item.code] = 0
        self._convoy_support_route_replan_frame_by_code[item.code] = self.sequence
        return None if not route else route[0]

    def _convoy_support_ready(self, tolerance_m: float = 7.5) -> bool:
        members = self._convoy_support_members()
        return all(
            _length(
                item.x - self._safe_convoy_support_point(position, len(members))[0],
                item.y - self._safe_convoy_support_point(position, len(members))[1],
            ) <= tolerance_m
            for position, item in enumerate(members)
        )

    def _project_to_safe_water(self, x: float, y: float, inset: float = 0.0) -> tuple[float, float]:
        return (
            max(self.safe_bounds[0] + inset, min(self.safe_bounds[1] - inset, x)),
            max(self.safe_bounds[2] + inset, min(self.safe_bounds[3] - inset, y)),
        )

    def _distance_to_protected(self, threat: _Threat) -> float:
        target = self.protected[threat.protected_index]
        return _length(threat.x - target.x, threat.y - target.y)

    def _escort_departure_distance(self) -> float:
        return sum(
            _length(
                item.x - self._protected_start_positions[item.code][0],
                item.y - self._protected_start_positions[item.code][1],
            )
            for item in self.protected
        ) / max(1, len(self.protected))

    def _emit_tactical_event(
        self,
        event_type: str,
        threat: _Threat,
        title: str,
        message: str,
    ) -> None:
        event_id = f"{self.run_id}:{event_type}:{threat.code}"
        if any(item["eventId"] == event_id for item in self._tactical_events):
            return
        self._tactical_events.append({
            "eventId": event_id,
            "type": event_type,
            "threatCode": threat.code,
            "title": title,
            "message": message,
            "confidence": round(threat.intent_confidence, 3),
            "sequence": self.sequence,
        })

    def _observe_guard_response(self) -> None:
        self.defense.observe()

    def _cover_threat(self, item: _Vehicle) -> _Threat | None:
        screen = self.defense.screen_for(item)
        return self.threats[screen.index] if screen and screen.phase != "RELEASED" else None

    def _screen_retreat_direction(self, threat: _Threat) -> tuple[float, float]:
        target = self.protected[threat.protected_index]
        return _unit(target.x-threat.x, target.y-threat.y)

    def _observe_attack_intent(
        self,
        threat: _Threat,
        target: _Protected,
        distance: float,
    ) -> bool:
        """Confirm an attack from motion evidence, never from distance alone."""
        to_target_x, to_target_y = _unit(target.x - threat.x, target.y - threat.y)
        relative_vx = threat.vx - target.vx
        relative_vy = threat.vy - target.vy
        closing_speed = relative_vx * to_target_x + relative_vy * to_target_y
        speed = max(1e-6, _length(threat.vx, threat.vy))
        hostile_toward_speed = (
            threat.vx * to_target_x + threat.vy * to_target_y
        )
        heading_alignment = (
            hostile_toward_speed
        ) / speed
        horizon = 8.0
        predicted_distance = _length(
            (threat.x + threat.vx * horizon) - (target.x + target.vx * horizon),
            (threat.y + threat.vy * horizon) - (target.y + target.vy * horizon),
        )
        # Intent is a course decision, not merely a successful closure.  A
        # protected target can be moving away slightly faster, but a hostile
        # that continuously points at it is still pursuing and must not remain
        # "unclassified" for minutes. Relative closure strengthens the same
        # observation; it is not required to establish the attack course.
        course_attack = (
            heading_alignment >= 0.62
            and hostile_toward_speed >= 0.4
        )
        relative_attack = (
            closing_speed >= 0.18
            and predicted_distance <= distance - 2.0
        )
        evidence = (
            distance <= self.defense.recognition_radius + 4.0
            and self.defense.recognition_ready()
            and course_attack
            and (relative_attack or heading_alignment >= 0.78)
        )
        threat.intent_hold_frames = (
            min(ATTACK_INTENT_HOLD_FRAMES, threat.intent_hold_frames + 1)
            if evidence
            else max(0, threat.intent_hold_frames - 2)
        )
        evidence_ratio = threat.intent_hold_frames / ATTACK_INTENT_HOLD_FRAMES
        threat.intent_confidence = max(
            0.15,
            min(
                0.98,
                0.18 + evidence_ratio * 0.60
                + max(0.0, heading_alignment) * 0.12
                + min(0.08, max(0.0, closing_speed) * 0.04),
            ),
        )
        convoy_has_departed = self.defense.recognition_ready()
        # A default incident must still show a real escort leg before the
        # decision transition. Only an imminent safety-radius breach bypasses
        # that gate.
        urgent = distance <= BREACH_DISTANCE_M + 42.0
        return (
            threat.intent_hold_frames >= ATTACK_INTENT_HOLD_FRAMES
            and (convoy_has_departed or urgent)
        )

    def _threat_risk(self, threat: _Threat) -> tuple[float, float, float]:
        """Return risk, predicted impact time and positive closing speed."""
        target = self.protected[threat.protected_index]
        dx, dy = target.x - threat.x, target.y - threat.y
        distance = max(1e-6, _length(dx, dy))
        ux, uy = dx / distance, dy / distance
        closing = max(
            0.0,
            (threat.vx - target.vx) * ux + (threat.vy - target.vy) * uy,
        )
        tti = distance / max(0.12, closing)
        risk = (
            max(0.0, THREAT_DETECTION_M - distance) * 0.75
            + max(0.0, 55.0 - tti) * 2.4
            + closing * 18.0
            + (30.0 if distance < URGENT_DISTANCE_M else 0.0)
        )
        return risk, tti, closing

    @staticmethod
    def _pursuit_distance(threat: _Threat) -> float:
        return max(0.0, threat.travelled_distance - threat.capture_start_travel_distance)

    def _choose_escape_direction(self, threat: _Threat) -> tuple[float, float]:
        """Choose one long open-water corridor and keep it for the chase."""
        target = self.protected[threat.protected_index]
        away_x, away_y = _unit(threat.x - target.x, threat.y - target.y)
        inset = 30.0
        left, right = self.safe_bounds[0] + inset, self.safe_bounds[1] - inset
        bottom, top = self.safe_bounds[2] + inset, self.safe_bounds[3] - inset

        def clearance(dx: float, dy: float) -> float:
            limits: list[float] = []
            if dx > 1e-6:
                limits.append((right - threat.x) / dx)
            elif dx < -1e-6:
                limits.append((left - threat.x) / dx)
            if dy > 1e-6:
                limits.append((top - threat.y) / dy)
            elif dy < -1e-6:
                limits.append((bottom - threat.y) / dy)
            return max(0.0, min((value for value in limits if value >= 0.0), default=0.0))

        candidates = [
            (math.cos(2.0 * math.pi * sample / 24.0), math.sin(2.0 * math.pi * sample / 24.0))
            for sample in range(24)
        ]
        # Prefer a genuine away course while open water exists. At a corner,
        # however, every direction in the away half-plane can point straight
        # through the coastline. Keeping that impossible heading leaves the
        # hostile clamped at the boundary forever and prevents the pursuit
        # distance from progressing. In that case allow a bounded lateral
        # turn (at most about 117 degrees from the away vector) so it can run
        # along the coast without making a direct charge at the convoy.
        minimum_progress_m = 12.0
        aligned = [
            direction for direction in candidates
            if clearance(*direction) >= minimum_progress_m
            and direction[0] * away_x + direction[1] * away_y >= 0.25
        ]
        if not aligned:
            aligned = [
                direction for direction in candidates
                if clearance(*direction) >= minimum_progress_m
                and direction[0] * away_x + direction[1] * away_y >= -0.45
            ]
        if not aligned:
            aligned = [
                direction for direction in candidates
                if clearance(*direction) >= 12.0
            ]
        return max(
            aligned or candidates,
            key=lambda direction: (
                clearance(*direction)
                + 18.0 * (direction[0] * away_x + direction[1] * away_y)
            ),
        )

    def _safe_escape_velocity(self, threat: _Threat, heading: float, speed: float,
                              previous_heading: float) -> tuple[float, float]:
        """Check the final velocity after every steering influence is applied.

        Reject entry into the protected corridor and approaching hulls, not
        every lateral manoeuvre with a negative radial component. The latter
        traps two enemies together against a shore and makes rings overlap.
        """
        target = self.protected[threat.protected_index]
        away_x, away_y = _unit(threat.x - target.x, threat.y - target.y)
        defenders = [v for v in self.vehicles if v.kind == "USV"
                     and _length(v.x - threat.x, v.y - threat.y) < 65.0]
        preferred = heading
        turn_limit = math.radians(7.0)
        shore_now = min(threat.x - self.safe_bounds[0], self.safe_bounds[1] - threat.x,
                        threat.y - self.safe_bounds[2], self.safe_bounds[3] - threat.y)
        inset = min(30.0 if threat.capture_stage == 0 else 38.0, shore_now)
        protected_distance = self._distance_to_protected(threat)
        protected_clearance = min(60.0, protected_distance)
        others = [t for t in self.threats if t is not threat and t.state not in {"WAITING", "ESCAPED"}]
        candidates = []
        angles = [heading, previous_heading]
        angles.extend(previous_heading + turn_limit * sample / 4.0 for sample in range(-4, 5))
        reachable_count = len(angles)
        # Also choose a safe steering destination if the current turn cannot
        # clear the obstruction. Translation still obeys the turn-rate bound.
        angles.extend(math.tau * sample / 48 for sample in range(48))
        for angle_index, angle in enumerate(angles):
            if angle_index == reachable_count and candidates:
                break
            dx, dy = math.cos(angle), math.sin(angle)
            for candidate_speed in (speed, max(0.0, speed - 0.10)):
                valid = True
                clearance = 80.0
                for seconds in (0.1, 1.0, 3.0):
                    x, y = threat.x + dx * candidate_speed * seconds, threat.y + dy * candidate_speed * seconds
                    projected = self._project_to_safe_water(x, y, inset)
                    if _length(x - projected[0], y - projected[1]) > 0.01:
                        valid = False
                        break
                    if _length(x - target.x, y - target.y) < protected_clearance - 0.001:
                        valid = False
                    for other in others:
                        initial = _length(threat.x - other.x, threat.y - other.y)
                        required = 76.0 if threat.capture_stage >= 1 or other.capture_stage >= 1 else 48.0
                        if _length(x - other.x, y - other.y) < min(required, initial) - 0.001:
                            valid = False
                    for defender in defenders:
                        vx, vy = defender.vx, defender.vy
                        d = _length(x - defender.x - vx * seconds, y - defender.y - vy * seconds)
                        initial = _length(threat.x - defender.x, threat.y - defender.y)
                        if d < min(14.5, initial - 0.1):
                            valid = False
                        clearance = min(clearance, d)
                if valid:
                    continuity = math.cos(angle - preferred)
                    turn = abs((angle - previous_heading + math.pi) % math.tau - math.pi)
                    reachable = 100.0 if turn <= turn_limit + 1e-6 else 0.0
                    outward = dx * away_x + dy * away_y
                    candidates.append((reachable + continuity * 8.0 + outward * 6.0 + min(clearance, 28.0) * 0.2 + candidate_speed * 1.5 - turn * 2.0,
                                       angle, candidate_speed))
        if candidates:
            _, angle, chosen_speed = max(candidates)
            delta = (angle - previous_heading + math.pi) % math.tau - math.pi
            if abs(delta) <= turn_limit + 1e-6:
                if chosen_speed < speed - 0.001:
                    threat.slowdown_reason = "ESCAPE_CORRIDOR_BLOCKED"
                return angle, chosen_speed
            threat.slowdown_reason = "ESCAPE_CORRIDOR_BLOCKED"
            return previous_heading + max(-turn_limit, min(turn_limit, delta)), 0.0
        # No valid translation. Turning at zero speed is explicit physical
        # evidence of blocked escape, not a hidden capture deadline.
        escape = self._choose_escape_direction(threat)
        angle = math.atan2(escape[1], escape[0])
        if math.cos(angle) * away_x + math.sin(angle) * away_y < 0.0:
            angle = math.atan2(away_y, away_x)
        delta = (angle - previous_heading + math.pi) % math.tau - math.pi
        threat.slowdown_reason = "ESCAPE_CORRIDOR_BLOCKED"
        return previous_heading + max(-turn_limit, min(turn_limit, delta)), 0.0

    def _choose_containment_clearance_direction(
        self,
        threat: _Threat,
        threat_index: int,
        fallback: tuple[float, float],
    ) -> tuple[float, float] | None:
        """Choose one course that clears convoy, other rings and shoreline."""
        ring_nearly_closed = (
            threat.capture_stage >= 2
            and threat.capture_arrival_ratio >= 0.875
            and threat.capture_max_gap_deg <= 75.0
        )
        # Once the real ring is geometrically closed, the hostile is the held
        # object and the protected convoy owns the separation manoeuvre. If
        # both sides keep fleeing each other at similar speed they can shadow
        # forever along a coast. Other containment rings remain constraints so
        # two circles cannot overlap.
        constraints: list[tuple[float, float, float]] = [
            (
                item.x,
                item.y,
                TARGET_SEPARATION_M
                if ring_nearly_closed
                else CONTAINMENT_STANDOFF_M,
            )
            for item in self.protected
        ]
        constraints.extend(
            (other.x, other.y, 76.0)
            for other_index, other in enumerate(self.threats)
            if other_index != threat_index
            and other.state not in {"WAITING", "ESCAPED"}
            and (
                other.state in {"CAPTURED", "SECURED"}
                or other.capture_stage >= 1
            )
        )
        if not constraints or all(
            _length(threat.x - x, threat.y - y) >= required
            for x, y, required in constraints
        ):
            return None

        fallback_x, fallback_y = _unit(*fallback)
        probe_distance = 22.0
        candidates: list[tuple[float, float, float]] = []
        for sample in range(48):
            angle = 2.0 * math.pi * sample / 48.0
            direction_x, direction_y = math.cos(angle), math.sin(angle)
            candidate_x, candidate_y = self._project_to_safe_water(
                threat.x + direction_x * probe_distance,
                threat.y + direction_y * probe_distance,
                38.0,
            )
            displacement = _length(candidate_x - threat.x, candidate_y - threat.y)
            if displacement < probe_distance * 0.35:
                continue
            margins = [
                _length(candidate_x - x, candidate_y - y) - required
                for x, y, required in constraints
            ]
            minimum_margin = min(margins)
            mean_margin = sum(margins) / len(margins)
            shore_clearance = min(
                candidate_x - self.safe_bounds[0],
                self.safe_bounds[1] - candidate_x,
                candidate_y - self.safe_bounds[2],
                self.safe_bounds[3] - candidate_y,
            )
            continuity = direction_x * fallback_x + direction_y * fallback_y
            score = (
                minimum_margin * 4.0
                + mean_margin * 0.55
                + min(24.0, shore_clearance) * 0.45
                + continuity * 1.2
            )
            candidates.append((score, direction_x, direction_y))
        if not candidates:
            return fallback_x, fallback_y
        _, direction_x, direction_y = max(candidates, key=lambda item: item[0])
        return direction_x, direction_y

    def _active_threats_for(self, protected_index: int) -> list[tuple[int, _Threat]]:
        return [(i, item) for i, item in enumerate(self.threats) if item.protected_index == protected_index and item.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}]

    def _retarget_attackers(self) -> None:
        """Let free attackers choose a credible convoy target with hysteresis.

        The previous modulo binding meant an enemy could continue toward a
        distant protected vessel while another one passed beside it.  We score
        predicted arrival time, close-guard density and shoreline clearance,
        then switch only when the new target is materially better.  Forced
        capture keeps its current incident identity so containment teams do
        not churn between targets.
        """
        if len(self.protected) < 2:
            return

        def score(threat: _Threat, protected_index: int) -> float:
            target = self.protected[protected_index]
            future_x = target.x + target.vx * 6.0
            future_y = target.y + target.vy * 6.0
            distance = _length(threat.x - future_x, threat.y - future_y)
            guards = sum(
                item.protected_index == protected_index
                and item.role in {"CLOSE_GUARD", "BLOCKER", "CONFRONT"}
                for item in self.vehicles
            )
            shore_clearance = min(
                target.x - self.safe_bounds[0], self.safe_bounds[1] - target.x,
                target.y - self.safe_bounds[2], self.safe_bounds[3] - target.y,
            )
            # Lower is more attractive. Sparse protection is attractive, but
            # distance remains dominant so enemies visibly pursue rather than
            # oscillating between convoys.
            return distance + guards * 7.5 - min(45.0, shore_clearance) * 0.12

        for threat in self.threats:
            if (
                threat.forced
                or threat.state in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}
                or self.sequence - threat.last_retarget_frame < 25
            ):
                continue
            current_score = score(threat, threat.protected_index)
            candidate = min(range(len(self.protected)), key=lambda index: score(threat, index))
            candidate_score = score(threat, candidate)
            if candidate != threat.protected_index and candidate_score <= current_score * 0.84:
                threat.protected_index = candidate
                threat.detected_frame = None
                threat.intercept_hold_frames = 0
                threat.intent = "RETARGETING"
            threat.last_retarget_frame = self.sequence

    def activate_capture(self, threat_code: str | None = None) -> str:
        candidates = [item for item in self.threats if item.state not in {"CAPTURED", "SECURED", "ESCAPED"}]
        if threat_code:
            candidates = [item for item in candidates if item.code == threat_code.strip().upper()]
        if not candidates:
            raise ValueError("No available moving threat can be captured")
        # With no explicit code the UI command means "actively capture every
        # current incident", not only the nearest target. This is essential
        # once the scenario contains two or more simultaneous attackers.
        selected = candidates if threat_code is None else candidates[:1]
        self._start_capture_for(selected, "MANUAL_OVERRIDE")
        return min(selected, key=self._distance_to_protected).code

    def _start_capture_for(self, selected: Sequence[_Threat], reason: str) -> None:
        pending = [
            threat for threat in selected
            if not threat.forced and threat.state not in {"CAPTURED", "SECURED", "ESCAPED"}
        ]
        if not pending:
            return
        for threat in pending:
            if threat.state == "WAITING":
                threat.state = "APPROACHING"
            threat.detected_frame = threat.detected_frame or self.sequence
            threat.forced, threat.state = True, "INTERCEPTING"
            threat.intent = "ESCAPE_PENDING"
            threat.intent_confidence = max(0.45, threat.intent_confidence)
            threat.escape_intent_hold_frames = 0
            threat.escape_intent_confirmed = False
            threat.auto_capture_reason = reason
            threat.capture_phase = math.radians(threat.heading) + math.pi
            threat.capture_started_frame = self.sequence
            threat.capture_stage = 0
            threat.capture_hold = 0
            threat.intercept_stage_frames = 0
            # Escort incidents enter interception from an observed attack.
            # "ESCAPE" is hostile behaviour, not an escort mission phase.
            threat.mission_stage = "PURSUIT"
            threat.containment_stage_latched = False
            threat.containment_soft_failure_frames = 0
            threat.capture_start_travel_distance = threat.travelled_distance
            threat.required_pursuit_distance = (
                100.0 if self.plan.effective_scale < 10
                else 120.0 if self.plan.effective_scale < 20
                else 140.0
            )
            threat.escape_dir_x, threat.escape_dir_y = self._choose_escape_direction(threat)
        self.capture_started_frame = (
            self.sequence if self.capture_started_frame is None
            else min(self.capture_started_frame, self.sequence)
        )
        active_forced = [
            threat for threat in self.threats
            if threat.forced and threat.state not in {"CAPTURED", "SECURED", "ESCAPED"}
        ]
        # Keep the original mixed close-guard pair beside the protected ship.
        # The minimum 3+3 fleet still provides a structurally complete 2 UAV +
        # 2 USV containment team, so stripping the last guards during pursuit
        # only makes an escort mission look and behave like pure capture.
        self._rebalance_capture_groups(active_forced)

    def _start_parallel_response(self) -> None:
        """Enable parallel incident handling only after an intent is confirmed."""
        if self._parallel_response_started or not self._parallel_response_enabled:
            return
        simultaneous = [
            threat for threat in self.threats
            if threat.detected_frame is not None
            and threat.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}
        ]
        if not simultaneous:
            return
        # Guard/block roles are assigned by _synchronize_guard_roles. Ring
        # teams are allocated only when interception truly transitions to
        # pursuit, so the generated convoy does not instantly collapse into a
        # pre-made circle at mission start.
        self._parallel_response_started = True

    def place_threat(self, x: float, y: float) -> None:
        threat = next((item for item in self.threats if item.state == "WAITING"), None) or min(self.threats, key=self._distance_to_protected)
        threat.x, threat.y = self._project_to_safe_water(float(x), float(y))
        threat.state, threat.forced, threat.capture_hold = "APPROACHING", False, 0

    def _assign_capture_group(self, threat: _Threat) -> None:
        active = [item for item in self.threats if item.forced and item.state not in {"CAPTURED", "SECURED", "ESCAPED"}]
        self._rebalance_capture_groups(active or [threat])

    def _rebalance_capture_groups(self, threats: Sequence[_Threat]) -> None:
        """Build stable, mixed, per-threat groups while preserving guards."""
        ordered_threats = sorted(
            {self.threats.index(item): item for item in threats}.items(),
            key=lambda pair: pair[0],
        )
        if not ordered_threats:
            return
        # A completed ring persists, but it must not permanently monopolise
        # the whole fleet. Keep the smallest robust mixed ring (2 UAV + 2 USV)
        # around every captured threat and release surplus craft for later
        # incidents. Without this hand-off THREAT-003/004 had no blocker or
        # capture members at 20+ and remained ATTACKING/INTERCEPTING forever.
        self._release_surplus_containment()
        selected_indices = {index for index, _ in ordered_threats}
        reserved_incident_groups = {
            group
            for index, threat in enumerate(self.threats)
            if index not in selected_indices
            and threat.detected_frame is not None
            and threat.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}
            for group in (f"BLOCK-{index + 1:03d}", f"WATCH-{index + 1:03d}")
        }
        for item in self.vehicles:
            if (
                item.role != "CLOSE_GUARD"
                and item.group_id not in reserved_incident_groups
                and (item.assigned_threat in selected_indices or item.assigned_threat is None)
            ):
                item.assigned_threat = None
                item.role = "RECON"
                item.group_id = f"RECON-{item.protected_index + 1:03d}"
        # Assign each kind independently so every group is mixed. Greedy
        # nearest selection is performed round by round and assignments then
        # remain fixed for the whole capture, eliminating cross-target churn.
        for kind in ("USV", "UAV"):
            available = [
                item for item in self.vehicles
                if item.kind == kind
                and item.code not in self._unavailable_codes
                and item.role not in {"CLOSE_GUARD", "BLOCKER", "CONFRONT"}
                and item.assigned_threat is None
            ]
            # Four craft of each kind per target is the preferred scalable
            # ring. Only fall back toward the structural 2-per-kind minimum
            # when the configured fleet cannot supply both that ring and a
            # response reserve.
            preferred_required = len(ordered_threats) * 4
            # Preserve a mixed quick-response reserve whenever capacity permits.
            # Previously every free craft was consumed by the first capture,
            # leaving a later attacker with no timely blocker.
            reserve = min(
                max(1, math.ceil(getattr(
                    self.plan,
                    "uav_count" if kind == "UAV" else "usv_count",
                    len(available),
                ) * 0.18)),
                max(0, len(available) - preferred_required),
            )
            reserve = min(
                reserve,
                max(0, len(available) - len(ordered_threats) * 2),
            )
            assignable = max(0, len(available) - reserve)
            per_threat_quota = min(
                4,
                max(2, assignable // max(1, len(ordered_threats))),
            )
            # A bounded quota prevents one early incident from absorbing every
            # free craft. Four of each kind gives an unambiguous eight-point
            # ring; remaining craft stay available for blockers and later
            # threats instead of producing a 32-member ring beside an empty
            # group.
            for _ in range(per_threat_quota):
                for threat_index, threat in ordered_threats:
                    if not available:
                        break
                    chosen = min(
                        available,
                        key=lambda item: _length(item.x - threat.x, item.y - threat.y),
                    )
                    chosen.assigned_threat = threat_index
                    chosen.role = "INTERCEPTOR"
                    chosen.group_id = f"CAPTURE-{threat_index + 1:03d}"
                    available.remove(chosen)
            available.sort(key=lambda item: item.code)
            # The fixed mixed teams already provide a complete ring for every
            # active threat. Keep all surplus craft in an ordered mobile reserve
            # around the single protected target. This gives every craft an
            # explicit job and avoids a large-fleet cloud of unrelated patrols.
            for position, reserved in enumerate(available):
                threat_index = ordered_threats[position % len(ordered_threats)][0]
                reserved.role = "CAPTURE_RESERVE"
                reserved.group_id = f"RESERVE-{threat_index + 1:03d}"
            minimum = 2
            for threat_index, threat in ordered_threats:
                members = [item for item in self.vehicles if item.kind == kind and item.assigned_threat == threat_index]
                while len(members) < minimum:
                    donors = sorted(
                        (
                            item for item in self.vehicles
                            if item.kind == kind
                            and item.assigned_threat in selected_indices
                            and item.assigned_threat != threat_index
                            and item.role == "INTERCEPTOR"
                            and sum(
                                other.kind == kind and other.assigned_threat == item.assigned_threat
                                for other in self.vehicles
                            ) > minimum
                        ),
                        key=lambda item: _length(item.x - threat.x, item.y - threat.y),
                    )
                    if not donors:
                        break
                    donor = donors[0]
                    donor.assigned_threat = threat_index
                    donor.group_id = f"CAPTURE-{threat_index + 1:03d}"
                    members.append(donor)

    def _release_capture_group(self, threat_index: int) -> None:
        for item in self.vehicles:
            if item.assigned_threat == threat_index:
                item.assigned_threat, item.role = None, "RETURNING"
                item.group_id = f"RECON-{item.protected_index + 1:03d}"

    def _capture_members(self, threat_index: int) -> list[_Vehicle]:
        return [item for item in self.vehicles if item.assigned_threat == threat_index]

    def _maybe_reassign_capture_members(self) -> None:
        active_indices = [
            index for index, threat in enumerate(self.threats)
            if threat.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}
            and self._capture_members(index)
        ]
        if len(active_indices) < 2:
            return
        participants = [
            item for item in self.vehicles
            if item.assigned_threat in active_indices
        ]
        if not participants:
            return

        agents: list[DecisionAgent] = []
        capacities: dict[str, dict[str, int]] = {}
        targets: list[DecisionTarget] = []
        for index in active_indices:
            threat = self.threats[index]
            members = self._capture_members(index)
            capacities[threat.code] = {
                "UAV": sum(item.kind == "UAV" for item in members),
                "USV": sum(item.kind == "USV" for item in members),
            }
            risk, tti, closing = self._threat_risk(threat)
            urgency = min(
                1.0,
                max(
                    0.1,
                    (1.0 if tti <= URGENT_TTI_SECONDS else 0.0) * 0.35
                    + min(1.0, max(0.0, closing) / 2.8) * 0.25
                    + min(1.0, risk / 120.0) * 0.40,
                ),
            )
            targets.append(DecisionTarget(
                code=threat.code,
                x=threat.x,
                y=threat.y,
                vx=threat.vx,
                vy=threat.vy,
                urgency=urgency,
                gap_bearing_deg=math.degrees(threat.gap_center_angle) % 360.0,
                gap_deg=threat.capture_max_gap_deg,
                locked=(
                    threat.capture_hold > 0
                    or threat.state in {"STABLE_CONTAINMENT", "CAPTURED", "SECURED"}
                ),
            ))
        for item in participants:
            heading = self._stable_headings.get(item.code)
            if heading is None:
                heading = (
                    math.degrees(math.atan2(item.vy, item.vx)) % 360.0
                    if _length(item.vx, item.vy) > 0.05
                    else 0.0
                )
            agents.append(DecisionAgent(
                code=item.code,
                kind=item.kind,
                x=item.x,
                y=item.y,
                speed_mps=_length(item.vx, item.vy),
                heading_deg=heading,
                maximum_speed_mps=self.uav_cruise if item.kind == "UAV" else 4.0,
                current_target=self.threats[item.assigned_threat].code,
                role=item.role,
                stalled_frames=self._vehicle_stall_frames.get(item.code, 0),
            ))
        result = self.dynamic_allocator.evaluate(
            agents,
            targets,
            capacities,
            frame=self.sequence,
        )
        self.assignment_last_result = {
            "reason": result.reason,
            "improvementRatio": round(result.improvement_ratio, 4),
            "currentCost": round(result.current_cost, 3),
            "proposedCost": round(result.proposed_cost, 3),
        }
        if not result.accepted:
            return
        index_by_code = {threat.code: index for index, threat in enumerate(self.threats)}
        changed_targets: set[int] = set()
        for item in participants:
            target_code = result.assignments[item.code]
            target_index = index_by_code[target_code]
            if item.assigned_threat == target_index:
                continue
            previous_index = item.assigned_threat
            changed_targets.update({previous_index, target_index})
            item.assigned_threat = target_index
            item.role = "INTERCEPTOR"
            item.group_id = f"CAPTURE-{target_index + 1:03d}"
            item.final_slot_angle = None
        for target_index in changed_targets:
            self._ring_slots.pop(target_index, None)
            self._ring_best_arrival.pop(target_index, None)
            self._ring_stalled_frames.pop(target_index, None)
        for change in result.changes:
            self.assignment_changes.append({
                "sequence": self.sequence,
                "deviceCode": change.agent_code,
                "deviceType": change.kind,
                "previousTarget": change.previous_target,
                "targetCode": change.target,
                "reason": change.reason,
                "previousEtaSec": (
                    None if change.previous_eta_sec is None
                    else round(change.previous_eta_sec, 2)
                ),
                "interceptEtaSec": round(change.intercept_eta_sec, 2),
            })
        self.assignment_changes = self.assignment_changes[-48:]

    def _nearest_threat(self, protected_index: int) -> tuple[int, _Threat] | None:
        active = self._active_threats_for(protected_index)
        target = self.protected[protected_index]
        return min(active, key=lambda pair: _length(pair[1].x - target.x, pair[1].y - target.y)) if active else None

    def _nearest_hazard(self, protected_index: int) -> tuple[int, _Threat] | None:
        target = self.protected[protected_index]
        hazards = [
            (index, item) for index, item in enumerate(self.threats)
            # A captured/secured hull and its containment ring remain a real
            # exclusion zone.  Dropping SECURED here made the convoy resume a
            # straight route through the stopped enemy.
            if item.state not in {"WAITING", "ESCAPED"}
        ]
        return min(hazards, key=lambda pair: _length(pair[1].x - target.x, pair[1].y - target.y)) if hazards else None

    def _protected_hazards(self, protected_index: int) -> list[_Threat]:
        return [
            item for item in self.threats
            if item.state not in {"WAITING", "ESCAPED"}
        ]

    def _choose_protected_escape(
        self,
        target: _Protected,
        hazards: Sequence[_Threat],
        speed: float,
        *,
        route_priority: bool = False,
    ) -> tuple[float, float]:
        """Maximise predicted clearance from every attacker, not only the nearest.

        The former nearest-only repulsion could send the protected vessel
        directly toward a second simultaneous attacker. Candidate headings are
        evaluated against all live enemy trajectories and the shoreline while
        retaining a smaller preference for the destination route.
        """
        goal_x, goal_y = _unit(target.destination_x - target.x, target.destination_y - target.y)
        horizon = 7.0
        left, right, bottom, top = self.safe_bounds
        best: tuple[float, float, float] | None = None
        for sample in range(48):
            angle = 2.0 * math.pi * sample / 48.0
            dx, dy = math.cos(angle), math.sin(angle)
            future_x = target.x + dx * speed * horizon
            future_y = target.y + dy * speed * horizon
            predicted: list[float] = []
            closing_penalty = 0.0
            for threat in hazards:
                threat_future_x = threat.x + threat.vx * horizon
                threat_future_y = threat.y + threat.vy * horizon
                future_distance = _length(future_x - threat_future_x, future_y - threat_future_y)
                current_distance = _length(target.x - threat.x, target.y - threat.y)
                predicted.append(future_distance)
                if threat.state not in {"CAPTURED", "SECURED"} and future_distance < current_distance:
                    closing_penalty += (current_distance - future_distance) * 4.2
            shore_clearance = min(
                future_x - left, right - future_x,
                future_y - bottom, top - future_y,
            )
            shore_penalty = max(0.0, 18.0 - shore_clearance) * 18.0
            minimum_clearance = min(predicted, default=200.0)
            mean_clearance = sum(predicted) / max(1, len(predicted))
            route_alignment = dx * goal_x + dy * goal_y
            if route_priority:
                # Captured threats are stationary keep-out zones, not active
                # pursuers. Favour a safe tangent that still advances through
                # the destination gate instead of orbiting the containment
                # group forever.
                clearance_penalty = max(
                    0.0,
                    POST_CAPTURE_TRANSIT_CLEARANCE_M - minimum_clearance,
                ) * 16.0
                score = (
                    route_alignment * 54.0
                    + min(72.0, minimum_clearance) * 0.48
                    + min(50.0, shore_clearance) * 0.72
                    - clearance_penalty
                    - shore_penalty
                )
            else:
                score = (
                    minimum_clearance * 2.4
                    + mean_clearance * 0.32
                    + route_alignment * 22.0
                    + min(50.0, shore_clearance) * 0.72
                    - closing_penalty
                    - shore_penalty
                )
            if best is None or score > best[0]:
                best = (score, dx, dy)
        assert best is not None
        return best[1], best[2]

    def _choose_convoy_escape(
        self,
        hazards: Sequence[_Threat],
        speed: float,
        *,
        route_priority: bool = False,
        covering: bool = False,
    ) -> tuple[float, float]:
        """Choose one safe heading for the complete protected formation.

        Scoring every protected hull against every hostile prevents separate
        targets from selecting opposite evasive corridors. The chosen intent
        is later applied as one rigid translation, preserving every slot.
        """
        center_x, center_y = self._convoy_center()
        destination_x = sum(item.destination_x for item in self.protected) / len(self.protected)
        destination_y = sum(item.destination_y for item in self.protected) / len(self.protected)
        if all(self._escort_route_progress(item) >= 0.98 for item in self.protected):
            # A safe gate is a crossing line, not a point attractor.  During a
            # still-live incident keep advancing through the gate while gently
            # returning to its lane; otherwise the convoy oscillates around
            # destination_x and can remain inside a forming ring forever.
            ux, uy = self._escort_route_axis()
            lane_error = -(destination_x - center_x) * uy + (destination_y - center_y) * ux
            correction = max(-0.65, min(0.65, lane_error / 36.0))
            goal_x, goal_y = _unit(ux - uy * correction, uy + ux * correction)
        else:
            goal_x, goal_y = _unit(destination_x - center_x, destination_y - center_y)
        horizon = 7.0
        left, right, bottom, top = self.safe_bounds
        current_minimum_clearance = min(
            (
                _length(target.x - threat.x, target.y - threat.y)
                for target in self.protected
                for threat in hazards
            ),
            default=math.inf,
        )
        # Evasion is a constrained navigation problem, not an unconstrained
        # clearance maximisation problem.  When there is room, reject headings
        # that do not advance the route.  Relax that forward cone in measured
        # bands and only permit a retreat during an actual close-range event.
        if covering:
            route_alignment_floor = -1.0
        elif route_priority:
            route_alignment_floor = 0.10
        elif current_minimum_clearance >= 110.0:
            route_alignment_floor = 0.55
        elif current_minimum_clearance >= 82.0:
            route_alignment_floor = 0.30
        elif current_minimum_clearance >= 58.0:
            route_alignment_floor = 0.0
        else:
            route_alignment_floor = -1.0
        candidates: list[tuple[float, float, float, float, float, float]] = []
        for sample in range(48):
            angle = 2.0 * math.pi * sample / 48.0
            dx, dy = math.cos(angle), math.sin(angle)
            predicted: list[float] = []
            closing_penalty = 0.0
            shore_clearance = math.inf
            for target in self.protected:
                future_x = target.x + dx * speed * horizon
                future_y = target.y + dy * speed * horizon
                shore_clearance = min(
                    shore_clearance,
                    future_x - left,
                    right - future_x,
                    future_y - bottom,
                    top - future_y,
                )
                for threat in hazards:
                    threat_future_x = threat.x + threat.vx * horizon
                    threat_future_y = threat.y + threat.vy * horizon
                    future_distance = _length(
                        future_x - threat_future_x,
                        future_y - threat_future_y,
                    )
                    current_distance = _length(target.x - threat.x, target.y - threat.y)
                    predicted.append(future_distance)
                    if (
                        threat.state not in {"CAPTURED", "SECURED"}
                        and future_distance < current_distance
                    ):
                        closing_penalty += (current_distance - future_distance) * 4.2
            shore_penalty = max(0.0, 18.0 - shore_clearance) * 18.0
            minimum_clearance = min(predicted, default=200.0)
            mean_clearance = sum(predicted) / max(1, len(predicted))
            route_alignment = dx * goal_x + dy * goal_y
            if covering:
                # Retreat is not route progress. Prefer a common direction
                # away from attackers, with continuity and shoreline safety.
                away_alignment = min((
                    dx * (center_x - threat.x) / max(1.0, _length(center_x - threat.x, center_y - threat.y))
                    + dy * (center_y - threat.y) / max(1.0, _length(center_x - threat.x, center_y - threat.y))
                    for threat in hazards
                ), default=1.0)
                prior = getattr(self, "_cover_retreat_vector", (dx, dy))
                score = (away_alignment * 100.0 + minimum_clearance * 0.6
                         + (dx * prior[0] + dy * prior[1]) * 24.0
                         - closing_penalty - shore_penalty)
            elif route_priority:
                clearance_penalty = max(
                    0.0,
                    POST_CAPTURE_TRANSIT_CLEARANCE_M - minimum_clearance,
                ) * 16.0
                score = (
                    route_alignment * 54.0
                    + min(72.0, minimum_clearance) * 0.48
                    + min(50.0, shore_clearance) * 0.72
                    - clearance_penalty
                    - shore_penalty
                )
            else:
                score = (
                    min(120.0, minimum_clearance) * 0.82
                    + min(120.0, mean_clearance) * 0.10
                    + route_alignment * 96.0
                    + min(50.0, shore_clearance) * 0.72
                    - closing_penalty * 0.45
                    - shore_penalty
                )
            candidates.append((
                score, dx, dy, route_alignment,
                minimum_clearance, shore_clearance,
            ))
        viable = [
            candidate for candidate in candidates
            if candidate[3] >= route_alignment_floor
            and candidate[5] >= 8.0
        ]
        # If prediction says every forward heading is unsafe, deliberately
        # broaden the cone instead of stopping.  This is the visible emergency
        # dodge; normal frames continue to make positive route progress.
        best = max(viable or candidates, key=lambda candidate: candidate[0])
        return best[1], best[2]

    def _choose_post_capture_bypass(
        self,
        hazards: Sequence[_Threat],
    ) -> tuple[float, float]:
        """Choose a persistent waypoint around completed containment rings.

        A one-frame clearance maximiser can orbit the near edge of a circular
        keep-out zone forever because every forward step looks worse than a
        tangent step. Select a point beyond the nearest blocking ring and keep
        steering toward it. This produces the visible, intentional S-turn a
        human navigator would expect and guarantees route progress.
        """
        center_x, center_y = self._convoy_center()
        destination_x = sum(item.destination_x for item in self.protected) / len(self.protected)
        destination_y = sum(item.destination_y for item in self.protected) / len(self.protected)
        route_dx = destination_x - center_x
        route_dy = destination_y - center_y
        route_length_sq = max(1.0, route_dx * route_dx + route_dy * route_dy)
        formation_half_height = max(
            (abs(item.y - center_y) for item in self.protected),
            default=0.0,
        )
        required_center_clearance = (
            POST_CAPTURE_TRANSIT_CLEARANCE_M + formation_half_height + 5.0
        )
        blocking: list[tuple[float, _Threat]] = []
        for hazard in hazards:
            projection = (
                (hazard.x - center_x) * route_dx
                + (hazard.y - center_y) * route_dy
            ) / route_length_sq
            if projection < -0.04 or projection > 1.08:
                continue
            projected_x = center_x + route_dx * max(0.0, min(1.0, projection))
            projected_y = center_y + route_dy * max(0.0, min(1.0, projection))
            corridor_distance = _length(hazard.x - projected_x, hazard.y - projected_y)
            if corridor_distance <= required_center_clearance + 8.0:
                blocking.append((_length(hazard.x - center_x, hazard.y - center_y), hazard))
        if not blocking:
            return _unit(route_dx, route_dy)

        _, obstacle = min(blocking, key=lambda pair: pair[0])
        travel_sign = 1.0 if route_dx >= 0.0 else -1.0
        pass_x = obstacle.x + travel_sign * 18.0
        margin = required_center_clearance + 9.0
        candidates: list[tuple[float, float, float]] = []
        for side in (-1.0, 1.0):
            waypoint_y = max(
                self.safe_bounds[2] + SHORE_MARGIN_M,
                min(
                    self.safe_bounds[3] - SHORE_MARGIN_M,
                    obstacle.y + side * margin,
                ),
            )
            waypoint_x = max(
                self.safe_bounds[0] + SHORE_MARGIN_M,
                min(self.safe_bounds[1] - SHORE_MARGIN_M, pass_x),
            )
            other_clearance = min(
                (
                    _length(waypoint_x - hazard.x, waypoint_y - hazard.y)
                    for hazard in hazards
                ),
                default=200.0,
            )
            shore_clearance = min(
                waypoint_x - self.safe_bounds[0],
                self.safe_bounds[1] - waypoint_x,
                waypoint_y - self.safe_bounds[2],
                self.safe_bounds[3] - waypoint_y,
            )
            detour = _length(waypoint_x - center_x, waypoint_y - center_y)
            # Stable code/seed tie-break means two identical frames choose the
            # same side instead of alternating around the obstacle.
            preferred_side = 1.0 if (self.seed + sum(ord(char) for char in obstacle.code)) % 2 == 0 else -1.0
            score = (
                min(100.0, other_clearance) * 2.2
                + min(60.0, shore_clearance) * 1.1
                - detour * 0.18
                + (3.0 if side == preferred_side else 0.0)
            )
            candidates.append((score, waypoint_x, waypoint_y))
        _, waypoint_x, waypoint_y = max(candidates, key=lambda item: item[0])
        return _unit(waypoint_x - center_x, waypoint_y - center_y)

    def _translate_protected_convoy(
        self,
        direction_x: float,
        direction_y: float,
        speed: float,
        state: str,
        hazards: Sequence[_Threat],
        containment_clearance_m: float = CONTAINMENT_STANDOFF_M,
    ) -> None:
        """Apply one rigid, safety-scored translation to every protected hull."""
        current_vx = sum(item.vx for item in self.protected) / len(self.protected)
        current_vy = sum(item.vy for item in self.protected) / len(self.protected)
        desired_vx, desired_vy = direction_x * speed, direction_y * speed
        maximum_speed = 2.25
        accel = 0.10
        shared_vx = current_vx + max(-accel, min(accel, desired_vx - current_vx))
        shared_vy = current_vy + max(-accel, min(accel, desired_vy - current_vy))
        shared_vx, shared_vy = _clamp_magnitude(shared_vx, shared_vy, maximum_speed)
        step = _length(shared_vx, shared_vy) * DT
        desired_heading_x, desired_heading_y = _unit(
            shared_vx,
            shared_vy,
            (direction_x, direction_y),
        )

        candidates: list[tuple[bool, float, float, float]] = []
        for sample in range(72):
            angle = 2.0 * math.pi * sample / 72.0
            candidate_x = math.cos(angle) * step
            candidate_y = math.sin(angle) * step
            margins: list[float] = []
            shore_clearance = math.inf
            for target in self.protected:
                next_x = target.x + candidate_x
                next_y = target.y + candidate_y
                shore_clearance = min(
                    shore_clearance,
                    next_x - self.safe_bounds[0],
                    self.safe_bounds[1] - next_x,
                    next_y - self.safe_bounds[2],
                    self.safe_bounds[3] - next_y,
                )
                for hazard in hazards:
                    required = (
                        containment_clearance_m
                        if (
                            hazard.state in {"CAPTURED", "SECURED"}
                            or (hazard.forced and hazard.capture_stage >= 2)
                        )
                        else TARGET_SEPARATION_M
                    )
                    margins.append(
                        _length(next_x - hazard.x, next_y - hazard.y) - required
                    )
            minimum_margin = min(margins, default=100.0)
            feasible = minimum_margin >= 0.0 and shore_clearance >= 0.5
            alignment = (
                math.cos(angle) * desired_heading_x
                + math.sin(angle) * desired_heading_y
            )
            clearance_score = (
                minimum_margin * 22.0
                if minimum_margin < 0.0
                else min(12.0, minimum_margin) * 0.5
            )
            score = (
                clearance_score
                + alignment * 18.0
                + min(20.0, shore_clearance) * 1.8
            )
            candidates.append((feasible, score, candidate_x, candidate_y))

        feasible_candidates = [candidate for candidate in candidates if candidate[0]]
        current_safe = all(
            _length(target.x - hazard.x, target.y - hazard.y) >= (
                containment_clearance_m
                if (
                    hazard.state in {"CAPTURED", "SECURED"}
                    or (hazard.forced and hazard.capture_stage >= 2)
                )
                else TARGET_SEPARATION_M
            )
            for target in self.protected
            for hazard in hazards
        )
        if feasible_candidates:
            _, _, displacement_x, displacement_y = max(
                feasible_candidates,
                key=lambda candidate: candidate[1],
            )
        elif current_safe:
            displacement_x = displacement_y = 0.0
        else:
            _, _, displacement_x, displacement_y = max(
                candidates,
                key=lambda candidate: candidate[1],
            )

        if _length(displacement_x, displacement_y) > 1e-8:
            shared_vx = displacement_x / DT
            shared_vy = displacement_y / DT
            heading = math.degrees(math.atan2(displacement_y, displacement_x)) % 360.0
        else:
            shared_vx = shared_vy = 0.0
            heading = self.protected[0].heading
        if (
            abs(displacement_x - desired_heading_x * step) > 0.01
            or abs(displacement_y - desired_heading_y * step) > 0.01
        ):
            self.avoidance_count += 1
        for target in self.protected:
            target.x += displacement_x
            target.y += displacement_y
            target.vx = shared_vx
            target.vy = shared_vy
            target.heading = heading
            target.state = state

    def _advance_protected(self) -> None:
        guards_unavailable = any(v.role == "CLOSE_GUARD" and v.code in self._unavailable_codes for v in self.vehicles)
        if (self._shore_assembly_pending or self._guard_replacement_pending
                or guards_unavailable or not self._active_force_sufficient()):
            for target in self.protected:
                target.vx = target.vy = 0.0
                target.state = "ASSEMBLING_ESCORT" if self._shore_assembly_pending else "WAITING_FOR_GUARD"
            return
        if self.defense.advance_convoy():
            return
        maximum_speed = 2.25
        cruise = min(maximum_speed, max(1.45, self.usv_cruise * 0.72))
        hazards = [
            item for item in self.threats
            if item.state not in {"WAITING", "ESCAPED"}
        ]
        mission_resolved = bool(self.threats) and all(
            item.state in {"CAPTURED", "SECURED", "ESCAPED"}
            for item in self.threats
        )
        # Once every hostile is resolved and the rigid convoy has cleared the
        # safe observation gate, hold that water-space while escorts regroup.
        # Continuing all the way to harbour made moving support slots overlap a
        # completed containment ring and needlessly lengthened the final phase.
        if mission_resolved and all(
            self._protected_reached_safe_gate(item)
            for item in self.protected
        ):
            for target in self.protected:
                target.vx = target.vy = 0.0
                target.state = "REGROUPING"
            return
        live_attackers = [
            item for item in hazards
            if item.state not in {"CAPTURED", "SECURED"}
            and min(
                _length(item.x - target.x, item.y - target.y)
                for target in self.protected
            ) < 165.0
        ]
        persistent_obstacles = [
            item for item in hazards
            if item.state in {"CAPTURED", "SECURED"}
            and min(
                _length(item.x - target.x, item.y - target.y)
                for target in self.protected
            ) < CONTAINMENT_REPLAN_M
        ]
        destination_x = sum(item.destination_x for item in self.protected) / len(self.protected)
        destination_y = sum(item.destination_y for item in self.protected) / len(self.protected)
        center_x, center_y = self._convoy_center()
        common_x, common_y = _unit(destination_x - center_x, destination_y - center_y)
        common_speed = cruise
        common_state = "ESCORTING"
        cover_pending = any(t.detected_frame is not None and not t.forced
                            and not t.cover_released for t in live_attackers)
        nearest_distance = min(
            (
                _length(threat.x - target.x, threat.y - target.y)
                for threat in hazards
                for target in self.protected
            ),
            default=math.inf,
        )
        clearing_completed_rings = (
            mission_resolved
            and all(self._escort_route_progress(item) >= 1.0 for item in self.protected)
            and not all(self._protected_reached_safe_gate(item) for item in self.protected)
        )
        if clearing_completed_rings:
            # The nominal destination can lie inside the union of two completed
            # containment buffers.  Continuing to attract the convoy to that
            # point pins it exactly on the transit boundary.  Once the route
            # gate is crossed, move along the aggregate outward gradient until
            # every final 42 m safety margin is satisfied, then hold.
            safely_outside_rings = all(
                _length(target.x - hazard.x, target.y - hazard.y)
                >= POST_CAPTURE_TRANSIT_CLEARANCE_M + 2.0
                for target in self.protected
                for hazard in persistent_obstacles
            )
            if safely_outside_rings:
                # The bypass may finish above or below the nominal gate.
                # Once clear, deliberately converge back to the gate lane;
                # continuing the outward gradient strands a safe convoy at
                # 100% x-progress but excessive lateral error.
                common_x, common_y = _unit(
                    destination_x - center_x,
                    destination_y - center_y,
                    (common_x, common_y),
                )
                common_state = "RETURNING_SAFE_GATE"
            else:
                repel_x = repel_y = 0.0
                for hazard in persistent_obstacles:
                    away_x, away_y = _unit(
                        center_x - hazard.x,
                        center_y - hazard.y,
                        (common_x, common_y),
                    )
                    separation = max(1.0, _length(center_x - hazard.x, center_y - hazard.y))
                    weight = max(0.25, POST_CAPTURE_TRANSIT_CLEARANCE_M + 6.0 - separation)
                    repel_x += away_x * weight
                    repel_y += away_y * weight
                common_x, common_y = _unit(repel_x, repel_y, (common_x, common_y))
                common_state = "CLEARING_CONTAINMENT"
            common_speed = min(maximum_speed, max(1.65, self.usv_cruise * 0.78))
        elif live_attackers:
            if nearest_distance >= 110.0:
                speed_ratio, minimum_speed = 0.82, 1.75
            elif nearest_distance >= 82.0:
                speed_ratio, minimum_speed = 0.72, 1.55
            elif nearest_distance >= 58.0:
                speed_ratio, minimum_speed = 0.62, 1.35
            else:
                speed_ratio, minimum_speed = 0.52, 1.15
            common_speed = min(
                maximum_speed,
                max(minimum_speed, self.usv_cruise * speed_ratio),
            )
            common_x, common_y = self._choose_convoy_escape(
                live_attackers,
                common_speed,
                covering=cover_pending,
            )
            if cover_pending:
                self._cover_retreat_vector = (common_x, common_y)
                common_state = "COVERED_WITHDRAWAL"
            else:
                common_state = "EVADING" if nearest_distance < 105.0 else "THREAT_DETECTED"
        elif persistent_obstacles:
            obstacle_distance = min(
                _length(center_x - obstacle.x, center_y - obstacle.y)
                for obstacle in persistent_obstacles
            )
            speed_ratio = (
                0.88 if obstacle_distance >= 94.0
                else 0.76 if obstacle_distance >= 72.0
                else 0.62
            )
            common_speed = min(
                maximum_speed,
                max(1.45, self.usv_cruise * speed_ratio),
            )
            common_x, common_y = self._choose_post_capture_bypass(
                persistent_obstacles,
            )
            common_state = "BYPASSING_CONTAINMENT"

        route_progress = min(
            (self._escort_route_progress(item) for item in self.protected),
            default=0.0,
        )
        if not mission_resolved and not cover_pending and route_progress >= 0.90:
            # Escort and containment run in parallel, but the convoy must not
            # cross the final safety gate before the threat rings are stable.
            # Decelerate into a visible gate-approach hold at 90% so the later
            # SAFE_GATE_TRANSIT stage always represents real forward motion.
            hold_points = [self._escort_route_station(item, 0.90) for item in self.protected]
            hold_x = sum(point[0] for point in hold_points) / len(hold_points)
            hold_y = sum(point[1] for point in hold_points) / len(hold_points)
            hold_distance = _length(hold_x - center_x, hold_y - center_y)
            if live_attackers and nearest_distance < CONTAINMENT_STANDOFF_M + 4.0:
                # The holding line is subordinate to safety.  Move within the
                # approach area away from the closest live ring so neither the
                # convoy nor the ring waits forever for the other to yield.
                closest = min(
                    live_attackers,
                    key=lambda hazard: _length(
                        center_x - hazard.x,
                        center_y - hazard.y,
                    ),
                )
                repel_x, repel_y = _unit(
                    center_x - closest.x,
                    center_y - closest.y,
                )
                hold_dir_x, hold_dir_y = _unit(
                    hold_x - center_x,
                    hold_y - center_y,
                )
                common_x, common_y = _unit(
                    repel_x * 1.8 + hold_dir_x * 0.45,
                    repel_y * 1.8 + hold_dir_y * 0.45,
                    (repel_x, repel_y),
                )
                # Evasive motion at the gate is lateral/backward within the
                # approach box.  Do not let a threat arriving from astern push
                # the protected target through the gate before containment.
                route_x, route_y = self._escort_route_axis()
                forward_limit = -0.15 if (center_x - hold_x) * route_x + (center_y - hold_y) * route_y >= 0.0 else 0.25
                forward = min(forward_limit, common_x * route_x + common_y * route_y)
                lateral = -common_x * route_y + common_y * route_x
                common_x, common_y = _unit(
                    route_x * forward - route_y * lateral,
                    route_y * forward + route_x * lateral,
                    (-0.15 * route_x - route_y, -0.15 * route_y + route_x),
                )
                common_speed = min(common_speed, 1.45)
                common_state = "SAFE_GATE_EVASIVE_HOLD"
            elif hold_distance <= 1.5:
                common_speed = 0.0
                common_x, common_y = _unit(
                    destination_x - center_x,
                    destination_y - center_y,
                )
                common_state = "SAFE_GATE_HOLDING"
            else:
                common_speed = min(common_speed, max(0.45, hold_distance * 0.35))
                common_x, common_y = _unit(hold_x - center_x, hold_y - center_y)
                common_state = "SAFE_GATE_HOLDING"

        for threat in self.threats:
            if threat.screen_established and not threat.cover_released and not threat.forced:
                # Move the established screen as a rigid, slowly retreating
                # line. It must not instantly jump to a new intercept point.
                rx, ry = self._screen_retreat_direction(threat)
                translated = {code: (x + rx * COVER_REVERSE_SPEED_MPS * DT,
                                     y + ry * COVER_REVERSE_SPEED_MPS * DT)
                              for code, (x, y) in threat.cover_slots.items()}
                if all(self._project_to_safe_water(x, y, 2.0) == (x, y)
                       for x, y in translated.values()):
                    threat.cover_slots = translated

        if len(self.protected) > 1:
            self._translate_protected_convoy(
                common_x,
                common_y,
                common_speed,
                common_state,
                hazards,
                (
                    POST_CAPTURE_TRANSIT_CLEARANCE_M
                    if mission_resolved
                    else CONTAINMENT_STANDOFF_M
                ),
            )
            if not mission_resolved:
                for target in self.protected:
                    target.x, target.y = self._limit_escort_approach(target, target.x, target.y)
            return

        for index, target in enumerate(self.protected):
            offset_x, offset_y = self._protected_formation_offsets[target.code]
            slot_error_x = center_x + offset_x - target.x
            slot_error_y = center_y + offset_y - target.y
            correction_x, correction_y = _clamp_magnitude(
                slot_error_x * 0.18,
                slot_error_y * 0.18,
                0.55,
            )
            desired_vx = common_x * common_speed + correction_x
            desired_vy = common_y * common_speed + correction_y
            target.state = common_state
            desired_vx, desired_vy = _clamp_magnitude(desired_vx, desired_vy, maximum_speed)
            # Preserve forward motion while changing escape corridors. Per-axis
            # interpolation drove the resultant speed through zero whenever two
            # successive avoidance headings straddled the current course, which
            # made a safe convoy orbit a captured ring indefinitely.
            desired_speed = _length(desired_vx, desired_vy)
            current_speed = _length(target.vx, target.vy)
            current_heading = (
                math.atan2(target.vy, target.vx)
                if current_speed > 0.05
                else math.radians(target.heading)
            )
            desired_heading = math.atan2(desired_vy, desired_vx)
            heading_error = (
                desired_heading - current_heading + math.pi
            ) % (2.0 * math.pi) - math.pi
            next_heading = current_heading + max(
                -math.radians(6.0),
                min(math.radians(6.0), heading_error),
            )
            next_speed = current_speed + max(
                -0.085,
                min(0.085, desired_speed - current_speed),
            )
            next_speed = max(min(cruise * 0.88, desired_speed), next_speed)
            next_speed = min(maximum_speed, next_speed)
            target.vx = math.cos(next_heading) * next_speed
            target.vy = math.sin(next_heading) * next_speed
            nx, ny = self._project_to_safe_water(target.x + target.vx * DT, target.y + target.vy * DT)
            relevant_hazards = [
                hazard for hazard in self.threats
                if hazard.state not in {"WAITING", "ESCAPED"}
            ]
            containment_clearance_m = (
                POST_CAPTURE_TRANSIT_CLEARANCE_M
                if mission_resolved
                else CONTAINMENT_STANDOFF_M
            )
            containment_conflict_active = any(
                hazard.forced
                and hazard.capture_stage >= 2
                and _length(target.x - hazard.x, target.y - hazard.y)
                < containment_clearance_m
                for hazard in relevant_hazards
            )
            if any(
                _length(nx - hazard.x, ny - hazard.y) < (
                    containment_clearance_m
                    if (
                        hazard.state in {"CAPTURED", "SECURED"}
                        or (hazard.forced and hazard.capture_stage >= 2)
                    )
                    else TARGET_SEPARATION_M
                )
                for hazard in relevant_hazards
            ):
                # Select a feasible one-tick heading against every hazard and
                # the shoreline together. The old fixed left/right tangent
                # could point outside the water at a corner; projection then
                # reduced the physical step to zero and stranded a convoy.
                step = min(2.25, max(cruise, _length(target.vx, target.vy))) * DT
                goal_x, goal_y = _unit(
                    target.destination_x - target.x,
                    target.destination_y - target.y,
                )
                velocity_x, velocity_y = _unit(target.vx, target.vy, (goal_x, goal_y))
                candidates: list[tuple[float, float, float, float, float, float]] = []
                for sample in range(72):
                    angle = 2.0 * math.pi * sample / 72.0
                    direction_x, direction_y = math.cos(angle), math.sin(angle)
                    candidate_x, candidate_y = self._project_to_safe_water(
                        target.x + direction_x * step,
                        target.y + direction_y * step,
                        0.5,
                    )
                    displacement = _length(candidate_x - target.x, candidate_y - target.y)
                    if displacement < step * 0.45:
                        continue
                    margins = [
                        _length(candidate_x - hazard.x, candidate_y - hazard.y) - (
                            containment_clearance_m
                            if (
                                hazard.state in {"CAPTURED", "SECURED"}
                                or (hazard.forced and hazard.capture_stage >= 2)
                            )
                            else TARGET_SEPARATION_M
                        )
                        for hazard in relevant_hazards
                    ]
                    minimum_margin = min(margins, default=100.0)
                    shore_clearance = min(
                        candidate_x - self.safe_bounds[0], self.safe_bounds[1] - candidate_x,
                        candidate_y - self.safe_bounds[2], self.safe_bounds[3] - candidate_y,
                    )
                    route_alignment = direction_x * goal_x + direction_y * goal_y
                    smooth_alignment = direction_x * velocity_x + direction_y * velocity_y
                    # A nearly closed ring is allowed to displace the escorted
                    # hull from its nominal lane.  Keeping the normal route
                    # attraction here made the target sit exactly on the
                    # 34 m collision boundary while the hostile ring waited
                    # for the required 78 m containment clearance.  In this
                    # short clearance manoeuvre, maximise actual separation;
                    # normal route following resumes as soon as the conflict
                    # is clear.
                    route_weight = 0.0 if containment_conflict_active else 16.0
                    score = (
                        minimum_margin * (22.0 if minimum_margin < 0.0 else 3.0)
                        + route_alignment * route_weight
                        + smooth_alignment * 2.0
                        + min(20.0, shore_clearance) * 1.8
                    )
                    candidates.append((
                        score, minimum_margin, candidate_x, candidate_y,
                        direction_x, direction_y,
                    ))
                feasible_candidates = [
                    candidate for candidate in candidates if candidate[1] >= 0.0
                ]
                if candidates:
                    best_candidate = max(
                        feasible_candidates or candidates,
                        key=lambda candidate: candidate[0],
                    )
                    _, _, nx, ny, direction_x, direction_y = best_candidate
                    target.vx = direction_x * step / DT
                    target.vy = direction_y * step / DT
                    self.avoidance_count += 1
            # Protected vessels are independent moving hulls, not points. In
            # multi-convoy scenes they can choose the same escape corridor and
            # converge even though each one is clear of its assigned threat.
            # Start a deterministic lateral split before their hull envelopes
            # touch; keep the correction at the vessel's physical step so the
            # WebGL presentation never jumps.
            for other_index, other in enumerate(self.protected):
                if other is target:
                    continue
                separation = _length(nx - other.x, ny - other.y)
                if separation >= 34.0:
                    continue
                away_x, away_y = _unit(
                    nx - other.x,
                    ny - other.y,
                    (0.0, 1.0 if index < other_index else -1.0),
                )
                side = 1.0 if index < other_index else -1.0
                split_x, split_y = _unit(away_x - away_y * 0.48 * side, away_y + away_x * 0.48 * side)
                step = min(2.25, max(cruise, _length(target.vx, target.vy))) * DT
                nx, ny = self._project_to_safe_water(
                    target.x + split_x * step,
                    target.y + split_y * step,
                    12.0,
                )
                target.vx, target.vy = split_x * step / DT, split_y * step / DT
                self.avoidance_count += 1
            # Final emitted-pose guard: a convoy never enters any enemy hull
            # or completed containment keep-out zone. If a crowded multi-
            # threat candidate cannot maintain the safety radius this tick,
            # hold the previous safe pose and let the next scored heading find
            # another corridor. Holding is physically smooth; reporting a
            # terminal failure after an avoidable one-frame overlap is not.
            if any(
                _length(nx - hazard.x, ny - hazard.y) < (
                    containment_clearance_m
                    if (
                        hazard.state in {"CAPTURED", "SECURED"}
                        or (hazard.forced and hazard.capture_stage >= 2)
                    )
                    else TARGET_SEPARATION_M
                )
                for hazard in hazards
            ):
                previous_is_safe = all(
                    _length(target.x - hazard.x, target.y - hazard.y) >= (
                        containment_clearance_m
                        if (
                            hazard.state in {"CAPTURED", "SECURED"}
                            or (hazard.forced and hazard.capture_stage >= 2)
                        )
                        else TARGET_SEPARATION_M
                    )
                    for hazard in hazards
                )
                if previous_is_safe:
                    nx, ny = target.x, target.y
                    target.vx = target.vy = 0.0
                    self.avoidance_count += 1
            if _length(nx - target.x, ny - target.y) > 1e-5:
                target.heading = math.degrees(math.atan2(ny - target.y, nx - target.x)) % 360.0
            if not mission_resolved and not containment_conflict_active:
                # A safety displacement may pass the staging line without
                # declaring mission arrival. Clipping it back into a closed
                # ring's clearance zone deadlocks both capture and escort.
                nx, ny = self._limit_escort_approach(target, nx, ny)
            target.x, target.y = nx, ny

    def _release_surplus_containment(self) -> None:
        """Keep a 2+2 containment ring and recall surplus craft as escorts.

        A minimum 3+3 mission previously committed every platform to the
        captured hostile and finished with no visible guard beside the
        protected ship.  Four mixed members are sufficient to preserve the
        stable containment contract; the remaining UAV and USV are therefore
        released only after every incident is resolved and become the final
        convoy-support pair.
        """
        if (
            self.plan.uav_count > 3
            or self.plan.usv_count > 3
            or len(self.threats) != 1
            or not self.threats
            or not all(
                item.state in {"CAPTURED", "SECURED", "ESCAPED"}
                for item in self.threats
            )
        ):
            return
        for completed_index, completed in enumerate(self.threats):
            if completed.state not in {"CAPTURED", "SECURED"}:
                continue
            released_any = False
            for kind in ("UAV", "USV"):
                completed_members = sorted(
                    (
                        item for item in self.vehicles
                        if item.kind == kind and item.assigned_threat == completed_index
                    ),
                    key=lambda item: int(item.code.rsplit("-", 1)[-1]),
                )
                # Preserve two members of each domain in the hostile ring.
                # Prefer recalling the craft that originally guarded the
                # convoy so the final picture also remains semantically
                # stable across repeated runs.
                release_count = max(0, len(completed_members) - 2)
                candidates = sorted(
                    completed_members,
                    key=lambda item: (
                        item.code not in self._initial_convoy_guard_codes,
                        int(item.code.rsplit("-", 1)[-1]),
                    ),
                )[:release_count]
                for released in candidates:
                    released.assigned_threat = None
                    released.role = "RETURNING"
                    released.group_id = f"RECON-{released.protected_index + 1:03d}"
                    released.final_slot_angle = None
                    released_any = True
            if released_any:
                self._freeze_even_slot_angles(completed_index)

    def _freeze_even_slot_angles(self, threat_index: int) -> None:
        """Preserve the current circular order and distribute it uniformly."""
        threat = self.threats[threat_index]
        members = self._capture_members(threat_index)
        if not members:
            return
        angular = sorted(
            (
                math.atan2(item.y - threat.y, item.x - threat.x)
                % (2.0 * math.pi),
                item,
            )
            for item in members
        )
        gaps = [
            (angular[(index + 1) % len(angular)][0] - angular[index][0])
            % (2.0 * math.pi)
            for index in range(len(angular))
        ]
        start = (max(range(len(gaps)), key=gaps.__getitem__) + 1) % len(angular)
        start_angle = angular[start][0]
        for offset in range(len(angular)):
            item = angular[(start + offset) % len(angular)][1]
            item.final_slot_angle = (
                start_angle + 2.0 * math.pi * offset / len(angular)
            ) % (2.0 * math.pi)

    def _consolidate_final_containment(self) -> None:
        """Final containment is already authoritative; never reshuffle it."""
        self._final_containment_consolidated = True
        return
        # Legacy consolidation code remains unreachable during migration.
        if self._final_containment_consolidated:
            return
        captured_indices = [
            index for index, threat in enumerate(self.threats)
            if threat.state in {"CAPTURED", "SECURED"}
        ]
        if not captured_indices:
            return
        for kind in ("UAV", "USV"):
            counts = {
                index: sum(
                    item.kind == kind and item.assigned_threat == index
                    for item in self.vehicles
                )
                for index in captured_indices
            }
            candidates = [
                item for item in self.vehicles
                if item.kind == kind
                and item.role != "CLOSE_GUARD"
                and item.assigned_threat not in captured_indices
            ]
            # Four craft of each kind make an unambiguous eight-point ring.
            # Pulling every surplus patrol craft into containment congests the
            # scene and can force a distant craft to cross the protected
            # convoy. Fill only deficient rings, always with the closest free
            # responder; remaining craft continue an orderly outer patrol.
            while candidates and any(counts[index] < 4 for index in captured_indices):
                target_index = min(
                    (index for index in captured_indices if counts[index] < 4),
                    key=lambda index: counts[index],
                )
                item = min(
                    candidates,
                    key=lambda candidate: _length(
                        candidate.x - self.threats[target_index].x,
                        candidate.y - self.threats[target_index].y,
                    ),
                )
                candidates.remove(item)
                item.assigned_threat = target_index
                item.role = "CONTAINMENT"
                item.group_id = f"CAPTURE-{target_index + 1:03d}"
                counts[target_index] += 1
        for target_index in captured_indices:
            self._freeze_even_slot_angles(target_index)
        self._final_containment_consolidated = True

    def _synchronize_guard_roles(self) -> None:
        self._release_surplus_containment()
        self.defense.synchronize()

    def _choose_attack_direction(
        self,
        threat: _Threat,
        target: _Protected,
        desired_speed: float,
    ) -> tuple[float, float, str]:
        """Select an attack/flanking direction by scoring future openings."""
        horizon = 8.0
        target_future = (
            target.x + target.vx * horizon,
            target.y + target.vy * horizon,
        )
        current_heading = math.radians(threat.heading)
        defenders = [
            item for item in self.vehicles
            if item.protected_index == threat.protected_index
            and item.role in {"CLOSE_GUARD", "FORMATION_GUARD", "BLOCKER", "CONFRONT"}
        ]
        left, right, bottom, top = self.safe_bounds
        best: tuple[float, float, float] | None = None
        for sample in range(32):
            angle = 2.0 * math.pi * sample / 32.0
            dx, dy = math.cos(angle), math.sin(angle)
            future_x = threat.x + dx * desired_speed * horizon
            future_y = threat.y + dy * desired_speed * horizon
            target_distance = _length(future_x - target_future[0], future_y - target_future[1])
            defender_clearance = min(
                (_length(future_x - item.x, future_y - item.y) for item in defenders),
                default=80.0,
            )
            shore_clearance = min(
                future_x - left, right - future_x,
                future_y - bottom, top - future_y,
            )
            turn_cost = abs((angle - current_heading + math.pi) % (2.0 * math.pi) - math.pi)
            direct_x, direct_y = _unit(target_future[0] - threat.x, target_future[1] - threat.y)
            attack_alignment = dx * direct_x + dy * direct_y
            # Before interception the enemy is an attacker, not an evader.
            # Defender clearance may choose a flank, but must not outweigh
            # visible progress toward the protected vessel.
            score = (
                -target_distance * 1.72
                + attack_alignment * 38.0
                + min(70.0, defender_clearance) * 0.28
                + min(45.0, shore_clearance) * 0.46
                - turn_cost * 3.2
            )
            if attack_alignment < 0.12:
                score -= 85.0
            if best is None or score > best[0]:
                best = (score, dx, dy)
        assert best is not None
        direct_x, direct_y = _unit(target_future[0] - threat.x, target_future[1] - threat.y)
        alignment = best[1] * direct_x + best[2] * direct_y
        intent = "ATTACKING" if alignment >= 0.72 else "FLANKING"
        return best[1], best[2], intent

    def _advance_threats(self) -> None:
        active = sum(item.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"} for item in self.threats)
        for index, threat in enumerate(self.threats):
            # CAPTURED is persistent containment.  The reference three-in-one
            # algorithm keeps the final GB-SFLA-CS waypoints after success;
            # releasing the group after 120 frames made the completed scene
            # dissolve into an unrelated recon pattern.
            if threat.state == "WAITING" and self.sequence >= threat.activate_frame and active < self.plan.simultaneous_threats:
                threat.state, active = "APPROACHING", active + 1
                target = self.protected[threat.protected_index]
                start_x, start_y = _unit(target.x - threat.x, target.y - threat.y)
                threat.vx = start_x * threat.cruise_speed
                threat.vy = start_y * threat.cruise_speed
            if threat.state in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}:
                threat.vx = threat.vy = 0.0
                continue
            target = self.protected[threat.protected_index]
            dx, dy = target.x - threat.x, target.y - threat.y
            distance = max(1e-6, _length(dx, dy))
            if (
                threat.detected_frame is None
                and self._observe_attack_intent(threat, target, distance)
            ):
                threat.detected_frame, threat.state = self.sequence, "DETECTED"
                threat.intent = "ATTACK_INTENT"
                threat.mission_stage = "THREAT_DETECTION"
                threat.attack_start_distance = distance
                threat.closest_attack_distance = distance
                self._emit_tactical_event(
                    "ATTACK_INTENT_CONFIRMED",
                    threat,
                    "识别到攻击意图",
                    "持续逼近且预测交会，正在重构守卫队形并调度混合拦截组。",
                )
                self._start_parallel_response()
            desired_x, desired_y = _unit(dx, dy)
            # Enemy motion is not derived from the friendly slider.  Each
            # threat owns a seeded 1.5-2.2 m/s cruise speed and may make a
            # bounded 2.8 m/s burst when defenders close in.
            desired_speed = threat.cruise_speed
            threat.slowdown_reason = "NONE"
            if threat.detected_frame is not None and not threat.forced:
                threat.closest_attack_distance = min(threat.closest_attack_distance, distance)
                blockers = [
                    item for item in self.vehicles
                    if item.code not in self._unavailable_codes
                    and item.role == "BLOCKER" and item.group_id == f"BLOCK-{index + 1:03d}"
                ]
                intercepted = False
                risk, tti, closing = self._threat_risk(threat)
                defensive_intercept = distance < BREACH_DISTANCE_M + 6.0
                # An approaching hostile cannot be allowed to shadow the
                # convoy forever while a distant screen member is still
                # arranging itself. Emergency interception depends on an
                # actual blocking hull, not completion of the entire group.
                if blockers and (threat.cover_released or defensive_intercept):
                    line_x, line_y = threat.x - target.x, threat.y - target.y
                    line_length = max(1e-6, _length(line_x, line_y))
                    line_x, line_y = line_x / line_length, line_y / line_length
                    for blocker in blockers:
                        rel_x, rel_y = blocker.x - target.x, blocker.y - target.y
                        along = rel_x * line_x + rel_y * line_y
                        lateral = abs(rel_x * line_y - rel_y * line_x)
                        if (line_length * 0.18 <= along <= line_length
                                and lateral <= INTERCEPT_LATERAL_M
                                and _length(blocker.x - threat.x, blocker.y - threat.y) <= INTERCEPT_DISTANCE_M):
                            intercepted = True
                            break
                if self.sequence < threat.breach_until_frame:
                    # The hostile has committed to a scored flank. Give the
                    # manoeuvre a visible window before the second defensive
                    # line is allowed to establish another hold.
                    intercepted = False
                threat.intercept_hold_frames = (
                    threat.intercept_hold_frames + 1
                    if intercepted
                    else max(0, threat.intercept_hold_frames - 1)
                )
                if intercepted and threat.mission_stage in {
                    "THREAT_DETECTION", "GUARD_RECONFIGURATION", "INTERCEPT"
                }:
                    threat.mission_stage = "BLOCKING"
                nearby_defenders = sum(
                    _length(item.x - threat.x, item.y - threat.y) <= 46.0
                    for item in self.vehicles
                    if item.role in {"CLOSE_GUARD", "BLOCKER", "CONFRONT"}
                    and item.protected_index == threat.protected_index
                )
                _, tti, closing = self._threat_risk(threat)
                urgent_intercept = (
                    (tti <= URGENT_TTI_SECONDS or distance <= URGENT_DISTANCE_M)
                    and closing > 0.05
                    and nearby_defenders >= 2
                )
                required_intercept_hold = (
                    URGENT_INTERCEPT_HOLD_FRAMES
                    if urgent_intercept
                    else INTERCEPT_HOLD_FRAMES
                )
                if threat.intercept_hold_frames >= required_intercept_hold:
                    # A seeded, capacity-aware first-line outcome makes the
                    # escort scenario a real attack/defence exchange.  A
                    # bounded subset of runs breaches the first line, but a
                    # second established intercept always transitions to
                    # pursuit and containment.
                    breach_roll = random.Random(
                        self.seed * 1009 + index * 97 + threat.intercept_attempts * 7919
                    ).random()
                    enough_water = distance > BREACH_DISTANCE_M + 38.0
                    first_line_breach = (
                        threat.intercept_attempts == 0
                        and enough_water
                        and threat.cover_released
                        and breach_roll < 0.32
                    )
                    threat.intercept_attempts += 1
                    threat.intercept_hold_frames = 0
                    if first_line_breach:
                        threat.breach_until_frame = self.sequence + 70
                        threat.state = "BREACHING"
                        threat.intent = "FLANKING_BREAKTHROUGH"
                    else:
                        self._start_capture_for([threat], "INTERCEPT_ESTABLISHED"
                                                if threat.cover_released else "DEFENSIVE_SCREEN_INTERCEPT")
                elif distance < BREACH_DISTANCE_M + 6.0:
                    # Last-resort safety transition if an unusually sparse
                    # fleet cannot occupy the formal block point in time.
                    self._start_capture_for([threat], "EMERGENCY_BREACH_PREVENTION")
            if threat.forced:
                if not self._capture_members(index):
                    self._assign_capture_group(threat)
                members = self._capture_members(index)
                pursuit_distance = self._pursuit_distance(threat)
                pursuit_run = threat.capture_stage == 0
                threat.state = (
                    "ESCAPE_PURSUIT"
                    if pursuit_run
                    else "ENCIRCLING"
                    if threat.capture_hold
                    else "INTERCEPTING"
                )
                if members:
                    cx = sum(item.x for item in members) / len(members)
                    cy = sum(item.y for item in members) / len(members)
                    escape_x, escape_y = _unit(threat.x - cx, threat.y - cy, (desired_x, desired_y))
                    nearest = min(members, key=lambda item: _length(item.x - threat.x, item.y - threat.y))
                    avoid_x, avoid_y = _unit(threat.x - nearest.x, threat.y - nearest.y, (escape_x, escape_y))
                    nearest_distance = _length(nearest.x - threat.x, nearest.y - threat.y)
                    if pursuit_run:
                        # Hold a coherent escape corridor long enough for the
                        # chase to be visible. Nearest-craft avoidance is only
                        # a small bias, so the enemy cannot spin in place.
                        desired_x, desired_y = _unit(
                            threat.escape_dir_x * 0.90 + avoid_x * 0.16,
                            threat.escape_dir_y * 0.90 + avoid_y * 0.16,
                        )
                        pressure = max(0.0, min(1.0, (70.0 - nearest_distance) / 46.0))
                        desired_speed = min(
                            threat.maximum_speed,
                            max(
                                threat.cruise_speed + pressure * 0.6,
                                _length(target.vx, target.vy) + 0.45,
                            ),
                        )
                        threat.intent = (
                            "ESCAPE_INTENT"
                            if threat.escape_intent_confirmed
                            else "ESCAPING"
                        )
                    else:
                        side = 1.0 if index % 2 == 0 else -1.0
                        desired_x, desired_y = _unit(
                            escape_x * 0.62 + avoid_x * 0.58 - escape_y * 0.28 * side,
                            escape_y * 0.62 + avoid_y * 0.58 + escape_x * 0.28 * side,
                        )
                        if threat.capture_hold > 5:
                            # Deceleration belongs exclusively to a real ring
                            # that is currently passing the geometric hold
                            # contract. A travelled-distance or stage flag can
                            # no longer make an uncontained enemy stop early.
                            hold_ratio = min(
                                1.0,
                                (threat.capture_hold - 5)
                                / max(1, CAPTURE_HOLD_FRAMES - 5),
                            )
                            desired_speed = max(
                                0.08,
                                threat.cruise_speed * (1.0 - 0.94 * hold_ratio),
                            )
                            threat.slowdown_reason = "CONTAINMENT_DECEL"
                        else:
                            # Slot error alone is a poor pressure signal while a
                            # moving target is still trying to break out: a
                            # defender can already occupy the correct angular
                            # sector but trail its translating slot by several
                            # metres.  Measure the physical cage as well.  The
                            # hostile yields speed only after at least three
                            # quarters of its assigned team are close and those
                            # craft cover the target from both sides (no open
                            # half-plane).  This lets a low-cruise 3+3 team finish
                            # closing without making an uncontained target stop.
                            close_members = [
                                member for member in members
                                if _length(member.x - threat.x, member.y - threat.y) <= 48.0
                            ]
                            close_ratio = len(close_members) / max(1, len(members))
                            close_angles = sorted(
                                math.atan2(member.y - threat.y, member.x - threat.x)
                                % (2.0 * math.pi)
                                for member in close_members
                            )
                            close_max_gap_deg = 360.0
                            if len(close_angles) >= 3:
                                close_max_gap_deg = math.degrees(max(
                                    (
                                        close_angles[(position + 1) % len(close_angles)]
                                        - close_angles[position]
                                    ) % (2.0 * math.pi)
                                    for position in range(len(close_angles))
                                ))
                            geometric_pressure = (
                                threat.capture_stage >= 2
                                and close_ratio >= 0.75
                                and close_max_gap_deg <= 180.0
                            )
                            if (
                                threat.capture_stage >= 2
                                and (
                                    threat.capture_arrival_ratio >= 0.65
                                    or geometric_pressure
                                )
                            ):
                                # The enemy only yields speed after the defenders
                                # have already occupied most of a real ring. This
                                # is geometric containment pressure, not a timer
                                # or a travelled-distance shortcut.
                                slot_pressure = max(
                                    0.0,
                                    (threat.capture_arrival_ratio - 0.65) / 0.35,
                                )
                                cage_pressure = max(
                                    0.0,
                                    min(
                                        1.0,
                                        (close_ratio - 0.75) / 0.25
                                        + (180.0 - close_max_gap_deg) / 180.0,
                                    ),
                                )
                                pressure = min(1.0, max(slot_pressure, cage_pressure))
                                # Once seven eighths of the team occupy a
                                # geometrically closed cage, let the hostile
                                # slow enough for the final translating USV
                                # slot to settle. Before that point the higher
                                # floor preserves an active escape attempt.
                                final_closure = (
                                    threat.capture_arrival_ratio >= 0.875
                                    and close_max_gap_deg <= 90.0
                                )
                                speed_floor = (
                                    max(0.45, threat.cruise_speed * 0.35)
                                    if final_closure
                                    else 1.05
                                )
                                desired_speed = max(
                                    speed_floor,
                                    threat.cruise_speed * (1.0 - 0.58 * pressure),
                                )
                                threat.slowdown_reason = "CONTAINMENT_PRESSURE"
                            else:
                                pressure = max(0.0, min(1.0, (70.0 - nearest_distance) / 46.0))
                                desired_speed = min(
                                    threat.maximum_speed,
                                    threat.cruise_speed + pressure * 0.6,
                                )
                        threat.intent = "BREAKOUT"
            elif threat.detected_frame is not None:
                desired_x, desired_y, threat.intent = self._choose_attack_direction(
                    threat,
                    target,
                    desired_speed,
                )
                in_breach_window = self.sequence < threat.breach_until_frame
                threat.state = (
                    "BREACHING"
                    if in_breach_window
                    else "BLOCKED" if threat.intercept_hold_frames > 0 else threat.intent
                )
                blocker = next((item for item in self.vehicles if item.role == "BLOCKER" and item.group_id == f"BLOCK-{index + 1:03d}"), None)
                if blocker is not None and _length(blocker.x - threat.x, blocker.y - threat.y) < 42.0:
                    bx, by = _unit(threat.x - blocker.x, threat.y - blocker.y)
                    side = 1.0 if index % 2 == 0 else -1.0
                    desired_x, desired_y = _unit(
                        desired_x * 0.62 + bx * 0.55 - desired_y * side * 0.32,
                        desired_y * 0.62 + by * 0.55 + desired_x * side * 0.32,
                    )
                    desired_speed = min(
                        threat.maximum_speed,
                        max(
                            threat.cruise_speed,
                            threat.cruise_speed + (0.55 if in_breach_window else 0.25),
                        ),
                    )
                    threat.state = "BREACHING" if in_breach_window else "FLANKING"
            if not threat.forced and not self.defense.released:
                desired_x, desired_y, desired_speed = self.defense.approach(threat)
            # Predict guard/hostile closest approach before the final hard
            # safety pass.  The old resolver treated a hostile as a fixed
            # obstacle and therefore made the friendly USV yield after the
            # tactical decision had already been made.  Here the hostile owns
            # the larger avoidance share: it visibly turns toward a free flank
            # while the blocker keeps its intercept corridor.
            surface_defenders = [
                item for item in self.vehicles
                if item.kind == "USV"
                and item.code not in self._unavailable_codes
                and (
                    item.role in {
                        "CLOSE_GUARD", "BLOCKER", "GAP_BLOCKER",
                        "INTERCEPTOR", "CAPTURE", "CONTAINMENT",
                    }
                    or item.assigned_threat == index
                )
            ]
            nearest_defender = min(
                surface_defenders,
                key=lambda item: _length(item.x - threat.x, item.y - threat.y),
                default=None,
            )
            threat.nearest_defender_code = "" if nearest_defender is None else nearest_defender.code
            threat.nearest_defender_distance = (
                math.inf
                if nearest_defender is None
                else _length(nearest_defender.x - threat.x, nearest_defender.y - threat.y)
            )
            coordinating_attack = not threat.forced and not self.defense.released
            if coordinating_attack:
                threat.state = "ATTACKING" if threat.detected_frame is not None else "APPROACHING"
                threat.intent = "ATTACK_INTENT" if threat.detected_frame is not None else "APPROACHING"
                if self._shore_assembly_pending and threat.detected_frame is None:
                    threat.state = threat.intent = "PATROLLING"
            if coordinating_attack and nearest_defender is not None:
                # An occupied approach lane causes physical braking. Do not
                # switch to random flanking while the initial screen aligns.
                desired_speed = min(desired_speed, max(0.0, (threat.nearest_defender_distance - 25.0) * .3))
                threat.slowdown_reason = "DEFENSIVE_SCREEN" if desired_speed < .5 else "NONE"
            if (
                nearest_defender is not None
                and not coordinating_attack
                and threat.capture_hold <= 0
                and threat.slowdown_reason != "CONTAINMENT_PRESSURE"
            ):
                hostile_vx, hostile_vy = desired_x * desired_speed, desired_y * desired_speed
                relative_x = nearest_defender.x - threat.x
                relative_y = nearest_defender.y - threat.y
                relative_vx = nearest_defender.vx - hostile_vx
                relative_vy = nearest_defender.vy - hostile_vy
                relative_speed_sq = relative_vx * relative_vx + relative_vy * relative_vy
                closest_time = (
                    0.0
                    if relative_speed_sq < 1e-6
                    else max(
                        0.0,
                        min(
                            4.0,
                            -(relative_x * relative_vx + relative_y * relative_vy)
                            / relative_speed_sq,
                        ),
                    )
                )
                closest_x = relative_x + relative_vx * closest_time
                closest_y = relative_y + relative_vy * closest_time
                closest_distance = _length(closest_x, closest_y)
                if (
                    threat.nearest_defender_distance <= 52.0
                    and closest_distance < 24.0
                ):
                    away_x, away_y = _unit(
                        threat.x - nearest_defender.x,
                        threat.y - nearest_defender.y,
                        (-desired_y, desired_x),
                    )
                    side_seed = sum(ord(char) for char in threat.code + nearest_defender.code)
                    side = 1.0 if (self.seed + side_seed) % 2 == 0 else -1.0
                    tangent_x, tangent_y = -away_y * side, away_x * side
                    urgency = min(
                        1.0,
                        max(
                            0.25,
                            (24.0 - closest_distance) / 24.0
                            + (52.0 - threat.nearest_defender_distance) / 80.0,
                        ),
                    )
                    desired_x, desired_y = _unit(
                        desired_x * (1.0 - urgency * 0.72)
                        + away_x * urgency * 0.72
                        + tangent_x * urgency * 0.52,
                        desired_y * (1.0 - urgency * 0.72)
                        + away_y * urgency * 0.72
                        + tangent_y * urgency * 0.52,
                    )
                    desired_speed = min(
                        threat.maximum_speed,
                        max(threat.cruise_speed, desired_speed + urgency * 0.35),
                    )
                    threat.intent = "EVADING_GUARD" if threat.forced else "FLANKING"
                    threat.state = threat.intent
                    threat.intent_confidence = min(0.96, 0.66 + urgency * 0.30)
                else:
                    threat.intent_confidence = max(0.55, threat.intent_confidence * 0.96)
            future_dx = threat.x + desired_x * desired_speed * 2.0 - target.x - target.vx * 2.0
            future_dy = threat.y + desired_y * desired_speed * 2.0 - target.y - target.vy * 2.0
            if _length(future_dx, future_dy) < TARGET_SEPARATION_M * 1.35:
                away_x, away_y = _unit(threat.x - target.x, threat.y - target.y)
                side = 1.0 if index % 2 == 0 else -1.0
                desired_x, desired_y = _unit(away_x - away_y * side, away_y + away_x * side)
            pursuit_run = threat.forced and threat.capture_stage == 0
            if threat.forced and not pursuit_run:
                nearest_protected = min(
                    self.protected,
                    key=lambda protected: _length(
                        threat.x - protected.x, threat.y - protected.y,
                    ),
                )
                protected_clearance = _length(
                    threat.x - nearest_protected.x,
                    threat.y - nearest_protected.y,
                )
                ring_nearly_closed = (
                    threat.capture_stage >= 2
                    and threat.capture_arrival_ratio >= 0.875
                    and threat.capture_max_gap_deg <= 75.0
                )
                if (
                    protected_clearance < CONTAINMENT_STANDOFF_M + 4.0
                    and not ring_nearly_closed
                ):
                    # A final 27 m ring plus hull separation cannot physically
                    # fit beside a convoy only 34 m away. Open a visible safety
                    # corridor first; otherwise the protected hull blocks one
                    # or more canonical slots and no amount of slot rotation
                    # can close the circle.
                    away_x, away_y = _unit(
                        threat.x - nearest_protected.x,
                        threat.y - nearest_protected.y,
                    )
                    side = 1.0 if index % 2 == 0 else -1.0
                    desired_x, desired_y = _unit(
                        away_x * 0.94 - away_y * 0.22 * side,
                        away_y * 0.94 + away_x * 0.22 * side,
                    )
                    # The convoy may travel almost as fast as the hostile's
                    # cruise profile. A fixed cruise floor then preserves the
                    # unsafe separation forever. Open the corridor with a
                    # bounded relative-speed advantage before trying to settle
                    # the final ring.
                    desired_speed = min(
                        threat.maximum_speed,
                        max(
                            threat.cruise_speed,
                            desired_speed,
                            _length(nearest_protected.vx, nearest_protected.vy) + 0.55,
                        ),
                    )
                    threat.slowdown_reason = "NONE"
                    threat.intent = "CLEARING_CONVOY"
            capture_inset = 30.0 if pursuit_run else 38.0 if threat.forced else 8.0
            water_left = self.safe_bounds[0] + capture_inset
            water_right = self.safe_bounds[1] - capture_inset
            water_bottom = self.safe_bounds[2] + capture_inset
            water_top = self.safe_bounds[3] - capture_inset
            shore_x = (1.0 if threat.x < water_left + 10.0 else 0.0) - (1.0 if threat.x > water_right - 10.0 else 0.0)
            shore_y = (1.0 if threat.y < water_bottom + 10.0 else 0.0) - (1.0 if threat.y > water_top - 10.0 else 0.0)
            if shore_x or shore_y:
                desired_x, desired_y = _unit(desired_x + shore_x * 1.35, desired_y + shore_y * 1.35)
            # Hostile targets also occupy water area. The vehicle resolver only
            # separates friendly proposals from fixed targets, so two moving
            # threats (or one moving threat and an already-contained one) used
            # to pass through each other at larger fleet sizes. Predict two
            # seconds ahead and choose opposite seeded passing sides early.
            separation_x = separation_y = 0.0
            for other_index, other in enumerate(self.threats):
                if other is threat or other.state in {"WAITING", "ESCAPED"}:
                    continue
                other_future_x = other.x + other.vx * 2.0
                other_future_y = other.y + other.vy * 2.0
                future_x = threat.x + desired_x * desired_speed * 2.0
                future_y = threat.y + desired_y * desired_speed * 2.0
                clearance = _length(future_x - other_future_x, future_y - other_future_y)
                current_clearance = _length(threat.x - other.x, threat.y - other.y)
                # A 38 m hull-to-hull threshold only prevents the target
                # centres from colliding; it cannot fit two 27 m final rings.
                # When either incident is already forming/holding a ring, keep
                # the centres far enough apart for both sets of slots plus the
                # safety resolver's target exclusion radius.  Otherwise a
                # perfectly valid slot can land inside a captured target's
                # keep-out zone and one pursuer will appear permanently dead.
                containment_nearby = (
                    other.state in {"CAPTURED", "SECURED", "STABLE_CONTAINMENT"}
                    or threat.capture_stage >= 1
                    or other.capture_stage >= 1
                )
                required_clearance = 76.0 if containment_nearby else 48.0
                if min(clearance, current_clearance) >= required_clearance:
                    continue
                away_x, away_y = _unit(
                    threat.x - other.x,
                    threat.y - other.y,
                    (0.0, 1.0 if index < other_index else -1.0),
                )
                side = 1.0 if index < other_index else -1.0
                urgency = 1.0 + max(
                    0.0,
                    (required_clearance - min(clearance, current_clearance))
                    / required_clearance,
                )
                separation_x += (away_x - away_y * 0.55 * side) * urgency
                separation_y += (away_y + away_x * 0.55 * side) * urgency
            if abs(separation_x) + abs(separation_y) > 1e-6:
                desired_x, desired_y = _unit(
                    desired_x * 0.52 + separation_x * 0.92,
                    desired_y * 0.52 + separation_y * 0.92,
                )
                desired_speed = max(threat.cruise_speed, desired_speed)
                threat.slowdown_reason = "NONE"
            if threat.forced and not pursuit_run and threat.capture_stage >= 1:
                clearance_direction = self._choose_containment_clearance_direction(
                    threat,
                    index,
                    (desired_x, desired_y),
                )
                if clearance_direction is not None:
                    desired_x, desired_y = clearance_direction
                    protected_speed = max(
                        (_length(item.vx, item.vy) for item in self.protected),
                        default=0.0,
                    )
                    desired_speed = min(
                        threat.maximum_speed,
                        max(threat.cruise_speed, protected_speed + 0.55),
                    )
                    threat.slowdown_reason = "NONE"
                    threat.intent = "CLEARING_CONTAINMENT_SPACE"
            # Steer heading and scalar speed independently.  Interpolating vx
            # and vy separately forced the resultant speed through zero on a
            # large turn even when the hostile still requested cruise speed.
            # This controller preserves visible intent and applies bounded
            # turn/acceleration rates at every fleet size.
            current_speed = _length(threat.vx, threat.vy)
            current_heading = (
                math.atan2(threat.vy, threat.vx)
                if current_speed > 0.05
                else math.radians(threat.heading)
            )
            desired_heading = math.atan2(desired_y, desired_x)
            heading_error = (
                desired_heading - current_heading + math.pi
            ) % (2.0 * math.pi) - math.pi
            maximum_turn = math.radians(7.0)
            next_heading = current_heading + max(
                -maximum_turn, min(maximum_turn, heading_error)
            )
            containment_decelerating = (
                threat.slowdown_reason
                in {"CONTAINMENT_PRESSURE", "CONTAINMENT_DECEL"}
            )
            if not containment_decelerating and not coordinating_attack:
                threat.slowdown_reason = "NONE"
                desired_speed = max(threat.cruise_speed, desired_speed)
            speed_delta_limit = 0.10 if desired_speed < current_speed else 0.08
            next_speed = current_speed + max(
                -speed_delta_limit,
                min(speed_delta_limit, desired_speed - current_speed),
            )
            if not containment_decelerating and not coordinating_attack:
                next_speed = max(threat.cruise_speed * 0.90, next_speed)
            next_speed = min(threat.maximum_speed, max(0.0, next_speed))
            if threat.forced:
                next_heading, next_speed = self._safe_escape_velocity(
                    threat, next_heading, next_speed, current_heading,
                )
            threat.vx = math.cos(next_heading) * next_speed
            threat.vy = math.sin(next_heading) * next_speed
            # Inset changes are planning constraints, never position commands.
            # A 30 -> 38 m containment margin previously teleported targets
            # sideways by up to 8 m in one 0.1 s frame. Only integrate the bow
            # velocity; if the reachable step is unsafe, stop at the current
            # pose and steer on subsequent frames instead of projecting it.
            raw_next_x = threat.x + threat.vx * DT
            raw_next_y = threat.y + threat.vy * DT
            nx, ny = raw_next_x, raw_next_y
            current_shore = min(threat.x - self.safe_bounds[0], self.safe_bounds[1] - threat.x,
                                threat.y - self.safe_bounds[2], self.safe_bounds[3] - threat.y)
            reachable_inset = min(capture_inset, max(0.5, current_shore))
            projected = self._project_to_safe_water(nx, ny, reachable_inset)
            boundary_blocked = _length(nx - projected[0], ny - projected[1]) > 1e-5
            current_clearance = self._distance_to_protected(threat)
            corridor_blocked = _length(nx - target.x, ny - target.y) < min(
                TARGET_SEPARATION_M, current_clearance,
            ) - 1e-5
            if boundary_blocked or corridor_blocked:
                nx, ny = threat.x, threat.y
                threat.vx = threat.vy = 0.0
                threat.slowdown_reason = "ESCAPE_CORRIDOR_BLOCKED"
                self.avoidance_count += 1
                if boundary_blocked:
                    threat.escape_dir_x, threat.escape_dir_y = self._choose_escape_direction(threat)
            threat.heading = math.degrees(next_heading) % 360.0
            movement_x, movement_y = nx - threat.x, ny - threat.y
            threat.travelled_distance += _length(movement_x, movement_y)
            threat.x, threat.y = nx, ny
            actual_distance = _length(threat.x - target.x, threat.y - target.y)
            if threat.forced and pursuit_run and not threat.escape_intent_confirmed:
                # Escape intent is relative to the selected open-water escape
                # corridor and defender pressure. The protected convoy can be
                # travelling in the same direction, so centre distance alone
                # would incorrectly label a real evasive turn as continued
                # attack.
                moving_away = (
                    movement_x * (threat.x - target.x)
                    + movement_y * (threat.y - target.y)
                    >= max(1.0, actual_distance) * 0.02
                )
                threat.escape_intent_hold_frames = (
                    min(ESCAPE_INTENT_HOLD_FRAMES, threat.escape_intent_hold_frames + 1)
                    if moving_away
                    else max(0, threat.escape_intent_hold_frames - 1)
                )
                if threat.escape_intent_hold_frames >= ESCAPE_INTENT_HOLD_FRAMES:
                    threat.escape_intent_confirmed = True
                    threat.intent = "ESCAPE_INTENT"
                    threat.intent_confidence = max(0.88, threat.intent_confidence)
                    self._emit_tactical_event(
                        "ESCAPE_INTENT_CONFIRMED",
                        threat,
                        "识别到逃逸意图",
                        "连续远离护航目标，已由阻断切换为预测追击与动态围捕。",
                    )
            # A detected attacker remains part of the incident until it is
            # intercepted and contained. Marking it ESCAPED merely because the
            # protected vessel temporarily opened the distance allowed the
            # mission to finish without demonstrating the required capture.
            threat.previous_distance = actual_distance

    def _capture_slots(self, members: Sequence[_Vehicle], threat: _Threat) -> list[FormationSlot]:
        ordered = sorted(
            members,
            key=lambda item: (int(item.code.rsplit("-", 1)[-1]), 0 if item.kind == "UAV" else 1),
        )
        # capture_stage >= 1 is the latched tactical hand-off.  A parallel
        # team may legitimately reach the predictive fan before the attacker
        # has travelled the legacy fixed distance; once assessment promotes
        # that incident, immediately replace the 112-degree chase fan with
        # canonical 360-degree ring slots.  Keying this only to the odometer
        # left every craft "100% arrived" on the wrong side of the target.
        pursuit_run = threat.capture_stage == 0
        base_radius = (58.0, 41.0, 27.0)[min(2, threat.capture_stage)]
        # Slot identities stay stable while the centre moves. Rotating every
        # slot with a manoeuvring enemy makes pursuers chase a spinning goal
        # and prevents the ring from ever closing.
        phase = threat.capture_phase
        by_code: dict[str, FormationSlot] = {}
        threat_index = self.threats.index(threat)
        if not pursuit_run:
            ring_members = [
                RingMember(item.code, item.kind, item.x, item.y, item.z)
                for item in ordered
            ]
            cached = self._ring_slots.get(threat_index, {})
            if set(cached) != {item.code for item in ordered}:
                cached = build_canonical_slots(
                    ring_members,
                    (threat.x, threat.y, 0.0),
                    phase=phase,
                    minimum_spacing_m=14.0,
                )
                self._ring_slots[threat_index] = cached
        for index, item in enumerate(ordered):
            if pursuit_run:
                # Chase in a trailing fan; real arrival and observed escape
                # trigger the full ring, not an elapsed-time deadline.
                centre = math.atan2(threat.escape_dir_y, threat.escape_dir_x) + math.pi
                spread = math.radians(112.0)
                angle = centre if len(ordered) == 1 else centre - spread / 2.0 + spread * index / (len(ordered) - 1)
                radius = 38.0 + (index % 2) * 9.0 + (3.5 if item.kind == "UAV" else 0.0)
            else:
                canonical = self._ring_slots[threat_index][item.code]
                angle = canonical.angle
                # UAV altitude already separates the two vehicle types.  A
                # shared horizontal radius produces one readable circle
                # instead of two offset, visually broken rings.
                radius = canonical.radius
            by_code[item.code] = FormationSlot(
                radius,
                angle,
                canonical.altitude if not pursuit_run else 25.0 + (index % 3) * 2.5 if item.kind == "UAV" else 0.0, 0,
            )
        return [by_code[item.code] for item in members]

    def _capture_radius_limit(self, members: Sequence[_Vehicle]) -> float:
        """Return the executed outer radius allowed by the shared contract."""
        if not members:
            return 0.0
        threat = self.threats[members[0].assigned_threat] if members[0].assigned_threat is not None else None
        if threat is None:
            return 0.0
        return max(slot.radius for slot in self._capture_slots(members, threat)) + 15.0

    def _live_containment(self, threat_index: int) -> dict[str, object]:
        """Assess the ring that is visible now, never a historical latch."""
        threat = self.threats[threat_index]
        members = self._capture_members(threat_index)
        if not members:
            contract = assess_containment(
                [],
                (threat.x, threat.y, 0.0),
                required_count=3,
                device_types=[],
                minimum_type_counts={"UAV": 1, "USV": 1},
                minimum_radius_m=18.0,
                maximum_radius_m=72.0,
                maximum_radial_spread_m=6.0,
                minimum_pairwise_separation_m=7.1,
                participating=0,
                tolerance_deg=1.0,
            )
            canonical = assess_canonical_ring(
                [], (threat.x, threat.y, 0.0), {},
                slot_tolerance_m=3.5,
                minimum_separation_m=7.0,
            )
            return {
                "members": members, "ready": False, "arrivalRatio": 0.0,
                "maxGapDeg": 360.0, "radialErrorM": math.inf,
                "contract": contract, "canonicalContract": canonical,
            }
        slots = self._capture_slots(members, threat)
        center_x, center_y = self._capture_center(threat, members)
        tolerance = 3.5 if threat.capture_stage >= 2 else 12.0
        errors = [
            _length(
                item.x - (center_x + math.cos(slot.angle) * slot.radius),
                item.y - (center_y + math.sin(slot.angle) * slot.radius),
            )
            for item, slot in zip(members, slots)
        ]
        participating = sum(error <= tolerance for error in errors)
        angles = sorted(
            math.atan2(item.y - threat.y, item.x - threat.x) % (2.0 * math.pi)
            for item in members
        )
        max_gap_deg = math.degrees(max(
            (angles[(index + 1) % len(angles)] - angles[index])
            % (2.0 * math.pi)
            for index in range(len(angles))
        ))
        contract = assess_containment(
            [(item.x, item.y, item.z) for item in members],
            (threat.x, threat.y, 0.0),
            required_count=len(members),
            device_types=[item.kind for item in members],
            minimum_type_counts={"UAV": 1, "USV": 1},
            minimum_radius_m=18.0 if threat.capture_stage >= 2 else 13.5,
            maximum_radius_m=self._capture_radius_limit(members),
            maximum_radial_spread_m=6.0 if threat.capture_stage >= 2 else 18.0,
            minimum_pairwise_separation_m=7.1,
            participating=participating,
            tolerance_deg=1.0,
        )
        slot_map = {
            item.code: RingSlot(
                index=index,
                angle=slot.angle,
                radius=slot.radius,
                altitude=slot.altitude,
            )
            for index, (item, slot) in enumerate(zip(members, slots))
        }
        canonical = assess_canonical_ring(
            [
                RingMember(
                    item.code, item.kind, item.x, item.y, item.z,
                    self._stable_headings.get(item.code),
                )
                for item in members
            ],
            (threat.x, threat.y, 0.0),
            slot_map,
            slot_tolerance_m=3.5,
            minimum_separation_m=7.0,
            require_inward_usv_heading=True,
            usv_heading_tolerance_deg=3.0,
        )
        return {
            "members": members,
            "ready": bool(contract.ready and canonical.ready
                          and not any(item.code in self._unavailable_codes for item in members)),
            "arrivalRatio": canonical.arrival_ratio,
            "maxGapDeg": canonical.maximum_gap_deg,
            "radialErrorM": canonical.maximum_slot_error_m,
            "contract": contract,
            "canonicalContract": canonical,
        }

    def _capture_center(self, threat: _Threat, members: Sequence[_Vehicle]) -> tuple[float, float]:
        if not members:
            return threat.x, threat.y
        mean_distance = sum(_length(item.x - threat.x, item.y - threat.y) for item in members) / len(members)
        mean_surface_speed = max(
            0.4,
            sum((self.uav_cruise if item.kind == "UAV" else self.usv_cruise) for item in members) / len(members),
        )
        # Predict far ahead only during interception. The final ring is always
        # centred on the live target so it cannot end up "capturing air".
        stage_factor = (1.0, 0.45, 0.0)[min(2, threat.capture_stage)]
        # Every moving slot needs one simulation tick of velocity feed-forward.
        # Without it a surface craft that is nominally at its slot is always
        # commanded to the enemy's previous position, so sparse 2+2 rings keep
        # a permanent 12-15 degree wake-side gap.  Interception adds the longer
        # predictive horizon; final containment keeps only this one-tick term.
        lookahead = DT + min(5.0, max(0.0, mean_distance / mean_surface_speed * 0.13)) * stage_factor
        return self._project_to_safe_water(
            threat.x + threat.vx * lookahead,
            threat.y + threat.vy * lookahead,
            32.0,
        )

    def _desired_position(self, item: _Vehicle) -> tuple[float, float, float]:
        defense_goal = self.defense.goal(item)
        if defense_goal is not None:
            return defense_goal
        cover = self._cover_threat(item)
        if cover is not None and item.code in cover.cover_slots:
            x, y = cover.cover_slots[item.code]
            return x, y, item.z
        if item.assigned_threat is not None:
            threat = self.threats[item.assigned_threat]
            members = self._capture_members(item.assigned_threat)
            slots = self._capture_slots(members, threat)
            item.role = (
                "CONTAINMENT" if threat.state == "CAPTURED"
                else "GAP_BLOCKER" if item.code == threat.gap_filler_code
                else "CAPTURE" if threat.capture_stage >= 1
                else "INTERCEPTOR"
            )
            center_x, center_y = self._capture_center(threat, members)
            slot = slots[members.index(item)]
            # The gap centre is an intent cue, not a replacement slot. The
            # selected craft accelerates to its stable angular slot; steering
            # it directly to the midpoint can merely move the opening to the
            # opposite side in sparse 2+2 groups.
            final_slot = slot.point((center_x, center_y, 0.0))
            if threat.capture_stage == 0:
                # A predictive pursuit fan is intentionally wider than the
                # final ring. Near a shoreline its outer point can fall beyond
                # the per-agent navigable margin even though the predicted
                # centre itself is valid. Project only that individual chase
                # point back into reachable water; moving the entire centre
                # inward slows every responder and delays large-fleet closure.
                reachable_x, reachable_y = self._project_to_safe_water(
                    final_slot[0], final_slot[1], 6.0,
                )
                final_slot = reachable_x, reachable_y, final_slot[2]
            if (
                threat.capture_stage == 1
                and self._ring_stalled_frames.get(item.assigned_threat, 0) < 180
            ):
                # Enter a moving ring through an outer tangential lane. Direct
                # chords make opposite craft cross in front of the target and
                # leave the global safety resolver with no option except
                # repeated braking. Stable angular order plus an outer staging
                # radius gives every USV a visible, purposeful approach path.
                # Once final containment begins, however, every craft must
                # converge directly to its fixed slot; retaining the staging
                # lane there can keep one moving USV orbiting outside forever.
                rel_x, rel_y = item.x - center_x, item.y - center_y
                current_radius = max(1.0, _length(rel_x, rel_y))
                current_angle = math.atan2(rel_y, rel_x)
                angular_error = (
                    slot.angle - current_angle + math.pi
                ) % (2.0 * math.pi) - math.pi
                if abs(angular_error) > math.radians(10.0):
                    staging_radius = max(
                        slot.radius + 12.0,
                        min(slot.radius + 30.0, current_radius),
                    )
                    next_angle = current_angle + max(
                        -math.radians(18.0),
                        min(math.radians(18.0), angular_error),
                    )
                    return (
                        center_x + math.cos(next_angle) * staging_radius,
                        center_y + math.sin(next_angle) * staging_radius,
                        final_slot[2],
                    )
            return final_slot
        if item.role == "CLOSE_GUARD":
            guards = sorted(
                (guard for guard in self.vehicles if guard.role == "CLOSE_GUARD"),
                key=lambda guard: (
                    int(guard.code.rsplit("-", 1)[-1]),
                    0 if guard.kind == "USV" else 1,
                ),
            )
            position = self._convoy_guard_slot_by_code.get(
                item.code,
                guards.index(item),
            )
            x, y = self._convoy_guard_point(position, len(guards))
            return x, y, item.z
        if item.role == "FORMATION_GUARD":
            total = self.plan.uav_count if item.kind == "UAV" else self.plan.usv_count
            original_guard_total = self._guard_count(total)
            # Slot identity is permanent. Redistributing all remaining guards
            # whenever one responder departs makes otherwise stationary craft
            # cross the convoy and produces visible reconfiguration jitter.
            # Vacated sectors instead become the deliberate launch corridor.
            position = max(
                0,
                int(item.code.rsplit("-", 1)[-1]) - 1 - original_guard_total,
            )
            formation_count = max(1, total - original_guard_total)
            x, y = self._formation_guard_point(
                position, formation_count, item.kind,
            )
            return x, y, item.z
        if item.role == "CONVOY_SUPPORT":
            if item.kind == "UAV":
                x, y = self._post_mission_point(item)
                return x, y, item.z
            inner_escape = self._convoy_inner_escape_point(item)
            if inner_escape is not None:
                return inner_escape[0], inner_escape[1], item.z
            route_point = self._convoy_support_route_point(item)
            if route_point is not None:
                return route_point[0], route_point[1], item.z
            supports = self._convoy_support_members()
            position = supports.index(item)
            x, y = self._safe_convoy_support_point(position, len(supports))
            return x, y, item.z
        if item.role == "LOCAL_OVERWATCH":
            x, y = self._post_watch_point(item)
            return x, y, item.z
        if item.role == "CAPTURE_RESERVE":
            reserves = self._convoy_reserve_members()
            position = reserves.index(item)
            x, y = self._safe_convoy_support_point(position, len(reserves))
            return x, y, item.z
        if (
            len(self.protected) > 1
            and item.role == "OUTER_INTERCEPT"
            and item.group_id.startswith("SUPPORT-")
        ):
            threat_index = int(item.group_id.rsplit("-", 1)[-1]) - 1
            if 0 <= threat_index < len(self.threats):
                threat = self.threats[threat_index]
                peers = sorted(
                    (
                        peer for peer in self.vehicles
                        if peer.group_id == item.group_id
                        and peer.role == item.role
                        and peer.assigned_threat is None
                    ),
                    key=lambda peer: (
                        int(peer.code.rsplit("-", 1)[-1]),
                        0 if peer.kind == "USV" else 1,
                    ),
                )
                position = peers.index(item) if item in peers else 0
                radius = 78.0
                angle = (
                    threat.capture_phase
                    + 2.0 * math.pi * position / max(1, len(peers))
                )
                center_x, center_y = self._project_to_safe_water(
                    threat.x + threat.vx * 2.0,
                    threat.y + threat.vy * 2.0,
                    radius + 8.0,
                )
                return (
                    center_x + math.cos(angle) * radius,
                    center_y + math.sin(angle) * radius,
                    item.z,
                )
        target = self.protected[item.protected_index]
        if item.role == "BLOCKER":
            threat_index = int(item.group_id.rsplit("-", 1)[-1]) - 1
            threat = self.threats[threat_index]
            ux, uy = _unit(threat.x - target.x, threat.y - target.y)
            lead = max(28.0, min(56.0, self._distance_to_protected(threat) * 0.48))
            if threat.screen_established:
                # Once the barrier is real, advance it toward the attacker.
                # Holding forever at the convoy midpoint only shadows a
                # flanking attacker and never establishes the close intercept.
                lead = max(28.0, self._distance_to_protected(threat) - 32.0)
            peers = sorted(
                (
                    peer for peer in self.vehicles
                    if peer.group_id == item.group_id
                    and peer.role == "BLOCKER"
                    and peer.assigned_threat is None
                ),
                key=lambda peer: peer.code,
            )
            position = peers.index(item) if item in peers else 0
            lateral = (position - (len(peers) - 1) / 2.0) * 15.0
            # Form a moving barrier across the predicted attack corridor.
            x, y = self._project_to_safe_water(
                target.x + ux * lead - uy * lateral,
                target.y + uy * lead + ux * lateral,
            )
            return x, y, item.z
        peers = [peer for peer in self.vehicles if peer.group_id == item.group_id and peer.assigned_threat is None]
        position = peers.index(item) if item in peers else 0
        nearest = self._nearest_threat(item.protected_index)
        observed = nearest
        if item.role == "CONFRONT" and item.group_id.startswith("WATCH-"):
            threat_index = int(item.group_id.rsplit("-", 1)[-1]) - 1
            if 0 <= threat_index < len(self.threats):
                observed = (threat_index, self.threats[threat_index])
        threat_angle = math.radians(target.heading) if observed is None else math.atan2(observed[1].y - target.y, observed[1].x - target.x)
        if item.role == "CONFRONT" and observed is not None:
            # Airborne responders form the forward half of the interception
            # screen instead of orbiting at right angles to the attack lane.
            # The target-relative slot moves continuously with the convoy and
            # never flips sides, which removes the visible zig-zag/yaw.
            threat = observed[1]
            ux, uy = _unit(threat.x - target.x, threat.y - target.y)
            lead = max(
                38.0,
                min(68.0, self._distance_to_protected(threat) * 0.55),
            )
            if threat.screen_established:
                lead = max(38.0, self._distance_to_protected(threat) - 26.0)
            lateral = (position - (len(peers) - 1) / 2.0) * 18.0
            x, y = self._project_to_safe_water(
                target.x + ux * lead - uy * lateral,
                target.y + uy * lead + ux * lateral,
                6.0,
            )
            return x, y, item.z
        else:
            radius = 58.0 + (position % 3) * 8.0
            angle = self.sequence * (0.0040 if item.kind == "UAV" else 0.0035) + 2.0 * math.pi * position / max(1, len(peers))
        # Keep the whole patrol orbit inside navigable water. Clamping each
        # orbit point independently creates a long flat arc at the boundary,
        # which made RECON craft appear dead for hundreds of frames when the
        # convoy travelled near the coast. Moving the orbit centre inward
        # preserves continuous patrol motion without detaching the craft from
        # its protected target.
        if item.role in {"RECON", "RETURNING", "CAPTURE_RESERVE"}:
            orbit_center_x, orbit_center_y = self._project_to_safe_water(
                target.x, target.y, radius + 8.0,
            )
            x = orbit_center_x + math.cos(angle) * radius
            y = orbit_center_y + math.sin(angle) * radius
        else:
            x, y = self._project_to_safe_water(
                target.x + math.cos(angle) * radius,
                target.y + math.sin(angle) * radius,
            )
        return x, y, 25.0 + (position % 3) * 2.5 if item.kind == "UAV" else 0.0

    @staticmethod
    def _move_towards(current: Sequence[float], desired: Sequence[float], step: float) -> tuple[float, float, float]:
        delta = tuple(float(desired[i]) - float(current[i]) for i in range(3))
        distance = math.sqrt(sum(value * value for value in delta))
        if distance <= step or distance < 1e-9:
            return float(desired[0]), float(desired[1]), float(desired[2])
        return tuple(float(current[i]) + delta[i] * step / distance for i in range(3))

    def _advance_vehicles(self) -> list[AgentFrame]:
        proposals: dict[str, tuple[str, tuple[float, float, float]]] = {}
        step_limits: dict[str, float] = {}
        task_errors: dict[str, float] = {}
        locked_threat_indices = {
            index
            for index, threat in enumerate(self.threats)
            if self._post_mission_formation_initialized
            and threat.state in {"CAPTURED", "SECURED"}
            and bool(self._live_containment(index)["ready"])
        }
        locked_ring_codes = {
            item.code
            for item in self.vehicles
            if item.assigned_threat in locked_threat_indices
        }
        for item in self.vehicles:
            if item.code in self._unavailable_codes:
                continue
            desired = self._desired_position(item)
            if item.code in locked_ring_codes:
                continue
            distance = _length(desired[0] - item.x, desired[1] - item.y)
            task_errors[item.code] = distance
            cruise = self.uav_cruise if item.kind == "UAV" else self.usv_cruise
            convoy_follower = (
                item.role in {"FORMATION_GUARD", "CONVOY_SUPPORT", "CAPTURE_RESERVE"}
            )
            speed_factor = (
                1.0
                if item.role in {
                    "INTERCEPTOR", "CAPTURE", "BLOCKER", "GAP_BLOCKER",
                    "OUTER_INTERCEPT", "CAPTURE_RESERVE", "CONVOY_SUPPORT",
                    "LOCAL_OVERWATCH", "FORMATION_GUARD",
                }
                else 0.78
                if item.role == "CONFRONT"
                else 0.72
                if item.role == "CONTAINMENT"
                else 0.66
            )
            speed = cruise * speed_factor
            if convoy_follower:
                convoy_speed = max(
                    (_length(target.vx, target.vy) for target in self.protected),
                    default=0.0,
                )
                follower_cap = (
                    self.uav_cruise
                    if item.kind == "UAV"
                    else min(4.0, max(self.usv_cruise, convoy_speed + 0.9))
                )
                # A moving rear-echelon slot cannot be caught at exactly the
                # convoy's speed. Give recalled/reserve craft a bounded closing
                # margin (the same 4 m/s operational cap used by interceptors)
                # until they are back in formation.
                closing_margin = min(
                    0.9 if item.kind == "USV" else 2.8,
                    0.10 + distance * (0.035 if item.kind == "USV" else 0.08),
                )
                if distance <= 24.0:
                    # Full cruise beside a dense guard square makes the safety
                    # projection alternate sides of the slot.  Preserve the
                    # convoy's translation speed but damp only the remaining
                    # closing component as the craft reaches its station.
                    correction_speed = max(
                        0.18,
                        distance * (0.22 if item.kind == "USV" else 0.34),
                    )
                    speed = min(
                        follower_cap,
                        max(
                            correction_speed,
                            convoy_speed + min(closing_margin, 0.35),
                        ),
                    )
                else:
                    speed = min(follower_cap, max(speed, convoy_speed + closing_margin))
            if item.role == "CLOSE_GUARD":
                protected = self.protected[item.protected_index]
                protected_speed = _length(protected.vx, protected.vy)
                # A guard must be able to close on the moving convoy. The old
                # 0.66 cruise multiplier was slower than an evading protected
                # vessel, so guards drifted hundreds of metres away in 30+30.
                guard_cap = self.uav_cruise if item.kind == "UAV" else min(4.0, self.usv_cruise)
                speed = min(
                    guard_cap,
                    max(speed, protected_speed + min(0.8, 0.18 + distance * 0.035)),
                )
                if self._shore_assembly_pending:
                    speed = min(guard_cap, max(speed, distance * 0.6))
            if item.assigned_threat is not None:
                threat = self.threats[item.assigned_threat]
                target_speed = _length(threat.vx, threat.vy)
                gap_boost = item.role == "GAP_BLOCKER"
                closing_margin = min(
                    1.25 if item.kind == "USV" and gap_boost else 1.0 if item.kind == "USV" else 3.6 if gap_boost else 2.8,
                    (0.48 if gap_boost else 0.28) + distance * (0.032 if gap_boost else 0.025),
                )
                pursuit_cap = (
                    min(4.0, max(cruise, target_speed + 0.8))
                    if item.kind == "USV"
                    else cruise
                )
                speed = min(pursuit_cap, max(speed, target_speed + closing_margin))
                # Slow only at the final slot. Earlier deceleration is what
                # previously produced long queues behind a moving enemy.
                if threat.capture_stage >= 2 and distance < 10.0:
                    # Do not decelerate below the translating ring centre.  A
                    # small distance-proportional reserve closes the remaining
                    # angular error while the target is still moving.
                    settled_speed = speed * max(0.38, distance / 10.0)
                    follow_speed = target_speed + min(
                        0.55 if item.kind == "USV" else 1.2,
                        0.08 + distance * (0.075 if item.kind == "USV" else 0.12),
                    )
                    speed = min(pursuit_cap, max(settled_speed, follow_speed))
            elif distance < 12.0 and not convoy_follower:
                speed *= 0.45
            proposals[item.code] = (
                item.kind,
                self._move_towards((item.x, item.y, item.z), desired, max(0.018, speed * DT)),
            )
            defense_motion = self.defense.motion(item)
            if defense_motion is not None:
                proposals[item.code] = (item.kind, defense_motion)
            physical_cap = self.uav_cruise if item.kind == "UAV" else (
                4.0
                if item.assigned_threat is not None
                or convoy_follower
                or item.role == "LOCAL_OVERWATCH"
                else self.usv_cruise + 0.049
            )
            if self._berth_layout is not None and item.kind == "USV":
                # The berth-side scene advertises a hard 4 m/s surface limit.
                # Legacy separation slack must not exceed it at the top speed.
                physical_cap = min(4.0, physical_cap)
            # The scene solver constrains horizontal travel. Reserve the
            # altitude component before handing it that budget so a departing
            # UAV cannot exceed its 3-D speed while descending into a ring.
            vertical_step = proposals[item.code][1][2] - item.z
            step_limits[item.code] = math.sqrt(max(0.0, (physical_cap * DT) ** 2 - vertical_step ** 2))
        fixed = {item.code: ("ESCORT_TARGET", (item.x, item.y, 0.0)) for item in self.protected}
        fixed.update({item.code: (item.kind, (item.x, item.y, item.z))
                      for item in self.vehicles if item.code in self._unavailable_codes})
        fixed.update({item.code: (item.kind, (item.x, item.y, item.z))
                      for item in self._withdrawn_vehicles.values()})
        fixed.update({
            item.code: ("THREAT_TARGET", (item.x, item.y, 0.0))
            for item in self.threats
            if item.state not in {"WAITING", "ESCAPED"}
        })
        # Once every hostile is resolved and the strict rings are valid, keep
        # those rings authoritative while surplus craft return. Otherwise the
        # symmetric collision solver lets a passing support USV push one ring
        # member 3.5+ m off-slot and reopen an already completed containment.
        fixed.update({
            item.code: (item.kind, (item.x, item.y, item.z))
            for item in self.vehicles
            if item.code in locked_ring_codes
        })
        # Solve moving guards and followers together. Treating a screen's next
        # pose as immovable could leave a nearby follower no reachable safe
        # point, and post-solve speed clipping then broke hull separation.
        resolved = self.safety.resolve_group(
            proposals, self.previous, fixed, max_steps=step_limits,
        )
        frames: list[AgentFrame] = []
        for item in self.vehicles:
            old = self.previous.get(item.code, (item.x, item.y, item.z))
            safe = resolved.get(item.code)
            current = (
                (item.x, item.y, item.z)
                if safe is None
                else (safe.x, safe.y, safe.z)
            )
            dx, dy = current[0] - item.x, current[1] - item.y
            displacement = _length(dx, dy)
            limit = step_limits.get(item.code, displacement)
            if displacement > limit > 0.0:
                current = (
                    item.x + dx * limit / displacement,
                    item.y + dy * limit / displacement,
                    current[2],
                )
            if safe is not None and safe.adjusted:
                self.avoidance_count += 1
            task_error = task_errors.get(item.code, 0.0)
            prior_error = self._vehicle_task_error.get(item.code, task_error)
            making_progress = task_error + 0.08 < prior_error or displacement >= 0.08
            if (
                item.assigned_threat is not None
                and task_error > 12.0
                and not making_progress
            ):
                stalled = self._vehicle_stall_frames.get(item.code, 0) + 1
                self._vehicle_stall_frames[item.code] = stalled
                if stalled >= 20:
                    threat = self.threats[item.assigned_threat]
                    if threat.capture_stage >= 1:
                        threat.gap_filler_code = item.code
                        item.role = "GAP_BLOCKER"
            else:
                self._vehicle_stall_frames[item.code] = 0
            self._vehicle_task_error[item.code] = task_error
            item.vx, item.vy = (current[0] - item.x) / DT, (current[1] - item.y) / DT
            item.x, item.y, item.z = current
            defense_heading = self.defense.heading(item)
            if defense_heading is not None:
                heading = defense_heading
                self._stable_headings[item.code] = heading
            else:
                heading = self.stabilize_heading(item.code, old, current, 0.0, 4.5 if item.kind == "UAV" else 4.2)
            if item.kind == "USV" and item.assigned_threat is not None:
                threat = self.threats[item.assigned_threat]
                if threat.capture_stage >= 2:
                    members = self._capture_members(item.assigned_threat)
                    slots = self._capture_slots(members, threat)
                    center_x, center_y = self._capture_center(threat, members)
                    member_index = next(
                        (
                            index for index, member in enumerate(members)
                            if member.code == item.code
                        ),
                        None,
                    )
                    if member_index is not None:
                        expected = slots[member_index].point(
                            (center_x, center_y, 0.0)
                        )
                        slot_error = _length(
                            current[0] - expected[0],
                            current[1] - expected[1],
                        )
                        if slot_error <= 8.0:
                            inward_heading = math.degrees(math.atan2(
                                threat.y - current[1],
                                threat.x - current[0],
                            )) % 360.0
                            heading_delta = (
                                inward_heading - heading + 180.0
                            ) % 360.0 - 180.0
                            heading = (
                                heading
                                + max(-6.0, min(6.0, heading_delta))
                            ) % 360.0
                            self._stable_headings[item.code] = heading
            self.previous[item.code] = current
            frames.append(AgentFrame(
                item.code, item.kind, *current, heading, item.role, "ACTIVE", item.group_id,
                self.threats[item.assigned_threat].code if item.assigned_threat is not None else self.protected[item.protected_index].code,
            ))
        return frames

    def _assess_threats(self) -> None:
        for index, threat in enumerate(self.threats):
            if not threat.forced or threat.state in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}:
                continue
            members = self._capture_members(index)
            if any(item.code in self._unavailable_codes for item in members):
                threat.capture_hold = 0
                continue
            if len(members) < 3:
                continue
            slots = self._capture_slots(members, threat)
            center_x, center_y = self._capture_center(threat, members)
            errors = []
            angles = []
            tolerance = (12.0, 7.0, 3.5)[min(2, threat.capture_stage)]
            for item, slot in zip(members, slots):
                expected = slot.point((center_x, center_y, 0.0))
                errors.append(_length(item.x - expected[0], item.y - expected[1]))
                angles.append(math.atan2(item.y - threat.y, item.x - threat.x) % (2.0 * math.pi))
            participating = sum(error <= tolerance for error in errors)
            arrival_ratio = participating / len(members)
            angles.sort()
            max_gap = max(
                (angles[(position + 1) % len(angles)] - angles[position]) % (2.0 * math.pi)
                for position in range(len(angles))
            )
            max_gap_deg = math.degrees(max_gap)
            threat.capture_arrival_ratio = arrival_ratio
            threat.capture_max_gap_deg = max_gap_deg
            threat.capture_radial_error = max(errors, default=0.0)
            largest_gap_index = max(
                range(len(angles)),
                key=lambda position: (
                    angles[(position + 1) % len(angles)] - angles[position]
                ) % (2.0 * math.pi),
            )
            threat.gap_center_angle = (
                angles[largest_gap_index]
                + max_gap * 0.5
            ) % (2.0 * math.pi)

            final_gap = maximum_capture_gap_deg(len(members))
            if threat.capture_stage >= 1 and max_gap_deg > final_gap + 0.5:
                angle_items = sorted(
                    (
                        math.atan2(item.y - threat.y, item.x - threat.x) % (2.0 * math.pi),
                        item,
                    )
                    for item in members
                )
                angular_gaps = [
                    (angle_items[(position + 1) % len(angle_items)][0] - angle_items[position][0])
                    % (2.0 * math.pi)
                    for position in range(len(angle_items))
                ]
                gap_index = max(range(len(angular_gaps)), key=angular_gaps.__getitem__)
                left = angle_items[gap_index][1]
                right = angle_items[(gap_index + 1) % len(angle_items)][1]
                left_other = angular_gaps[(gap_index - 1) % len(angular_gaps)]
                right_other = angular_gaps[(gap_index + 1) % len(angular_gaps)]
                threat.gap_filler_code = left.code if left_other <= right_other else right.code
                threat.gap_center_angle = (
                    angle_items[gap_index][0] + angular_gaps[gap_index] * 0.5
                ) % (2.0 * math.pi)
            elif max_gap_deg <= final_gap + 0.5:
                threat.gap_filler_code = ""

            # Contract only after the current ring is substantially formed.
            # Stage zero is predictive interception; later stages also demand
            # angular spread so a line/queue cannot be mistaken for a ring.
            pursuit_distance = self._pursuit_distance(threat)
            # Require a visible chase and real slot arrival. A blocked escape
            # can also hand off once its pursuers actually reach the fan.
            formation_handoff_ready = (
                threat.escape_intent_confirmed and arrival_ratio >= 0.70
                and (pursuit_distance >= 36.0 or threat.slowdown_reason == "ESCAPE_CORRIDOR_BLOCKED")
            )
            pursuit_complete = (
                threat.capture_stage >= 1
                or formation_handoff_ready
            )
            if not pursuit_complete:
                threat.mission_stage = "PURSUIT"
            else:
                # Interception and blocking were already shown before the
                # hostile committed to escape. Returning to either label after
                # PURSUIT made the global stepper visibly jump backwards.
                threat.mission_stage = "ENCIRCLEMENT"
            if threat.capture_stage == 0 and pursuit_complete and arrival_ratio >= 0.70:
                threat.capture_stage = 1
                threat.capture_hold = 0
                threat.mission_stage = "ENCIRCLEMENT"
                continue
            # The transition is an approach gate, not the final containment
            # contract. Three surviving responders cannot have a largest gap
            # below 120 degrees; the legacy 95-degree gate stranded a valid
            # mixed triangle forever after a settled withdrawal. Preserve the
            # original gate for four or more while allowing that triangle to
            # enter precise slot convergence (the strict canonical gap, inward
            # headings and consecutive hold are still checked below).
            stage_two_gap = max(
                min(95.0, maximum_capture_gap_deg(len(members)) + 14.0),
                360.0 / len(members) + 5.0,
            )
            if threat.capture_stage == 1 and arrival_ratio >= 0.88 and max_gap_deg <= stage_two_gap + 1e-6:
                threat.capture_stage = 2
                threat.capture_hold = 0
                threat.mission_stage = "ENCIRCLEMENT"
                continue
            relative_speed = sum(_length(item.vx - threat.vx, item.vy - threat.vy) for item in members) / len(members)
            contract = assess_containment(
                [(item.x, item.y, item.z) for item in members],
                (threat.x, threat.y, 0.0),
                required_count=len(members),
                device_types=[item.kind for item in members],
                minimum_type_counts={"UAV": 1, "USV": 1},
                minimum_radius_m=18.0 if threat.capture_stage >= 2 else 13.5,
                maximum_radius_m=self._capture_radius_limit(members),
                # Two opposite members may each be within the strict 3.5 m
                # canonical-slot tolerance while their radial difference is
                # slightly above the former 5.25 m limit. Keep this secondary
                # thickness check consistent without weakening slot, angular,
                # separation or inward-heading requirements.
                maximum_radial_spread_m=6.0,
                minimum_pairwise_separation_m=7.1,
                participating=participating,
                # Sub-degree tolerance absorbs floating-point/one-tick
                # tracking error at the 4+5 asymmetric geometry boundary;
                # it does not permit a visually open sector.
                tolerance_deg=1.0,
            )
            slot_map = {
                item.code: RingSlot(
                    index=position,
                    angle=slot.angle,
                    radius=slot.radius,
                    altitude=slot.altitude,
                )
                for position, (item, slot) in enumerate(zip(members, slots))
            }
            canonical = assess_canonical_ring(
                [
                    RingMember(
                        item.code, item.kind, item.x, item.y, item.z,
                        self._stable_headings.get(item.code),
                    )
                    for item in members
                ],
                (threat.x, threat.y, 0.0),
                slot_map,
                slot_tolerance_m=3.5,
                minimum_separation_m=7.0,
                require_inward_usv_heading=True,
                usv_heading_tolerance_deg=3.0,
            )
            protected_clearance_ready = all(
                _length(threat.x - protected.x, threat.y - protected.y)
                >= CONTAINMENT_STANDOFF_M - 1.0
                for protected in self.protected
            )
            best_arrival = self._ring_best_arrival.get(index, 0.0)
            if canonical.ready or canonical.arrival_ratio > best_arrival + 0.02:
                self._ring_best_arrival[index] = canonical.arrival_ratio
                self._ring_stalled_frames[index] = 0
            elif threat.capture_stage >= 1:
                stalled = self._ring_stalled_frames.get(index, 0) + 1
                self._ring_stalled_frames[index] = stalled
                # Slot identity remains authoritative. Rotating the complete
                # ring made already-correct USVs cross paths and visibly yaw;
                # the local gap-filler acceleration closes the remaining slot.
            final_ready = (
                threat.capture_stage >= 2
                and contract.ready
                and canonical.ready
                and protected_clearance_ready
                and relative_speed <= 4.5
            )
            if final_ready:
                threat.containment_stage_latched = True
                threat.containment_soft_failure_frames = 0
                threat.state = "CAPTURE_HOLD"
                threat.capture_hold = min(
                    CAPTURE_HOLD_FRAMES,
                    threat.capture_hold + 1,
                )
                threat.mission_stage = "STABLE_CONTAINMENT"
                if threat.capture_hold >= CAPTURE_HOLD_FRAMES:
                    threat.state, threat.vx, threat.vy = "CAPTURED", 0.0, 0.0
                    threat.captured_frame = self.sequence
            else:
                # Completion requires consecutive valid emitted frames. A
                # broken visual ring immediately cancels the confirmation hold.
                threat.containment_stage_latched = False
                threat.containment_soft_failure_frames = 0
                threat.capture_hold = 0
                if not pursuit_complete:
                    threat.state = "ESCAPE_PURSUIT"
                    threat.mission_stage = "PURSUIT"
                else:
                    # Completing the measured pursuit is the irreversible
                    # hand-off into encirclement.  Slot arrival can still be
                    # below the stage-one threshold for several frames, but
                    # that is forward encirclement work—not a return to the
                    # already completed intercept phase.
                    threat.state = "ENCIRCLING"
                    threat.mission_stage = "ENCIRCLEMENT"

    def _slot_convergence_progress(
        self, threat: _Threat, members: Sequence[_Vehicle], arrival_ratio: float = 0.0,
    ) -> float:
        """Describe measured slot approach without granting capture evidence.

        The integer arrival ratio cannot show the fourth craft closing a 70 m
        error while the other three track their stations. Each unarrived craft
        earns bounded partial credit from distance it has actually closed, using
        its largest observed error for this target/stage/slot as the reference.
        There is no time-based increment or extrapolated pose in this metric.
        """
        if not self.mission_active:
            return self._capture_slot_progress.get(threat.code, 0.0)
        if not members or not all(math.isfinite(value) for value in (threat.x, threat.y)):
            return 0.0
        if threat.capture_stage < 1:
            progress = (max(0.0, min(1.0, arrival_ratio))
                        if math.isfinite(arrival_ratio)
                        and all(v.code not in self._unavailable_codes
                                and all(math.isfinite(value) for value in (v.x, v.y, v.z))
                                for v in members)
                        else 0.0)
            self._capture_slot_progress[threat.code] = progress
            return progress
        tolerance = 7.0 if threat.capture_stage == 1 else 3.5
        slots = self._capture_slots(members, threat)
        credit = 0.0
        all_available = True
        for member, slot in zip(members, slots):
            valid = (member.code not in self._unavailable_codes
                     and all(math.isfinite(value) for value in (member.x, member.y, member.z)))
            if not valid:
                all_available = False
                continue
            goal = slot.point((threat.x, threat.y, 0.0))
            error = math.hypot(member.x - goal[0], member.y - goal[1])
            key = (threat.code, threat.capture_started_frame, threat.capture_stage,
                   member.code, slot.angle, slot.radius)
            reference = max(error, self._capture_slot_reference_errors.get(key, error))
            self._capture_slot_reference_errors[key] = reference
            credit += (1.0 if error <= tolerance else min(
                0.95, max(0.0, (reference - error) / max(1e-6, reference - tolerance)),
            ))
        progress = credit / len(members)
        if all_available and math.isfinite(arrival_ratio):
            # Keep this frame's observed arrival evidence as a floor.
            # Held/invalid craft cannot inherit its pre-override stale count.
            progress = max(progress, max(0.0, min(1.0, arrival_ratio)))
        self._capture_slot_progress[threat.code] = progress
        return progress

    def _escort_route_progress(self, item: _Protected) -> float:
        """Return progress from the real convoy start to its terminal gate.

        The previous metric used the left world boundary as its origin and then
        forced the terminal frame to 100%. A convoy that had physically covered
        only 34% of its route therefore appeared as 48% and jumped to 100% in a
        single frame. The same terminal gate now defines both the displayed
        route and the completion contract.
        """
        if self._berth_layout is None:
            start_x = self.protected_start_x[item.code]
            gate_x = item.destination_x - PROTECTED_SAFE_GATE_OFFSET_M
            route_length = max(1.0, gate_x - start_x)
            return max(0.0, min(1.0, (item.x - start_x) / route_length))
        start_x, start_y = self._protected_start_positions[item.code]
        ux, uy = self._escort_route_axis()
        gate_x, gate_y = self._escort_route_station(item, 1.0)
        route_length = max(1.0, (gate_x - start_x) * ux + (gate_y - start_y) * uy)
        travelled = (item.x - start_x) * ux + (item.y - start_y) * uy
        return max(0.0, min(1.0, travelled / route_length))

    def _protected_reached_safe_gate(self, item: _Protected) -> bool:
        protected_index = self.protected.index(item)
        captured = [
            threat for threat in self.threats
            if threat.state in {"CAPTURED", "SECURED"}
        ]
        # Multi-protected targets move as one rigid convoy. Attackers may
        # retarget during the mission, so keying the final safety gate to each
        # threat's last protected_index can leave one convoy member with an
        # empty relevant set forever. Every hull must instead clear every
        # completed containment zone before the shared convoy may finish.
        relevant = (
            captured
            if len(self.protected) > 1
            else [
                threat for threat in captured
                if threat.protected_index == protected_index
            ]
        )
        safe_from_containment = (
            bool(relevant)
            and all(
                _length(item.x - threat.x, item.y - threat.y)
                >= TARGET_SEPARATION_M + 8.0
                for threat in relevant
            )
        )
        # Completed containment rings remain keep-out zones, but they no longer
        # permit a 34%-route early exit. The convoy must bypass them and reach
        # the same physical gate used by the progress metric.
        if self._escort_route_progress(item) < 1.0:
            return False
        if relevant and not safe_from_containment:
            return False
        ux, uy = self._escort_route_axis()
        lateral_error = abs(-(item.x - item.destination_x) * uy + (item.y - item.destination_y) * ux)
        if lateral_error <= 24.0:
            return True
        return (
            safe_from_containment
            and lateral_error <= 64.0
        )

    def _update_metrics_and_terminal(self) -> None:
        for threat in self.threats:
            if threat.state == "WAITING":
                continue
            target = self.protected[threat.protected_index]
            distance = _length(threat.x - target.x, threat.y - target.y)
            self.min_protected_threat_distance = min(self.min_protected_threat_distance, distance)
            if distance < BREACH_DISTANCE_M:
                self._terminal_status, self._terminal_reason = "FAILED", f"{threat.code} breached {target.code} safety radius"
        for index, left in enumerate(self.vehicles):
            for right in self.vehicles[index + 1:]:
                # Collision telemetry must use physical 3-D separation.  The
                # earlier horizontal-only value reported a UAV passing safely
                # 27 m above a USV as a 6.4 m near-collision.
                separation = math.sqrt(
                    (left.x - right.x) ** 2
                    + (left.y - right.y) ** 2
                    + (left.z - right.z) ** 2
                )
                self.min_agent_distance = min(self.min_agent_distance, separation)
        navigable_bounds = self.safety.bounds
        for x, y in [(item.x, item.y) for item in [*self.protected, *self.threats, *self.vehicles]
                     if item.code not in self._unavailable_codes]:
            self.min_shore_distance = min(self.min_shore_distance, x - navigable_bounds[0], navigable_bounds[1] - x, y - navigable_bounds[2], navigable_bounds[3] - y)
        resolved = all(item.state in {"CAPTURED", "SECURED", "ESCAPED"} for item in self.threats)
        if resolved:
            self._release_surplus_containment()
            self._redeploy_surplus_to_convoy()
        arrived = all(self._protected_reached_safe_gate(item) for item in self.protected)
        if resolved:
            self._consolidate_final_containment()
        if resolved and arrived:
            self._replan_final_convoy_support_formation()
        # Surplus members can be reassigned after one threat is captured. The
        # remaining 2+2 persistent ring needs several physical frames to close
        # its new slots; do not report mission success during that transition.
        # This keeps the terminal screenshot and WebGL state consistent with
        # the same geometric contract used to declare the capture itself.
        captured_rings_ready = True
        for threat_index, threat in enumerate(self.threats):
            if threat.state not in {"CAPTURED", "SECURED"}:
                continue
            if not bool(self._live_containment(threat_index)["ready"]):
                captured_rings_ready = False
                break
        stable_containment_ready = resolved and captured_rings_ready
        if stable_containment_ready:
            if self._stable_containment_ready_frame is None:
                self._stable_containment_ready_frame = self.sequence
            if (
                self._safe_gate_transit_started_frame is None
                and self.sequence - self._stable_containment_ready_frame
                >= STABLE_CONTAINMENT_DISPLAY_FRAMES
            ):
                self._safe_gate_transit_started_frame = self.sequence
        else:
            self._stable_containment_ready_frame = None
            self._safe_gate_transit_started_frame = None
        post_formation = self._post_mission_formation_status()
        support_ready = bool(post_formation["ready"])
        if resolved and arrived and not support_ready:
            maximum_error = float(post_formation["maximumErrorM"])
            if maximum_error < self._post_mission_best_maximum_error - 0.5:
                self._post_mission_best_maximum_error = maximum_error
                self._post_mission_stalled_frames = 0
            else:
                self._post_mission_stalled_frames += 1
            if self._post_mission_stalled_frames >= 120:
                self._replan_stalled_convoy_support_formation()
                post_formation = self._post_mission_formation_status()
                support_ready = bool(post_formation["ready"])
        else:
            self._post_mission_stalled_frames = 0
        self._convoy_support_ready_frames = (
            self._convoy_support_ready_frames + 1
            if resolved and support_ready
            else 0
        )
        self._protected_arrival_ready = arrived
        self._captured_rings_ready = captured_rings_ready
        if not resolved:
            self._terminal_blocker = "THREATS_UNRESOLVED"
        elif self._safe_gate_transit_started_frame is None:
            self._terminal_blocker = "STABLE_CONTAINMENT_CONFIRMING"
        elif not arrived:
            self._terminal_blocker = "SAFE_GATE_TRANSIT"
        elif not captured_rings_ready:
            self._terminal_blocker = "CONTAINMENT_RECONFIGURING"
        elif not support_ready:
            blocker_code = str(post_formation["blockerCode"])
            self._terminal_blocker = (
                f"POST_MISSION_FORMATION:{blocker_code}"
                if blocker_code
                else "POST_MISSION_FORMATION"
            )
        elif self._convoy_support_ready_frames < POST_MISSION_STABLE_FRAMES:
            self._terminal_blocker = "POST_MISSION_STABILIZING"
        else:
            self._terminal_blocker = "NONE"
        if (
            self._terminal_status is None
            and resolved
            and arrived
            and captured_rings_ready
            and self._safe_gate_transit_started_frame is not None
            and self.sequence - self._safe_gate_transit_started_frame
            >= SAFE_GATE_TRANSIT_MIN_FRAMES
            and self._convoy_support_ready_frames >= POST_MISSION_STABLE_FRAMES
            and not (self._unavailable_codes - set(self._withdrawn_vehicles))
            and not self._guard_replacement_pending
            and self._active_force_sufficient()
        ):
            self._terminal_status, self._terminal_reason = "COMPLETED", "all protected targets reached safety and all threats were resolved"
            self._terminal_blocker = "NONE"

    def _phase(self) -> str:
        if self._shore_assembly_pending:
            return "ASSEMBLING_ESCORT"
        active = [item for item in self.threats if item.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}]
        if self._terminal_status:
            return self._terminal_status
        if any(item.state == "CAPTURED" for item in self.threats):
            return "CONTAINMENT"
        if any(item.state == "CAPTURE_HOLD" for item in active):
            return "CAPTURE_HOLD"
        if any(item.forced for item in active):
            return "ACTIVE_CAPTURE"
        if any(item.state == "CONFRONTING" for item in active):
            return "BLOCKING"
        if any(item.detected_frame is not None for item in active):
            return "GUARDING"
        return "ESCORTING"

    def _mission_stage(self) -> str:
        active = [item for item in self.threats if item.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}]
        if self._terminal_status == "COMPLETED":
            return "COMPLETED"
        if active:
            stage_rank = {
                "GUARDING": 0, "THREAT_DETECTION": 1,
                "GUARD_RECONFIGURATION": 2, "INTERCEPT": 3,
                "BLOCKING": 4, "PURSUIT": 5,
                "ENCIRCLEMENT": 6, "STABLE_CONTAINMENT": 7,
            }
            # Report the earliest unresolved incident. Per-target cards still
            # show advanced incidents, while the global stepper can only move
            # forward once every live threat has crossed the phase boundary.
            return min(
                (item.mission_stage for item in active),
                key=lambda stage: stage_rank.get(stage, 0),
            )
        if any(item.state in {"CAPTURED", "SECURED"} for item in self.threats):
            if self._safe_gate_transit_started_frame is not None:
                return "SAFE_GATE_TRANSIT"
            return "STABLE_CONTAINMENT"
        return "GUARDING"

    def _reported_stage(self) -> str:
        raw = self._mission_stage()
        stage_rank = {
            "ESCORTING": 0, "GUARDING": 0, "THREAT_DETECTION": 1,
            "GUARD_RECONFIGURATION": 2, "INTERCEPT": 3,
            "BLOCKING": 4, "PURSUIT": 5, "ENCIRCLEMENT": 6,
            "STABLE_CONTAINMENT": 7, "SAFE_GATE_TRANSIT": 8,
            "COMPLETED": 9,
        }
        # Per-target cards show each incident's exact state. The single global
        # stepper is a mission narrative and must never move backwards merely
        # because a later incident entered an earlier local phase.
        if stage_rank.get(raw, 0) >= stage_rank.get(
            self._reported_mission_stage, 0,
        ):
            self._reported_mission_stage = raw
        return self._reported_mission_stage

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        initial, self._initial_frame_pending = self._initial_frame_pending, False
        if self._shore_assembly_pending and self._protection_status()[0] == 2:
            self._shore_assembly_pending = False
        if self._guard_replacement_pending and self._protection_status()[0] == 2:
            self._guard_replacement_pending = False
        if not initial and self._terminal_status is None:
            self._advance_protected()
            self._retarget_attackers()
            self._advance_threats()
            self._synchronize_guard_roles()
            self._maybe_reassign_capture_members()
            self._redeploy_surplus_to_convoy()
        agents = self._advance_vehicles() if not initial and self._terminal_status is None else [
            AgentFrame(
                item.code, item.kind, item.x, item.y, item.z, self._stable_headings.get(item.code, 0.0),
                item.role, "ACTIVE" if self._terminal_status is None else "HOLDING", item.group_id,
                self.threats[item.assigned_threat].code if item.assigned_threat is not None else self.protected[item.protected_index].code,
            ) for item in self.vehicles
        ]
        if not initial and self._terminal_status is None:
            self._observe_guard_response()
            self._assess_threats()
            self._update_metrics_and_terminal()
        containment = {
            index: self._live_containment(index)
            for index, threat in enumerate(self.threats)
            if threat.forced or threat.state in {"CAPTURED", "SECURED"}
        }
        visually_captured = {
            index for index, snapshot in containment.items()
            if self.threats[index].state in {"CAPTURED", "SECURED"}
            and bool(snapshot["ready"])
        }
        targets = [TargetFrame(item.code, "ESCORT_TARGET", item.x, item.y, 0.0, item.heading, True, f"GUARD-{i + 1:03d}", item.state, 0) for i, item in enumerate(self.protected)]
        targets.extend(TargetFrame(
            item.code, "THREAT_TARGET", item.x, item.y, 0.0, item.heading,
            item.state != "WAITING", f"CAPTURE-{i + 1:03d}",
            (
                item.state
                if item.state not in {"CAPTURED", "SECURED"} or i in visually_captured
                else "RECONFIGURING"
            ),
            3 if item.forced else 2 if item.detected_frame is not None else 1,
        ) for i, item in enumerate(self.threats))
        visible = [item for item in self.threats if item.state != "WAITING"]
        roles: dict[str, int] = {}
        for item in self.vehicles:
            roles[item.role] = roles.get(item.role, 0) + 1
        raw_escort_progress = sum(
            self._escort_route_progress(item)
            for item in self.protected
        ) / len(self.protected)
        # Avoid small backwards percentage changes while the convoy performs a
        # safety detour. This is presentation state only; terminal readiness is
        # always checked against the current physical positions above.
        self._display_escort_progress = max(
            self._display_escort_progress,
            raw_escort_progress,
        )
        escort_progress = self._display_escort_progress
        capture_values: list[float] = []
        capture_groups: list[dict[str, object]] = []
        for index, threat in enumerate(self.threats):
            snapshot = containment.get(index)
            members = self._capture_members(index) if snapshot is None else snapshot["members"]
            visual_ready = index in visually_captured
            slot_convergence = self._slot_convergence_progress(
                threat, members, float(snapshot["arrivalRatio"]) if snapshot is not None else 0.0,
            ) if threat.forced else 0.0
            if visual_ready:
                value = 1.0
            elif threat.forced:
                pursuit_progress = min(1.0, self._pursuit_distance(threat) / max(1.0, threat.required_pursuit_distance))
                value = min(
                    0.98,
                    0.05 + pursuit_progress * 0.18 + threat.capture_stage * 0.22
                    + slot_convergence * 0.22
                    + min(1.0, threat.capture_hold / CAPTURE_HOLD_FRAMES) * 0.16,
                )
            else:
                value = 0.0
            capture_values.append(value)
            if not (threat.forced or threat.state in {"CAPTURED", "SECURED"}):
                # Keep the mission panel structurally complete from frame one.
                # An approaching attacker has no assigned ring yet, but it is
                # still one of the configured capture objectives and must not
                # disappear from the denominator or the target list.
                capture_groups.append({
                    "threatCode": threat.code,
                    "state": threat.state,
                    "missionStage": threat.mission_stage,
                    "stage": 0,
                    "memberCount": len(members),
                    "uavCount": sum(item.kind == "UAV" for item in members),
                    "usvCount": sum(item.kind == "USV" for item in members),
                    "arrivalRatio": 0.0,
                    "slotConvergenceProgress": 0.0,
                    "maxAngularGapDeg": 360.0,
                    "radialErrorM": None,
                    "holdFrames": 0,
                    "holdRequiredFrames": CAPTURE_HOLD_FRAMES,
                    "stableContainmentFrames": 0,
                    "stableContainmentRequiredFrames": CAPTURE_HOLD_FRAMES,
                    "pursuitDistanceM": 0.0,
                    "requiredPursuitDistanceM": round(threat.required_pursuit_distance, 2),
                    "pursuitProgress": 0.0,
                    "intent": threat.intent,
                    "intentConfidence": round(threat.intent_confidence, 3),
                    "speedMps": round(_length(threat.vx, threat.vy), 3),
                    "cruiseSpeedMps": round(threat.cruise_speed, 3),
                    "maximumSpeedMps": round(threat.maximum_speed, 3),
                    "targetHeadingDeg": round(threat.heading % 360.0, 2),
                    "targetTravelDistanceM": round(threat.travelled_distance, 2),
                    "nearestInterceptorCode": threat.nearest_defender_code or None,
                    "nearestInterceptorDistanceM": (
                        None
                        if math.isinf(threat.nearest_defender_distance)
                        else round(threat.nearest_defender_distance, 2)
                    ),
                    "slowdownReason": threat.slowdown_reason,
                    "triggerReason": threat.auto_capture_reason,
                    "gapFillerCode": "",
                    "gapCenterDeg": 0.0,
                    "interceptAttempts": threat.intercept_attempts,
                    "slotReplanCount": self._ring_replans.get(index, 0),
                })
            if threat.forced or threat.state in {"CAPTURED", "SECURED"}:
                assert snapshot is not None
                contract = snapshot["contract"]
                canonical_contract = snapshot["canonicalContract"]
                arrival_ratio = float(snapshot["arrivalRatio"])
                max_gap_deg = float(snapshot["maxGapDeg"])
                radial_error = float(snapshot["radialErrorM"])
                capture_groups.append({
                    "threatCode": threat.code,
                    "state": threat.state if visual_ready or threat.state not in {"CAPTURED", "SECURED"} else "RECONFIGURING",
                    "missionStage": threat.mission_stage if visual_ready or threat.state not in {"CAPTURED", "SECURED"} else "STABLE_CONTAINMENT",
                    "stage": threat.capture_stage,
                    "memberCount": len(members),
                    "uavCount": sum(item.kind == "UAV" for item in members),
                    "usvCount": sum(item.kind == "USV" for item in members),
                    "arrivalRatio": round(arrival_ratio, 3),
                    "slotConvergenceProgress": round(slot_convergence, 4),
                    "maxAngularGapDeg": round(max_gap_deg, 2),
                    "radialErrorM": None if math.isinf(radial_error) else round(radial_error, 2),
                    "holdFrames": threat.capture_hold,
                    "holdRequiredFrames": CAPTURE_HOLD_FRAMES,
                    "stableContainmentFrames": threat.capture_hold,
                    "stableContainmentRequiredFrames": CAPTURE_HOLD_FRAMES,
                    "pursuitDistanceM": round(self._pursuit_distance(threat), 2),
                    "requiredPursuitDistanceM": round(threat.required_pursuit_distance, 2),
                    "pursuitProgress": round(min(1.0, self._pursuit_distance(threat) / max(1.0, threat.required_pursuit_distance)), 3),
                    "intent": threat.intent,
                    "intentConfidence": round(threat.intent_confidence, 3),
                    "speedMps": round(_length(threat.vx, threat.vy), 3),
                    "cruiseSpeedMps": round(threat.cruise_speed, 3),
                    "maximumSpeedMps": round(threat.maximum_speed, 3),
                    "targetHeadingDeg": round(threat.heading % 360.0, 2),
                    "targetTravelDistanceM": round(threat.travelled_distance, 2),
                    "nearestInterceptorCode": threat.nearest_defender_code or None,
                    "nearestInterceptorDistanceM": (
                        None
                        if math.isinf(threat.nearest_defender_distance)
                        else round(threat.nearest_defender_distance, 2)
                    ),
                    "slowdownReason": threat.slowdown_reason,
                    "triggerReason": threat.auto_capture_reason,
                    "gapFillerCode": threat.gap_filler_code,
                    "gapCenterDeg": round(math.degrees(threat.gap_center_angle) % 360.0, 2),
                    "interceptAttempts": threat.intercept_attempts,
                    "slotReplanCount": self._ring_replans.get(index, 0),
                    "assignmentStrategy": "PREDICTIVE_DYNAMIC",
                    "assignmentRevision": self.dynamic_allocator.assignment_revision,
                    "reassignmentCount": self.dynamic_allocator.reassignment_count,
                    "recentReassignments": [
                        change for change in self.assignment_changes
                        if change.get("targetCode") == threat.code
                        or change.get("previousTarget") == threat.code
                    ][-4:],
                    "containmentContract": {
                        "ready": bool(snapshot["ready"]),
                        "blocker": "NONE" if visual_ready else contract.blocker,
                        "maxGapDeg": round(max_gap_deg, 2),
                        "maxAllowedGapDeg": round(maximum_capture_gap_deg(len(members)), 2),
                        "arrivalRatio": round(arrival_ratio, 3),
                        "allMembersParticipating": arrival_ratio >= 1.0,
                        "sectorCount": contract.sector_count,
                        "coveredSectors": contract.covered_sectors,
                        "minimumSeparationM": contract.minimum_separation_m,
                        "requiredSeparationM": contract.required_separation_m,
                        "uavCount": contract.uav_count,
                        "usvCount": contract.usv_count,
                        "invalidParticipants": contract.invalid,
                        "stationaryParticipants": contract.stationary,
                        "detachedParticipants": contract.detached,
                    },
                    "canonicalContainmentContract": {
                        "ready": bool(canonical_contract.ready),
                        "blocker": canonical_contract.blocker,
                        "maxGapDeg": round(canonical_contract.maximum_gap_deg, 2),
                        "maxAllowedGapDeg": round(canonical_contract.allowed_gap_deg, 2),
                        "arrivalRatio": round(canonical_contract.arrival_ratio, 3),
                        "maximumSlotErrorM": round(canonical_contract.maximum_slot_error_m, 3),
                        "radialSpreadM": round(canonical_contract.radial_spread_m, 3),
                        "minimumSeparationM": round(canonical_contract.minimum_separation_m, 3),
                        "inwardOrientedUsvCount": canonical_contract.inward_oriented_usv_count,
                        "usvCount": canonical_contract.usv_count,
                        "maximumUsvHeadingErrorDeg": round(
                            canonical_contract.maximum_usv_heading_error_deg, 3
                        ),
                    },
                })
        capture_progress = sum(capture_values) / max(1, len(capture_values))
        post_formation = self._post_mission_formation_status()
        if self._terminal_status == "COMPLETED":
            capture_progress = 1.0
        overall_progress = escort_progress if self.capture_started_frame is None else escort_progress * 0.4 + capture_progress * 0.6
        reported_stage = self._reported_stage()
        # Keep the aggregate percentage consistent with the global stepper.
        # Averaging three completed rings with one early incident previously
        # produced 92% while the screen still (correctly) said PURSUIT.  The
        # cap is presentation-only; per-target progress and every completion
        # contract remain unchanged.
        stage_progress_ceiling = {
            "GUARDING": 0.24,
            "THREAT_DETECTION": 0.44,
            "GUARD_RECONFIGURATION": 0.54,
            "INTERCEPT": 0.64,
            "BLOCKING": 0.79,
            "PURSUIT": 0.86,
            "ENCIRCLEMENT": 0.94,
            "STABLE_CONTAINMENT": 0.999,
            "SAFE_GATE_TRANSIT": 0.999,
        }
        if self._terminal_status != "COMPLETED":
            overall_progress = min(
                overall_progress,
                stage_progress_ceiling.get(reported_stage, 0.999),
            )
        # Keep the user-facing mission progress monotonic. Temporary
        # containment repairs are reflected by the stage and blocker fields,
        # not by making the completion percentage move backward.
        self._display_progress = max(
            getattr(self, "_display_progress", 0.0),
            min(0.99, overall_progress) if self._terminal_status != "COMPLETED" else 1.0,
        )
        capture_elapsed = 0 if self.capture_started_frame is None else max(0, self.sequence - self.capture_started_frame)
        current_threat_distances = [
            self._distance_to_protected(item)
            for item in self.threats
            if item.state not in {"WAITING", "ESCAPED"}
        ]
        moving_threats = [item for item in self.threats if item.state not in {"WAITING", "ESCAPED"}]
        unresolved_threats = [
            item for item in self.threats
            if item.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}
        ]
        stage_rank = {
            "GUARDING": 0, "THREAT_DETECTION": 1,
            "GUARD_RECONFIGURATION": 2, "INTERCEPT": 3,
            "BLOCKING": 4, "PURSUIT": 5,
            "ENCIRCLEMENT": 6, "STABLE_CONTAINMENT": 7,
            "SAFE_GATE_TRANSIT": 8,
        }
        stage_subject = min(
            unresolved_threats,
            key=lambda item: (stage_rank.get(item.mission_stage, 0), item.code),
            default=None,
        )
        close_guard_count = sum(item.role == "CLOSE_GUARD" for item in self.vehicles)
        formation_guard_count = sum(item.role == "FORMATION_GUARD" for item in self.vehicles)
        capture_assigned_count = sum(item.assigned_threat is not None for item in self.vehicles)
        mobile_support_count = len(self.vehicles) - close_guard_count - capture_assigned_count
        protection_ready_count, assembly_distance = self._protection_status()
        required_uavs, required_usvs, required_total = self._active_force_requirements()
        force_sufficient = self._active_force_sufficient()
        metrics = {
            "requiredActiveUavCount": required_uavs,
            "requiredActiveUsvCount": required_usvs,
            "requiredActiveMissionDeviceCount": required_total,
            "activeForceSufficient": force_sufficient,
            "missionDegradedReason": "" if force_sufficient else "INSUFFICIENT_ACTIVE_FORCE",
            "withdrawnDeviceCodes": sorted(self._withdrawn_vehicles),
            "guardReplacementPending": self._guard_replacement_pending,
            "escortAssemblyState": "ASSEMBLING" if self._shore_assembly_pending else "READY",
            "escortAssemblyProgress": round(self._escort_assembly_progress, 6),
            "escortProtectionReady": protection_ready_count == 2,
            "escortProtectionReadyCount": protection_ready_count,
            "escortProtectionRequiredCount": 2,
            "escortAssemblyDistanceM": (round(assembly_distance, 3)
                                         if math.isfinite(assembly_distance) else None),
            "scenarioPlan": self.plan.to_dict(), "protectedCount": self.plan.protected_count,
            "threatCount": self.plan.threat_count, "visibleThreatCount": len(visible),
            "capturedThreatCount": len(visually_captured),
            "escapedThreatCount": sum(item.state == "ESCAPED" for item in self.threats),
            "simultaneousThreatLimit": self.plan.simultaneous_threats, "avoidanceCount": self.avoidance_count,
            "parallelResponseEnabled": self._parallel_response_enabled,
            "parallelResponseStarted": self._parallel_response_started,
            "roles": roles,
            "closeGuardCount": close_guard_count,
            "formationGuardCount": formation_guard_count,
            "captureAssignedCount": capture_assigned_count,
            "mobileSupportCount": mobile_support_count,
            "unresolvedThreatCount": len(unresolved_threats),
            "stageSubjectThreatCode": None if stage_subject is None else stage_subject.code,
            "threatIntents": {item.code: item.intent for item in self.threats if item.state != "WAITING"},
            "threatIntentConfidence": {
                item.code: round(item.intent_confidence, 3)
                for item in self.threats if item.state != "WAITING"
            },
            "escortDepartureDistanceM": round(self._escort_departure_distance(), 3),
            "escortDepartureReady": self._escort_departure_distance() >= ESCORT_DEPARTURE_MIN_M,
            "tacticalEvents": self._tactical_events[-20:],
            "defenseCoordination": self.defense.metrics(),
            "guardWithdrawalStates": [{
                "threatCode": item.code,
                "observedSeconds": round(item.cover_frames * DT, 1),
                "reverseDistanceM": round(item.cover_distance, 2),
                "released": item.cover_released,
                "emergencyIntercept": item.auto_capture_reason in {
                    "DEFENSIVE_SCREEN_INTERCEPT", "EMERGENCY_BREACH_PREVENTION",
                },
            } for item in self.threats],
            "assignmentStrategy": "PREDICTIVE_DYNAMIC",
            "assignmentRevision": self.dynamic_allocator.assignment_revision,
            "reassignmentCount": self.dynamic_allocator.reassignment_count,
            "assignmentDecision": self.assignment_last_result,
            "recentAssignmentChanges": self.assignment_changes[-12:],
            "attackingThreatCount": sum(
                item.detected_frame is not None and not item.forced
                and item.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}
                for item in self.threats
            ),
            "interceptedThreatCount": sum(
                item.intercept_hold_frames > 0 and not item.forced
                for item in self.threats
            ),
            "firstLineBreachCount": sum(item.breach_until_frame > 0 for item in self.threats),
            "closestThreatDistanceM": None if not current_threat_distances else round(min(current_threat_distances), 3),
            "protectedTargetMeanSpeedMps": round(
                sum(_length(item.vx, item.vy) for item in self.protected) / max(1, len(self.protected)), 3,
            ),
            "threatMeanSpeedMps": round(
                sum(_length(item.vx, item.vy) for item in moving_threats) / max(1, len(moving_threats)), 3,
            ),
            "attackClosingDistanceM": round(sum(
                max(0.0, item.attack_start_distance - item.closest_attack_distance)
                for item in self.threats if math.isfinite(item.attack_start_distance)
            ), 3),
            "minProtectedThreatDistanceM": None if math.isinf(self.min_protected_threat_distance) else round(self.min_protected_threat_distance, 3),
            "minAgentDistanceM": None if math.isinf(self.min_agent_distance) else round(self.min_agent_distance, 3),
            # The controller works inside bounds already inset from the coastline.
            # Report the real coastline clearance rather than the inset-relative value.
            "minShoreDistanceM": None if math.isinf(self.min_shore_distance) else round(self.min_shore_distance + SHORE_MARGIN_M, 3),
            "threatTravelDistanceM": round(sum(item.travelled_distance for item in self.threats), 3),
            "escortProgress": round(escort_progress, 3),
            "captureProgress": round(capture_progress, 3),
            "missionProgress": round(self._display_progress, 3),
            "progress": round(self._display_progress, 3),
            "captureElapsedFrames": capture_elapsed,
            "convoySupportCount": len(self._convoy_support_members()),
            "convoySupportReady": self._convoy_support_ready(),
            "localOverwatchCount": len(self._post_watch_members()),
            "postMissionFormationReady": bool(post_formation["ready"]),
            "postMissionFormationReadyCount": int(post_formation["readyCount"]),
            "postMissionFormationRequiredCount": int(post_formation["requiredCount"]),
            "postMissionFormationProgress": round(float(post_formation["progress"]), 3),
            "postMissionFormationMaximumErrorM": round(float(post_formation["maximumErrorM"]), 3),
            "postMissionFormationBlockerCode": str(post_formation["blockerCode"]),
            "postMissionSlotReplanCount": self._post_mission_slot_replans,
            "convoySupportStableFrames": self._convoy_support_ready_frames,
            "convoySupportRequiredStableFrames": POST_MISSION_STABLE_FRAMES,
            "protectedArrivalReady": self._protected_arrival_ready,
            "capturedRingsReady": self._captured_rings_ready,
            "terminalBlocker": self._terminal_blocker,
            "simulationElapsedSeconds": round(max(0, self.sequence - 1) * DT, 1),
            "captureGroups": capture_groups,
            "missionStage": reported_stage,
            "stageSequence": ["GUARDING", "THREAT_DETECTION", "GUARD_RECONFIGURATION", "INTERCEPT", "BLOCKING", "PURSUIT", "ENCIRCLEMENT", "STABLE_CONTAINMENT", "SAFE_GATE_TRANSIT", "COMPLETED"],
            "terminalReason": self._terminal_reason,
            "worldBounds": list(self.safe_bounds),
        }
        agents.extend(AgentFrame(v.code, v.kind, v.x, v.y, v.z,
                                self._stable_headings.get(v.code, 0.0), "WITHDRAWN", "RETURNED", "RETURNED", "")
                      for v in self._withdrawn_vehicles.values())
        agents.sort(key=lambda v: v.code)
        return RuntimeFrame(
            self.run_id, self.code, self.sequence, int(time.time() * 1000), self._phase(),
            agents, targets, metrics, route=[], obstacles=[], terminalStatus=self._terminal_status,
        )
