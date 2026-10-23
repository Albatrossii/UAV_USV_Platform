"""Airborne rejoin must not inherit the assigned helipad's departure route."""
import math

import pytest

from app.adapters.single_device import SingleDeviceControlAdapter
from app.adapters import AdaptiveCaptureAdapter, CaptureAdapter
from test_return_infrastructure import (
    CapacityFleet, existing_scene_infrastructure, infrastructure, pose,
    return_to_slot, xyz,
)


class RejoinFleet(CapacityFleet):
    offset = 0.0
    altitude = 12.0

    def step(self):
        frame = super().step()
        target = pose(frame, "UAV-001")
        target.x += self.offset
        target.z = self.altitude
        return frame


def make_subject(native):
    base = RejoinFleet(71, {
        "uavCount": 3 if native else 2, "usvCount": 3 if native else 2,
        "uavSpeedMps": 5, "usvSpeedMps": 3,
        "returnInfrastructure": existing_scene_infrastructure() if native else infrastructure(),
    })
    base.altitude = 26.0 if native else 12.0
    subject = SingleDeviceControlAdapter(base, "TEST")
    subject.step()
    return subject, base


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("interruption", ["UAV_HOVER", "UAV_LAND", "UAV_RETURN"])
def test_offshore_rejoin_moves_toward_fleet_on_first_frame_without_takeoff(native, interruption):
    subject, base = make_subject(native)
    subject.control_device("UAV-001", interruption)
    held_frame = subject.step()
    held = pose(held_frame, "UAV-001")
    # The formation advances while the operator has this aircraft on hold.
    base.offset = 10.0
    base.altitude = held.z
    assert subject.control_device("UAV-001", "UAV_RESUME") == "REJOINING"
    first = subject.step()
    aircraft = pose(first, "UAV-001")
    assert aircraft.x > held.x
    assert aircraft.y == held.y and aircraft.z == held.z
    assert math.dist(xyz(held), xyz(aircraft)) <= 0.5 + 1e-9
    assert first.metrics["deviceControlStates"]["UAV-001"]["returnStage"] == "REJOINING"
    for code in ("UAV-002", "USV-001", "USV-002"):
        assert xyz(pose(first, code)) == xyz(pose(held_frame, code))
    previous = aircraft
    for _ in range(30):
        # Duplicate delivery must not restart or delay the route.
        subject.control_device("UAV-001", "UAV_RESUME")
        frame = subject.step()
        aircraft = pose(frame, "UAV-001")
        assert math.dist(xyz(previous), xyz(aircraft)) <= 0.5 + 1e-9
        assert aircraft.z == held.z
        if aircraft.status == "ACTIVE":
            assert "UAV-001" not in frame.metrics["deviceControlStates"]
            break
        previous = aircraft
    else:
        pytest.fail("airborne device failed to rejoin the stationary formation target")


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("interruption", [None, "UAV_HOVER", "UAV_LAND"])
def test_parked_aircraft_still_takes_off_even_after_hold_or_stop(native, interruption):
    subject, _ = make_subject(native)
    parked = pose(return_to_slot(subject, "UAV-001"), "UAV-001")
    if interruption:
        subject.control_device("UAV-001", interruption)
        subject.step()
    subject.control_device("UAV-001", "UAV_RESUME")
    frame = subject.step()
    aircraft = pose(frame, "UAV-001")
    assert aircraft.x == parked.x and aircraft.y == parked.y
    assert 0 < aircraft.z - parked.z <= 0.2 + 1e-9
    assert frame.metrics["deviceControlStates"]["UAV-001"]["returnStage"] == "TAKING_OFF"


@pytest.mark.parametrize("native", [False, True])
def test_interrupted_helipad_takeoff_keeps_safe_vertical_departure(native):
    subject, _ = make_subject(native)
    return_to_slot(subject, "UAV-001")
    subject.control_device("UAV-001", "UAV_RESUME")
    for _ in range(5):
        subject.step()
    subject.control_device("UAV-001", "UAV_HOVER")
    held = pose(subject.step(), "UAV-001")
    subject.control_device("UAV-001", "UAV_RESUME")
    frame = subject.step()
    aircraft = pose(frame, "UAV-001")
    assert aircraft.x == held.x and aircraft.y == held.y
    assert 0 < aircraft.z - held.z <= 0.2 + 1e-9
    assert frame.metrics["deviceControlStates"]["UAV-001"]["returnStage"] == "TAKING_OFF"


@pytest.mark.parametrize("algorithm", [CaptureAdapter, AdaptiveCaptureAdapter])
def test_real_capture_algorithm_offshore_hover_rejoins_without_pad_climb(algorithm):
    config = {"uavCount": 3, "usvCount": 3, "targetCount": 1, "seed": 42,
              "uavSpeedMps": 5, "usvSpeedMps": 3,
              "returnInfrastructure": existing_scene_infrastructure()}
    subject = SingleDeviceControlAdapter(algorithm(72, config), "GB_SFLA_CS_SINGLE_DEVICE")
    subject.step()
    subject.set_mission_active(True)
    subject.control_device("UAV-001", "UAV_HOVER")
    for _ in range(20):
        frame = subject.step()
    held = pose(frame, "UAV-001")
    slot = subject._parking_slots["UAV-001"]
    assert held.z < slot.approachUpM
    assert math.hypot(held.x - slot.eastM, held.y - slot.northM) > 0.5
    subject.control_device("UAV-001", "UAV_RESUME")
    frame = subject.step()
    aircraft = pose(frame, "UAV-001")
    assert math.hypot(aircraft.x - held.x, aircraft.y - held.y) > 0
    assert math.dist(xyz(held), xyz(aircraft)) <= 0.5 + 1e-9
    assert frame.metrics["deviceControlStates"].get("UAV-001", {}).get("returnStage") != "TAKING_OFF"
