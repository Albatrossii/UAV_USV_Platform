"""Accelerated route cycling; this is not a wall-clock or Unity rendering test."""
import math

import pytest

from test_return_infrastructure import native_anchor_adapter, native_anchor_layout, xyz


@pytest.mark.parametrize("origin_up", [0.0, 7.5, 30.0])
def test_repeated_native_fleet_return_departure_has_no_drift_or_stale_reservations(origin_up):
    subject = native_anchor_adapter(origin_up)
    frame = subject.step()
    slots = {s["deviceCode"]: s for s in native_anchor_layout(origin_up)["slots"]}
    original_layout = frame.metrics["returnInfrastructure"]
    sequence = frame.sequence

    def advance_until(status):
        nonlocal frame, sequence
        for index in range(4500):
            previous = {a.code: xyz(a) for a in frame.agents}
            frame = subject.step()
            assert frame.sequence > sequence
            sequence = frame.sequence
            assert frame.metrics["returnInfrastructure"] == original_layout
            assert {a.code for a in frame.agents} == set(slots)
            for agent in frame.agents:
                assert all(math.isfinite(v) for v in xyz(agent))
                assert math.dist(previous[agent.code], xyz(agent)) <= (0.5 if agent.type == "UAV" else 0.3) + 1e-8
                if agent.type == "USV":
                    assert agent.z == -origin_up
            if all(a.status == status for a in frame.agents):
                return
            # Retransmitted identical intent must not restart an in-flight route.
            if index % 71 == 0:
                for code in slots:
                    subject.control_device(code, f"{code[:3]}_{'RETURN' if status == 'RETURNED' else 'RESUME'}")
        pytest.fail(f"cycle never reached {status}")

    for cycle in range(10):
        for code in slots:
            action = "LAND" if code.startswith("UAV") and cycle % 2 else "RETURN"
            subject.control_device(code, f"{code[:3]}_{action}")
        advance_until("RETURNED")
        for agent in frame.agents:
            slot = slots[agent.code]
            assert xyz(agent) == (slot["eastM"], slot["northM"], slot["upM"])
        for code in slots:
            subject.control_device(code, f"{code[:3]}_RESUME")
        advance_until("ACTIVE")
        assert frame.metrics["deviceControlStates"] == {}
        assert subject._berth_channel_queues == {}
