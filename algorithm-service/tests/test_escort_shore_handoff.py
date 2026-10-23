"""Executed shore poses, dynamic close protection and operator handoff."""
from __future__ import annotations

import math
from copy import deepcopy

import pytest

from runner import build_adapter
from test_return_infrastructure import pose, xyz
from test_shore_departure import shore_config


def released_escort(seed=42):
    adapter = build_adapter("ESCORT_GUARD", 99051, shore_config(
        seed=seed, targetShoreDistanceRatio=2 / 3,
    ))
    frame = adapter.step()
    adapter.set_mission_active(True)
    for _ in range(300):
        previous, frame = frame, adapter.step()
        if frame.metrics["deploymentState"] == "COMPLETE":
            return adapter, previous, frame
    pytest.fail("escort did not transfer at its short safe-release endpoints")


def assert_continuous(previous, frame):
    for agent in frame.agents:
        prior = pose(previous, agent.code)
        assert math.dist(xyz(prior), xyz(agent)) <= (0.5 if agent.type == "UAV" else 0.4) + 1e-6


def test_release_reconciles_real_position_velocity_and_heading_before_planning():
    adapter, previous, frame = released_escort()
    assert frame.metrics["deploymentMode"] == "SAFE_RELEASE"
    assert frame.metrics["deploymentElapsedSeconds"] < 25
    assert frame.phase == "ASSEMBLING_ESCORT"
    assert adapter.base.sequence == 1
    for item in adapter.base.vehicles:
        actual, prior = pose(frame, item.code), pose(previous, item.code)
        assert (item.x, item.y, item.z) == xyz(actual)
        assert (item.vx, item.vy) == pytest.approx(((actual.x - prior.x) / .1, (actual.y - prior.y) / .1))
        assert adapter.base._stable_headings[item.code] == actual.heading
        assert item.x > 220, "handoff must still be near the real shore, not a convoy birth slot"
    following = adapter.step()
    assert_continuous(frame, following)
    assert any(xyz(pose(frame, v.code)) != xyz(v) for v in following.agents)


@pytest.mark.parametrize("seed", [1, 42, 20260814])
def test_dynamic_escort_forms_real_mixed_protection_and_completes_strict_contract(seed):
    adapter, _, frame = released_escort(seed)
    protected_start = xyz(frame.targets[0])
    phases = {frame.phase}
    contact_travel = 0.0
    assembly_frames = 0
    for _ in range(5500):
        previous = frame
        pending = adapter.base._shore_assembly_pending
        frame = adapter.step()
        assert_continuous(previous, frame)
        phases.add(frame.phase)
        if frame.phase == "ASSEMBLING_ESCORT":
            assembly_frames += 1
            assert xyz(frame.targets[0]) == protected_start
            contact_travel += math.dist(xyz(previous.targets[1]), xyz(frame.targets[1]))
        if pending and not adapter.base._shore_assembly_pending:
            assert frame.metrics["escortProtectionReadyCount"] == 2
            assert frame.metrics["escortProtectionRequiredCount"] == 2
        if adapter.base.capture_started_frame is not None:
            for kind in ("UAV", "USV"):
                guards = [v for v in adapter.base.vehicles if v.kind == kind and v.role == "CLOSE_GUARD"]
                assert len(guards) == 1
                assert guards[0].assigned_threat is None
        if frame.terminalStatus:
            break
    assert assembly_frames > 0 and contact_travel > 15.0
    assert {"ASSEMBLING_ESCORT", "ESCORTING", "GUARDING", "ACTIVE_CAPTURE", "CONTAINMENT", "COMPLETED"} <= phases
    assert frame.terminalStatus == "COMPLETED"
    assert frame.metrics["protectedArrivalReady"]
    assert frame.metrics["capturedRingsReady"]
    assert frame.metrics["terminalBlocker"] == "NONE"
    assert frame.metrics["escortProgress"] == 1.0
    assert adapter.base.defense.screens[0].reverse_frames >= 40
    assert adapter.base.defense.screens[0].distance >= 2.5


def test_pause_and_operator_hold_preserve_real_state_and_cannot_supply_guard_evidence():
    adapter, _, frame = released_escort()
    for _ in range(25):
        frame = adapter.step()
    adapter.set_mission_active(False)
    paused = deepcopy(frame)
    base_sequence = adapter.base.sequence
    for _ in range(10):
        frame = adapter.step()
        assert [xyz(v) for v in frame.agents] == [xyz(v) for v in paused.agents]
        assert frame.targets == paused.targets
        assert adapter.base.sequence == base_sequence
    adapter.set_mission_active(True)
    guard = next(v for v in adapter.base.vehicles if v.kind == "USV" and v.role == "CLOSE_GUARD")
    adapter.control_device(guard.code, "USV_HOLD")
    held = xyz(pose(frame, guard.code))
    for _ in range(15):
        frame = adapter.step()
        assert xyz(pose(frame, guard.code)) == held
        assert (guard.x, guard.y, guard.z) == held
        assert guard.code in adapter.base._unavailable_codes
        assert frame.targets[0].state == "ASSEMBLING_ESCORT"
        assert frame.terminalStatus is None
    adapter.control_device(guard.code, "USV_RESUME")
    previous = frame
    for _ in range(2000):
        frame = adapter.step()
        assert_continuous(previous, frame)
        if guard.code not in adapter._overrides:
            break
        previous = frame
    else:
        pytest.fail("the held guard did not physically rejoin its live close-protection slot")
    assert math.dist(held, xyz(pose(frame, guard.code))) > 20
    assert guard.code not in adapter.base._unavailable_codes


def test_operator_controlled_ring_member_never_satisfies_capture_hold():
    adapter, _, frame = released_escort()
    for _ in range(3000):
        frame = adapter.step()
        if adapter.base.capture_started_frame is not None:
            break
    member = next(v for v in adapter.base.vehicles if v.kind == "USV" and v.assigned_threat is not None)
    adapter.control_device(member.code, "USV_HOLD")
    for _ in range(35):
        frame = adapter.step()
        assert adapter.base.threats[member.assigned_threat].capture_hold == 0
        assert frame.terminalStatus is None
        assert (member.x, member.y, member.z) == xyz(pose(frame, member.code))


def capture_escort():
    adapter, _, frame = released_escort()
    for _ in range(3500):
        frame = adapter.step()
        if adapter.base.capture_started_frame is not None:
            return adapter, frame
    pytest.fail("escort did not reach its verified pursuit handoff")


@pytest.mark.parametrize("kind,guard", [("UAV", False), ("USV", False), ("USV", True)])
def test_settled_withdrawal_reallocates_real_mixed_force_and_can_complete(kind, guard):
    adapter, frame = capture_escort()
    selected = next(v for v in adapter.base.vehicles if v.kind == kind
                    and (v.role == "CLOSE_GUARD" if guard else v.assigned_threat is not None))
    code = selected.code
    adapter.control_device(code, f"{kind}_RETURN")
    settled = False
    replacement_observed = False
    replacement_arrived = False
    for _ in range(6500):
        previous = frame
        pending = adapter.base._guard_replacement_pending
        ready_before_step = adapter.base._protection_status()[0]
        frame = adapter.step()
        assert_continuous(previous, frame)
        if pending and not adapter.base._guard_replacement_pending:
            replacement_arrived = ready_before_step == 2
        assert len(frame.agents) == 6, "withdrawn craft must still be displayed"
        if pending and adapter.base._guard_replacement_pending:
            assert xyz(frame.targets[0]) == xyz(previous.targets[0])
        if pose(frame, code).status == "RETURNED":
            settled = True
            assert code in adapter.base._withdrawn_vehicles
            assert code not in {v.code for v in adapter.base.vehicles}
            assert all(code not in {v.code for v in adapter.base._capture_members(i)}
                       for i in range(len(adapter.base.threats)))
            if guard:
                replacement_observed |= any(v.kind == kind and v.role == "CLOSE_GUARD"
                                            and v.code != code for v in adapter.base.vehicles)
        if frame.terminalStatus:
            break
    assert settled
    assert not guard or replacement_observed
    assert not guard or replacement_arrived
    assert frame.terminalStatus == "COMPLETED", frame.metrics.get("terminalBlocker")
    assert frame.metrics["requiredActiveMissionDeviceCount"] == 5
    assert frame.metrics["requiredActiveUavCount"] == 2
    assert frame.metrics["requiredActiveUsvCount"] == 2
    assert frame.metrics["capturedRingsReady"] and frame.metrics["protectedArrivalReady"]
    guards = [v for v in adapter.base.vehicles if v.role == "CLOSE_GUARD"]
    assert {v.kind for v in guards} == {"UAV", "USV"}
    assert all(math.hypot(v.x - adapter.base.protected[0].x,
                          v.y - adapter.base.protected[0].y) < 45 for v in guards)
    members = adapter.base._capture_members(0)
    assert len(members) == 3 and {v.kind for v in members} == {"UAV", "USV"}
    assert adapter.base.threats[0].capture_hold >= 25


def test_held_member_at_valid_ring_slot_is_not_live_containment_evidence():
    adapter, frame = capture_escort()
    for _ in range(3500):
        frame = adapter.step()
        if adapter.base._live_containment(0)["ready"]:
            break
    else:
        pytest.fail("ring did not become geometrically valid")
    member = adapter.base._capture_members(0)[0]
    adapter.control_device(member.code, "UAV_HOVER" if member.kind == "UAV" else "USV_HOLD")
    adapter.base.reconcile_execution(list(adapter._last_agents.values()), adapter._last_agents,
                                    tuple(adapter._overrides))
    assert not adapter.base._live_containment(0)["ready"]
    assert adapter.base.threats[0].capture_hold == 0


def test_pre_screen_usv_withdrawal_reports_insufficient_force_without_weakening_screen():
    adapter, _, frame = released_escort()
    member = next(v for v in adapter.base.vehicles if v.kind == "USV" and v.role != "CLOSE_GUARD")
    adapter.control_device(member.code, "USV_RETURN")
    for _ in range(2000):
        frame = adapter.step()
        if pose(frame, member.code).status == "RETURNED":
            frame = adapter.step()
            break
    else:
        pytest.fail("withdrawal did not reach its actual berth")
    assert not adapter.base.threats[0].cover_verified
    assert frame.metrics["requiredActiveUsvCount"] == 3
    assert not frame.metrics["activeForceSufficient"]
    assert frame.metrics["missionDegradedReason"] == "INSUFFICIENT_ACTIVE_FORCE"
    assert frame.terminalStatus is None


@pytest.mark.parametrize("kind", ["UAV", "USV"])
def test_returned_member_can_resume_from_real_berth_and_rejoin_live_planner(kind):
    adapter, frame = capture_escort()
    selected = next(v for v in adapter.base.vehicles if v.kind == kind and v.assigned_threat is not None)
    code = selected.code
    adapter.control_device(code, f"{kind}_RETURN")
    for _ in range(2000):
        previous, frame = frame, adapter.step()
        assert_continuous(previous, frame)
        if pose(frame, code).status == "RETURNED":
            break
    else:
        pytest.fail("member did not reach its real berth")
    assert code in adapter.base._withdrawn_vehicles
    berth = xyz(pose(frame, code))
    adapter.control_device(code, f"{kind}_RESUME")
    for _ in range(3000):
        previous, frame = frame, adapter.step()
        assert_continuous(previous, frame)
        assert len(frame.agents) == 6
        if code not in adapter._overrides:
            break
    else:
        pytest.fail("returned member remained excluded from the live planner")
    assert code not in adapter.base._withdrawn_vehicles
    assert code not in adapter.base._unavailable_codes
    assert code in {v.code for v in adapter.base.vehicles}
    assert math.dist(berth, xyz(pose(frame, code))) > 20
    assert adapter.base.rejoin_target(code) is not None
