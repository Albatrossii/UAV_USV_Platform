"""Native berth returns use executed poses and finish only at the real berth."""
from __future__ import annotations

import math
from itertools import combinations

import pytest

from app.navigation.safety import SceneSafetyFilter
from runner import build_adapter
from test_return_infrastructure import pose, xyz
from test_shore_departure import shore_config, slot_xyz, slots


ALGORITHMS = ("ESCORT_GUARD", "GB_SFLA_CS")
USVS = tuple(f"USV-{number:03d}" for number in range(1, 4))
DT = 0.1
SAFETY = SceneSafetyFilter()


def subject(algorithm: str, elapsed: float = 0):
    changes = {"targetShoreDistanceRatio": 2 / 3}
    if algorithm == "ESCORT_GUARD":
        changes["escortStartAtBerth"] = True
    adapter = build_adapter(algorithm, 99361, shore_config(**changes))
    frame = adapter.step()
    if elapsed:
        adapter.set_mission_active(True)
        for _ in range(round(elapsed / DT)):
            frame = adapter.step()
        assert frame.metrics["simulationElapsedSeconds"] == elapsed
    return adapter, frame


def heading_change(before: float, after: float) -> float:
    return abs((after - before + 180) % 360 - 180)


def swept_distance(before_a, after_a, before_b, after_b) -> float:
    start = tuple(a - b for a, b in zip(xyz(before_a), xyz(before_b)))
    end = tuple(a - b for a, b in zip(xyz(after_a), xyz(after_b)))
    change = tuple(b - a for a, b in zip(start, end))
    norm = sum(value * value for value in change)
    time = max(0.0, min(1.0, -sum(a * b for a, b in zip(start, change)) / norm)) if norm else 0.0
    return math.sqrt(sum((a + time * b) ** 2 for a, b in zip(start, change)))


def assert_return_frame(adapter, before, frame, returning):
    layout = slots(adapter.config)
    for code in returning:
        prior, current = pose(before, code), pose(frame, code)
        assert all(math.isfinite(value) for value in (*xyz(current), current.heading))
        assert current.z == pytest.approx(layout[code]["upM"], abs=1e-9)
        assert math.dist(xyz(prior), xyz(current)) <= adapter.config["usvSpeedMps"] * DT + 1e-8, code
        assert heading_change(prior.heading, current.heading) <= 6.0 + 1e-8, code
        state = frame.metrics["deviceControlStates"][code]
        assert state["parkingSlot"] == code
        if current.status == "RETURNED":
            assert xyz(current) == pytest.approx(slot_xyz(layout[code]), abs=1e-7)
            assert heading_change(current.heading, layout[code]["headingDeg"]) < 1e-6
            assert state["returnedAtHome"] is True
        else:
            assert current.status == "RETURNING"
            assert state["returnedAtHome"] is False
    for left, right in combinations(frame.agents, 2):
        if left.code not in returning and right.code not in returning:
            continue
        required = SAFETY.required_separation(left.type, right.type, left.z, right.z)
        actual = swept_distance(pose(before, left.code), left, pose(before, right.code), right)
        assert actual >= required - 1e-6, (left.code, right.code, actual, required)


def finish_returns(adapter, frame, codes, max_steps=2200):
    distances = dict.fromkeys(codes, 0.0)
    for _ in range(max_steps):
        before, frame = frame, adapter.step()
        assert_return_frame(adapter, before, frame, codes)
        for code in codes:
            distances[code] += math.dist(xyz(pose(before, code)), xyz(pose(frame, code)))
        if all(pose(frame, code).status == "RETURNED" for code in codes):
            return frame, distances
    pytest.fail(f"native returns did not dock: {frame.metrics['deviceControlStates']}")


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_return_at_original_berths_does_not_leave_port_or_visit_offshore_anchor(algorithm):
    adapter, frame = subject(algorithm)
    initial = {code: xyz(pose(frame, code)) for code in USVS}
    for code in USVS:
        adapter.control_device(code, "USV_RETURN")
    adapter.set_mission_active(True)
    frame, distances = finish_returns(adapter, frame, USVS, max_steps=20)
    assert distances == dict.fromkeys(USVS, 0.0)
    for _ in range(10):
        before, frame = frame, adapter.step()
        assert_return_frame(adapter, before, frame, USVS)
        for code in USVS:
            assert adapter.control_device(code, "USV_RETURN") == "RETURNED"
            assert xyz(pose(frame, code)) == initial[code]


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("elapsed", (40.0, 60.0))
def test_midmission_usv002_takes_short_safe_return_instead_of_old_245m_detour(algorithm, elapsed):
    adapter, frame = subject(algorithm, elapsed)
    code = "USV-002"
    start = xyz(pose(frame, code))
    home = slot_xyz(slots(adapter.config)[code])
    adapter.control_device(code, "USV_RETURN")
    override = adapter._overrides[code]
    direct_safe = adapter._native_return_planner.route_clear(
        start, home, adapter._return_obstacles(code, override),
    )
    travelled, first_movement = 0.0, None
    for _ in range(2200):
        before, frame = frame, adapter.step()
        assert_return_frame(adapter, before, frame, (code,))
        previous, current = xyz(pose(before, code)), xyz(pose(frame, code))
        step = math.dist(previous, current)
        travelled += step
        if first_movement is None and step > 1e-7:
            first_movement = tuple(b - a for a, b in zip(previous, current))
            toward_berth = sum(delta * (goal - origin) for delta, goal, origin in zip(first_movement, home, start))
            assert toward_berth >= -1e-7 or not direct_safe, "a certified direct return must not start in the opposite direction"
        if pose(frame, code).status == "RETURNED":
            break
    else:
        pytest.fail(f"{algorithm} at {elapsed}s did not dock: {frame.metrics['deviceControlStates'][code]}")
    assert first_movement is not None
    # The reported regression travelled about 245 m via a compulsory offshore
    # anchor. A 20% reduction is a conservative, independently fixed threshold.
    assert travelled < 245.0 * .8, (algorithm, elapsed, travelled, math.dist(start, home))


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_hold_rereturn_and_pause_replan_from_real_pose_while_three_boats_dock(algorithm):
    adapter, frame = subject(algorithm, 40.0)
    code = "USV-002"
    adapter.control_device(code, "USV_RETURN")
    travelled = 0.0
    for _ in range(300):
        before, frame = frame, adapter.step()
        assert_return_frame(adapter, before, frame, (code,))
        travelled += math.dist(xyz(pose(before, code)), xyz(pose(frame, code)))
        if travelled >= 6.0:
            break
    assert travelled >= 6.0 and pose(frame, code).status == "RETURNING"
    held = (*xyz(pose(frame, code)), pose(frame, code).heading)
    adapter.control_device(code, "USV_HOLD")
    for _ in range(12):
        frame = adapter.step()
        assert (*xyz(pose(frame, code)), pose(frame, code).heading) == held
    for other in USVS:
        adapter.control_device(other, "USV_RETURN")
    override = adapter._overrides[code]
    assert (override.x, override.y, override.z, override.heading) == held
    # A resumed return starts here; an old offshore entrance is not mandatory.
    assert adapter._return_remaining_distance(override) < 245.0 * .8
    adapter.set_mission_active(False)
    paused_elapsed = frame.metrics["simulationElapsedSeconds"]
    paused_poses = {other: (*xyz(pose(frame, other)), pose(frame, other).heading) for other in USVS}
    for _ in range(12):
        before, frame = frame, adapter.step()
        assert_return_frame(adapter, before, frame, USVS)
        assert frame.metrics["simulationElapsedSeconds"] == paused_elapsed
        assert {other: (*xyz(pose(frame, other)), pose(frame, other).heading) for other in USVS} == paused_poses
    adapter.set_mission_active(True)
    frame, distances = finish_returns(adapter, frame, USVS)
    assert distances[code] < 245.0 * .8
    assert set(frame.metrics["returnedDeviceCodes"]) >= set(USVS)


def test_blocked_return_holds_without_false_arrival_and_recovers_after_clearance(monkeypatch):
    adapter, frame = subject("ESCORT_GUARD", 40.0)
    code = "USV-002"
    berth = adapter._parking_slots[code]
    original_obstacles = adapter._return_obstacles
    monkeypatch.setattr(adapter, "_return_obstacles", lambda selected, override: [
        *original_obstacles(selected, override), (berth.eastM, berth.northM, 20.0),
    ])
    initial = xyz(pose(frame, code))
    adapter.control_device(code, "USV_RETURN")
    for _ in range(25):
        frame = adapter.step()
        state = frame.metrics["deviceControlStates"][code]
        assert xyz(pose(frame, code)) == initial
        assert state["returnStage"] == "WAITING_FOR_CLEAR_PATH"
        assert state["returnPathBlocked"] is True
        assert state["returnRemainingM"] is None
        assert not state["returnedAtHome"]
        assert frame.terminalStatus is None
    monkeypatch.setattr(adapter, "_return_obstacles", original_obstacles)
    frame, _ = finish_returns(adapter, frame, (code,))
    assert pose(frame, code).status == "RETURNED"


def test_last_millimetres_do_not_snap_faster_than_a_low_configured_speed():
    adapter, _ = subject("ESCORT_GUARD")
    code = "USV-002"
    adapter.config["usvSpeedMps"] = .001
    adapter.control_device(code, "USV_RETURN")
    override = adapter._overrides[code]
    berth = override.parking_slot
    override.x -= .004
    override.heading = 0.0
    override.route = [("DOCKING", berth.position, .001)]
    for _ in range(100):
        previous = (override.x, override.y, override.z)
        arrived = adapter._advance_native_return(code, override)
        assert math.dist(previous, (override.x, override.y, override.z)) <= .001 * DT + 1e-10
        if arrived:
            break
    else:
        pytest.fail("low-speed docking did not settle")
    assert (override.x, override.y, override.z) == berth.position
    assert override.heading == berth.headingDeg
