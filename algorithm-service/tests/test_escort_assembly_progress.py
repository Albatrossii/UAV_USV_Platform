"""Close-protection progress is observed geometry, never elapsed time."""
from __future__ import annotations

import math
from copy import deepcopy

import pytest

from app.adapters.adaptive_escort import AdaptiveEscortAdapter
from app.schemas import AgentFrame
from runner import build_adapter
from test_shore_departure import shore_config


def assembly_subject():
    adapter = AdaptiveEscortAdapter(99201, {
        "uavCount": 3, "usvCount": 3, "seed": 42,
        "shoreDeploymentMode": "SAFE_RELEASE",
    })
    adapter.set_mission_active(False)
    return adapter


def observations(adapter, uav_error, usv_error):
    guards = [v for v in adapter.vehicles if v.role == "CLOSE_GUARD"]
    result = []
    for item in guards:
        position = adapter._convoy_guard_slot_by_code.get(item.code, 0)
        x, y = adapter._convoy_guard_point(position, len(guards))
        result.append(AgentFrame(
            item.code, item.kind, x + (uav_error if item.kind == "UAV" else usv_error),
            y, item.z, 0.0, "CLOSE_GUARD",
        ))
    return result


def reconcile(adapter, agents, unavailable=()):
    adapter.reconcile_execution(agents, {agent.code: agent for agent in agents}, unavailable)


def initialized_assembly():
    adapter = assembly_subject()
    reconcile(adapter, observations(adapter, 108, 208))
    adapter.set_mission_active(True)
    return adapter


def test_unobserved_preview_and_parked_poses_cannot_initialize_or_complete_assembly():
    adapter = assembly_subject()
    preview = adapter.step()
    assert preview.metrics["escortAssemblyProgress"] == 0
    assert not adapter._escort_assembly_initial_errors
    parked = observations(adapter, 0, 0)
    for agent in parked:
        agent.status, agent.role = "PARKED", "DEPLOYMENT"
    reconcile(adapter, parked)
    assert adapter._escort_assembly_progress == 0
    assert not adapter._escort_assembly_initial_errors
    # First authoritative release establishes actual 108/208 m baselines,
    # not the preview's initially perfect offshore guard positions.
    reconcile(adapter, observations(adapter, 108, 208))
    assert sorted(adapter._escort_assembly_initial_errors.values()) == [108, 208]
    assert adapter._escort_assembly_progress == 0


def test_zero_ready_guards_show_real_distance_closed_against_same_arrival_tolerance():
    adapter = initialized_assembly()
    reconcile(adapter, observations(adapter, 58, 108))
    assert adapter._protection_status()[0] == 0
    assert adapter._escort_assembly_progress == pytest.approx(.5)
    reconcile(adapter, observations(adapter, 33, 58))
    assert adapter._protection_status()[0] == 0
    assert adapter._escort_assembly_progress == pytest.approx(.75)
    assert not adapter._escort_assembly_completed


def test_time_repeated_snapshots_and_pause_do_not_add_progress():
    adapter = initialized_assembly()
    actual = observations(adapter, 58, 108)
    reconcile(adapter, actual)
    initial = adapter._escort_assembly_progress
    for _ in range(5):
        adapter.sequence += 10000
        reconcile(adapter, actual)
    assert adapter._escort_assembly_progress == initial
    adapter.set_mission_active(False)
    reconcile(adapter, observations(adapter, 0, 0))
    assert adapter._escort_assembly_progress == initial
    assert not adapter._escort_assembly_completed


@pytest.mark.parametrize("invalid", ["MISSING", "NAN", "PARKED", "OPERATOR", "UNAVAILABLE"])
def test_unobserved_invalid_and_operator_poses_do_not_gain_credit(invalid):
    adapter = initialized_assembly()
    actual = observations(adapter, 0, 0)
    unavailable = ()
    if invalid == "MISSING":
        actual = []
    elif invalid == "NAN":
        for agent in actual:
            agent.z = math.nan
    elif invalid == "PARKED":
        for agent in actual:
            agent.status = "PARKED"
    elif invalid == "OPERATOR":
        for agent in actual:
            agent.role = "OPERATOR_CONTROLLED"
    else:
        unavailable = tuple(agent.code for agent in actual)
    reconcile(adapter, actual, unavailable)
    assert adapter._escort_assembly_progress == 0
    assert not adapter._escort_assembly_completed


def test_arrival_boundary_latches_completed_initial_phase_and_new_run_is_independent():
    adapter = initialized_assembly()
    reconcile(adapter, observations(adapter, 8 + 1e-9, 8 + 1e-9))
    assert adapter._escort_assembly_progress < 1
    assert round(adapter._escort_assembly_progress, 6) < 1
    assert not adapter._escort_assembly_completed
    reconcile(adapter, observations(adapter, 8, 8))
    assert adapter._protection_status()[0] == 2
    assert adapter._escort_assembly_progress == 1
    assert adapter._escort_assembly_completed
    # Later withdrawal/replacement belongs to the mission, not a new initial
    # departure stage. Even unavailable or missing replacements cannot erase it.
    adapter._guard_replacement_pending = True
    reconcile(adapter, observations(adapter, 200, 300))
    reconcile(adapter, [])
    assert adapter._escort_assembly_progress == 1
    reopened = assembly_subject()
    assert reopened._escort_assembly_progress == 0
    assert not reopened._escort_assembly_initial_errors


def test_observing_progress_does_not_mutate_tactical_slot_cache_or_mission_progress():
    adapter = initialized_assembly()
    actual = observations(adapter, 58, 108)
    adapter.threats[0].detected_frame = adapter.sequence
    adapter._guard_sector_angle, adapter._guard_sector_sequence = .75, -1
    before = deepcopy((adapter._guard_sector_angle, adapter._guard_sector_sequence,
                       adapter._display_progress, adapter._shore_assembly_pending))
    adapter._observe_escort_assembly({agent.code: agent for agent in actual})
    assert (adapter._guard_sector_angle, adapter._guard_sector_sequence,
            adapter._display_progress, adapter._shore_assembly_pending) == before


def test_real_shore_scene_progresses_before_first_guard_arrives_and_pause_freezes():
    adapter = build_adapter("ESCORT_GUARD", 99202, shore_config(
        seed=42, targetShoreDistanceRatio=2 / 3,
    ))
    frame = adapter.step()
    assert frame.metrics["escortAssemblyProgress"] == 0
    adapter.set_mission_active(True)
    samples = []
    for _ in range(1200):
        frame = adapter.step()
        progress = frame.metrics["escortAssemblyProgress"]
        if frame.metrics["deploymentState"] != "COMPLETE":
            assert progress == 0
        elif frame.metrics["escortProtectionReadyCount"] == 0:
            samples.append(progress)
            if len(samples) == 40:
                adapter.set_mission_active(False)
                references = dict(adapter.base._escort_assembly_initial_errors)
                for _ in range(10):
                    paused = adapter.step()
                    assert paused.metrics["escortAssemblyProgress"] == progress
                    assert adapter.base._escort_assembly_initial_errors == references
                adapter.set_mission_active(True)
        if progress == 1:
            break
    assert len(samples) >= 40
    assert samples[-1] > samples[30] > samples[10] > samples[0]
    assert progress == 1
    assert frame.metrics["escortProtectionReadyCount"] == 2
    assert adapter.base._escort_assembly_completed
