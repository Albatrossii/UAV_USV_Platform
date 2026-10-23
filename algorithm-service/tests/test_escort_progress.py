"""Measured convergence must remain visible between integer slot arrivals."""
from __future__ import annotations

import math

import pytest

from app.adapters.adaptive_escort import AdaptiveEscortAdapter
from runner import build_adapter
from test_shore_departure import shore_config


def ring_subject(count=3, targets=1):
    adapter = AdaptiveEscortAdapter(99190, {"uavCount": count, "usvCount": count, "seed": 42})
    adapter._start_capture_for(adapter.threats[:targets], "PROGRESS_TEST")
    result = []
    for index, threat in enumerate(adapter.threats[:targets]):
        threat.capture_stage = 1
        threat.x, threat.y, threat.vx, threat.vy = -70.0 + index * 140, 0.0, 0.0, 0.0
        members = adapter._capture_members(index)
        slots = adapter._capture_slots(members, threat)
        for member, slot in zip(members, slots):
            member.x, member.y, member.z = slot.point((threat.x, threat.y, 0.0))
        last = members[-1]
        goal = (last.x, last.y, last.z)
        last.x += 100.0
        threat.capture_arrival_ratio = (len(members) - 1) / len(members)
        result.append((threat, members, last, goal))
    return adapter, result


def test_remaining_member_earns_only_real_distance_credit_without_completing_capture():
    adapter, [(threat, members, last, goal)] = ring_subject()
    initial = adapter._slot_convergence_progress(threat, members)
    assert initial == .75
    adapter.sequence += 10000
    assert adapter._slot_convergence_progress(threat, members) == initial
    last.x = goal[0] + 55.0
    midway = adapter._slot_convergence_progress(threat, members)
    assert initial < midway < 1
    last.x = goal[0] + 7.01
    assert adapter._slot_convergence_progress(threat, members) == pytest.approx((3 + .95) / 4)
    assert threat.capture_hold == 0 and threat.capture_stage == 1
    assert adapter._terminal_status is None
    adapter.set_mission_active(False)
    last.x = goal[0]
    assert adapter._slot_convergence_progress(threat, members) == pytest.approx((3 + .95) / 4)


@pytest.mark.parametrize("unavailable", ["HOLD", "INVALID"])
def test_held_or_invalid_member_cannot_gain_virtual_slot_credit(unavailable):
    adapter, [(threat, members, last, goal)] = ring_subject()
    adapter._slot_convergence_progress(threat, members)
    last.x = goal[0] + 55
    assert adapter._slot_convergence_progress(threat, members) > .75
    if unavailable == "HOLD":
        adapter._unavailable_codes.add(last.code)
        last.x = goal[0]
    else:
        last.x = math.nan
    assert adapter._slot_convergence_progress(threat, members) == .75
    assert threat.capture_hold == 0


def test_target_slot_references_are_independent_and_reset_on_stage_transition():
    adapter, groups = ring_subject(10, 2)
    baseline = [adapter._slot_convergence_progress(t, members) for t, members, _, _ in groups]
    threat, members, last, goal = groups[0]
    last.x = goal[0] + 55
    assert adapter._slot_convergence_progress(threat, members) > baseline[0]
    other, others, _, _ = groups[1]
    assert adapter._slot_convergence_progress(other, others) == baseline[1]
    threat.capture_stage = 2
    assert adapter._slot_convergence_progress(threat, members) == baseline[0]


@pytest.mark.parametrize("unavailable", ["HOLD", "INVALID"])
def test_approach_stage_uses_only_current_valid_arrival_evidence(unavailable):
    adapter, [(threat, members, last, _)] = ring_subject()
    threat.capture_stage = 0
    threat.capture_arrival_ratio = 1.0
    assert adapter._slot_convergence_progress(threat, members, .25) == .25
    adapter.set_mission_active(False)
    assert adapter._slot_convergence_progress(threat, members, 1.0) == .25
    adapter.set_mission_active(True)
    if unavailable == "HOLD":
        adapter._unavailable_codes.add(last.code)
    else:
        last.z = math.nan
    assert adapter._slot_convergence_progress(threat, members, 1.0) == 0.0
    adapter.set_mission_active(False)
    assert adapter._slot_convergence_progress(threat, members, 1.0) == 0.0


def test_screenshot_183_seconds_reflects_closing_ring_and_keeps_original_terminal_time():
    adapter = build_adapter("ESCORT_GUARD", 99191, shore_config(seed=42, targetShoreDistanceRatio=2 / 3))
    frame = adapter.step()
    adapter.set_mission_active(True)
    previous_progress, samples = 0.0, {}
    for _ in range(3000):
        frame = adapter.step()
        metric = frame.metrics
        progress = metric["missionProgress"]
        assert progress >= previous_progress
        if frame.terminalStatus != "COMPLETED":
            assert progress <= .99
        previous_progress = progress
        elapsed = metric["simulationElapsedSeconds"]
        if elapsed in (170, 183, 200, 220, 230):
            group = metric["captureGroups"][0]
            samples[elapsed] = (progress, group["arrivalRatio"], group["slotConvergenceProgress"], group["radialErrorM"])
        if elapsed == 183:
            # This screenshot previously showed an unchanged 73% for 77 s.
            # A global pause cannot advance either geometry or the new metric.
            paused_sequence = adapter.base.sequence
            adapter.set_mission_active(False)
            for _ in range(12):
                paused = adapter.step()
                assert paused.metrics["missionProgress"] == progress
                assert adapter.base.sequence == paused_sequence
            adapter.set_mission_active(True)
        if frame.terminalStatus:
            break
    assert frame.terminalStatus == "COMPLETED"
    assert frame.metrics["simulationElapsedSeconds"] == 256.6
    assert frame.metrics["protectedArrivalReady"] and frame.metrics["capturedRingsReady"]
    assert frame.metrics["progress"] == 1.0
    for earlier, later in zip((170, 183, 200, 220), (183, 200, 220, 230)):
        assert samples[later][0] > samples[earlier][0]
        assert samples[later][2] > samples[earlier][2]
        assert samples[later][3] < samples[earlier][3]
        assert samples[later][1] == .75
