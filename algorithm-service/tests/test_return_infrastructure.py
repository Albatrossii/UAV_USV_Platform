from __future__ import annotations

import copy
import math

import pytest

from app.adapters.base import AlgorithmAdapter
from app.adapters.return_infrastructure import validate_return_infrastructure
from app.adapters.single_device import SingleDeviceControlAdapter
from app.schemas import AgentFrame, RuntimeFrame


def infrastructure() -> dict:
    shore = {"eastMinM": -60, "eastMaxM": 60, "northMinM": -45, "northMaxM": -5, "surfaceUpM": 1.4}
    slots = []
    for number, east in ((1, -40), (2, -20)):
        slots.append({"deviceCode": f"UAV-{number:03d}", "kind": "HELIPAD",
                      "eastM": east, "northM": -20, "upM": 1.4, "headingDeg": 90,
                      "approachEastM": east, "approachNorthM": -20, "approachUpM": 21.4})
    for number, east in ((1, 15), (2, 35)):
        slots.append({"deviceCode": f"USV-{number:03d}", "kind": "BERTH",
                      "eastM": east, "northM": 8, "upM": 0, "headingDeg": 90,
                      "approachEastM": east + 8, "approachNorthM": 20, "approachUpM": 0})
    return {"version": "fixed-shore-v1", "coordinateFrame": "FLEET_LOCAL_ENU", "shore": shore, "slots": slots}


class StationaryFleet(AlgorithmAdapter):
    code = "TEST_SHORE"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        return RuntimeFrame(
            runId=self.run_id, algorithmCode=self.code, sequence=self.sequence,
            timestamp=self.sequence * 100, phase="RUNNING", targets=[], agents=[
                AgentFrame("UAV-001", "UAV", 0, 40, 12, 0, "SCOUT"),
                AgentFrame("UAV-002", "UAV", 20, 50, 30, 0, "SCOUT"),
                AgentFrame("USV-001", "USV", 0, 45, 0, 0, "GUARD"),
                AgentFrame("USV-002", "USV", 35, 45, 0, 0, "GUARD"),
            ],
        )


def adapter(layout: dict | None = None) -> SingleDeviceControlAdapter:
    return SingleDeviceControlAdapter(StationaryFleet(14, {
        "uavCount": 2, "usvCount": 2, "uavSpeedMps": 5, "usvSpeedMps": 3,
        "returnInfrastructure": infrastructure() if layout is None else layout,
    }), "ESCORT_GUARD_SINGLE_DEVICE")


def pose(frame: RuntimeFrame, code: str) -> AgentFrame:
    return next(agent for agent in frame.agents if agent.code == code)


def xyz(agent: AgentFrame) -> tuple[float, float, float]:
    return agent.x, agent.y, agent.z


def return_to_slot(subject: SingleDeviceControlAdapter, code: str) -> RuntimeFrame:
    subject.control_device(code, f"{code[:3]}_RETURN")
    for _ in range(2000):
        frame = subject.step()
        if pose(frame, code).status == "RETURNED":
            return frame
    raise AssertionError("return did not settle within its bounded test horizon")


def test_uav_returns_via_safe_altitude_and_speed_limited_vertical_landing() -> None:
    subject = adapter()
    previous = pose(subject.step(), "UAV-001")
    assert subject.control_device("UAV-001", "UAV_RETURN") == "RETURNING"
    seen = set()
    for _ in range(2000):
        frame = subject.step()
        agent = pose(frame, "UAV-001")
        state = frame.metrics["deviceControlStates"][agent.code]
        stage = state["returnStage"]
        seen.add(stage)
        assert state["parkingSlot"] == "UAV-001" and state["parkingKind"] == "HELIPAD"
        assert agent.z >= 1.4
        assert math.dist(xyz(previous), xyz(agent)) <= 0.5 + 1e-9
        if stage == "ASCENDING":
            assert (agent.x, agent.y) == (0, 40)
            assert 0 <= agent.z - previous.z <= 0.2 + 1e-9
        elif stage == "CRUISING":
            assert agent.z == 21.4
        elif stage in {"DESCENDING", "PARKED"}:
            assert (agent.x, agent.y) == (-40, -20)
            assert -0.1 - 1e-9 <= agent.z - previous.z <= 0
        previous = agent
        if agent.status == "RETURNED":
            break
    else:
        raise AssertionError("UAV never arrived")
    assert seen == {"ASCENDING", "CRUISING", "DESCENDING", "PARKED"}
    assert xyz(previous) == (-40, -20, 1.4)
    assert previous.heading == 90
    assert frame.metrics["returnedDeviceCodes"] == ["UAV-001"]


def test_high_uav_does_not_descend_until_directly_above_its_pad() -> None:
    subject = adapter()
    subject.step()
    subject.control_device("UAV-002", "UAV_RETURN")
    for _ in range(2000):
        frame = subject.step()
        agent = pose(frame, "UAV-002")
        if (agent.x, agent.y) != (-20, -20):
            assert agent.z == 30
        if agent.status == "RETURNED":
            assert xyz(agent) == (-20, -20, 1.4)
            break
    else:
        raise AssertionError("high UAV never returned")


def test_usv_stays_on_water_through_entrance_channel_and_slow_docking() -> None:
    subject = adapter()
    previous = pose(subject.step(), "USV-001")
    subject.control_device("USV-001", "USV_RETURN")
    seen = set()
    for _ in range(2000):
        frame = subject.step()
        agent = pose(frame, "USV-001")
        stage = frame.metrics["deviceControlStates"][agent.code]["returnStage"]
        seen.add(stage)
        assert agent.z == 0 and agent.y > -5
        assert math.dist(xyz(previous), xyz(agent)) <= 0.3 + 1e-9
        if stage == "CHANNEL":
            assert agent.x == 23
            assert math.dist(xyz(previous), xyz(agent)) <= 0.15 + 1e-9
        elif stage in {"DOCKING", "PARKED"}:
            assert agent.y == 8
            assert math.dist(xyz(previous), xyz(agent)) <= 0.06 + 1e-9
        previous = agent
        if agent.status == "RETURNED":
            break
    else:
        raise AssertionError("USV never docked")
    assert seen == {"APPROACHING", "CHANNEL", "DOCKING", "PARKED"}
    assert xyz(previous) == (15, 8, 0) and previous.heading == 90


def test_every_device_has_its_own_fixed_slot_and_return_is_idempotent_and_drift_free() -> None:
    subject = adapter()
    first = subject.step()
    for agent in first.agents:
        subject.control_device(agent.code, f"{agent.type}_RETURN")
    for _ in range(2000):
        frame = subject.step()
        if all(agent.status == "RETURNED" for agent in frame.agents):
            break
    else:
        raise AssertionError("fleet did not return")
    frozen = {agent.code: (*xyz(agent), agent.heading) for agent in frame.agents}
    assert len(set(frozen.values())) == 4
    for _ in range(50):
        for code in frozen:
            assert subject.control_device(code, f"{code[:3]}_RETURN") == "RETURNED"
        settled = subject.step()
        assert {agent.code: (*xyz(agent), agent.heading) for agent in settled.agents} == frozen
        assert all(agent.status == "RETURNED" for agent in settled.agents)
        assert settled.metrics["returningDeviceCount"] == 0


def test_repeated_return_does_not_restart_the_route_or_change_the_destination() -> None:
    subject = adapter()
    subject.step()
    subject.control_device("USV-001", "USV_RETURN")
    for _ in range(2000):
        assert subject.control_device("USV-001", "USV_RETURN") in {"RETURNING", "RETURNED"}
        frame = subject.step()
        if pose(frame, "USV-001").status == "RETURNED":
            assert xyz(pose(frame, "USV-001")) == (15, 8, 0)
            break
    else:
        raise AssertionError("duplicate returns reset route progress")


@pytest.mark.parametrize("code", ["UAV-001", "USV-001"])
def test_parked_device_resumes_via_safe_takeoff_or_undocking_then_rejoins(code: str) -> None:
    subject = adapter()
    subject.step()
    returned = return_to_slot(subject, code)
    previous = pose(returned, code)
    assert subject.control_device(code, f"{code[:3]}_RESUME") == "REJOINING"
    stages = set()
    for _ in range(1000):
        frame = subject.step()
        agent = pose(frame, code)
        assert math.dist(xyz(previous), xyz(agent)) <= (0.5 if code.startswith("UAV") else 0.3) + 1e-9
        if agent.status == "ACTIVE":
            assert code not in frame.metrics["deviceControlStates"]
            break
        assert agent.status == "REJOINING"
        stage = frame.metrics["deviceControlStates"][code]["returnStage"]
        stages.add(stage)
        if stage == "TAKING_OFF":
            assert (agent.x, agent.y) == (-40, -20)
            assert 0 <= agent.z - previous.z <= 0.2 + 1e-9
        if code.startswith("USV"):
            assert agent.z == 0 and agent.y > -5
            if stage == "UNDOCKING":
                assert agent.y == 8
                assert 0 <= agent.x - previous.x <= 0.06 + 1e-9
            elif stage == "EXITING":
                assert agent.x == 23
        previous = agent
        # Duplicate resume receipts must not restart takeoff/undocking.
        assert subject.control_device(code, f"{code[:3]}_RESUME") == "REJOINING"
    else:
        raise AssertionError("device never rejoined")
    assert ("TAKING_OFF" in stages) if code.startswith("UAV") else {"UNDOCKING", "EXITING"}.issubset(stages)
    assert "REJOINING" in stages


def test_shore_rejoin_does_not_force_a_jump_to_a_faster_moving_algorithm_target() -> None:
    class EscapingFleet(StationaryFleet):
        moving = False

        def step(self) -> RuntimeFrame:
            frame = super().step()
            if self.moving:
                frame.agents[0].x = float(self.sequence * 4)
            return frame

    base = EscapingFleet(14, {"returnInfrastructure": infrastructure(), "uavSpeedMps": 5})
    subject = SingleDeviceControlAdapter(base, "TEST")
    subject.step()
    parked = return_to_slot(subject, "UAV-001")
    previous = pose(parked, "UAV-001")
    base.moving = True
    subject.control_device("UAV-001", "UAV_RESUME")
    for _ in range(200):
        frame = subject.step()
        current = pose(frame, "UAV-001")
        assert current.status == "REJOINING"
        assert math.dist(xyz(previous), xyz(current)) <= 0.5 + 1e-9
        previous = current
    assert frame.metrics["deviceControlStates"]["UAV-001"]["returnStage"] == "REJOINING"


def test_metadata_is_authoritative_on_every_frame_and_immune_to_input_or_output_mutation() -> None:
    layout = infrastructure()
    expected = copy.deepcopy(layout)
    subject = adapter(layout)
    first = subject.step()
    assert first.metrics["returnInfrastructure"] == expected
    layout["slots"][0]["eastM"] = 999
    first.metrics["returnInfrastructure"]["slots"][0]["eastM"] = 123
    for _ in range(4):
        assert subject.step().metrics["returnInfrastructure"] == expected
    assert xyz(pose(return_to_slot(subject, "UAV-001"), "UAV-001")) == (-40, -20, 1.4)


@pytest.mark.parametrize("path,value", [
    (("version",), "other"), (("coordinateFrame",), "GLOBAL_ENU"),
    (("shore", "surfaceUpM"), 0), (("shore", "eastMaxM"), -60),
    (("shore", "northMinM"), math.nan), (("shore", "northMaxM"), math.inf),
    (("slots", 0, "kind"), "BERTH"), (("slots", 0, "deviceCode"), "UAV-999"),
    (("slots", 0, "eastM"), 100), (("slots", 0, "northM"), 10),
    (("slots", 0, "upM"), 0), (("slots", 0, "approachUpM"), 20),
    (("slots", 0, "approachEastM"), -30), (("slots", 0, "headingDeg"), 360),
    (("slots", 0, "eastM"), True), (("slots", 0, "upM"), "1.4"),
    (("slots", 2, "northM"), -10), (("slots", 2, "upM"), 1),
    (("slots", 2, "approachUpM"), 1), (("slots", 2, "approachEastM"), 22),
    (("slots", 2, "approachNorthM"), 7), (("slots", 2, "headingDeg"), 0),
    (("slots", 3, "approachNorthM"), 21),
])
def test_invalid_metadata_is_rejected_before_prepared_frames(path: tuple, value: object) -> None:
    layout = infrastructure()
    parent = layout
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValueError, match="returnInfrastructure"):
        adapter(layout)


@pytest.mark.parametrize("kind", ["duplicate", "overlap", "missing", "null"])
def test_duplicate_missing_or_overlapping_slots_are_rejected(kind: str) -> None:
    layout = infrastructure()
    if kind == "duplicate":
        layout["slots"][1]["deviceCode"] = "UAV-001"
    elif kind == "overlap":
        layout["slots"][1]["eastM"] = layout["slots"][1]["approachEastM"] = -40
    elif kind == "missing":
        layout["slots"].pop()
    else:
        layout["slots"] = None
    with pytest.raises(ValueError, match="returnInfrastructure"):
        adapter(layout)


def test_first_frame_rejects_incomplete_fleet_when_legacy_counts_are_absent() -> None:
    layout = infrastructure()
    layout["slots"].pop()
    subject = SingleDeviceControlAdapter(StationaryFleet(14, {"returnInfrastructure": layout}), "TEST")
    with pytest.raises(ValueError, match="complete prepared fleet"):
        subject.step()


def test_initial_usv_on_shore_is_rejected_instead_of_teleported_to_water() -> None:
    class GroundedFleet(StationaryFleet):
        def step(self) -> RuntimeFrame:
            frame = super().step()
            frame.agents[2].y = -20
            return frame
    subject = SingleDeviceControlAdapter(GroundedFleet(14, {"returnInfrastructure": infrastructure()}), "TEST")
    with pytest.raises(ValueError, match="northern water"):
        subject.step()


def test_absent_metadata_keeps_legacy_first_frame_home_and_no_new_metrics() -> None:
    assert validate_return_infrastructure({}) is None
    subject = SingleDeviceControlAdapter(StationaryFleet(14, {}), "TEST")
    original = subject.step()
    returned = return_to_slot(subject, "UAV-001")
    assert xyz(pose(returned, "UAV-001")) == xyz(pose(original, "UAV-001"))
    assert "returnInfrastructure" not in returned.metrics
    assert "returnStage" not in returned.metrics["deviceControlStates"]["UAV-001"]


@pytest.mark.parametrize("code", ["UAV-001", "USV-001"])
def test_existing_runner_receipts_accept_return_stages_without_new_external_states(code: str) -> None:
    from runner import _device_command_receipt

    subject = adapter()
    subject.step()
    receipt = _device_command_receipt(subject, {"deviceCode": code, "commandType": f"{code[:3]}_RETURN"})
    assert receipt["success"] is True
    assert receipt["deviceStatus"] == "RETURNING"
    assert receipt["frame"]["metrics"]["returnInfrastructure"] == infrastructure()
    return_to_slot(subject, code)
    repeat = _device_command_receipt(subject, {"deviceCode": code, "commandType": f"{code[:3]}_RETURN"})
    assert repeat["success"] is True and repeat["deviceStatus"] == "RETURNED"
    resume = _device_command_receipt(subject, {"deviceCode": code, "commandType": f"{code[:3]}_RESUME"})
    assert resume["success"] is True and resume["deviceStatus"] == "REJOINING"


def multirow_infrastructure() -> dict:
    # Exact 1 UAV + 9 USV / height=280 geometry of the frontend's v1 builder.
    slots = [{"deviceCode": "UAV-001", "kind": "HELIPAD", "eastM": -108, "northM": -222,
              "upM": 1.4, "headingDeg": 90, "approachEastM": -108, "approachNorthM": -222, "approachUpM": 21.4}]
    for index in range(9):
        east = -64 + (index % 8) * 24
        north = -208 + 18 + (index // 8) * 24
        slots.append({"deviceCode": f"USV-{index + 1:03d}", "kind": "BERTH", "eastM": east, "northM": north,
                      "upM": 0, "headingDeg": 90, "approachEastM": east + 8, "approachNorthM": -146, "approachUpM": 0})
    return {"version": "fixed-shore-v1", "coordinateFrame": "FLEET_LOCAL_ENU",
            "shore": {"eastMinM": -128, "eastMaxM": 128, "northMinM": -244, "northMaxM": -208, "surfaceUpM": 1.4},
            "slots": slots}


class MultirowFleet(AlgorithmAdapter):
    code = "TEST_MULTIROW_SHORE"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        frame = RuntimeFrame(runId=self.run_id, algorithmCode=self.code, sequence=self.sequence,
                             timestamp=self.sequence * 100, phase="RUNNING", targets=[], agents=[
            AgentFrame("UAV-001", "UAV", 0, 0, 25, 0, "SCOUT"),
            *[AgentFrame(f"USV-{index + 1:03d}", "USV", -96 + index * 20, 80 + index * 7, 0, 0, "GUARD") for index in range(9)],
        ])
        if self.config.get("nearEntranceCollisionRegression"):
            pose(frame, "USV-001").x, pose(frame, "USV-001").y = -66, -136
            pose(frame, "USV-009").x, pose(frame, "USV-009").y = -46, -136
        return frame


def multirow_adapter(near_entrance: bool = False) -> SingleDeviceControlAdapter:
    return SingleDeviceControlAdapter(MultirowFleet(16, {"uavCount": 1, "usvCount": 9, "usvSpeedMps": 3,
                                                        "returnInfrastructure": multirow_infrastructure(),
                                                        "nearEntranceCollisionRegression": near_entrance}), "TEST")


@pytest.mark.parametrize("near_entrance", [False, True])
def test_multirow_returns_share_a_fifo_channel_without_overlap_and_duplicates_do_not_requeue(near_entrance: bool) -> None:
    subject = multirow_adapter(near_entrance)
    first = subject.step()
    waiting_pose = xyz(pose(first, "USV-009"))
    subject.control_device("USV-001", "USV_RETURN")
    subject.step()  # The reported collision case submits the second command one frame later.
    subject.control_device("USV-009", "USV_RETURN")
    seen_waiting = False
    minimum_distance = math.inf
    for _ in range(5000):
        subject.control_device("USV-009", "USV_RETURN")
        frame = subject.step()
        first_boat, second_boat = pose(frame, "USV-001"), pose(frame, "USV-009")
        minimum_distance = min(minimum_distance, math.dist(xyz(first_boat), xyz(second_boat)))
        if first_boat.status != "RETURNED":
            assert second_boat.status == "RETURNING"
            assert frame.metrics["deviceControlStates"]["USV-009"]["returnStage"] == "WAITING_FOR_CHANNEL"
            assert xyz(second_boat) == waiting_pose
            seen_waiting = True
        if first_boat.status == second_boat.status == "RETURNED":
            break
    else:
        raise AssertionError("FIFO did not let both same-column boats finish")
    assert seen_waiting and minimum_distance >= 8 - 1e-9
    assert xyz(first_boat) == (-64, -190, 0)
    assert xyz(second_boat) == (-64, -166, 0)
    assert subject._berth_channel_queues == {}


def test_different_berth_columns_can_return_in_parallel() -> None:
    subject = multirow_adapter()
    before = subject.step()
    subject.control_device("USV-001", "USV_RETURN")
    subject.control_device("USV-002", "USV_RETURN")
    frame = subject.step()
    for code in ("USV-001", "USV-002"):
        assert xyz(pose(frame, code)) != xyz(pose(before, code))
        assert frame.metrics["deviceControlStates"][code]["returnStage"] == "APPROACHING"


@pytest.mark.parametrize("hold_command", ["USV_HOLD", "USV_STOP"])
def test_stopping_the_channel_owner_keeps_waiters_blocked_until_a_safe_exit(hold_command: str) -> None:
    subject = multirow_adapter()
    first = subject.step()
    waiting_pose = xyz(pose(first, "USV-009"))
    subject.control_device("USV-001", "USV_RETURN")
    for _ in range(2000):
        frame = subject.step()
        if frame.metrics["deviceControlStates"]["USV-001"]["returnStage"] == "CHANNEL":
            break
    else:
        raise AssertionError("first boat never entered channel")
    frozen_owner = xyz(pose(frame, "USV-001"))
    subject.control_device("USV-001", hold_command)
    subject.control_device("USV-009", "USV_RETURN")
    for _ in range(20):
        frame = subject.step()
        assert xyz(pose(frame, "USV-001")) == frozen_owner
        assert xyz(pose(frame, "USV-009")) == waiting_pose
        assert frame.metrics["deviceControlStates"]["USV-001"]["returnStage"] == "HOLDING_CHANNEL"
        assert frame.metrics["deviceControlStates"]["USV-009"]["returnStage"] == "WAITING_FOR_CHANNEL"
    subject.control_device("USV-001", "USV_RESUME")
    released = False
    for _ in range(1000):
        frame = subject.step()
        owner = pose(frame, "USV-001")
        stage = frame.metrics["deviceControlStates"]["USV-009"]["returnStage"]
        if stage != "WAITING_FOR_CHANNEL":
            assert owner.y >= -138  # Entrance -146 plus the 8m outbound clearance.
            released = True
            break
        assert xyz(pose(frame, "USV-009")) == waiting_pose
    assert released


def test_same_column_departures_queue_until_first_boat_clears_the_entrance() -> None:
    subject = multirow_adapter()
    subject.step()
    return_to_slot(subject, "USV-001")
    return_to_slot(subject, "USV-009")
    subject.control_device("USV-001", "USV_RESUME")
    subject.control_device("USV-009", "USV_RESUME")
    second_started = False
    for _ in range(2000):
        frame = subject.step()
        first, second = pose(frame, "USV-001"), pose(frame, "USV-009")
        assert math.dist(xyz(first), xyz(second)) >= 8 - 1e-9
        state = frame.metrics["deviceControlStates"].get("USV-009", {})
        if state.get("returnStage") == "WAITING_FOR_CHANNEL":
            assert xyz(second) == (-64, -166, 0)
        elif not second_started:
            assert first.y >= -138
            second_started = True
        if first.status == second.status == "ACTIVE":
            break
    else:
        raise AssertionError("both queued departures did not rejoin")
    assert second_started


def test_holding_a_waiter_removes_only_its_reservation_and_does_not_block_later_returns() -> None:
    subject = multirow_adapter()
    subject.step()
    subject.control_device("USV-001", "USV_RETURN")
    subject.control_device("USV-009", "USV_RETURN")
    subject.control_device("USV-009", "USV_HOLD")
    return_to_slot(subject, "USV-001")
    assert subject._berth_channel_queues == {}
    assert pose(return_to_slot(subject, "USV-009"), "USV-009").status == "RETURNED"


def existing_scene_infrastructure() -> dict:
    layout = infrastructure()
    layout["version"] = "scene-existing-v1"
    layout["shore"]["surfaceUpM"] = 19.62
    layout["shore"]["eastMaxM"] = 80
    for slot in layout["slots"]:
        if slot["kind"] == "HELIPAD":
            slot["upM"], slot["approachUpM"] = 19.62, 39.62
    third_pad = {**layout["slots"][0], "deviceCode": "UAV-003", "eastM": 0, "approachEastM": 0}
    third_berth = {**layout["slots"][2], "deviceCode": "USV-003", "eastM": 55, "approachEastM": 63}
    layout["slots"].extend([third_pad, third_berth])
    return layout


class CapacityFleet(AlgorithmAdapter):
    code = "TEST_EXISTING_SCENE_CAPACITY"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        origin_up = self.config.get("fleetOrigin", {}).get("upM", 0)
        agents = [AgentFrame(f"{kind}-{index:03d}", kind, index * 5, 45, (25 if kind == "UAV" else 0) - origin_up, 0, "SCOUT")
                  for kind, count_key in (("UAV", "uavCount"), ("USV", "usvCount"))
                  for index in range(1, int(self.config[count_key]) + 1)]
        return RuntimeFrame(runId=self.run_id, algorithmCode=self.code, sequence=self.sequence,
                            timestamp=self.sequence * 100, phase="RUNNING", targets=[], agents=agents)


def capacity_adapter(count: int = 4) -> SingleDeviceControlAdapter:
    return SingleDeviceControlAdapter(CapacityFleet(17, {"uavCount": count, "usvCount": count,
                                                       "returnInfrastructure": existing_scene_infrastructure()}), "TEST")


@pytest.mark.parametrize("count", [4, 128])
def test_existing_scene_sparse_slots_prepare_without_reducing_the_full_fleet(count: int) -> None:
    subject = capacity_adapter(count)
    frame = subject.step()
    assert len(frame.agents) == count * 2
    assert frame.metrics["returnInfrastructure"] == existing_scene_infrastructure()
    assert len(frame.metrics["returnInfrastructure"]["slots"]) == 6


@pytest.mark.parametrize("code,hold", [("UAV-004", "UAV_HOVER"), ("USV-004", "USV_HOLD")])
def test_unassigned_existing_scene_device_rejects_return_without_home_fallback_or_override_changes(code: str, hold: str) -> None:
    subject = capacity_adapter()
    subject.step()
    subject.control_device(code, hold)
    held = pose(subject.step(), code)
    for _ in range(2):
        with pytest.raises(ValueError, match="RETURN_CAPACITY_EXCEEDED"):
            subject.control_device(code, f"{code[:3]}_RETURN")
        current = pose(subject.step(), code)
        assert current.status == "HOLDING" and xyz(current) == xyz(held)
    assert subject.control_device(code, f"{code[:3]}_RESUME") == "REJOINING"
    for _ in range(20):
        current = pose(subject.step(), code)
    assert current.status == "ACTIVE"


def test_original_scene_pad_uses_its_authoritative_surface_height_not_the_old_artificial_shore_height() -> None:
    subject = capacity_adapter()
    subject.step()
    returned = return_to_slot(subject, "UAV-001")
    assert xyz(pose(returned, "UAV-001")) == (-40, -20, 19.62)
    assert returned.metrics["returnInfrastructure"]["version"] == "scene-existing-v1"


def test_existing_scene_still_rejects_unknown_slot_members_and_more_than_three_physical_pads() -> None:
    layout = existing_scene_infrastructure()
    layout["slots"][0]["deviceCode"] = "UAV-005"
    with pytest.raises(ValueError, match="configured"):
        validate_return_infrastructure({"uavCount": 4, "usvCount": 4, "returnInfrastructure": layout})
    layout = existing_scene_infrastructure()
    layout["slots"].append({**layout["slots"][0], "deviceCode": "UAV-004", "eastM": 20, "approachEastM": 20})
    with pytest.raises(ValueError, match="only 3"):
        validate_return_infrastructure({"uavCount": 4, "usvCount": 4, "returnInfrastructure": layout})


def test_existing_scene_first_frame_rejects_a_slot_for_a_device_not_in_the_actual_fleet() -> None:
    subject = capacity_adapter()
    subject.base.config["uavCount"] = 2
    with pytest.raises(ValueError, match="prepared fleet"):
        subject.step()


def native_anchor_layout(origin_up: float = 0.0) -> dict:
    slots = []
    for index in range(3):
        offset = (index - 1) * 14
        east, north = -75 + math.cos(.559) * offset + 360, -215 + math.sin(.559) * offset + 285
        slots.append({"deviceCode": f"UAV-{index + 1:03d}", "kind": "HELIPAD", "eastM": east, "northM": north,
                      "upM": 19.65 - origin_up, "headingDeg": 90 - math.degrees(.559),
                      "approachEastM": east, "approachNorthM": north, "approachUpM": 39.65 - origin_up})
    for index, (east, north, entry_north) in enumerate(((-92, -180, -210), (-108, -160, -190), (-108, -140, -170))):
        slots.append({"deviceCode": f"USV-{index + 1:03d}", "kind": "BERTH", "eastM": east + 360, "northM": north + 285,
                      "upM": -origin_up, "headingDeg": 90, "approachEastM": 120,
                      "approachNorthM": entry_north + 285, "approachUpM": -origin_up})
    return {"version": "scene-existing-v1", "coordinateFrame": "FLEET_LOCAL_ENU",
            "shore": {"eastMinM": 250, "eastMaxM": 325, "northMinM": 40, "northMaxM": 100, "surfaceUpM": 19.65 - origin_up},
            "slots": slots}


def native_anchor_adapter(origin_up: float = 0.0) -> SingleDeviceControlAdapter:
    return SingleDeviceControlAdapter(CapacityFleet(18, {"uavCount": 3, "usvCount": 3,
                                                       "fleetOrigin": {"eastM": -360, "northM": -285, "upM": origin_up},
                                                       "returnInfrastructure": native_anchor_layout(origin_up)}), "TEST")


def corridor_cross_error(agent: AgentFrame, slot: dict) -> float:
    dx, dy = slot["eastM"] - slot["approachEastM"], slot["northM"] - slot["approachNorthM"]
    return abs((agent.x - slot["approachEastM"]) * dy - (agent.y - slot["approachNorthM"]) * dx) / math.hypot(dx, dy)


@pytest.mark.parametrize("code", ["USV-001", "USV-002", "USV-003"])
@pytest.mark.parametrize("origin_up", [0.0, 7.5])
def test_native_berth_follows_its_oblique_corridor_at_sea_level_with_slow_last_eight_metres(code: str, origin_up: float) -> None:
    subject = native_anchor_adapter(origin_up)
    slot = next(item for item in native_anchor_layout(origin_up)["slots"] if item["deviceCode"] == code)
    entry = slot["approachEastM"], slot["approachNorthM"], slot["approachUpM"]
    destination = slot["eastM"], slot["northM"], slot["upM"]
    previous = pose(subject.step(), code)
    subject.control_device(code, "USV_RETURN")
    reached_entry = False
    saw_slow_docking = False
    for _ in range(4000):
        frame = subject.step()
        agent = pose(frame, code)
        state = frame.metrics["deviceControlStates"][code]
        assert agent.z == -origin_up
        assert state["returnStage"] != "CHANNEL"
        movement = math.dist(xyz(previous), xyz(agent))
        assert movement <= .3 + 1e-9
        if reached_entry:
            assert corridor_cross_error(agent, slot) < 1e-6
            assert math.dist(xyz(agent), destination) <= math.dist(xyz(previous), destination) + 1e-9
            assert movement <= .15 + 1e-9
            if state["returnStage"] in {"DOCKING", "PARKED"}:
                saw_slow_docking = True
                assert math.dist(xyz(previous), destination) <= 8 + 1e-6
                assert movement <= .06 + 1e-9
        reached_entry |= math.dist(xyz(agent), entry) < 1e-9
        previous = agent
        if agent.status == "RETURNED":
            break
    else:
        raise AssertionError("native berth did not settle")
    assert reached_entry and saw_slow_docking and xyz(previous) == destination
    subject.control_device(code, "USV_RESUME")
    for _ in range(2000):
        frame = subject.step()
        agent = pose(frame, code)
        stage = frame.metrics["deviceControlStates"].get(code, {}).get("returnStage")
        assert math.dist(xyz(previous), xyz(agent)) <= .3 + 1e-9
        assert agent.z == -origin_up
        if stage == "REJOINING" or agent.status == "ACTIVE":
            assert math.dist(xyz(previous), entry) < 1e-9
            break
        assert corridor_cross_error(agent, slot) < 1e-6
        assert math.dist(xyz(agent), entry) <= math.dist(xyz(previous), entry) + 1e-9
        assert math.dist(xyz(previous), xyz(agent)) <= (.06 if stage == "UNDOCKING" else .15) + 1e-9
        previous = agent
    else:
        raise AssertionError("native departure did not exit its corridor")


def test_native_independent_corridors_with_same_approach_east_do_not_share_a_queue() -> None:
    subject = native_anchor_adapter()
    before = subject.step()
    for index in range(1, 4):
        subject.control_device(f"USV-{index:03d}", "USV_RETURN")
    frame = subject.step()
    for index in range(1, 4):
        code = f"USV-{index:03d}"
        assert xyz(pose(frame, code)) != xyz(pose(before, code))
        assert frame.metrics["deviceControlStates"][code]["returnStage"] != "WAITING_FOR_CHANNEL"
    assert len(subject._berth_channel_queues) == 3


def test_interrupted_native_docking_reverses_from_current_corridor_pose_not_an_axis_derived_point() -> None:
    subject = native_anchor_adapter()
    subject.step()
    subject.control_device("USV-001", "USV_RETURN")
    slot = native_anchor_layout()["slots"][3]
    entry = slot["approachEastM"], slot["approachNorthM"], 0
    for _ in range(3000):
        frame = subject.step()
        agent = pose(frame, "USV-001")
        if corridor_cross_error(agent, slot) < 1e-6 and 40 < math.dist(xyz(agent), entry) < 80:
            break
    else:
        raise AssertionError("native boat did not reach the middle of its corridor")
    subject.control_device("USV-001", "USV_HOLD")
    held = pose(subject.step(), "USV-001")
    subject.control_device("USV-001", "USV_RESUME")
    frame = subject.step()
    resumed = pose(frame, "USV-001")
    assert frame.metrics["deviceControlStates"]["USV-001"]["returnStage"] == "EXITING"
    assert corridor_cross_error(resumed, slot) < 1e-6
    assert math.dist(xyz(resumed), entry) < math.dist(xyz(held), entry)
    assert math.dist(xyz(resumed), xyz(held)) <= .15 + 1e-9


def test_native_altitude_origin_is_applied_once_for_landing_and_rejects_boats_off_local_sea_level() -> None:
    subject = native_anchor_adapter(30.0)
    subject.step()
    landed = return_to_slot(subject, "UAV-001")
    assert pose(landed, "UAV-001").z == pytest.approx(19.65 - 30)
    layout = native_anchor_layout(7.5)
    layout["slots"][3]["upM"] = 0
    with pytest.raises(ValueError, match="sea-level"):
        validate_return_infrastructure({"fleetOrigin": {"upM": 7.5}, "returnInfrastructure": layout})
