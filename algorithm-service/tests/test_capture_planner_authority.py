"""Optimisation must affect executable goals, not only diagnostic counters."""
import contextlib
import io
import math
from dataclasses import replace

import numpy as np
import pytest

from app.adapters.capture import CaptureAdapter
from app.adapters.adaptive_capture import AdaptiveCaptureAdapter


def quiet_adapter(cls=CaptureAdapter, **config):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cls(9701, {"uavCount": 3, "usvCount": 3, "seed": 42, **config})


def quiet_step(adapter):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return adapter.step()


def test_sfla_phase_radius_and_height_all_change_actual_goals_without_candidate_side_effects():
    adapter = quiet_adapter()
    algorithm = adapter.env.algorithm
    algorithm._begin_step_cache()
    ids = [int(row[7]) for row in adapter.env.agents]
    baseline = np.asarray([0.0, 1.0, 0.96])
    first = algorithm._plan_to_waypoints(ids, 0, baseline, commit=True)
    ownership = {key: dict(value["slots"]) for key, value in algorithm.formation_slot_assignment.items()}
    points = {key: value["points"].copy() for key, value in algorithm.formation_slot_assignment.items()}
    committed = {key: value.copy() for key, value in algorithm.desired_slots.items()}
    for parameter, change in ((0, 0.08), (1, 0.02), (2, 0.02)):
        varied = baseline.copy()
        varied[parameter] += change
        candidate = algorithm._plan_to_waypoints(ids, 0, varied)
        assert max(float(np.linalg.norm(candidate[i]-first[i])) for i in ids) > 0.1
        algorithm._local_plan_cost(ids, 0, varied)
        for key, record in algorithm.formation_slot_assignment.items():
            assert record["slots"] == ownership[key]
            np.testing.assert_array_equal(record["points"], points[key])
        for key in committed:
            np.testing.assert_array_equal(algorithm.desired_slots[key], committed[key])


def test_selected_sfla_plan_improves_cost_with_bounded_changes_and_stable_ownership():
    adapter = quiet_adapter()
    algorithm = adapter.env.algorithm
    algorithm._begin_step_cache()
    ids = [int(row[7]) for row in adapter.env.agents]
    previous = np.array([0.0, 1.0, 0.96])
    for _ in range(3):
        algorithm._sfla_target_waypoints(ids, 0)
        chosen = algorithm.plan_params_by_target[0]
        diagnostics = algorithm.last_plan_diagnostics[0]
        assert diagnostics["selectedCost"] <= diagnostics["baselineCost"] + 1e-10
        assert abs((chosen[0]-previous[0]+math.pi) % math.tau-math.pi) <= math.radians(3)+1e-10
        assert np.max(np.abs(chosen[1:]-previous[1:])) <= 0.010000001
        previous = chosen.copy()
    assert not np.allclose(previous, [0.0, 1.0, 0.96])


def test_vendor_latch_is_independent_of_real_granular_ball_capacity():
    adapter = quiet_adapter(uavCount=6, usvCount=6)
    assert adapter.source.MIN_CAPTURE_AGENTS == 3
    assert adapter.env.capture_latch_enabled is False
    algorithm = adapter.env.algorithm
    adapter.env.agents[:6, :3] = (0, 0, 0)
    adapter.env.agents[6:, :3] = (500, 0, 0)
    algorithm._begin_step_cache()
    ball = algorithm._new_ball([int(row[7]) for row in adapter.env.agents])
    assert algorithm._split_candidate(ball) is not None
    adapter.env._update_capture_state()
    assert not adapter.env.permanently_captured


def test_global_gb_cs_has_all_targets_and_preserves_mixed_capacity_in_every_candidate():
    adapter = quiet_adapter(AdaptiveCaptureAdapter, uavCount=6, usvCount=6, targetCount=2)
    quiet_step(adapter)
    assert adapter.global_environment.agents.shape == (12, 8)
    assert len(adapter.global_environment.targets) == 2
    planner = adapter.global_planner
    assert planner.plan_revision == 1
    assert adapter.assignment_last_result["globalTargetCount"] == 2
    original = dict(planner.last_assignments)
    saw_exchange = False
    for _ in range(10):
        candidate = planner._mutate_assignment(original, [0, 1])
        saw_exchange |= candidate != original
        for target in (0, 1):
            for kind in (0, 1):
                assert sum(value == target and int(planner._active_agent_map()[key][6]) == kind
                           for key, value in candidate.items()) == 3
    assert saw_exchange


def test_multitarget_controller_and_completion_read_the_selected_sfla_geometry():
    adapter = quiet_adapter(AdaptiveCaptureAdapter, uavCount=6, usvCount=6, targetCount=2)
    quiet_step(adapter)
    frame = quiet_step(adapter)
    for index, child in enumerate(adapter.children):
        group = [agent for agent in frame.agents if agent.assignedTargetCode == f"TARGET-{index+1:03d}"]
        selected = adapter._selected_ring_slots(index, group, frame.targets[index])
        planned = child._planned_formation_slots()
        for local, global_code in adapter.agent_code_maps[index].items():
            assert selected[global_code].radius == pytest.approx(planned[local].radius)
            assert selected[global_code].angle == pytest.approx(planned[local].angle)
            assert selected[global_code].altitude == pytest.approx(planned[local].altitude)


def test_capture_reconciles_true_pose_and_availability_without_restarting_mission():
    adapter = quiet_adapter()
    quiet_step(adapter)
    frame = quiet_step(adapter)
    previous = {agent.code: agent for agent in frame.agents}
    actual = [replace(agent, x=agent.x+12, heading=137.0) if agent.code == "USV-001" else agent
              for agent in frame.agents]
    before = (adapter.sequence, adapter.target_travelled_distance, adapter.target_start_scene)
    adapter.reconcile_execution(actual, previous, ["USV-001"])
    row = next(row for row in adapter.env.agents if int(row[7]) == 2000)
    moved = next(agent for agent in actual if agent.code == "USV-001")
    assert adapter._to_scene(row[:3], "USV") == pytest.approx((moved.x, moved.y, moved.z))
    assert math.degrees(row[4]) == pytest.approx(137)
    assert before == (adapter.sequence, adapter.target_travelled_distance, adapter.target_start_scene)
    speed = row[3]
    adapter.reconcile_execution(actual, {agent.code: agent for agent in actual}, [])
    assert row[3] == speed  # availability-only reconciliation preserves speed
    assert adapter.rejoin_target("USV-001") is not None


@pytest.mark.parametrize("cls", [CaptureAdapter, AdaptiveCaptureAdapter])
def test_returned_uav_withdraws_and_remaining_five_can_complete(cls):
    adapter = quiet_adapter(cls, targetCount=1)
    previous = quiet_step(adapter)
    returned = [replace(agent, status="RETURNED") if agent.code == "UAV-001" else agent
                for agent in previous.agents]
    adapter.reconcile_execution(returned, {agent.code: agent for agent in previous.agents}, ["UAV-001"])
    for _ in range(2600):
        frame = quiet_step(adapter)
        adapter.reconcile_execution(frame.agents, {agent.code: agent for agent in previous.agents}, ["UAV-001"])
        if frame.terminalStatus:
            break
        previous = frame
    assert frame.terminalStatus == "COMPLETED", frame.metrics
    assert len(frame.agents) == 6  # returned hull remains visible, outside evidence
    assert next(agent for agent in frame.agents if agent.code == "UAV-001").status == "RETURNED"
    if isinstance(adapter, CaptureAdapter):
        assert frame.metrics["requiredCaptureAgents"] == 5
        assert frame.metrics["captureAgents"] == 5
        assert 1000 not in adapter.env.guarding_agents[0]
    else:
        assert frame.metrics["captureGroups"][0]["requiredMemberCount"] == 5
        assert len(adapter.ring_member_codes[0]) == 5


@pytest.mark.parametrize("status", ["HOLD", "RETURNING"])
def test_temporary_unavailability_cannot_supply_containment_evidence(status):
    adapter = quiet_adapter()
    frame = quiet_step(adapter)
    actual = [replace(agent, status=status) if agent.code == "UAV-001" else agent for agent in frame.agents]
    adapter.reconcile_execution(actual, {agent.code: agent for agent in frame.agents}, ["UAV-001"])
    assert not adapter.withdrawn_codes
    frame = quiet_step(adapter)
    assert frame.terminalStatus is None
    assert frame.metrics["requiredCaptureAgents"] == 6
    assert frame.metrics["containmentContract"]["invalidParticipants"] == 1


def test_global_capacity_drops_returned_member_without_renumbering_other_agents():
    adapter = quiet_adapter(AdaptiveCaptureAdapter, uavCount=6, usvCount=6, targetCount=2)
    previous = quiet_step(adapter)
    actual = [replace(agent, status="RETURNED") if agent.code == "UAV-001" else agent for agent in previous.agents]
    original_ids = dict(adapter.global_agent_ids)
    adapter.reconcile_execution(actual, {agent.code: agent for agent in previous.agents}, ["UAV-001"])
    frame = quiet_step(adapter)
    assert len(frame.agents) == 12
    assert adapter.global_agent_ids == original_ids
    assert len(adapter.global_environment.agents) == 11
    assert sum(capacity[0] for capacity in adapter.global_environment.assignment_type_capacities.values()) == 5
    assert original_ids["UAV-001"] not in adapter.global_planner.last_assignments
    assert sum(len(members) for members in adapter.ring_member_codes) == 11


def test_withdrawal_does_not_remove_the_required_air_surface_capability():
    adapter = quiet_adapter(AdaptiveCaptureAdapter, targetCount=1)
    frame = quiet_step(adapter)
    actual = [replace(agent, status="RETURNED") if agent.type == "UAV" else agent for agent in frame.agents]
    returned_codes = [agent.code for agent in actual if agent.status == "RETURNED"]
    adapter.reconcile_execution(actual, {agent.code: agent for agent in frame.agents}, returned_codes)
    child = adapter.children[0]
    child.target_travelled_distance = child.required_pursuit_distance + 20.0
    frame = quiet_step(adapter)
    assert frame.terminalStatus is None
    assert frame.metrics["captureGroups"][0]["captureBlocker"] == "POST_GLOBAL_TYPE_LAYER"
