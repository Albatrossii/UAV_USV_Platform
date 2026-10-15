"""Real shore wrapper integration for the opt-in berth-side escorted vessel."""
from copy import deepcopy
from itertools import combinations
import math

import pytest

from app.adapters.adaptive_escort import BREACH_DISTANCE_M
from runner import build_adapter
from test_return_infrastructure import pose, xyz
from test_shore_departure import shore_config


def subject(seed=42, uav_speed=5, usv_speed=3):
    return build_adapter("ESCORT_GUARD", 99520, shore_config(
        escortStartAtBerth=True, targetShoreDistanceRatio=2 / 3,
        seed=seed, uavSpeedMps=uav_speed, usvSpeedMps=usv_speed,
    ))


def continuous(adapter, previous, frame):
    for current in frame.agents:
        before = pose(previous, current.code)
        limit = adapter.base.uav_cruise * .1 if current.type == "UAV" else .4
        assert math.dist(xyz(current), xyz(before)) <= limit + 1e-5, (
            frame.sequence, current.code, xyz(before), xyz(current),
        )
    old_targets = {target.code: target for target in previous.targets}
    for target in frame.targets:
        assert math.dist(xyz(target), xyz(old_targets[target.code])) <= .281, (
            "target teleported", frame.sequence, target.code,
        )
    assert frame.metrics["missionProgress"] >= previous.metrics["missionProgress"]


def safe_hulls(adapter, frame):
    for first, second in combinations([*frame.agents, *frame.targets], 2):
        required = adapter.base.safety.required_separation(first.type, second.type, first.z, second.z)
        assert math.dist(xyz(first), xyz(second)) >= required - .02, (
            "hull overlap", frame.sequence, first.code, second.code,
        )


def release(adapter):
    frame = adapter.step()
    adapter.set_mission_active(True)
    for _ in range(600):
        previous, frame = frame, adapter.step()
        continuous(adapter, previous, frame)
        if frame.metrics["deploymentState"] == "COMPLETE":
            return frame
    pytest.fail("short shore release did not finish")


@pytest.mark.parametrize("seed,uav_speed,usv_speed", [
    (1, 5, 3), (42, 5, 3), (20260814, 5, 3), (42, 4, 2), (42, 8, 4),
])
def test_berth_preview_to_complete_escort_remains_continuous_and_safe(seed, uav_speed, usv_speed):
    adapter = subject(seed, uav_speed, usv_speed)
    frame = adapter.step()
    protected = next(t for t in frame.targets if t.code == "PROTECTED-001")
    original_protected = xyz(protected)
    boats = [agent for agent in frame.agents if agent.type == "USV"]
    berth_center = tuple(sum(getattr(agent, axis) for agent in boats) / len(boats) for axis in ("x", "y"))
    assert 30 <= math.dist((protected.x, protected.y), berth_center) <= 85
    assert min(math.dist(xyz(boat), xyz(protected)) for boat in boats) < 70
    assert max(math.dist(xyz(boat), xyz(protected)) for boat in boats) < 100
    assert protected.x < berth_center[0], "the escorted vessel must start on the sea side of the berths"
    assert adapter.base.protected[0].destination_x < protected.x, "the safe route must depart toward open water"
    for _ in range(10):
        parked = adapter.step()
        assert parked.targets == frame.targets
        assert parked.agents == frame.agents
        assert parked.metrics["missionProgress"] == 0
    adapter.set_mission_active(True)
    phases = set()
    for _ in range(9000):
        previous, frame = frame, adapter.step()
        continuous(adapter, previous, frame)
        safe_hulls(adapter, frame)
        phases.add(frame.phase)
        if frame.metrics["deploymentState"] != "COMPLETE":
            assert frame.targets == previous.targets, "preview must match the frozen algorithm targets"
        if frame.phase == "ASSEMBLING_ESCORT":
            assert xyz(next(t for t in frame.targets if t.code == "PROTECTED-001")) == original_protected
        if frame.terminalStatus:
            break
    assert frame.terminalStatus == "COMPLETED", (seed, uav_speed, usv_speed, frame.phase, frame.metrics.get("terminalBlocker"))
    assert {"DEPLOYING", "ASSEMBLING_ESCORT", "ACTIVE_CAPTURE", "COMPLETED"} <= phases
    assert frame.metrics["protectedArrivalReady"] and frame.metrics["capturedRingsReady"]
    assert frame.metrics["missionProgress"] == 1
    assert adapter.base.min_protected_threat_distance > BREACH_DISTANCE_M
    assert adapter.base.defense.screens[0].reverse_frames >= 40


@pytest.mark.parametrize("kind,hold,resume", [
    ("UAV", "UAV_HOVER", "UAV_RESUME"), ("USV", "USV_HOLD", "USV_RESUME"),
])
def test_berth_mission_pause_hold_and_real_rejoin_do_not_jump(kind, hold, resume):
    adapter = subject()
    frame = release(adapter)
    frozen = deepcopy(frame)
    sequence = adapter.base.sequence
    adapter.set_mission_active(False)
    for _ in range(20):
        frame = adapter.step()
        assert frame.agents == frozen.agents and frame.targets == frozen.targets
        assert frame.metrics["missionProgress"] == frozen.metrics["missionProgress"]
        assert adapter.base.sequence == sequence
    adapter.set_mission_active(True)
    selected = next(v for v in adapter.base.vehicles if v.kind == kind and v.role == "CLOSE_GUARD")
    code = selected.code
    adapter.control_device(code, hold)
    held = xyz(pose(frame, code))
    for _ in range(20):
        previous, frame = frame, adapter.step()
        continuous(adapter, previous, frame)
        assert xyz(pose(frame, code)) == held
        assert frame.terminalStatus is None
    adapter.control_device(code, resume)
    for _ in range(2500):
        previous, frame = frame, adapter.step()
        continuous(adapter, previous, frame)
        if code not in adapter._overrides:
            break
    else:
        pytest.fail(f"{code} did not physically rejoin the berth-side escort")
    assert code not in adapter.base._unavailable_codes
    assert math.dist(held, xyz(pose(frame, code))) > 1


@pytest.mark.parametrize("code", ["UAV-001", "USV-001", "USV-002", "USV-003"])
def test_berth_mission_return_reaches_original_slot_without_teleport(code):
    adapter = subject()
    frame = release(adapter)
    kind = code[:3]
    # Let the craft move away from its initial release point, then return via
    # the existing validated facility route, not the escorted ship's new pose.
    for _ in range(60):
        frame = adapter.step()
    adapter.control_device(code, f"{kind}_RETURN")
    for _ in range(3000):
        previous, frame = frame, adapter.step()
        continuous(adapter, previous, frame)
        selected = pose(frame, code)
        for target in frame.targets:
            required = adapter.base.safety.required_separation(kind, target.type, selected.z, target.z)
            assert math.dist(xyz(selected), xyz(target)) >= required - .02, (code, target.code, frame.sequence)
        if pose(frame, code).status == "RETURNED":
            break
    else:
        pytest.fail(f"{code} failed to return to its original facility")
    assert xyz(pose(frame, code)) == pytest.approx(adapter._parking_slots[code].position)
    parked = xyz(pose(frame, code))
    for _ in range(10):
        frame = adapter.step()
        assert xyz(pose(frame, code)) == parked
