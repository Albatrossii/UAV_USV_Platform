from __future__ import annotations

import math

from app.adapters.base import AlgorithmAdapter
from app.adapters.single_device import SingleDeviceControlAdapter
from app.schemas import AgentFrame, RuntimeFrame, TargetFrame


class MovingAdapter(AlgorithmAdapter):
    code = "TEST"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        x = float(self.sequence)
        return RuntimeFrame(
            runId=self.run_id,
            algorithmCode=self.code,
            sequence=self.sequence,
            timestamp=self.sequence * 100,
            phase="RUNNING",
            agents=[
                AgentFrame("UAV-001", "UAV", x, 0.0, 20.0, 0.0, "SCOUT"),
                AgentFrame("USV-001", "USV", x, 10.0, 0.0, 0.0, "GUARD"),
            ],
            targets=[],
        )


class CompletedFleetAdapter(AlgorithmAdapter):
    code = "TEST_COMPLETED"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        x = float(self.sequence)
        return RuntimeFrame(
            runId=self.run_id,
            algorithmCode=self.code,
            sequence=self.sequence,
            timestamp=self.sequence * 100,
            phase="COMPLETED",
            agents=[
                AgentFrame("UAV-001", "UAV", x, 0.0, 20.0, 0.0, "SCOUT"),
                AgentFrame("UAV-002", "UAV", x, 5.0, 20.0, 0.0, "SCOUT"),
                AgentFrame("USV-001", "USV", x, 10.0, 0.0, 0.0, "GUARD"),
            ],
            targets=[],
            metrics={"progress": 1.0},
            terminalStatus="COMPLETED",
        )


class CompletedCaptureFleetAdapter(AlgorithmAdapter):
    code = "TEST_CAPTURE_COMPLETED"

    def step(self) -> RuntimeFrame:
        self.sequence += 1
        agents = []
        for index in range(10):
            angle = 2.0 * math.pi * index / 10.0
            kind = "UAV" if index < 5 else "USV"
            local_index = index + 1 if kind == "UAV" else index - 4
            agents.append(AgentFrame(
                f"{kind}-{local_index:03d}", kind,
                20.0 * math.cos(angle), 20.0 * math.sin(angle),
                20.0 if kind == "UAV" else 0.0, 0.0, "CAPTURE",
            ))
        return RuntimeFrame(
            runId=self.run_id,
            algorithmCode=self.code,
            sequence=self.sequence,
            timestamp=self.sequence * 100,
            phase="COMPLETED",
            agents=agents,
            targets=[TargetFrame("THREAT-001", "THREAT", 0.0, 0.0, 0.0)],
            metrics={"progress": 1.0, "requiredCaptureAgents": 10},
            terminalStatus="COMPLETED",
        )


def adapter() -> SingleDeviceControlAdapter:
    return SingleDeviceControlAdapter(
        MovingAdapter(7, {"uavSpeedMps": 5.0, "usvSpeedMps": 3.0}),
        "ESCORT_GUARD_SINGLE_DEVICE",
    )


def test_hold_overrides_only_selected_device() -> None:
    subject = adapter()
    subject.step()
    assert subject.control_device("uav_001", "UAV_HOVER") == "HOLDING"

    frame = subject.step()
    selected, untouched = frame.agents

    assert selected.x == 1.0
    assert selected.status == "HOLDING"
    assert selected.role == "OPERATOR_CONTROLLED"
    assert untouched.x == 2.0
    assert frame.algorithmCode == "ESCORT_GUARD_SINGLE_DEVICE"
    assert frame.metrics["operatorControlledDeviceCount"] == 1


def test_resume_rejoins_live_algorithm_without_jumping() -> None:
    subject = adapter()
    subject.step()
    subject.control_device("UAV-001", "UAV_HOVER")
    subject.step()
    subject.control_device("UAV-001", "UAV_RESUME")

    first_rejoin = subject.step().agents[0]
    assert 1.0 < first_rejoin.x < 3.0
    assert first_rejoin.status == "REJOINING"

    latest = first_rejoin
    for _ in range(40):
        latest = subject.step().agents[0]
    assert latest.status == "ACTIVE"


def test_rejects_command_for_wrong_device_type() -> None:
    subject = adapter()
    subject.step()

    try:
        subject.control_device("USV-001", "UAV_RETURN")
    except ValueError as error:
        assert "requires a UAV" in str(error)
    else:
        raise AssertionError("expected device type mismatch")


def test_return_blocks_completion_only_until_device_reaches_home() -> None:
    subject = SingleDeviceControlAdapter(
        CompletedFleetAdapter(8, {"uavSpeedMps": 5.0}),
        "ESCORT_GUARD_SINGLE_DEVICE",
    )
    subject.step()
    subject.step()
    subject.control_device("UAV-001", "UAV_RETURN")

    returning = subject.step()
    assert returning.terminalStatus is None
    assert returning.metrics["completionBlocker"] == "WAITING_FOR_RETURN"
    assert returning.metrics["returningDeviceCount"] == 1

    returned = subject.step()
    assert returned.terminalStatus == "COMPLETED"
    assert returned.agents[0].status == "RETURNED"
    assert returned.agents[0].role == "RETURNED"
    assert returned.metrics["returnedDeviceCount"] == 1
    assert returned.metrics["completionBlocker"] == "NONE"


def test_insufficient_returned_fleet_stays_recoverable() -> None:
    subject = SingleDeviceControlAdapter(
        MovingAdapter(9, {"uavSpeedMps": 5.0}),
        "ESCORT_GUARD_SINGLE_DEVICE",
    )
    subject.step()
    subject.step()
    subject.control_device("UAV-001", "UAV_RETURN")
    subject.step()
    degraded = subject.step()

    assert degraded.terminalStatus is None
    assert degraded.metrics["completionBlocker"] == "INSUFFICIENT_ACTIVE_FORCE"
    assert degraded.metrics["missionDegradedReason"] == "INSUFFICIENT_ACTIVE_FORCE"
    assert degraded.metrics["activeMissionDeviceDeficit"] == 1


def test_capture_return_uses_structural_minimum_not_original_group_size() -> None:
    subject = SingleDeviceControlAdapter(
        CompletedCaptureFleetAdapter(10, {"uavSpeedMps": 5.0}),
        "GB_SFLA_CS_SINGLE_DEVICE",
    )
    subject.step()
    subject.step()
    subject.control_device("UAV-001", "UAV_RETURN")
    subject.step()
    completed = subject.step()

    assert completed.terminalStatus == "COMPLETED"
    assert completed.metrics["requiredActiveMissionDeviceCount"] == 3
    assert completed.metrics["activeMissionDeviceCount"] == 9
    assert completed.metrics["completionBlocker"] == "NONE"


def test_hold_is_excluded_from_roster_but_does_not_block_valid_completion() -> None:
    subject = SingleDeviceControlAdapter(
        CompletedFleetAdapter(11, {}),
        "ESCORT_GUARD_SINGLE_DEVICE",
    )
    subject.step()
    subject.control_device("UAV-001", "UAV_HOVER")

    completed = subject.step()

    assert completed.terminalStatus == "COMPLETED"
    assert completed.metrics["completionBlocker"] == "NONE"
    assert completed.metrics["activeMissionDeviceCount"] == 2
    assert completed.metrics["activeMissionDeviceCodes"] == ["UAV-002", "USV-001"]
    assert completed.metrics["excludedMissionDeviceCodes"] == ["UAV-001"]


def test_hold_enters_recoverable_degraded_mode_when_a_type_is_lost() -> None:
    subject = adapter()
    subject.step()
    subject.control_device("UAV-001", "UAV_HOVER")

    degraded = subject.step()

    assert degraded.terminalStatus is None
    assert degraded.metrics["completionBlocker"] == "INSUFFICIENT_ACTIVE_FORCE"
    assert degraded.metrics["missingActiveDeviceTypes"] == ["UAV"]
    assert degraded.metrics["missionCapability"] == "DEGRADED"


def test_capture_repairs_the_real_ring_after_multiple_devices_exit() -> None:
    subject = SingleDeviceControlAdapter(
        CompletedCaptureFleetAdapter(
            12,
            {"uavSpeedMps": 15.0, "usvSpeedMps": 4.0},
        ),
        "GB_SFLA_CS_SINGLE_DEVICE",
    )
    subject.step()
    remaining = {"UAV-001", "UAV-002", "USV-001"}
    for agent in list(subject._last_agents.values()):
        if agent.code in remaining:
            continue
        command = "UAV_HOVER" if agent.type == "UAV" else "USV_HOLD"
        subject.control_device(agent.code, command)

    first = subject.step()
    assert first.terminalStatus is None
    assert first.metrics["completionBlocker"] == "ACTIVE_FORMATION_NOT_CLOSED"
    assert first.metrics["activeFormationReplanning"] is True

    latest = first
    for _ in range(120):
        latest = subject.step()
        if latest.terminalStatus == "COMPLETED":
            break

    assert latest.terminalStatus == "COMPLETED"
    assert latest.metrics["activeFormationBlocker"] == "NONE"
