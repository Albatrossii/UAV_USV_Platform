"""Near-shore demo target placement is real initialization, never a render warp."""
from __future__ import annotations

import copy
import math
from itertools import combinations

import pytest

from app.adapters.adaptive_escort import AdaptiveEscortAdapter, BREACH_DISTANCE_M
from app.adapters.shore_target_layout import read_shore_target_layout
from runner import build_adapter
from test_shore_departure import check_speed, shore_config, slots, slot_xyz
from test_return_infrastructure import xyz


def near_config(**changes):
    return shore_config(targetShoreDistanceRatio=2 / 3, **changes)


def target_xy(adapter):
    return {item.code: (item.x, item.y) for item in adapter.protected + adapter.threats}


def test_opt_in_initialization_scales_both_original_target_types_exactly_once():
    config = near_config()
    original_config = copy.deepcopy(config)
    baseline = AdaptiveEscortAdapter(1, shore_config())
    near = AdaptiveEscortAdapter(2, config)
    layout = read_shore_target_layout(config)
    assert layout is not None
    assert (layout.east, layout.north) == pytest.approx((257 + 1 / 3, 125))
    for code, point in target_xy(baseline).items():
        changed = target_xy(near)[code]
        assert changed == pytest.approx(layout.position(*point))
        assert math.dist(changed, (layout.east, layout.north)) == pytest.approx(
            math.dist(point, (layout.east, layout.north)) * 2 / 3,
        )
    assert near._protected_start_positions == {item.code: (item.x, item.y) for item in near.protected}
    assert near.protected_start_x == {item.code: item.x for item in near.protected}
    assert near._escort_departure_distance() == 0
    assert [(p.destination_x, p.destination_y) for p in near.protected] == [
        (p.destination_x, p.destination_y) for p in baseline.protected
    ]
    assert config == original_config, "neither return berths nor caller configuration may be mutated"
    emitted = near.step()
    assert {t.code: (t.x, t.y) for t in emitted.targets} == target_xy(near)


def test_omitted_option_and_identity_ratio_preserve_legacy_encounter():
    assert read_shore_target_layout({}) is None
    old = AdaptiveEscortAdapter(1, shore_config())
    identity = AdaptiveEscortAdapter(1, shore_config(targetShoreDistanceRatio=1))
    assert old.protected == identity.protected
    assert old.threats == identity.threats
    assert old.vehicles == identity.vehicles
    for _ in range(25):
        left, right = old.step(), identity.step()
        assert left.agents == right.agents and left.targets == right.targets
        assert left.metrics == right.metrics


def test_global_and_local_berth_metadata_produce_same_local_placement():
    config = near_config()
    global_config = copy.deepcopy(config)
    global_config["returnInfrastructure"]["coordinateFrame"] = "GLOBAL_ENU"
    for slot in global_config["returnInfrastructure"]["slots"]:
        slot["eastM"] += config["fleetOrigin"]["eastM"]
        slot["northM"] += config["fleetOrigin"]["northM"]
    assert read_shore_target_layout(config) == read_shore_target_layout(global_config)
    assert target_xy(AdaptiveEscortAdapter(1, config)) == target_xy(AdaptiveEscortAdapter(2, global_config))


@pytest.mark.parametrize("ratio", [0, -1, 1.01, True, "0.666", float("nan"), float("inf")])
def test_invalid_ratio_is_rejected_without_silent_geometry_changes(ratio):
    with pytest.raises(ValueError, match="targetShoreDistanceRatio"):
        read_shore_target_layout(shore_config(targetShoreDistanceRatio=ratio))


@pytest.mark.parametrize("fault", ["not_shore", "no_berths", "nan_berth", "unknown_frame", "no_global_origin"])
def test_invalid_shore_reference_fails_closed(fault):
    config = near_config()
    if fault == "not_shore":
        config["startFromShore"] = False
    elif fault == "no_berths":
        config["returnInfrastructure"]["slots"] = []
    elif fault == "nan_berth":
        config["returnInfrastructure"]["slots"][-1]["eastM"] = float("nan")
    elif fault == "unknown_frame":
        config["returnInfrastructure"]["coordinateFrame"] = "UNKNOWN"
    else:
        config["returnInfrastructure"]["coordinateFrame"] = "GLOBAL_ENU"
        del config["fleetOrigin"]
    with pytest.raises(ValueError):
        read_shore_target_layout(config)


def test_unsafe_ratio_does_not_silently_clamp_target_into_water():
    with pytest.raises(ValueError, match="outside navigable water"):
        AdaptiveEscortAdapter(1, shore_config(targetShoreDistanceRatio=.1))


@pytest.mark.parametrize("seed", [1, 42, 20260814])
def test_near_shore_targets_wait_for_departure_then_complete_continuous_safe_mission(seed):
    adapter = build_adapter("ESCORT_GUARD", 99, near_config(seed=seed))
    initial = adapter.step()
    for _ in range(15):
        frame = adapter.step()
        assert frame.targets == initial.targets
        for agent in frame.agents:
            assert xyz(agent) == slot_xyz(slots(adapter.config)[agent.code])
            assert agent.status == "PARKED"
    adapter.set_mission_active(True)
    previous = frame
    complete = False
    minimum_margin = math.inf
    phases = set()
    for _ in range(8000):
        frame = adapter.step()
        phases.add(frame.phase)
        if not complete:
            check_speed(previous, frame)
            assert frame.targets == initial.targets, "targets must not drift during shore deployment"
            assert adapter.base.sequence == 1
            boats = [agent for agent in frame.agents if agent.type == "USV"]
            for boat in boats:
                for target in frame.targets:
                    required = adapter.base.safety.required_separation(boat.type, target.type)
                    assert math.dist(xyz(boat), xyz(target)) >= required - .02, (seed, frame.sequence, boat.code, target.code)
            for first, second in combinations(boats, 2):
                assert math.dist(xyz(first), xyz(second)) >= 8, "preserve the existing departure corridor separation"
            complete = frame.metrics["deploymentState"] == "COMPLETE"
        else:
            prior_agents = {item.code: item for item in previous.agents}
            prior_targets = {item.code: item for item in previous.targets}
            for agent in frame.agents:
                old = prior_agents[agent.code]
                # The existing capture controller assigns discrete UAV flight
                # layers when roles change. Check full 3-D motion at the shore
                # handoff and horizontal continuity throughout the old mission.
                if adapter.base.sequence == 2:
                    assert math.dist(xyz(agent), xyz(old)) <= (1.5 if agent.type == "UAV" else .4) + 1e-7
                assert math.dist((agent.x, agent.y), (old.x, old.y)) <= (1.5 if agent.type == "UAV" else .4) + 1e-7
            for target in frame.targets:
                old = prior_targets[target.code]
                assert math.dist((old.x, old.y), (target.x, target.y)) <= .281, "target initialization must never be reapplied to output frames"
            for first, second in combinations(frame.agents + frame.targets, 2):
                distance = math.dist(xyz(first), xyz(second))
                required = adapter.base.safety.required_separation(first.type, second.type, first.z, second.z)
                minimum_margin = min(minimum_margin, distance - required)
                assert distance >= required - .02, (seed, frame.sequence, first.code, second.code)
        previous = frame
        if frame.terminalStatus:
            break
    assert complete and frame.terminalStatus == "COMPLETED"
    assert "DEPLOYING" in phases and "ACTIVE_CAPTURE" in phases
    assert adapter.base.min_protected_threat_distance > BREACH_DISTANCE_M
    assert minimum_margin >= -.02
    print(f"near-shore seed={seed}: completed sequence={frame.sequence}, minimum hull margin={minimum_margin:.3f}m")
