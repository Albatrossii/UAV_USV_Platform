"""Capture contracts for the already-scaled, near-shore demonstration target."""
from __future__ import annotations

import copy
import math
from itertools import combinations

import pytest

from runner import build_adapter
from test_shore_departure import check_speed, shore_config, slot_xyz, slots
from test_return_infrastructure import pose, xyz


ORIGIN = (-360.0, -285.0, 0.0)
SHORE_REFERENCE = (-308.0 / 3.0, -160.0)
ORIGINAL_TARGET = (-415.0, -285.0)
TARGET = tuple(anchor + (value - anchor) * (2.0 / 3.0)
               for anchor, value in zip(SHORE_REFERENCE, ORIGINAL_TARGET))
LOCAL_TARGET = (TARGET[0] - ORIGIN[0], TARGET[1] - ORIGIN[1], 0.0)


def near_shore_config(seed=42, **changes):
    config = shore_config(seed=seed, targetShoreDistanceRatio=2.0 / 3.0,
                          initialPosesCoordinateFrame="GLOBAL_ENU")
    config["initialPoses"] = [
        {"deviceCode": slot["deviceCode"],
         "eastM": slot["eastM"] + ORIGIN[0],
         "northM": slot["northM"] + ORIGIN[1],
         "upM": slot["upM"], "headingDeg": slot["headingDeg"]}
        for slot in config["returnInfrastructure"]["slots"]
    ] + [{"deviceCode": "TARGET-001", "eastM": TARGET[0], "northM": TARGET[1],
          "upM": 0.0, "headingDeg": 90.0}]
    config.update(changes)
    return config


@pytest.mark.parametrize("seed", [1, 42, 20260814])
def test_explicit_scaled_target_is_not_scaled_or_pushed_away_again(seed):
    config = near_shore_config(seed)
    original = copy.deepcopy(config)
    adapter = build_adapter("GB_SFLA_CS", 201, config)
    initial = adapter.step()
    assert config == original
    assert xyz(initial.targets[0]) == pytest.approx(LOCAL_TARGET)
    assert math.dist(TARGET, SHORE_REFERENCE) / math.dist(ORIGINAL_TARGET, SHORE_REFERENCE) == pytest.approx(2 / 3)
    assert [item["deviceCode"] for item in adapter.base.config["initialPoses"]] == [
        "UAV-001", "UAV-002", "UAV-003",
        "USV-001", "USV-002", "USV-003", "TARGET-001",
    ]
    for _ in range(30):
        frame = adapter.step()
        assert frame.targets == initial.targets
        assert frame.metrics["deploymentState"] == "PARKED"
        assert frame.metrics["progress"] == 0.0
        assert frame.terminalStatus is None
        for agent in frame.agents:
            assert xyz(agent) == slot_xyz(slots(config)[agent.code])
    assert adapter.base.sequence == 1


@pytest.mark.parametrize("seed", [1, 42, 20260814])
def test_near_shore_departure_is_safe_and_handoff_does_not_jump(seed):
    adapter = build_adapter("GB_SFLA_CS", 202, near_shore_config(seed))
    previous = adapter.step()
    assert xyz(previous.targets[0]) == pytest.approx(LOCAL_TARGET)
    initial_targets = copy.deepcopy(previous.targets)
    adapter.set_mission_active(True)
    minimum_target_distance = math.inf
    minimum_fleet_distance = math.inf
    for _ in range(4000):
        frame = adapter.step()
        check_speed(previous, frame)
        assert frame.targets == initial_targets
        assert adapter.base.sequence == 1
        target = frame.targets[0]
        for agent in frame.agents:
            if agent.type == "USV":
                distance = math.dist(xyz(agent), xyz(target))
                minimum_target_distance = min(minimum_target_distance, distance)
                required = adapter.base.safety.required_separation("USV", "TARGET")
                assert distance >= required - 1e-7, (agent.code, distance, xyz(agent))
        for first, second in combinations(frame.agents, 2):
            if first.type == second.type:
                minimum_fleet_distance = min(minimum_fleet_distance, math.dist(xyz(first), xyz(second)))
        if frame.metrics["deploymentState"] == "COMPLETE":
            break
        assert frame.phase == "DEPLOYING" and not adapter.base.mission_active
        previous = frame
    else:
        pytest.fail("near-shore deployment did not complete")
    assert minimum_fleet_distance >= 8
    following = adapter.step()
    for agent in following.agents:
        assert math.dist(xyz(agent), xyz(pose(frame, agent.code))) <= (1.5 if agent.type == "UAV" else .4) + 1e-7
    assert math.dist(xyz(following.targets[0]), xyz(frame.targets[0])) <= .5
    assert adapter.base.sequence == 2
    print(f"seed={seed}: departure fleet separation={minimum_fleet_distance:.6f}m; target separation={minimum_target_distance:.6f}m")


def test_legacy_explicit_pose_still_enforces_release_to_target_spawn_distance():
    config = near_shore_config()
    config.pop("targetShoreDistanceRatio")
    adapter = build_adapter("GB_SFLA_CS", 203, config)
    adapter.step()
    assembly = adapter._deployment_frame
    target = assembly.targets[0]
    # The near-shore release points replace the old offshore assembly grid.
    # This explicit target is already more than 90 m from them, so preserving
    # its requested position is now the correct contract.
    assert xyz(target) == pytest.approx(LOCAL_TARGET)
    assert min(math.dist((agent.x, agent.y), (target.x, target.y)) for agent in assembly.agents) >= 90.0


def test_explicit_near_shore_target_cannot_overlap_assembly_boat():
    config = near_shore_config()
    slot = next(
        item for item in config["returnInfrastructure"]["slots"]
        if item["deviceCode"] == "USV-003"
    )
    dx = slot["approachEastM"] - slot["eastM"]
    dy = slot["approachNorthM"] - slot["northM"]
    ratio = 16.0 / math.hypot(dx, dy)
    config["initialPoses"][-1].update(
        eastM=ORIGIN[0] + slot["eastM"] + dx * ratio,
        northM=ORIGIN[1] + slot["northM"] + dy * ratio,
    )
    with pytest.raises(ValueError, match="overlaps"):
        build_adapter("GB_SFLA_CS", 204, config)


def test_near_shore_mission_progresses_and_completes_without_shortcutting_pursuit():
    adapter = build_adapter("GB_SFLA_CS", 205, near_shore_config())
    frame = adapter.step()
    adapter.set_mission_active(True)
    for _ in range(4000):
        frame = adapter.step()
        if frame.metrics["deploymentState"] == "COMPLETE":
            break
    else:
        pytest.fail("near-shore deployment did not complete")
    assert frame.metrics["progress"] == 0.0
    assert frame.metrics["requiredPursuitDistanceM"] == 80.0
    for _ in range(5000):
        frame = adapter.step()
        if frame.metrics["captured"]:
            assert frame.metrics["targetTravelDistanceM"] >= frame.metrics["requiredPursuitDistanceM"]
            assert frame.metrics["progress"] == 1.0
            assert frame.terminalStatus == "COMPLETED"
            print(f"near-shore capture completed at frame={frame.sequence}, target travel={frame.metrics['targetTravelDistanceM']}m")
            return
    pytest.fail(f"near-shore capture did not complete: phase={frame.phase}, metrics={frame.metrics}")
