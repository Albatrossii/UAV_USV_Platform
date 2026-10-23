from __future__ import annotations

import math
import time
import contextlib
import sys
from dataclasses import replace
from types import SimpleNamespace
from typing import Dict, List, Mapping, Sequence

import numpy as np

from app.adapters.base import AlgorithmAdapter
from app.adapters.capture import CaptureAdapter
from app.capture import (
    RingMember,
    RingSlot,
    FormationSlot,
    allocate_balanced_groups,
    assess_canonical_ring,
    build_canonical_slots,
    maximum_capture_gap_deg,
)
from app.decision import DecisionAgent, DecisionTarget
from app.navigation import TASK_CENTER_SCENE_MAP, SceneSafetyFilter
from app.scenario import derive_scenario_plan
from app.schemas import AgentFrame, RuntimeFrame, TargetFrame


class AdaptiveCaptureAdapter(AlgorithmAdapter):
    """Global GB/CS assignment with per-target SFLA and executed-ring control.

    One planner observes every target and every available craft. Local solvers
    optimise the geometry assigned by that planner; the coordinator controls
    those selected goals and validates the final globally safe positions.
    """

    code = "GB_SFLA_CS"
    version = "2.0.0"

    def __init__(self, run_id: int, config: Dict[str, object] | None = None) -> None:
        super().__init__(run_id, config)
        uav_count = max(1, min(128, int(self.config.get("uavCount", 3))))
        usv_count = max(1, min(128, int(self.config.get("usvCount", 3))))
        requested_targets = max(1, int(self.config.get("targetCount", 1)))
        geometric_limit = max(1, (uav_count + usv_count) // 3)
        self.target_count = min(requested_targets, uav_count, usv_count, geometric_limit)
        self.children: List[CaptureAdapter] = []
        self.agent_code_maps: List[Dict[str, str]] = []
        # Child capture solvers operate in the expanded adaptive scenario
        # (roughly 360x280 m for the realtime tiers), not the legacy
        # Task-Center 72x60 m presentation box.  Applying the latter bounds
        # to the merged fleet clamps multi-target agents near the initial
        # staging area, producing the “分组后原地不动” symptom and preventing
        # 9+9 from ever closing its ring.
        plan = derive_scenario_plan(uav_count, usv_count)
        # Keep the complete outer ring inside the coordinator's water area.
        # A fixed +/-190 m vertical bound clipped the north/south slots after
        # a target had completed its visible escape run. Several UAVs were
        # then projected onto the same boundary point, leaving an artificial
        # 90+ degree opening that could never converge.
        # The target solver may use the full scenario extent.  The coordinator
        # therefore needs another complete outer-ring diameter beyond that
        # extent; otherwise a target near an edge remains valid while half of
        # its requested ring is clamped to the same boundary coordinate.
        half_width = max(420.0, plan.world_width * 0.5 + 220.0)
        half_height = max(360.0, plan.world_height * 0.5 + 220.0)
        self.safety = SceneSafetyFilter({
            "bounds": [-half_width, half_width, -half_height, half_height],
            "obstacles": list(TASK_CENTER_SCENE_MAP.get("obstacles", [])),
        })
        self.previous_scene: Dict[str, tuple[float, float, float]] = {}
        # Hold counters for the collision-safe poses actually emitted to
        # Unity. Child solvers assess before the cross-target safety pass, so
        # their private hold cannot be authoritative for a merged scene.
        self.executed_hold_frames: List[int] = [0 for _ in range(self.target_count)]
        # Geometry produced by the global safety pass can cross a threshold for
        # one frame while a neighbour yields.  Treat those small angular/radial
        # excursions as a local repair action instead of resetting the whole
        # containment state machine.  Hard safety failures still regress at
        # once; soft failures use a Schmitt-style grace window.
        self.executed_soft_failure_frames: List[int] = [0 for _ in range(self.target_count)]
        self.containment_stage_latched: List[bool] = [False for _ in range(self.target_count)]
        self.soft_failure_grace_frames = max(
            3, int(self.config.get("containmentSoftFailureGraceFrames", 8))
        )
        self.display_progress = 0.0
        self.reported_mission_stage = "PREVIEW"
        # One authoritative horizontal ring per target. Slot identities remain
        # stable for the lifetime of the assignment and are assessed again on
        # the exact collision-safe coordinates emitted to Unity.
        self.ring_slots: List[Dict[str, RingSlot]] = [
            {} for _ in range(self.target_count)
        ]
        self.support_slots: List[Dict[str, RingSlot]] = [
            {} for _ in range(self.target_count)
        ]
        self.ring_member_codes: List[set[str]] = []
        self.ring_best_arrival: List[float] = [0.0 for _ in range(self.target_count)]
        self.ring_stalled_frames: List[int] = [0 for _ in range(self.target_count)]
        self.ring_replans: List[int] = [0 for _ in range(self.target_count)]
        initial = self.initial_pose_map()

        target_points = [
            initial.get(f"TARGET-{index + 1:03d}")
            for index in range(self.target_count)
        ]
        uav_groups = self._partition_codes(
            "UAV", uav_count, self.target_count, initial, target_points,
        )
        usv_groups = self._partition_codes(
            "USV", usv_count, self.target_count, initial, target_points,
        )
        for target_index in range(self.target_count):
            global_uavs = uav_groups[target_index]
            global_usvs = usv_groups[target_index]
            # Build one readable mixed core from the capacity actually assigned
            # to this target.  The former hard-coded 5+5 slice made larger
            # fleets look identical and prevented an asymmetric group from
            # using its available craft.  Six per domain is the inner-ring
            # readability/safety ceiling; surplus craft remain on an explicit
            # moving support ring and are eligible for ETA-based relief.
            primary_per_domain = min(
                6,
                max(1, min(len(global_uavs), len(global_usvs))),
            )
            self.ring_member_codes.append(set(
                global_uavs[:primary_per_domain]
                + global_usvs[:primary_per_domain]
            ))
            code_map: Dict[str, str] = {}
            child_poses: List[Dict[str, object]] = []
            for kind, codes in (("UAV", global_uavs), ("USV", global_usvs)):
                for local_index, global_code in enumerate(codes, start=1):
                    local_code = f"{kind}-{local_index:03d}"
                    code_map[local_code] = global_code
                    pose = initial.get(global_code)
                    if pose is not None:
                        copied = dict(pose)
                        copied["deviceCode"] = local_code
                        child_poses.append(copied)
            target_code = f"TARGET-{target_index + 1:03d}"
            target_pose = initial.get(target_code)
            if target_pose is not None:
                copied_target = dict(target_pose)
                copied_target["deviceCode"] = "TARGET-001"
                child_poses.append(copied_target)

            child_config = dict(self.config)
            child_config.update({
                "uavCount": len(global_uavs),
                "usvCount": len(global_usvs),
                "targetCount": 1,
                "seed": int(self.config.get("seed", 42)) + target_index * 997,
                "initialPoses": child_poses,
                "externalContainmentAuthority": True,
            })
            child = CaptureAdapter(run_id * 100 + target_index + 1, child_config)
            self.children.append(child)
            self.agent_code_maps.append(code_map)

        # One GB/CS planner sees the whole scene and exclusively owns target
        # assignment. Children optimise/execute their assigned local geometry.
        self.global_environment = SimpleNamespace(
            agents=np.empty((0, 8)), targets=np.empty((0, 5)),
            permanently_captured=set(), guarding_agents={},
            previous_assignments={}, assignment_only=True,
            minimum_ball_agents=3, assignment_type_capacities={},
            locked_target_ids=set(), target_velocities={},
        )
        self.children[0]._activate_vendor_runtime_config()
        self.global_planner = self.children[0].source.GBSFLACSAlgorithm(self.global_environment)
        self.global_planner.full_plan_interval = 1
        # Compatibility counters only; no parallel decision engine.
        self.dynamic_allocator = SimpleNamespace(assignment_revision=0, reassignment_count=0)
        self.assignment_evaluation_frames = max(5, int(self.config.get("assignmentEvaluationFrames", 10)))
        self.assignment_last_evaluation = -10000
        self.assignment_candidate = None
        self.assignment_candidate_cycles = 0
        self.assignment_last_switch = -10000
        self.unavailable_codes: set[str] = set()
        self.withdrawn_codes: set[str] = set()
        self.global_agent_ids = {code: index for index, code in enumerate(sorted(
            code for mapping in self.agent_code_maps for code in mapping.values()))}
        self.child_geometry_signatures = [None for _ in self.children]
        self.assignment_changes: List[Dict[str, object]] = []
        self.assignment_last_result: Dict[str, object] = {
            "reason": "INITIAL_DISTANCE_BALANCE",
            "improvementRatio": 0.0,
        }
        self.agent_assignment_stalls: Dict[str, int] = {}
        self.agent_assignment_distances: Dict[str, float] = {}

    @staticmethod
    def _local_agent_rows(child: CaptureAdapter) -> Dict[str, np.ndarray]:
        rows: Dict[str, np.ndarray] = {}
        uav_no = usv_no = 0
        for raw in child.env.agents:
            if int(raw[6]) == 0:
                uav_no += 1
                code = f"UAV-{uav_no:03d}"
            else:
                usv_no += 1
                code = f"USV-{usv_no:03d}"
            rows[code] = raw
        return rows

    @staticmethod
    def _gap_geometry(
        target: tuple[float, float, float],
        members: List[tuple[float, float, float]],
    ) -> tuple[float, float | None]:
        if len(members) < 2:
            return 360.0, None
        angles = sorted(
            math.atan2(item[1] - target[1], item[0] - target[0])
            % (2.0 * math.pi)
            for item in members
        )
        gap, start = max(
            (
                (angles[(index + 1) % len(angles)] - angles[index])
                % (2.0 * math.pi),
                angles[index],
            )
            for index in range(len(angles))
        )
        return math.degrees(gap), math.degrees((start + gap * 0.5) % (2.0 * math.pi))

    def _current_assignment_snapshot(
        self,
    ) -> tuple[List[DecisionAgent], List[DecisionTarget], Dict[str, Dict[str, int]]]:
        target_centers: Dict[str, tuple[float, float, float]] = {}
        for index, child in enumerate(self.children):
            target_code = f"TARGET-{index + 1:03d}"
            target_centers[target_code] = child.previous_scene.get(
                "TARGET",
                child._to_scene(child.env.targets[0, :3], "TARGET"),
            )

        decisions: List[DecisionAgent] = []
        positions_by_target: Dict[str, List[tuple[float, float, float]]] = {
            code: [] for code in target_centers
        }
        for target_index, (child, code_map) in enumerate(
            zip(self.children, self.agent_code_maps)
        ):
            current_target = f"TARGET-{target_index + 1:03d}"
            rows = self._local_agent_rows(child)
            for local_code, global_code in code_map.items():
                if global_code in self.withdrawn_codes:
                    continue
                raw = rows[local_code]
                kind = "UAV" if int(raw[6]) == 0 else "USV"
                position = self.previous_scene.get(
                    global_code,
                    child.previous_scene.get(local_code, child._to_scene(raw[:3], kind)),
                )
                center = target_centers[current_target]
                distance = math.hypot(position[0] - center[0], position[1] - center[1])
                previous_distance = self.agent_assignment_distances.get(global_code)
                outer_radius = self.children[int(current_target.rsplit("-", 1)[1]) - 1].outer_formation_radius
                if (
                    previous_distance is not None
                    and distance > outer_radius + 18.0
                    and distance >= previous_distance - 0.10
                ):
                    self.agent_assignment_stalls[global_code] = (
                        self.agent_assignment_stalls.get(global_code, 0) + 1
                    )
                else:
                    self.agent_assignment_stalls[global_code] = 0
                self.agent_assignment_distances[global_code] = distance
                positions_by_target[current_target].append(position)
                heading = math.degrees(float(raw[4])) % 360.0
                decisions.append(DecisionAgent(
                    code=global_code,
                    kind=kind,
                    x=position[0],
                    y=position[1],
                    speed_mps=max(0.0, float(raw[3])),
                    heading_deg=heading,
                    maximum_speed_mps=(
                        min(15.0, max(0.1, float(self.config.get("uavSpeedMps", 5.0))))
                        if kind == "UAV"
                        else 4.0
                    ),
                    current_target=current_target,
                    stalled_frames=self.agent_assignment_stalls[global_code],
                ))

        targets: List[DecisionTarget] = []
        capacities: Dict[str, Dict[str, int]] = {}
        for index, child in enumerate(self.children):
            target_code = f"TARGET-{index + 1:03d}"
            center = target_centers[target_code]
            gap_deg, gap_bearing = self._gap_geometry(
                center,
                positions_by_target[target_code],
            )
            target_speed = math.hypot(*child.target_velocity)
            confidence = max(0.0, min(1.0, child.last_containment_confidence))
            targets.append(DecisionTarget(
                code=target_code,
                x=center[0],
                y=center[1],
                vx=child.target_velocity[0],
                vy=child.target_velocity[1],
                urgency=min(1.0, 0.25 + target_speed / 3.5 + (1.0 - confidence) * 0.25),
                gap_bearing_deg=gap_bearing,
                gap_deg=gap_deg,
                locked=(
                    self.containment_stage_latched[index]
                    or self.executed_hold_frames[index] > 0
                    or child.captured_at_sequence is not None
                ),
            ))
            mapped_codes = [code for code in self.agent_code_maps[index].values() if code not in self.withdrawn_codes]
            capacities[target_code] = {
                "UAV": sum(code.startswith("UAV-") for code in mapped_codes),
                "USV": sum(code.startswith("USV-") for code in mapped_codes),
            }
        return decisions, targets, capacities

    def _apply_dynamic_assignments(
        self,
        assignments: Mapping[str, str],
    ) -> None:
        raw_by_global: Dict[str, np.ndarray] = {}
        pose_by_global: Dict[str, tuple[float, float, float]] = {}
        for child, code_map in zip(self.children, self.agent_code_maps):
            rows = self._local_agent_rows(child)
            for local_code, global_code in code_map.items():
                raw_by_global[global_code] = rows[local_code].copy()
                kind = "UAV" if global_code.startswith("UAV-") else "USV"
                pose_by_global[global_code] = self.previous_scene.get(
                    global_code,
                    child.previous_scene.get(local_code, child._to_scene(rows[local_code][:3], kind)),
                )

        for target_index, child in enumerate(self.children):
            target_code = f"TARGET-{target_index + 1:03d}"
            old_map = self.agent_code_maps[target_index]
            rows = self._local_agent_rows(child)
            new_map: Dict[str, str] = {}
            for kind in ("UAV", "USV"):
                local_codes = sorted(code for code in old_map if code.startswith(f"{kind}-"))
                assigned_codes = sorted(
                    code for code, assigned in assignments.items()
                    if assigned == target_code and code.startswith(f"{kind}-")
                )
                retained = {
                    local_code: global_code
                    for local_code, global_code in old_map.items()
                    if local_code in local_codes and global_code in assigned_codes
                }
                remaining_locals = [code for code in local_codes if code not in retained]
                remaining_globals = [code for code in assigned_codes if code not in retained.values()]
                new_map.update(retained)
                new_map.update(zip(remaining_locals, remaining_globals))

            changed = new_map != old_map
            self.agent_code_maps[target_index] = new_map
            for local_code, global_code in new_map.items():
                destination = rows[local_code]
                source = raw_by_global[global_code]
                destination[:6] = source[:6]
                kind = "UAV" if global_code.startswith("UAV-") else "USV"
                scene_pose = pose_by_global[global_code]
                destination[:3] = child._to_internal(scene_pose, kind)
                child.previous_scene[local_code] = scene_pose
            if changed:
                child.presentation_slot_assignments.clear()
                child.env.previous_assignments = {}
                self.ring_slots[target_index].clear()
                self.support_slots[target_index].clear()
                primary_per_domain = min(
                    6,
                    max(
                        1,
                        min(
                            sum(code.startswith("UAV-") for code in new_map.values()),
                            sum(code.startswith("USV-") for code in new_map.values()),
                        ),
                    ),
                )
                uavs = sorted(code for code in new_map.values() if code.startswith("UAV-") and code not in self.withdrawn_codes)
                usvs = sorted(code for code in new_map.values() if code.startswith("USV-") and code not in self.withdrawn_codes)
                self.ring_member_codes[target_index] = set(
                    uavs[:6] + usvs[:6]
                )

    def _maybe_reassign_capture_groups(self) -> None:
        if not self.mission_active or self.target_count < 2:
            return
        agents, targets, capacities = self._current_assignment_snapshot()
        if self.sequence - self.assignment_last_evaluation < self.assignment_evaluation_frames:
            return
        self.assignment_last_evaluation = self.sequence
        planner = self.global_planner
        env = self.global_environment
        ids = self.global_agent_ids
        codes = {index: code for code, index in ids.items()}
        current = {ids[agent.code]: int(agent.current_target.rsplit("-", 1)[1])-1 for agent in agents}
        env.agents = np.asarray([
            [agent.x*3, agent.y*3, 75.0 if agent.kind == "UAV" else 0.0,
             agent.speed_mps*0.3, math.radians(agent.heading_deg), 0.0,
             0 if agent.kind == "UAV" else 1, ids[agent.code]] for agent in agents
        ])
        env.targets = np.asarray([[target.x*3, target.y*3, 0, 0, 0] for target in targets])
        env.maximum_agent_speeds = {ids[agent.code]: agent.maximum_speed_mps*0.3 for agent in agents}
        env.target_velocities = {i: (target.vx*3, target.vy*3) for i, target in enumerate(targets)}
        env.assignment_type_capacities = {
            i: {0: capacities[target.code]["UAV"], 1: capacities[target.code]["USV"]}
            for i, target in enumerate(targets)
        }
        env.locked_target_ids = {i for i, target in enumerate(targets) if target.locked}
        # During manual detach keep capacities stable and require a new plan
        # after rejoin; unavailable craft cannot be traded as ghost resources.
        env.locked_target_ids.update(current[ids[code]] for code in self.unavailable_codes if code in ids and ids[code] in current)
        planner.last_assignments = dict(current)
        self.children[0]._activate_vendor_runtime_config()
        with contextlib.redirect_stdout(sys.stderr):
            proposed = planner.step()
        planner.last_assignments = dict(current)
        current_cost = planner._composite_cost(current)
        proposed_cost = planner._composite_cost(proposed)
        improvement = max(0.0, (current_cost-proposed_cost)/max(current_cost, 1e-9))
        signature = tuple(sorted(proposed.items()))
        if signature == self.assignment_candidate:
            self.assignment_candidate_cycles += 1
        else:
            self.assignment_candidate, self.assignment_candidate_cycles = signature, 1
        accepted = (
            proposed != current
            and improvement >= float(self.config.get("assignmentMinimumImprovement", 0.12))
            and self.assignment_candidate_cycles >= max(1, int(self.config.get("assignmentConfirmationCycles", 2)))
            and self.sequence-self.assignment_last_switch >= max(10, int(self.config.get("assignmentCooldownFrames", 30)))
        )
        self.assignment_last_result = {
            "reason": "GB_CS_IMPROVEMENT" if accepted else "GB_CS_KEEP_CURRENT",
            "improvementRatio": round(improvement, 4),
            "currentCost": round(current_cost, 3),
            "proposedCost": round(proposed_cost, 3),
            "globalTargetCount": len(env.targets),
            "globalAgentCount": len(env.agents),
            "granularBallCount": len(planner.leaf_balls),
            "planRevision": planner.plan_revision,
        }
        if not accepted:
            planner.last_assignments = current
            return
        selected = {codes[agent_id]: f"TARGET-{target_id+1:03d}" for agent_id, target_id in proposed.items()}
        selected.update({code: f"TARGET-{index+1:03d}" for index, mapping in enumerate(self.agent_code_maps)
                         for code in mapping.values() if code in self.withdrawn_codes})
        self._apply_dynamic_assignments(selected)
        planner.last_assignments = dict(proposed)
        self.assignment_last_switch = self.sequence
        self.dynamic_allocator.assignment_revision += 1
        for agent_id, target_id in proposed.items():
            if current[agent_id] == target_id:
                continue
            self.dynamic_allocator.reassignment_count += 1
            self.assignment_changes.append({
                "sequence": self.sequence,
                "deviceCode": codes[agent_id],
                "deviceType": codes[agent_id].split("-")[0],
                "previousTarget": f"TARGET-{current[agent_id]+1:03d}",
                "targetCode": f"TARGET-{target_id+1:03d}",
                "reason": "GB_CS_IMPROVEMENT",
            })
        self.assignment_changes = self.assignment_changes[-48:]

    @staticmethod
    def _partition_codes(
        kind: str,
        count: int,
        groups: int,
        initial: Mapping[str, Mapping[str, object]],
        target_points: List[Mapping[str, object] | None],
    ) -> List[List[str]]:
        """Build capacity-balanced teams with travel distance as their cost."""
        codes = [f"{kind}-{index + 1:03d}" for index in range(count)]
        positions = {
            code: (
                float(initial[code].get("eastM", 0.0)),
                float(initial[code].get("northM", 0.0)),
                float(initial[code].get("upM", 0.0)),
            )
            for code in codes
            if code in initial
        }
        targets = [
            None if point is None else (
                float(point.get("eastM", 0.0)),
                float(point.get("northM", 0.0)),
                float(point.get("upM", 0.0)),
            )
            for point in target_points
        ]
        return allocate_balanced_groups(codes, groups, positions, targets)

    def set_mission_active(self, active: bool) -> None:
        super().set_mission_active(active)
        for child in self.children:
            child.set_mission_active(active)

    def reconcile_execution(
        self,
        agents: Sequence[AgentFrame],
        previous_agents: Mapping[str, AgentFrame],
        unavailable_codes: Sequence[str] = (),
    ) -> None:
        actual = {agent.code: agent for agent in agents}
        changed = set(unavailable_codes) != self.unavailable_codes
        self.unavailable_codes = set(unavailable_codes)
        withdrawn = {agent.code for agent in agents if agent.status == "RETURNED"}
        membership_changed = withdrawn != self.withdrawn_codes
        self.withdrawn_codes = withdrawn
        for agent in agents:
            self.previous_scene[agent.code] = (agent.x, agent.y, agent.z)
            self._stable_headings[agent.code] = agent.heading
        for index, (child, mapping) in enumerate(zip(self.children, self.agent_code_maps)):
            local_agents = []
            previous_local = {}
            for local_code, global_code in mapping.items():
                if global_code not in actual:
                    continue
                local = replace(actual[global_code], code=local_code)
                local_agents.append(local)
                previous = previous_agents.get(global_code)
                if previous is actual[global_code]:
                    previous_local[local_code] = local
                elif previous is not None:
                    previous_local[local_code] = replace(previous, code=local_code)
            unavailable_local = [local for local, global_code in mapping.items() if global_code in self.unavailable_codes]
            child.reconcile_execution(local_agents, previous_local, unavailable_local)
            if set(unavailable_local) - child.withdrawn_codes or membership_changed:
                self.executed_hold_frames[index] = 0
                self.containment_stage_latched[index] = False
            if membership_changed:
                available = [code for code in mapping.values() if code not in withdrawn]
                self.ring_member_codes[index] = set(
                    sorted(code for code in available if code.startswith("UAV-"))[:6]
                    + sorted(code for code in available if code.startswith("USV-"))[:6])
                self.ring_slots[index].clear()
                self.support_slots[index].clear()
                self.child_geometry_signatures[index] = None
        if changed or membership_changed:
            self.assignment_last_evaluation = -10000

    def rejoin_target(self, device_code: str) -> tuple[float, float, float] | None:
        for child, mapping in zip(self.children, self.agent_code_maps):
            for local_code, global_code in mapping.items():
                if global_code == device_code:
                    return child.rejoin_target(local_code)
        return None

    @staticmethod
    def _hard_containment_failure(blocker: object) -> bool:
        """Failures that cannot be hidden behind containment hysteresis."""
        name = str(blocker or "")
        return any(token in name for token in {
            "INSUFFICIENT_AGENTS",
            "INCOMPLETE_PARTICIPATION",
            "TARGET_OUTSIDE_HULL",
            "INNER_RADIUS",
            "OUTER_RADIUS",
            "MINIMUM_SEPARATION",
            "INVALID_PARTICIPANT",
        })

    def _update_executed_hold(
        self,
        index: int,
        current: int,
        required: int,
        executed: Mapping[str, object],
    ) -> tuple[int, bool]:
        """Only consecutive valid emitted frames can confirm containment."""
        if bool(executed.get("ready", False)):
            self.executed_soft_failure_frames[index] = 0
            return min(required, current + 1), True
        self.executed_soft_failure_frames[index] = 0
        return 0, False

    @staticmethod
    def _as_ring_members(agents: List[AgentFrame]) -> List[RingMember]:
        return [
            RingMember(
                agent.code, agent.type, agent.x, agent.y, agent.z,
                agent.heading,
            )
            for agent in agents
        ]

    def _ensure_ring_slots(
        self,
        index: int,
        agents: List[AgentFrame],
        target: TargetFrame,
    ) -> Dict[str, RingSlot]:
        current_codes = {agent.code for agent in agents}
        if set(self.ring_slots[index]) != current_codes:
            members = self._as_ring_members(agents)
            center = (target.x, target.y, target.z)
            base_phase = math.radians(target.heading)
            samples = max(8, min(24, len(agents) * 2))
            phase_span = 2.0 * math.pi / max(3, len(agents))
            candidates = [
                build_canonical_slots(
                    members,
                    center,
                    phase=base_phase + phase_span * sample / samples,
                    minimum_spacing_m=14.0,
                )
                for sample in range(samples)
            ]
            self.ring_slots[index] = min(
                candidates,
                key=lambda candidate: sum(
                    math.hypot(
                        member.x - candidate[member.code].point(center)[0],
                        member.y - candidate[member.code].point(center)[1],
                    )
                    for member in members
                ),
            )
        return self.ring_slots[index]

    def _prepare_child_geometry(self, index: int) -> None:
        """Supply canonical base slots to SFLA before it selects any plan."""
        child = self.children[index]
        mapping = self.agent_code_maps[index]
        rows = self._local_agent_rows(child)
        center = child.previous_scene.get("TARGET", child._to_scene(child.env.targets[0, :3], "TARGET"))
        agents = []
        for local_code, global_code in mapping.items():
            if global_code in self.withdrawn_codes:
                continue
            kind = "UAV" if local_code.startswith("UAV-") else "USV"
            point = self.previous_scene.get(global_code, child.previous_scene.get(local_code, child._to_scene(rows[local_code][:3], kind)))
            agents.append(AgentFrame(global_code, kind, *point, 0.0, "INTERCEPTOR"))
        target = TargetFrame(f"TARGET-{index+1:03d}", "CAPTURE_TARGET", *center, 0.0, True)
        primary = [agent for agent in agents if agent.code in self.ring_member_codes[index]]
        base = self._ensure_ring_slots(index, primary, target)
        support_agents = [agent for agent in agents if agent.code not in base]
        if set(self.support_slots[index]) != {agent.code for agent in support_agents}:
            candidates = build_canonical_slots(self._as_ring_members(support_agents), center, phase=math.pi/4, minimum_spacing_m=14.0) if support_agents else {}
            radius = max((slot.radius for slot in base.values()), default=24.0) + 28.0
            self.support_slots[index] = {
                code: RingSlot(slot.index, slot.angle, radius, slot.altitude)
                for code, slot in candidates.items()
            }
        all_slots = {**base, **self.support_slots[index]}
        signature = tuple((code, slot.angle, slot.radius, slot.altitude) for code, slot in sorted(all_slots.items()))
        if self.child_geometry_signatures[index] == signature:
            return
        self.child_geometry_signatures[index] = signature
        child.env.algorithm.formation_slot_assignment.clear()
        for kind, type_value in (("UAV", 0), ("USV", 1)):
            locals_for_type = sorted(code for code in mapping if code.startswith(kind+"-") and mapping[code] not in self.withdrawn_codes)
            specs = [FormationSlot(all_slots[mapping[code]].radius, all_slots[mapping[code]].angle,
                                   all_slots[mapping[code]].altitude, 0 if mapping[code] in base else 1)
                     for code in locals_for_type]
            if kind == "UAV":
                child.uav_slot_specs = specs
            else:
                child.usv_slot_specs = specs
            child._vendor_runtime_config[f"CAPTURE_FORMATION_{kind}_SLOT_SPECS"] = [
                (slot.radius*3, slot.angle, (slot.altitude-9)*4.4 if kind == "UAV" else 0.0, slot.ring)
                for slot in specs
            ]
            ids = [int(rows[code][7]) for code in locals_for_type]
            child.env.algorithm.formation_slot_assignment[(0, type_value)] = {
                "signature": tuple(sorted(ids)), "slots": {agent_id: offset for offset, agent_id in enumerate(ids)},
            }
        child.outer_formation_radius = max((slot.radius for slot in all_slots.values()), default=46.0)
        child._vendor_runtime_config["CAPTURE_RADIUS"] = (child.outer_formation_radius + 40.0) * 3.0
        child.env.algorithm.planning_step = 0

    def _selected_ring_slots(self, index: int, agents: List[AgentFrame], target: TargetFrame) -> Dict[str, RingSlot]:
        child = self.children[index]
        local_plans = child._planned_formation_slots()
        global_plans = {self.agent_code_maps[index][code]: slot for code, slot in local_plans.items()}
        if all(agent.code in global_plans for agent in agents):
            return {agent.code: RingSlot(offset, global_plans[agent.code].angle, global_plans[agent.code].radius,
                                        global_plans[agent.code].altitude)
                    for offset, agent in enumerate(agents)}
        return self._ensure_ring_slots(index, agents, target)

    def _command_canonical_ring(
        self,
        index: int,
        child: CaptureAdapter,
        agents: List[AgentFrame],
        target: TargetFrame,
    ) -> None:
        """Move every assigned member toward one stable, target-centred ring."""
        if not self.mission_active:
            return
        if child.target_travelled_distance < child.required_pursuit_distance + 20.0:
            return
        agents = [agent for agent in agents if agent.code not in self.withdrawn_codes]
        primary_agents = [
            agent for agent in agents
            if agent.code in self.ring_member_codes[index]
        ]
        support_agents = [
            agent for agent in agents
            if agent.code not in self.ring_member_codes[index]
        ]
        slots = self._selected_ring_slots(index, primary_agents, target)
        previous_target = self.previous_scene.get(target.code)
        velocity_x = 0.0 if previous_target is None else (target.x - previous_target[0]) / 0.1
        velocity_y = 0.0 if previous_target is None else (target.y - previous_target[1]) / 0.1
        current_members = self._as_ring_members(primary_agents)
        provisional = assess_canonical_ring(
            current_members,
            (target.x, target.y, target.z),
            slots,
            slot_tolerance_m=12.0,
            minimum_separation_m=0.0,
        )
        # Arrival is a pursuer-progress metric, not evidence that the target
        # has lost manoeuvring room.  Do not slow the target merely because a
        # fraction of the ring is nearby; the child controller keeps escaping
        # until the executed strict containment contract starts its hold.
        center = (
            target.x + velocity_x * 0.10,
            target.y + velocity_y * 0.10,
            target.z,
        )
        target_speed = math.hypot(velocity_x, velocity_y)
        for agent in primary_agents:
            slot = slots[agent.code]
            current = self.previous_scene.get(agent.code, (agent.x, agent.y, agent.z))
            final_slot = slot.point(center)
            slot_error = math.hypot(final_slot[0] - current[0], final_slot[1] - current[1])
            configured = float(child.config.get(
                "uavSpeedMps" if agent.type == "UAV" else "usvSpeedMps",
                5.0 if agent.type == "UAV" else 3.0,
            ))
            if agent.type == "UAV":
                speed = min(15.0, max(configured, target_speed + min(3.5, 0.5 + slot_error * 0.04)))
            else:
                speed = min(4.0, max(configured, target_speed + min(1.25, 0.3 + slot_error * 0.025)))
            # Reduce only in the last metres; never fall behind a translating
            # target while the ring is still closing.
            if slot_error < 7.0:
                speed = max(target_speed + 0.12, speed * max(0.35, slot_error / 7.0))
            step = max(0.02, speed * 0.1)
            # Approach a slot in polar coordinates. A straight chord can cross
            # the target or another member and become permanently rejected by
            # swept collision safety. Radial-then-tangential convergence keeps
            # circular order and gives every member a collision-free route.
            rel_x, rel_y = current[0] - center[0], current[1] - center[1]
            current_radius = max(1.0, math.hypot(rel_x, rel_y))
            current_angle = math.atan2(rel_y, rel_x)
            angular_error = (slot.angle - current_angle + math.pi) % (2.0 * math.pi) - math.pi
            radial_error = slot.radius - current_radius
            radial_step = max(-step * 0.55, min(step * 0.55, radial_error))
            angular_step = max(
                -step * 0.82 / max(8.0, current_radius),
                min(step * 0.82 / max(8.0, current_radius), angular_error),
            )
            if abs(angular_error) > math.pi * 0.52:
                waypoint_radius = current_radius + radial_step
                waypoint_angle = current_angle + angular_step
                desired = (
                    center[0] + math.cos(waypoint_angle) * waypoint_radius,
                    center[1] + math.sin(waypoint_angle) * waypoint_radius,
                    final_slot[2],
                )
            else:
                desired = final_slot
            dx, dy = desired[0] - current[0], desired[1] - current[1]
            distance = math.hypot(dx, dy)
            if distance <= step or distance < 1e-9:
                next_x, next_y = desired[0], desired[1]
            else:
                next_x = current[0] + dx * step / distance
                next_y = current[1] + dy * step / distance
            if math.hypot(next_x - current[0], next_y - current[1]) > 1e-6:
                agent.heading = math.degrees(math.atan2(next_y - current[1], next_x - current[0])) % 360.0
            remaining_slot_error = math.hypot(
                final_slot[0] - next_x, final_slot[1] - next_y
            )
            if agent.type == "USV" and remaining_slot_error <= 8.0:
                inward_heading = math.degrees(math.atan2(
                    target.y - next_y, target.x - next_x
                )) % 360.0
                current_heading = self._stable_headings.get(
                    agent.code, agent.heading
                )
                heading_delta = (
                    inward_heading - current_heading + 180.0
                ) % 360.0 - 180.0
                agent.heading = (
                    current_heading
                    + max(-6.0, min(6.0, heading_delta))
                ) % 360.0
                self._stable_headings[agent.code] = agent.heading
            elif agent.type == "USV":
                self._stable_headings[agent.code] = agent.heading
            agent.x, agent.y, agent.z = next_x, next_y, desired[2]
            agent.role = "RING_MEMBER"

        if support_agents:
            selected_support = self._selected_ring_slots(index, support_agents, target)
            support_center = (
                target.x + velocity_x * 0.10,
                target.y + velocity_y * 0.10,
                target.z,
            )
            for agent in support_agents:
                slot = selected_support[agent.code]
                current = self.previous_scene.get(
                    agent.code, (agent.x, agent.y, agent.z)
                )
                final_slot = slot.point(support_center)
                configured = float(child.config.get(
                    "uavSpeedMps" if agent.type == "UAV" else "usvSpeedMps",
                    5.0 if agent.type == "UAV" else 3.0,
                ))
                speed_limit = 15.0 if agent.type == "UAV" else 4.0
                speed = min(
                    speed_limit,
                    max(configured, target_speed + (1.2 if agent.type == "UAV" else 0.5)),
                )
                step = max(0.02, speed * 0.1)
                rel_x = current[0] - support_center[0]
                rel_y = current[1] - support_center[1]
                current_radius = max(1.0, math.hypot(rel_x, rel_y))
                current_angle = math.atan2(rel_y, rel_x)
                angular_error = (
                    slot.angle - current_angle + math.pi
                ) % (2.0 * math.pi) - math.pi
                radial_error = slot.radius - current_radius
                radial_step = max(
                    -step * 0.65, min(step * 0.65, radial_error)
                )
                angular_step = max(
                    -step * 0.7 / max(8.0, current_radius),
                    min(
                        step * 0.7 / max(8.0, current_radius),
                        angular_error,
                    ),
                )
                desired_radius = current_radius + radial_step
                desired_angle = current_angle + angular_step
                desired = (
                    support_center[0] + math.cos(desired_angle) * desired_radius,
                    support_center[1] + math.sin(desired_angle) * desired_radius,
                    final_slot[2],
                )
                if abs(angular_error) <= 0.10 and abs(radial_error) <= 5.0:
                    desired = final_slot
                dx, dy = desired[0] - current[0], desired[1] - current[1]
                distance = math.hypot(dx, dy)
                if distance <= step or distance < 1e-9:
                    next_x, next_y = desired[0], desired[1]
                else:
                    next_x = current[0] + dx * step / distance
                    next_y = current[1] + dy * step / distance
                if math.hypot(next_x - current[0], next_y - current[1]) > 1e-6:
                    agent.heading = math.degrees(math.atan2(
                        next_y - current[1], next_x - current[0]
                    )) % 360.0
                agent.x, agent.y, agent.z = next_x, next_y, desired[2]
                agent.role = "CAPTURE_RESERVE"

    def _executed_containment(
        self,
        index: int,
        child: CaptureAdapter,
        agents: List[AgentFrame],
        target: TargetFrame,
    ) -> Dict[str, object]:
        """Assess the poses that Unity actually receives after global safety."""
        if len(agents) < 3:
            return {
                "ready": False,
                "targetInside": False,
                "maxGapDeg": 360.0,
                "maxAllowedGapDeg": 0.0,
                "minimumRadiusM": 0.0,
                "maximumRadiusM": 0.0,
                "blocker": "POST_GLOBAL_INSUFFICIENT_AGENTS",
            }
        visible_chase_complete = (
            child.target_travelled_distance
            >= child.required_pursuit_distance + 20.0
        )
        slots = self._selected_ring_slots(index, agents, target)
        contract = assess_canonical_ring(
            self._as_ring_members(agents),
            (target.x, target.y, target.z),
            slots,
            slot_tolerance_m=3.5,
            minimum_separation_m=7.0,
            require_inward_usv_heading=True,
            usv_heading_tolerance_deg=3.0,
        )
        center = (target.x, target.y, target.z)
        detached_codes = [
            agent.code
            for agent in agents
            if math.hypot(
                agent.x - slots[agent.code].point(center)[0],
                agent.y - slots[agent.code].point(center)[1],
            ) > 3.5
        ]
        if any(code in self.unavailable_codes - self.withdrawn_codes for code in self.agent_code_maps[index].values()):
            blocker = "POST_GLOBAL_INVALID_PARTICIPANT"
        elif not visible_chase_complete:
            blocker = "PURSUIT_DISTANCE"
        elif not any(agent.type == "UAV" for agent in agents) or not any(agent.type == "USV" for agent in agents):
            blocker = "POST_GLOBAL_TYPE_LAYER"
        elif not contract.ready:
            blocker = f"POST_GLOBAL_{contract.blocker}"
        else:
            blocker = "NONE"
        return {
            "ready": blocker == "NONE",
            "targetInside": contract.target_inside,
            "maxGapDeg": contract.maximum_gap_deg,
            "maxAllowedGapDeg": contract.allowed_gap_deg,
            "minimumRadiusM": min(
                (math.hypot(agent.x - target.x, agent.y - target.y) for agent in agents),
                default=0.0,
            ),
            "maximumRadiusM": max(
                (math.hypot(agent.x - target.x, agent.y - target.y) for agent in agents),
                default=0.0,
            ),
            "radialSpreadM": contract.radial_spread_m,
            "sectorCount": len(agents),
            "coveredSectors": contract.arrived_count,
            "minimumSeparationM": contract.minimum_separation_m,
            "requiredSeparationM": 7.0,
            "uavCount": sum(agent.type == "UAV" for agent in agents),
            "usvCount": sum(agent.type == "USV" for agent in agents),
            "invalidParticipants": 0,
            "stationaryParticipants": 0,
            "detachedParticipants": len(agents) - contract.arrived_count,
            "participating": contract.arrived_count,
            "required": len(agents),
            "detachedParticipantCodes": detached_codes,
            "arrivalRatio": contract.arrival_ratio,
            "maximumSlotErrorM": contract.maximum_slot_error_m,
            "inwardOrientedUsvCount": contract.inward_oriented_usv_count,
            "maximumUsvHeadingErrorDeg": contract.maximum_usv_heading_error_deg,
            "blocker": blocker,
        }

    @staticmethod
    def _gap_repair_proposal(
        child: CaptureAdapter,
        agents: List[AgentFrame],
        target: TargetFrame,
    ) -> tuple[str, tuple[float, float, float], Dict[str, object]] | None:
        """Select one physical craft to close the executed ring's worst gap.

        Child solvers target ideal slots before the coordinator performs
        cross-target collision resolution.  When that final safety pass moves
        one craft, every child continuing at the same speed can preserve the
        new gap forever.  Repair the *executed* geometry with one ETA-selected
        blocker while its neighbours hold their sectors.
        """
        if len(agents) < 3:
            return None
        radii = {
            item.code: math.hypot(item.x - target.x, item.y - target.y)
            for item in agents
        }
        upper_radius = child.outer_formation_radius + 15.0
        farthest = max(agents, key=lambda item: radii[item.code])
        if radii[farthest.code] > upper_radius:
            angle = math.atan2(farthest.y - target.y, farthest.x - target.x)
            speed = 4.0 if farthest.type == "USV" else min(
                15.0, max(5.0, float(child.config.get("uavSpeedMps", 5.0))),
            )
            step = speed * 0.1
            desired_radius = max(13.5, radii[farthest.code] - step)
            return farthest.code, (
                target.x + math.cos(angle) * desired_radius,
                target.y + math.sin(angle) * desired_radius,
                farthest.z,
            ), {
                "gapFillerCode": farthest.code,
                "gapFillerSpeedMps": round(speed, 2),
                "gapRepairMode": "RADIAL_RECOVERY",
            }

        ordered = sorted(
            agents,
            key=lambda item: math.atan2(item.y - target.y, item.x - target.x) % (2.0 * math.pi),
        )
        angles = [
            math.atan2(item.y - target.y, item.x - target.x) % (2.0 * math.pi)
            for item in ordered
        ]
        gaps = [
            (angles[(index + 1) % len(angles)] - angles[index]) % (2.0 * math.pi)
            for index in range(len(angles))
        ]
        gap_index = max(range(len(gaps)), key=gaps.__getitem__)
        allowed = math.radians(maximum_capture_gap_deg(len(agents)))
        # Keep repairing to the geometric limit, even though the final latch
        # allows half a degree of integration noise.  This produces a visibly
        # closed executed ring (important for asymmetric 6+8) instead of
        # accepting the edge of the tolerance band as the resting shape.
        if gaps[gap_index] <= allowed + math.radians(0.02):
            return None

        left_index = gap_index
        right_index = (gap_index + 1) % len(ordered)
        left_other_gap = gaps[(left_index - 1) % len(gaps)]
        right_other_gap = gaps[right_index]
        # Moving the endpoint with the smaller opposite gap increases the
        # better-covered sector and therefore reduces the global maximum.
        use_left = left_other_gap <= right_other_gap
        filler = ordered[left_index if use_left else right_index]
        radius = max(13.5, min(child.outer_formation_radius + 8.0, radii[filler.code]))
        configured = float(child.config.get(
            "usvSpeedMps" if filler.type == "USV" else "uavSpeedMps",
            3.0 if filler.type == "USV" else 5.0,
        ))
        speed = min(4.0, max(configured, 3.6)) if filler.type == "USV" else min(
            15.0, max(configured, 6.5),
        )
        max_arc_step = speed * 0.1 / max(8.0, radius)
        required = min(gaps[gap_index] * 0.48, gaps[gap_index] - allowed + math.radians(2.0))
        angle_step = min(required, max_arc_step)
        desired_angle = angles[left_index] + angle_step if use_left else angles[right_index] - angle_step
        return filler.code, (
            target.x + math.cos(desired_angle) * radius,
            target.y + math.sin(desired_angle) * radius,
            filler.z,
        ), {
            "gapFillerCode": filler.code,
            "gapFillerSpeedMps": round(speed, 2),
            "gapRepairMode": "ANGULAR_INTERCEPT",
            "gapCenterDeg": round(math.degrees(
                (angles[left_index] + gaps[gap_index] * 0.5) % (2.0 * math.pi)
            ), 2),
        }

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        self._maybe_reassign_capture_groups()
        for index in range(len(self.children)):
            self._prepare_child_geometry(index)
        target_centers = [
            child.previous_scene.get("TARGET")
            for child in self.children
        ]
        for index, child in enumerate(self.children):
            child.peer_target_positions = [
                point for other_index, point in enumerate(target_centers)
                if other_index != index and point is not None
            ]
        child_frames = [child.step() for child in self.children]
        agents: List[AgentFrame] = []
        targets: List[TargetFrame] = []
        groups: List[Dict[str, object]] = []
        metrics_list = [frame.metrics for frame in child_frames]

        for index, frame in enumerate(child_frames):
            target_code = f"TARGET-{index + 1:03d}"
            group_id = f"CAPTURE-{index + 1:03d}"
            code_map = self.agent_code_maps[index]
            # A child latches `metrics.captured` at the exact frame where the
            # ring is confirmed; its presentation terminal flag can arrive
            # one polling frame later.  Use the latched signal consistently
            # for group state, progress and aggregate completion so the UI
            # cannot show "stage 3 / hold 0 / ENCIRCLEMENT" for one target.
            # Child solvers still provide target pursuit dynamics, but they no
            # longer own the multi-target completion signal. Only the emitted
            # canonical-ring contract below may promote this group.
            child_captured = False
            for agent in frame.agents:
                agents.append(AgentFrame(
                    code_map.get(agent.code, agent.code), agent.type,
                    agent.x, agent.y, agent.z, agent.heading, agent.role,
                    agent.status, group_id, target_code,
                ))
            target = frame.targets[0]
            raw_stage = int(frame.metrics.get("captureStage", 1) or 1)
            # Stage 3 is reserved for a latched capture.  Older child
            # frames can carry a stale stage value for one poll after a
            # geometry replan; clamp that value so the UI never presents
            # "ENCIRCLEMENT · stage 3/3".
            group_stage = 3 if child_captured else min(2, max(1, raw_stage))
            targets.append(TargetFrame(
                target_code, "CAPTURE_TARGET", target.x, target.y, target.z,
                target.heading, target.visible, group_id,
                "CAPTURED" if child_captured else str(frame.metrics.get("targetBehavior", frame.phase)),
                3,
            ))
            child_stage = str(frame.metrics.get("missionStage", frame.phase))
            groups.append({
                "threatCode": target_code,
                "state": "CAPTURED" if child_captured else child_stage,
                "missionStage": "COMPLETED" if child_captured else child_stage,
                "memberCount": len(frame.agents),
                "uavCount": sum(item.type == "UAV" for item in frame.agents),
                "usvCount": sum(item.type == "USV" for item in frame.agents),
                "progress": 1.0 if child_captured else frame.metrics.get("progress", 0.0),
                # Preserve the child solver's real closure diagnostics.  The
                # previous coordinator only copied progress and blocker, so
                # the UI rendered every multi-target group as stage 1/3,
                # arrival 0% and hold 0/0 even after the ring was complete.
                "stage": group_stage,
                "arrivalRatio": 1.0 if child_captured else frame.metrics.get("arrivalRatio", 0.0),
                "holdFrames": (
                    max(
                        int(frame.metrics.get("captureHoldFrames", 0)),
                        int(frame.metrics.get("requiredCaptureHoldFrames", 0)),
                    )
                    if child_captured
                    else int(frame.metrics.get("captureHoldFrames", 0))
                ),
                "holdRequiredFrames": frame.metrics.get("requiredCaptureHoldFrames", 0),
                "pursuitDistanceM": frame.metrics.get("targetTravelDistanceM", 0.0),
                "requiredPursuitDistanceM": frame.metrics.get("requiredPursuitDistanceM", 0.0),
                "targetSpeedMps": frame.metrics.get("targetSpeedMps", 0.0),
                "maximumSpeedMps": round(child.target_max_mps, 3),
                "targetTravelDistanceM": frame.metrics.get("targetTravelDistanceM", 0.0),
                "targetBehavior": frame.metrics.get("targetBehavior", ""),
                "intent": frame.metrics.get("targetIntent", frame.metrics.get("targetBehavior", "")),
                "intentConfidence": frame.metrics.get("targetIntentConfidence", 0.0),
                "targetHeadingDeg": frame.metrics.get("targetHeadingDeg", target.heading),
                "targetSpeedReason": frame.metrics.get("targetSpeedReason", ""),
                "gapCenterDeg": frame.metrics.get("gapCenterDeg", target.heading),
                "nearestInterceptorCode": code_map.get(
                    str(frame.metrics.get("nearestInterceptorCode", "")),
                    frame.metrics.get("nearestInterceptorCode"),
                ),
                "nearestInterceptorDistanceM": frame.metrics.get("nearestInterceptorDistanceM"),
                "assignmentStrategy": "GLOBAL_GB_CS",
                "plannerSource": frame.metrics.get("plannerSource", "GB_SFLA_CS"),
                "planRevision": frame.metrics.get("planRevision", 0),
                "plannerDiagnostics": frame.metrics.get("plannerDiagnostics", {}),
                "plannerGoals": {
                    code_map.get(code, code): goal
                    for code, goal in frame.metrics.get("plannerGoals", {}).items()
                },
                "assignmentRevision": self.dynamic_allocator.assignment_revision,
                "reassignmentCount": self.dynamic_allocator.reassignment_count,
                "recentReassignments": [
                    change for change in self.assignment_changes
                    if change.get("targetCode") == target_code
                    or change.get("previousTarget") == target_code
                ][-4:],
                "ringGeometryReady": frame.metrics.get("ringGeometryReady", False),
                "captureBlocker": "NONE" if child_captured else frame.metrics.get("captureBlocker", ""),
                "containmentConfidence": frame.metrics.get("containmentConfidence", 0.0),
                "stableContainmentFrames": frame.metrics.get("stableContainmentFrames", 0),
                "stableContainmentRequiredFrames": frame.metrics.get("requiredStableContainmentFrames", frame.metrics.get("requiredCaptureHoldFrames", 0)),
                "gapRepairRequired": frame.metrics.get("gapRepairRequired", False),
            })

        for index, (child, target) in enumerate(zip(self.children, targets)):
            group_agents = [
                agent for agent in agents
                if agent.assignedTargetCode == target.code
            ]
            self._command_canonical_ring(index, child, group_agents, target)

        # Child solvers are intentionally independent per target, but their
        # output shares one Unity ocean.  Enforce a final whole-fleet safety
        # pass so craft assigned to different targets cannot intersect while
        # their pursuit corridors cross.
        proposals = {
            agent.code: (agent.type, (agent.x, agent.y, agent.z))
            for agent in agents
        }
        fixed_targets = {
            target.code: (target.type, (target.x, target.y, target.z))
            for target in targets
        }
        globally_resolved = self.safety.resolve_group(
            proposals,
            self.previous_scene,
            fixed_targets,
        )
        global_adjustments = 0
        global_adjustments_by_target = {
            f"TARGET-{index + 1:03d}": 0
            for index in range(self.target_count)
        }
        agents_by_code = {agent.code: agent for agent in agents}
        for agent in agents:
            safe = globally_resolved[agent.code]
            if safe.adjusted:
                global_adjustments += 1
                global_adjustments_by_target[agent.assignedTargetCode] = (
                    global_adjustments_by_target.get(agent.assignedTargetCode, 0) + 1
                )
            agent.x, agent.y, agent.z = safe.x, safe.y, safe.z
            self.previous_scene[agent.code] = (safe.x, safe.y, safe.z)

        # Canonical slot tracking already closes angular gaps. Do not run a
        # second independent gap solver after global safety: that old pass moved
        # one endpoint off its slot and made the two controllers fight forever.
        repair_metrics: Dict[str, Dict[str, object]] = {}
        repairs = 0
        for target in targets:
            self.previous_scene[target.code] = (target.x, target.y, target.z)

        # Feed the executed global poses back into each child. Without this,
        # a child would plan the next frame from its pre-avoidance position and
        # repeatedly drive the renderer back into the same conflict.
        for child, code_map in zip(self.children, self.agent_code_maps):
            for local_code, global_code in code_map.items():
                executed = agents_by_code.get(global_code)
                if executed is None:
                    continue
                child.previous_scene[local_code] = (
                    executed.x,
                    executed.y,
                    executed.z,
                )
                agent_type = 0 if local_code.startswith("UAV-") else 1
                ordinal = int(local_code.rsplit("-", 1)[1])
                agent_id = (1000 if agent_type == 0 else 2000) + ordinal - 1
                for raw in child.env.agents:
                    if int(raw[6]) == agent_type and int(raw[7]) == agent_id:
                        raw[:3] = child._to_internal(
                            (executed.x, executed.y, executed.z),
                            executed.type,
                        )
                        break

        # Child metrics are computed before this coordinator resolves
        # cross-target collisions. Recheck each ring from the executed global
        # poses and invalidate a candidate/hold if global safety opened it.
        # This keeps the UI, the child state machine and Unity on one geometry.
        for index, (child, frame, target, group) in enumerate(zip(
            self.children, child_frames, targets, groups
        )):
            target_code = f"TARGET-{index + 1:03d}"
            executed_agents = [
                agent for agent in agents
                if (
                    agent.assignedTargetCode == target_code
                    and agent.code in self.ring_member_codes[index]
                )
            ]
            executed = self._executed_containment(index, child, executed_agents, target)
            gap_limit = max(1.0, float(executed.get("maxAllowedGapDeg", 0.0)))
            gap_score = max(
                0.0,
                min(1.0, gap_limit / max(gap_limit, float(executed.get("maxGapDeg", 360.0)))),
            )
            child.set_executed_containment_feedback(
                ready=bool(executed["ready"]),
                confidence=min(
                    1.0,
                    float(executed.get("arrivalRatio", 0.0)) * 0.62
                    + gap_score * 0.38,
                ),
                max_gap_deg=float(executed.get("maxGapDeg", 360.0)),
                allowed_gap_deg=float(executed.get("maxAllowedGapDeg", 0.0)),
            )
            frame.metrics["postGlobalContainmentReady"] = executed["ready"]
            frame.metrics["postGlobalTargetInsideFormation"] = executed["targetInside"]
            frame.metrics["postGlobalCombinedMaxGapDeg"] = executed["maxGapDeg"]
            frame.metrics["postGlobalMaxAllowedGapDeg"] = executed["maxAllowedGapDeg"]
            frame.metrics["postGlobalMinimumRadiusM"] = executed["minimumRadiusM"]
            frame.metrics["postGlobalMaximumRadiusM"] = executed["maximumRadiusM"]
            frame.metrics["globalAvoidanceCount"] = global_adjustments_by_target.get(target_code, 0)
            group["postGlobalContainmentReady"] = executed["ready"]
            group["postGlobalMaxGapDeg"] = executed["maxGapDeg"]
            group["postGlobalMaxAllowedGapDeg"] = executed["maxAllowedGapDeg"]
            group["arrivalRatio"] = executed.get("arrivalRatio", 0.0)
            group["ringMemberCount"] = len(executed_agents)
            group["arrivedMemberCount"] = executed.get("participating", 0)
            group["requiredMemberCount"] = executed.get("required", len(executed_agents))
            group["detachedParticipantCodes"] = executed.get(
                "detachedParticipantCodes", []
            )
            group["supportMemberCount"] = max(
                0, int(group.get("memberCount", 0)) - len(executed_agents)
            )
            group["maximumSlotErrorM"] = executed.get("maximumSlotErrorM")
            group["inwardOrientedUsvCount"] = executed.get(
                "inwardOrientedUsvCount", 0
            )
            group["maximumUsvHeadingErrorDeg"] = executed.get(
                "maximumUsvHeadingErrorDeg", 180.0
            )
            group["globalAvoidanceCount"] = global_adjustments_by_target.get(target_code, 0)
            group.update(repair_metrics.get(target_code, {}))
            ready = bool(executed["ready"])
            arrival = float(executed.get("arrivalRatio", 0.0))
            if ready or arrival > self.ring_best_arrival[index] + 0.02:
                self.ring_best_arrival[index] = arrival
                self.ring_stalled_frames[index] = 0
            elif executed.get("blocker") != "PURSUIT_DISTANCE":
                self.ring_stalled_frames[index] += 1
            repairable_local_stall = (
                arrival >= 0.50
                and self.ring_stalled_frames[index] >= 60
            )
            if not ready and repairable_local_stall:
                # Replace only the worst missing member when a same-type outer
                # reserve can reach that exact slot sooner. Already-arrived
                # members keep their identities and positions; this is the
                # visible gap-filler behaviour instead of a whole-ring shuffle.
                current_slots = self.ring_slots[index]
                executed_by_code = {
                    agent.code: agent for agent in executed_agents
                }
                missing = max(
                    executed_agents,
                    key=lambda agent: math.hypot(
                        agent.x - current_slots[agent.code].point(
                            (target.x, target.y, target.z)
                        )[0],
                        agent.y - current_slots[agent.code].point(
                            (target.x, target.y, target.z)
                        )[1],
                    ),
                    default=None,
                )
                reserve_agents = [
                    agent for agent in agents
                    if (
                        agent.assignedTargetCode == target_code
                        and agent.code not in self.ring_member_codes[index]
                        and missing is not None
                        and agent.type == missing.type
                    )
                ]
                promoted = None
                if missing is not None and reserve_agents:
                    missing_slot = current_slots[missing.code]
                    slot_point = missing_slot.point(
                        (target.x, target.y, target.z)
                    )
                    missing_distance = math.hypot(
                        missing.x - slot_point[0],
                        missing.y - slot_point[1],
                    )
                    candidate = min(
                        reserve_agents,
                        key=lambda agent: (
                            math.hypot(
                                agent.x - slot_point[0], agent.y - slot_point[1]
                            )
                            / max(
                                0.2,
                                float(child.config.get(
                                    "uavSpeedMps" if agent.type == "UAV" else "usvSpeedMps",
                                    5.0 if agent.type == "UAV" else 3.0,
                                )),
                            )
                        ),
                    )
                    candidate_distance = math.hypot(
                        candidate.x - slot_point[0],
                        candidate.y - slot_point[1],
                    )
                    if candidate_distance + 5.0 < missing_distance:
                        self.ring_member_codes[index].remove(missing.code)
                        self.ring_member_codes[index].add(candidate.code)
                        del current_slots[missing.code]
                        current_slots[candidate.code] = missing_slot
                        self.support_slots[index] = {}
                        promoted = candidate.code
                        group["gapFillerCode"] = candidate.code
                        group["replacedRingMemberCode"] = missing.code
                if promoted is not None:
                    self.ring_replans[index] += 1
                else:
                    # No reserve can take over this exact slot. Shift the
                    # whole-ring phase so a pair of crossed polar approaches
                    # cannot remain blocked behind each other indefinitely.
                    self.ring_slots[index] = {}
                    self._ensure_ring_slots(index, executed_agents, target)
                    self.ring_replans[index] += 1
                self.ring_stalled_frames[index] = 0
                self.ring_best_arrival[index] = arrival
                group["slotReplanned"] = True
            group["slotReplanCount"] = self.ring_replans[index]
            updated_hold, keep_latched_stage = self._update_executed_hold(
                index,
                self.executed_hold_frames[index],
                child.capture_hold_frames,
                executed,
            )
            self.executed_hold_frames[index] = updated_hold
            if updated_hold > 0:
                self.containment_stage_latched[index] = True
            if ready:
                group["postGlobalHoldFrames"] = updated_hold
                group["postGlobalHoldRequiredFrames"] = child.capture_hold_frames
                group["missionStage"] = "STABLE_CONTAINMENT"
                group["state"] = "STABLE_CONTAINMENT"
                group["captureBlocker"] = "HOLD_CONFIRMATION"
                frame.terminalStatus = None
                frame.metrics["captured"] = False
                frame.metrics["formationReady"] = True
                frame.metrics["captureBlocker"] = "HOLD_CONFIRMATION"
                target.state = "STABLE_CONTAINMENT"
                if self.mission_active and updated_hold >= child.capture_hold_frames:
                    child.confirm_executed_containment()
                    frame.terminalStatus = "COMPLETED"
                    frame.phase = "CAPTURED"
                    frame.metrics["captured"] = True
                    frame.metrics["formationReady"] = True
                    frame.metrics["captureHoldFrames"] = child.capture_hold_frames
                    frame.metrics["captureStage"] = 3
                    frame.metrics["captureBlocker"] = "NONE"
                    group["state"] = "CAPTURED"
                    group["stage"] = 3
                    group["progress"] = 1.0
                    group["holdFrames"] = child.capture_hold_frames
                    group["captureBlocker"] = "NONE"
                    target.state = "CAPTURED"
                continue
            if keep_latched_stage:
                group["localAction"] = "GAP_REPAIR"
                group["softFailureFrames"] = self.executed_soft_failure_frames[index]
                group["captureBlocker"] = executed["blocker"]
                group["state"] = "STABLE_CONTAINMENT"
                group["missionStage"] = group["state"]
                frame.terminalStatus = None
                frame.metrics["captured"] = False
                frame.metrics["formationReady"] = False
                frame.metrics["captureBlocker"] = executed["blocker"]
                target.state = "RECONFIGURING"
                continue
            self.executed_hold_frames[index] = 0
            self.executed_soft_failure_frames[index] = 0
            self.containment_stage_latched[index] = False
            child.containment_candidate_at_sequence = None
            child.formation_ready_at_sequence = None
            if child.captured_at_sequence is not None:
                # A child freezes its target when it first latches capture.
                # If global collision resolution later opens that ring, revoke
                # through the child state machine so the target resumes its
                # seeded breakout speed and the coordinator cannot own state.
                child.revoke_executed_containment()
            frame.terminalStatus = None
            if frame.phase == "CAPTURED":
                frame.phase = "ENCIRCLEMENT"
            frame.metrics["captured"] = False
            frame.metrics["formationReady"] = False
            frame.metrics["captureHoldFrames"] = 0
            frame.metrics["captureStage"] = 1
            frame.metrics["captureBlocker"] = executed["blocker"]
            group["state"] = frame.phase
            group["stage"] = 1
            group["holdFrames"] = 0
            group["captureBlocker"] = executed["blocker"]
            group["missionStage"] = (
                "STABLE_CONTAINMENT"
                if self.containment_stage_latched[index]
                else "ENCIRCLEMENT"
                if str(frame.metrics.get("missionStage", frame.phase)) in {
                    "ENCIRCLEMENT", "GAP_REPAIR", "STABLE_CONTAINMENT"
                } and (
                    repair_metrics.get(target_code)
                or executed["blocker"] in {"ANGULAR_GAP", "SECTOR_COVERAGE", "RADIAL_SPREAD"}
                )
                else str(frame.metrics.get("missionStage", frame.phase))
            )
            target.state = child.target_behavior_state

        # A child may have already latched its capture state in the vendor
        # environment while the adapter terminal flag is delayed by one
        # presentation frame.  Use both signals so the aggregate progress
        # cannot remain below 100% after every target has actually closed.
        completed = [
            frame.terminalStatus == "COMPLETED"
            or bool(frame.metrics.get("captured", False))
            for frame in child_frames
        ]
        all_completed = all(completed)
        phases = [frame.phase for frame in child_frames]
        if all(phase == "PREVIEW" for phase in phases):
            phase = "PREVIEW"
        elif all_completed:
            phase = "CAPTURED"
        elif "ESCAPE_PURSUIT" in phases:
            phase = "ESCAPE_PURSUIT"
        elif "INTERCEPTING" in phases:
            phase = "INTERCEPTING"
        else:
            phase = "ENCIRCLEMENT"

        group_stages = [str(group.get("missionStage", group.get("state", ""))) for group in groups]
        if all_completed:
            mission_stage = "COMPLETED"
        else:
            # The aggregate task displays the earliest unresolved target, not
            # the most advanced one. This prevents a completed subgroup from
            # making the global stepper jump forward and then back while a
            # second target is still filling its ring.
            stage_rank = {
                "ESCAPE": 0, "PURSUIT": 1, "INTERCEPT": 2,
                "ENCIRCLEMENT": 3, "STABLE_CONTAINMENT": 4,
            }
            unresolved_stages = [
                group_stages[index]
                for index, done in enumerate(completed)
                if not done
            ]
            mission_stage = min(
                unresolved_stages or ["ENCIRCLEMENT"],
                key=lambda item: stage_rank.get(item, 3),
            )
        report_rank = {
            "PREVIEW": 0, "ESCAPE": 0, "PURSUIT": 1, "INTERCEPT": 2,
            "ENCIRCLEMENT": 3, "STABLE_CONTAINMENT": 4, "COMPLETED": 5,
        }
        if all_completed:
            self.reported_mission_stage = "COMPLETED"
        elif self.reported_mission_stage == "COMPLETED":
            # COMPLETED is an authoritative live terminal state, not a
            # monotonic presentation milestone. The global collision pass can
            # reopen a ring after a child solver briefly considered it closed;
            # in that case the stepper must immediately return to the earliest
            # unresolved stage instead of showing completion below 100%.
            self.reported_mission_stage = mission_stage
        elif report_rank.get(mission_stage, 0) >= report_rank.get(self.reported_mission_stage, 0):
            self.reported_mission_stage = mission_stage
        mission_stage = self.reported_mission_stage

        def mean_metric(name: str) -> float:
            return sum(float(item.get(name, 0.0)) for item in metrics_list) / max(1, len(metrics_list))

        blockers = [
            f"TARGET-{index + 1:03d}:{item.get('captureBlocker')}"
            for index, item in enumerate(metrics_list)
            if not completed[index]
            and item.get("captureBlocker") not in {None, "", "NONE"}
        ]
        ring_diagnostics: Dict[str, object] = {}
        for index, item in enumerate(metrics_list):
            raw = item.get("ringDiagnostics", {})
            if isinstance(raw, Mapping):
                for name, diagnostic in raw.items():
                    ring_diagnostics[f"TARGET-{index + 1:03d}/{name}"] = diagnostic

        raw_progress = 1.0 if all_completed else min(
            0.999,
            sum(
                1.0 if done else float(metrics_list[index].get("progress", 0.0))
                for index, done in enumerate(completed)
            ) / max(1, len(completed)),
        )
        # Mission progress is a user-facing completion indicator. It must be
        # monotonic; changing containment quality is reported separately by
        # missionStage, captureBlocker and ring diagnostics.
        self.display_progress = max(self.display_progress, raw_progress)
        metrics: Dict[str, object] = {
            "targetCount": self.target_count,
            "visibleTargetCount": len(targets),
            "capturedTargetCount": sum(completed),
            "capturedThreatCount": sum(completed),
            "progress": round(self.display_progress, 3),
            "captured": all_completed,
            "formationReady": all(
                completed[index] or bool(item.get("formationReady", False))
                for index, item in enumerate(metrics_list)
            ),
            "ringGeometryReady": all(
                completed[index] or bool(item.get("ringGeometryReady", False))
                for index, item in enumerate(metrics_list)
            ),
            "captureAgents": sum(int(item.get("captureAgents", 0)) for item in metrics_list),
            "requiredCaptureAgents": sum(int(item.get("requiredCaptureAgents", 0)) for item in metrics_list),
            "targetTravelDistanceM": min(float(item.get("targetTravelDistanceM", 0.0)) for item in metrics_list),
            "requiredPursuitDistanceM": max(float(item.get("requiredPursuitDistanceM", 0.0)) for item in metrics_list),
            "targetSpeedMps": mean_metric("targetSpeedMps"),
            "targetBehavior": "MULTI_CAPTURE" if len(set(str(item.get("targetBehavior", "")) for item in metrics_list)) > 1 else str(metrics_list[0].get("targetBehavior", "")),
            "containmentConfidence": mean_metric("containmentConfidence"),
            "captureBlocker": " | ".join(blockers) if blockers else "NONE",
            "replanCount": sum(int(item.get("replanCount", 0)) for item in metrics_list),
            "avoidanceCount": sum(int(item.get("avoidanceCount", 0)) for item in metrics_list) + global_adjustments,
            "globalAvoidanceCount": global_adjustments,
            "captureHoldFrames": min(
                (
                    max(
                        int(item.get("captureHoldFrames", 0)),
                        int(item.get("requiredCaptureHoldFrames", 0)),
                    )
                    if completed[index]
                    else int(item.get("captureHoldFrames", 0))
                )
                for index, item in enumerate(metrics_list)
            ),
            "requiredCaptureHoldFrames": max(int(item.get("requiredCaptureHoldFrames", 0)) for item in metrics_list),
            "captureGroups": groups,
            "assignmentStrategy": "GLOBAL_GB_CS",
            "assignmentRevision": self.dynamic_allocator.assignment_revision,
            "reassignmentCount": self.dynamic_allocator.reassignment_count,
            "assignmentDecision": self.assignment_last_result,
            "recentAssignmentChanges": self.assignment_changes[-12:],
            "agentDecisions": [
                {
                    "deviceCode": agent.code,
                    "assignedTarget": agent.assignedTargetCode,
                    "role": agent.role,
                    "decisionReason": (
                        "GAP_INTERCEPT"
                        if agent.role in {"GAP_BLOCKER", "CAPTURE_RESERVE"}
                        else "PREDICTED_INTERCEPT"
                    ),
                }
                for agent in agents
            ],
            "missionStage": mission_stage,
            "stageSequence": ["ESCAPE", "PURSUIT", "INTERCEPT", "ENCIRCLEMENT", "STABLE_CONTAINMENT", "COMPLETED"],
            "ringDiagnostics": ring_diagnostics,
        }
        return RuntimeFrame(
            self.run_id, self.code, self.sequence, int(time.time() * 1000), phase,
            agents, targets, metrics, route=[], obstacles=[],
            terminalStatus="COMPLETED" if all_completed else None,
        )
