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
