from __future__ import annotations

from app.adapters.base import AlgorithmAdapter
from app.adapters.single_device import SingleDeviceControlAdapter
from app.schemas import AgentFrame, RuntimeFrame


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
        x = float(self.sequence)
        agents = [
            AgentFrame(f"UAV-{index:03d}", "UAV", x, float(index), 20.0, 0.0, "CAPTURE")
            for index in range(1, 6)
        ] + [
            AgentFrame(f"USV-{index:03d}", "USV", x, float(index + 10), 0.0, 0.0, "CAPTURE")
            for index in range(1, 6)
        ]
        return RuntimeFrame(
            runId=self.run_id,
            algorithmCode=self.code,
            sequence=self.sequence,
            timestamp=self.sequence * 100,
            phase="COMPLETED",
            agents=agents,
            targets=[],
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
    assert completed.metrics["requiredActiveMissionDeviceCount"] == 5
    assert completed.metrics["activeMissionDeviceCount"] == 9
    assert completed.metrics["completionBlocker"] == "NONE"
