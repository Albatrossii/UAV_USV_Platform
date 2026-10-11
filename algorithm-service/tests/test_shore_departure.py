"""Contract tests for departure from the original scene's pad and berth slots."""
from __future__ import annotations

import copy
import math
from itertools import combinations

import pytest

from app.adapters.base import AlgorithmAdapter
from app.adapters.single_device import SingleDeviceControlAdapter
from app.schemas import AgentFrame, RuntimeFrame, TargetFrame
from runner import build_adapter
from test_return_infrastructure import corridor_cross_error, native_anchor_layout, pose, xyz


def shore_config(**changes) -> dict:
    return {
        "startFromShore": True, "uavCount": 3, "usvCount": 3,
        "uavSpeedMps": 5, "usvSpeedMps": 3, "targetCount": 1,
        "protectedCount": 1, "threatCount": 1, "seed": 42,
        "adaptiveMultiTarget": True,
        "fleetOrigin": {"eastM": -360, "northM": -285, "upM": 0},
        "returnInfrastructure": native_anchor_layout(), **changes,
    }


class AssemblyFleet(AlgorithmAdapter):
    """A stationary assembly target, with visible motion only after START."""
    code = "ASSEMBLY_TEST"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        shift = self.sequence * .1 if self.mission_active else 0
        return RuntimeFrame(
            runId=self.run_id, algorithmCode=self.code, sequence=self.sequence,
            timestamp=self.sequence * 100, phase="RUNNING", agents=[
                AgentFrame(f"{kind}-{i + 1:03d}", kind, 0 + shift, i * 25,
                           18 if kind == "UAV" else 0, 0, "ASSEMBLY")
                for kind in ("UAV", "USV") for i in range(3)
            ], targets=[TargetFrame("THREAT-001", "THREAT", shift, -30, 0)],
            metrics={"progress": self.sequence / 100 if self.mission_active else 0},
        )


def subject(**changes) -> SingleDeviceControlAdapter:
    return SingleDeviceControlAdapter(AssemblyFleet(91, shore_config(**changes)), "ESCORT_GUARD")


def slots(config: dict) -> dict:
    return {slot["deviceCode"]: slot for slot in config["returnInfrastructure"]["slots"]}


def slot_xyz(slot: dict) -> tuple:
    return slot["eastM"], slot["northM"], slot["upM"]


def check_speed(previous: RuntimeFrame, current: RuntimeFrame) -> None:
    for agent in current.agents:
        maximum = .5 if agent.type == "UAV" else .3
        assert math.dist(xyz(agent), xyz(pose(previous, agent.code))) <= maximum + 1e-7, agent.code


def run_deployment(adapter: SingleDeviceControlAdapter, max_steps: int = 4000):
    previous = adapter.step()
    adapter.set_mission_active(True)
    for _ in range(max_steps):
        current = adapter.step()
        check_speed(previous, current)
        yield previous, current
        if current.metrics["deploymentState"] == "COMPLETE":
            return
        previous = current
    pytest.fail("departure did not reach the assembly formation")


def test_prestart_stays_on_original_slots_without_planner_or_target_preview_drift():
    adapter = subject()
    initial = adapter.step()
    layout = slots(adapter.config)
    for _ in range(50):
        frame = adapter.step()
        assert frame.phase == "PARKED" and frame.metrics["deploymentState"] == "PARKED"
        assert frame.metrics["progress"] == 0 and frame.terminalStatus is None
        assert frame.metrics["operatorControlledDeviceCount"] == 0
        assert frame.metrics["simulationElapsedSeconds"] == 0
        assert frame.metrics["deploymentElapsedSeconds"] == 0
        assert frame.metrics["deploymentProgress"] == 0
        assert frame.metrics["deploymentReadyCount"] == 0
        assert frame.metrics["deploymentTotalCount"] == 6
        assert frame.metrics["deploymentWithdrawnCount"] == 0
        assert frame.targets == initial.targets
        assert frame.sequence > initial.sequence
        for agent in frame.agents:
            assert xyz(agent) == slot_xyz(layout[agent.code])
            assert agent.status == "PARKED" and agent.role == "DEPLOYMENT"
    assert adapter.base.sequence == 1 and not adapter.base.mission_active


def test_uavs_take_off_vertically_then_clear_island_westward_before_descending():
    adapter = subject()
    layout = slots(adapter.config)
    seen = {f"UAV-{i:03d}": set() for i in range(1, 4)}
    for previous, frame in run_deployment(adapter):
        for code, stages in seen.items():
            agent, before = pose(frame, code), pose(previous, code)
            stage = frame.metrics["deviceControlStates"].get(code, {}).get("returnStage")
            stages.add(stage)
            slot = layout[code]
            if stage == "TAKING_OFF":
                assert (agent.x, agent.y) == (slot["eastM"], slot["northM"])
                assert 0 <= agent.z - before.z <= .2 + 1e-7
            elif stage == "DEPARTING":
                assert agent.z == pytest.approx(slot["approachUpM"])
                assert agent.y == slot["northM"] and agent.x <= before.x
            if agent.z < before.z - 1e-7:
                assert before.x <= 120 + 1e-7, "descent must start beyond the island and berth approach boundary"
    for stages in seen.values():
        assert {"TAKING_OFF", "DEPARTING", "REJOINING", None}.issubset(stages)


def test_usvs_undock_slowly_along_each_native_corridor_at_sea_level():
    adapter = subject()
    layout = slots(adapter.config)
    seen = {f"USV-{i:03d}": set() for i in range(1, 4)}
    for previous, frame in run_deployment(adapter):
        for code, stages in seen.items():
            agent, before = pose(frame, code), pose(previous, code)
            stage = frame.metrics["deviceControlStates"].get(code, {}).get("returnStage")
            stages.add(stage)
            assert agent.z == 0
            if stage in {"UNDOCKING", "EXITING"}:
                assert corridor_cross_error(agent, layout[code]) < 1e-7
                assert math.dist(xyz(before), xyz(agent)) <= (.06 if stage == "UNDOCKING" else .15) + 1e-7
                assert math.dist(xyz(agent), slot_xyz(layout[code])) >= math.dist(xyz(before), slot_xyz(layout[code])) - 1e-7
    for stages in seen.values():
        assert {"UNDOCKING", "EXITING", "REJOINING", None}.issubset(stages)


def test_automatic_departure_publishes_physical_stage_instead_of_rejoining():
    adapter = subject()
    adapter.step()
    adapter.set_mission_active(True)
    seen = set()
    for _ in range(4000):
        frame = adapter.step()
        for agent in frame.agents:
            state = frame.metrics["deviceControlStates"].get(agent.code, {})
            if state.get("motionState") != "REJOINING":
                continue
            stage = state.get("returnStage")
            seen.add(stage)
            if stage == "REJOINING":
                assert agent.status == "ASSEMBLING"
            elif stage:
                assert agent.status == stage
        if frame.metrics["deploymentState"] == "COMPLETE":
            break
    assert {"TAKING_OFF", "DEPARTING", "UNDOCKING", "EXITING", "REJOINING"}.issubset(seen)


@pytest.mark.parametrize("algorithm", ["ESCORT_GUARD", "GB_SFLA_CS"])
@pytest.mark.parametrize("seed", [1, 42, 20260814])
def test_real_planner_waits_for_full_assembly_and_handoff_is_continuous(algorithm, seed):
    adapter = build_adapter(algorithm, 92, shore_config(seed=seed))
    minimum = {"UAV": math.inf, "USV": math.inf}
    closest = {}
    initial_targets = None
    last = None
    for previous, frame in run_deployment(adapter):
        initial_targets = previous.targets if initial_targets is None else initial_targets
        assert frame.targets == initial_targets, "threat or protected ship advanced before fleet assembly"
        assert adapter.base.sequence == 1
        assert frame.metrics["simulationElapsedSeconds"] == pytest.approx(previous.metrics["simulationElapsedSeconds"] + .1)
        assert frame.metrics["deploymentElapsedSeconds"] == frame.metrics["simulationElapsedSeconds"]
        assert frame.metrics["deploymentProgress"] >= previous.metrics["deploymentProgress"]
        assert frame.metrics["deploymentReadyCount"] == sum(agent.status == "ACTIVE" for agent in frame.agents)
        assert frame.metrics["deploymentTotalCount"] == 6
        assert frame.metrics["deploymentWithdrawnCount"] == 0
        if frame.metrics["deploymentState"] != "COMPLETE":
            assert not adapter.base.mission_active
            assert frame.phase == "DEPLOYING" and frame.terminalStatus is None
            assert frame.metrics["progress"] == 0
            assert 0 < frame.metrics["deploymentProgress"] <= 1
        for first, second in combinations(frame.agents, 2):
            if first.type == second.type:
                distance = math.dist(xyz(first), xyz(second))
                if distance < minimum[first.type]:
                    minimum[first.type] = distance
                    closest[first.type] = (frame.sequence, first.code, xyz(first), second.code, xyz(second))
        last = frame
    assert last is not None and adapter.base.mission_active
    assert last.metrics["deployingDeviceCodes"] == []
    assert last.metrics["deploymentProgress"] == 1
    assert last.metrics["deploymentReadyCount"] == 6
    assert all(agent.status == "ACTIVE" for agent in last.agents)
    following = adapter.step()
    # The live planner may accelerate above cruise speed within its physical
    # limits (15 m/s UAV, 4 m/s USV); transfer must never teleport to a new pose.
    for agent in following.agents:
        assert math.dist(xyz(agent), xyz(pose(last, agent.code))) <= (1.5 if agent.type == "UAV" else .4) + 1e-7
    assert adapter.base.sequence == 2
    assert following.metrics["simulationElapsedSeconds"] == pytest.approx(last.metrics["simulationElapsedSeconds"] + .1)
    assert following.metrics["deploymentElapsedSeconds"] == last.metrics["deploymentElapsedSeconds"]
    print(f"{algorithm} seed {seed} minimum departure separation: {minimum}; closest poses: {closest}")
    assert min(minimum.values()) >= 8, f"unsafe departure path: {minimum}"


@pytest.mark.parametrize("code,command,interruption_step", [
    ("UAV-001", "UAV_HOVER", 10),
    ("UAV-001", "UAV_HOVER", 250),
    ("USV-001", "USV_HOLD", 10),
])
def test_operator_hold_blocks_assembly_until_resume_without_teleporting(code, command, interruption_step):
    adapter = subject()
    adapter.step()
    adapter.set_mission_active(True)
    for _ in range(interruption_step):
        adapter.step()
    adapter.control_device(code, command)
    held = adapter.step()
    for _ in range(2400):
        frame = adapter.step()
        assert xyz(pose(frame, code)) == xyz(pose(held, code))
        assert frame.metrics["deploymentState"] == "DEPLOYING"
        assert not adapter.base.mission_active
    assert frame.metrics["deployingDeviceCodes"] == [code]
    held_progress = frame.metrics["deploymentProgress"]
    for _ in range(20):
        frame = adapter.step()
        assert frame.metrics["deploymentProgress"] == held_progress
        assert frame.metrics["deploymentReadyCount"] == 5
    adapter.control_device(code, f"{code[:3]}_RESUME")
    previous = frame
    for _ in range(3000):
        frame = adapter.step()
        check_speed(previous, frame)
        agent = pose(frame, code)
        if code.startswith("UAV") and agent.z < pose(previous, code).z - 1e-7:
            assert pose(previous, code).x <= 120 + 1e-7
        if frame.metrics["deploymentState"] == "COMPLETE":
            break
        previous = frame
    else:
        pytest.fail("operator resume did not finish interrupted deployment")


@pytest.mark.parametrize("code", ["UAV-001", "USV-001"])
def test_return_during_deployment_withdraws_device_only_after_arrival(code):
    adapter = subject()
    adapter.step()
    adapter.set_mission_active(True)
    for _ in range(200):
        adapter.step()
    adapter.control_device(code, f"{code[:3]}_RETURN")
    parked = False
    previous = adapter.step()
    for _ in range(3500):
        frame = adapter.step()
        check_speed(previous, frame)
        agent = pose(frame, code)
        if agent.status == "RETURNED":
            parked = True
            assert xyz(agent) == slot_xyz(slots(adapter.config)[code])
            assert code not in frame.metrics["deployingDeviceCodes"]
            assert frame.metrics["deploymentWithdrawnCount"] == 1
            assert frame.metrics["deploymentReadyCount"] <= 5
        else:
            assert not parked
            assert code in frame.metrics["deployingDeviceCodes"]
            assert not adapter.base.mission_active
            assert frame.metrics["deploymentWithdrawnCount"] == 0
        if frame.metrics["deploymentState"] == "COMPLETE":
            break
        previous = frame
    else:
        pytest.fail("withdrawal prevented remaining fleet deployment from completing")
    assert parked and frame.metrics["returnedDeviceCodes"] == [code]
    assert frame.metrics["deploymentReadyCount"] == 5
    assert frame.metrics["deploymentTotalCount"] == 6
    assert frame.metrics["deploymentProgress"] == 1


@pytest.mark.parametrize("code", ["UAV-001", "USV-001"])
def test_returned_device_rejoining_before_last_arrival_restores_assembly_requirement(code):
    adapter = subject()
    adapter.step()
    adapter.set_mission_active(True)
    for _ in range(10):
        adapter.step()
    # Keep one departing aircraft on hold while the selected member withdraws
    # to its assigned slot and all other members reach their assembly points.
    adapter.control_device("UAV-002", "UAV_HOVER")
    adapter.control_device(code, f"{code[:3]}_RETURN")
    for _ in range(4000):
        frame = adapter.step()
        if pose(frame, code).status == "RETURNED" and frame.metrics["deployingDeviceCodes"] == ["UAV-002"]:
            break
    else:
        pytest.fail("withdrawal and other member arrivals did not settle")
    adapter.control_device("UAV-002", "UAV_RESUME")
    assembly = pose(adapter._deployment_frame, "UAV-002")
    for _ in range(2000):
        frame = adapter.step()
        if math.dist(xyz(pose(frame, "UAV-002")), xyz(assembly)) < 2:
            break
    else:
        pytest.fail("last outstanding aircraft never approached assembly")

    # A late decision to rejoin must restore pending membership immediately;
    # otherwise the other aircraft's arrival starts the planner during takeoff.
    adapter.control_device(code, f"{code[:3]}_RESUME")
    assert code in adapter._deployment_pending
    seen_stages = set()
    saw_only_selected_pending = False
    previous = frame
    for _ in range(4000):
        frame = adapter.step()
        check_speed(previous, frame)
        agent = pose(frame, code)
        stage = frame.metrics["deviceControlStates"].get(code, {}).get("returnStage")
        seen_stages.add(stage)
        if frame.metrics["deploymentState"] == "COMPLETE":
            assert agent.status == "ACTIVE" and adapter.base.mission_active
            break
        assert code in frame.metrics["deployingDeviceCodes"]
        assert not adapter.base.mission_active
        saw_only_selected_pending |= frame.metrics["deployingDeviceCodes"] == [code]
        if code.startswith("UAV") and agent.z < pose(previous, code).z - 1e-7:
            assert pose(previous, code).x <= 120 + 1e-7
        previous = frame
    else:
        pytest.fail("late rejoin did not complete assembly")
    assert saw_only_selected_pending
    required = {"TAKING_OFF", "DEPARTING"} if code.startswith("UAV") else {"UNDOCKING", "EXITING"}
    assert required.issubset(seen_stages)


def test_pause_during_departure_freezes_every_pose_then_resumes_same_route():
    adapter = subject()
    adapter.step()
    adapter.set_mission_active(True)
    before = adapter.step()
    adapter.set_mission_active(False)
    for _ in range(20):
        frame = adapter.step()
        assert frame.agents == before.agents
        assert frame.targets == before.targets
        assert frame.metrics["simulationElapsedSeconds"] == before.metrics["simulationElapsedSeconds"]
        assert frame.metrics["deploymentElapsedSeconds"] == before.metrics["deploymentElapsedSeconds"]
        assert frame.metrics["deploymentProgress"] == before.metrics["deploymentProgress"]
    adapter.set_mission_active(True)
    following = adapter.step()
    check_speed(frame, following)
    assert xyz(pose(following, "UAV-001")) != xyz(pose(frame, "UAV-001"))
    assert following.metrics["simulationElapsedSeconds"] == pytest.approx(before.metrics["simulationElapsedSeconds"] + .1)


@pytest.mark.parametrize("code,command", [
    ("UAV-001", "UAV_HOVER"),
    ("USV-001", "USV_HOLD"),
    ("USV-001", "USV_STOP"),
])
def test_hold_on_withdrawn_member_does_not_report_it_as_arrived(code, command):
    adapter = subject()
    adapter.step()
    adapter.set_mission_active(True)
    for _ in range(10):
        adapter.step()
    adapter.control_device("UAV-002", "UAV_HOVER")
    adapter.control_device(code, f"{code[:3]}_RETURN")
    for _ in range(4000):
        frame = adapter.step()
        if pose(frame, code).status == "RETURNED" and frame.metrics["deployingDeviceCodes"] == ["UAV-002"]:
            break
    else:
        pytest.fail("withdrawal and other member arrivals did not settle")
    assert frame.metrics["deploymentReadyCount"] == 4
    assert frame.metrics["deploymentWithdrawnCount"] == 1
    before = frame
    adapter.control_device(code, command)
    frame = adapter.step()
    assert xyz(pose(frame, code)) == xyz(pose(before, code))
    assert frame.metrics["deploymentReadyCount"] == 4
    assert frame.metrics["deploymentWithdrawnCount"] == 1
    assert frame.metrics["deploymentProgress"] == before.metrics["deploymentProgress"]
    adapter.control_device(code, f"{code[:3]}_RESUME")
    frame = adapter.step()
    assert code in frame.metrics["deployingDeviceCodes"]
    assert frame.metrics["deploymentWithdrawnCount"] == 0
    assert frame.metrics["deploymentReadyCount"] == 4


@pytest.mark.parametrize("count_key", ["uavCount", "usvCount"])
def test_more_than_three_native_slots_is_rejected_before_departure(count_key):
    with pytest.raises(ValueError, match="3"):
        subject(**{count_key: 4})


def test_shore_departure_requires_infrastructure():
    config = shore_config()
    config.pop("returnInfrastructure")
    with pytest.raises(ValueError, match="停机坪"):
        SingleDeviceControlAdapter(AssemblyFleet(91, config), "ESCORT_GUARD")


def test_legacy_non_shore_start_keeps_existing_offshore_behavior():
    adapter = subject(startFromShore=False)
    frame = adapter.step()
    assert "deploymentState" not in frame.metrics
    assert all(agent.status == "ACTIVE" for agent in frame.agents)
    assert not frame.metrics["deviceControlStates"]
    following = adapter.step()
    assert pose(following, "UAV-001").x > pose(frame, "UAV-001").x
    assert adapter.base.sequence == 2


@pytest.mark.parametrize("algorithm", ["ESCORT_GUARD", "GB_SFLA_CS"])
def test_runner_keeps_target_poses_but_does_not_use_parked_poses_as_assembly(algorithm):
    config = shore_config(initialPosesCoordinateFrame="FLEET_LOCAL_ENU")
    target = {"deviceCode": "THREAT-001", "eastM": 10, "northM": 20, "upM": 0}
    parked = [{"deviceCode": slot["deviceCode"], "eastM": slot["eastM"],
               "northM": slot["northM"], "upM": slot["upM"]}
              for slot in config["returnInfrastructure"]["slots"]]
    config["initialPoses"] = [*parked, target]
    original = copy.deepcopy(config)
    adapter = build_adapter(algorithm, 93, config)
    assert config == original
    planner_poses = adapter.base.config["initialPoses"]
    assert planner_poses[-1] == target
    if algorithm == "ESCORT_GUARD":
        assert planner_poses == [target]
        assert "shoreDeploymentMode" not in adapter.base.config
        frame = adapter.step()
        assert all(xyz(agent) == slot_xyz(slots(config)[agent.code]) for agent in frame.agents)
        return
    assert adapter.base.config["shoreDeploymentMode"] == "SAFE_RELEASE"
    launch_by_code = {
        item["deviceCode"]: item for item in planner_poses[:-1]
    }
    assert set(launch_by_code) == set(slots(config))
    for code, slot in slots(config).items():
        launch = launch_by_code[code]
        if code.startswith("UAV-"):
            assert (launch["eastM"], launch["northM"], launch["upM"]) == (
                slot["approachEastM"], slot["approachNorthM"], slot["approachUpM"],
            )
        else:
            assert math.hypot(
                launch["eastM"] - slot["eastM"],
                launch["northM"] - slot["northM"],
            ) == pytest.approx(16.0)
    frame = adapter.step()
    assert all(xyz(agent) == slot_xyz(slots(config)[agent.code]) for agent in frame.agents)
    assert frame.metrics["deploymentMode"] == "SAFE_RELEASE"


def test_safe_release_starts_capture_without_long_offshore_assembly():
    adapter = build_adapter("GB_SFLA_CS", 94, shore_config())
    adapter.step()
    adapter.set_mission_active(True)
    for _ in range(300):
        frame = adapter.step()
        if frame.metrics["deploymentState"] == "COMPLETE":
            break
    else:
        pytest.fail("safe shoreline release did not hand off within 30 simulated seconds")
    assert frame.metrics["deploymentElapsedSeconds"] < 25
    assert frame.metrics["deploymentProgress"] == 1
    assert frame.metrics["deploymentReadyCount"] == 6
    assert adapter.base.mission_active
