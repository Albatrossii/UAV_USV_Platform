"""Display composition must cover shore departure without driving the solver."""
from copy import deepcopy

import pytest

from app.adapters.single_device import SingleDeviceControlAdapter
from app.schemas import RuntimeFrame
from test_shore_departure import AssemblyFleet, shore_config


def adapter(code="ESCORT_GUARD", run_id=99420, **config):
    return SingleDeviceControlAdapter(AssemblyFleet(run_id, shore_config(**config)), code)


def measure(subject, *, deployment=1, assembly=1, task=0, terminal=None, **metrics):
    frame = RuntimeFrame(subject.run_id, subject.code, 1, 100, "RUNNING", [], [], {
        "deploymentProgress": deployment, "escortAssemblyProgress": assembly,
        "missionProgress": task, **metrics,
    }, terminalStatus=terminal)
    subject._compose_lifecycle_progress(frame)
    return frame


def test_escort_composes_departure_near_guard_and_original_task_progress():
    subject = adapter()
    subject.set_mission_active(True)
    depart = measure(subject, deployment=.5, assembly=0)
    assert depart.metrics["missionProgress"] == .05
    near_guard = measure(subject, assembly=.5)
    assert near_guard.metrics["missionProgress"] == .2
    task = measure(subject, task=.5)
    assert task.metrics["missionProgress"] == task.metrics["progress"] == .65
    assert task.metrics["taskProgress"] == .5
    assert task.metrics["progressCompositionVersion"] == "shore-lifecycle-v1"
    assert task.metrics["progressWeights"] == {"deployment": .1, "escortAssembly": .2, "task": .7}
    assert task.metrics["progressComponents"] == {"deployment": 1, "escortAssembly": 1, "task": .5}


@pytest.mark.parametrize("code", ["GB_SFLA_CS", "GB_SFLA_CS_SINGLE_DEVICE"])
def test_capture_uses_only_departure_and_task_weights(code):
    subject = adapter(code)
    subject.set_mission_active(True)
    frame = measure(subject, task=.5, assembly=None)
    assert frame.metrics["missionProgress"] == .55
    assert frame.metrics["taskProgress"] == .5
    assert frame.metrics["progressWeights"] == {"deployment": .1, "escortAssembly": 0, "task": .9}


@pytest.mark.parametrize("mode", ["SAFE_RELEASE", "FULL_ASSEMBLY"])
def test_pending_departure_cannot_earn_credit_from_frozen_near_guard_or_task_preview(mode):
    subject = adapter(shoreDeploymentMode=mode)
    subject.set_mission_active(True)
    frame = measure(subject, deployment=.5, assembly=1, task=.5, deploymentState="DEPLOYING")
    assert frame.metrics["progress"] == .05
    assert frame.metrics["taskProgress"] == .5
    assert frame.metrics["progressComponents"] == {"deployment": .5, "escortAssembly": 0, "task": 0}
    assert frame.metrics["progressRawComponents"] == {"deployment": .5, "escortAssembly": 1, "task": .5}
    frame = measure(subject, deployment=1, assembly=1, task=.5, deploymentState="COMPLETE")
    assert frame.metrics["progress"] == .65


def test_prestart_parked_is_zero_even_if_preview_has_a_planned_formation():
    subject = adapter()
    frame = measure(subject, deployment=1, assembly=1, task=.8)
    assert frame.metrics["missionProgress"] == 0
    assert frame.metrics["taskProgress"] == .8
    assert not subject.mission_active


def test_pause_and_repeated_composed_snapshots_never_accumulate_weights():
    subject = adapter()
    subject.set_mission_active(True)
    frame = measure(subject, task=.5)
    original = deepcopy(frame)
    for _ in range(30):
        subject._compose_lifecycle_progress(frame)
        assert frame.metrics["missionProgress"] == .65
        assert frame.metrics["taskProgress"] == .5
    subject.set_mission_active(False)
    for _ in range(30):
        copied = deepcopy(frame)
        copied.timestamp += 5000
        subject._compose_lifecycle_progress(copied)
        assert copied.metrics["missionProgress"] == original.metrics["missionProgress"]
    assert frame.agents == original.agents and frame.targets == original.targets


def test_pause_during_departure_keeps_earned_progress_when_phase_reads_parked():
    subject = adapter()
    subject.set_mission_active(True)
    assert measure(subject, deployment=.5, assembly=0).metrics["progress"] == .05
    subject.set_mission_active(False)
    paused = measure(subject, deployment=.8, assembly=0, deploymentState="PARKED")
    assert paused.metrics["progress"] == .05


def test_withdrawal_and_hold_cannot_roll_back_the_run_or_fake_success():
    subject = adapter()
    subject.set_mission_active(True)
    assert measure(subject, task=.5).metrics["progress"] == .65
    lower = measure(subject, deployment=.5, assembly=.2, task=.1,
                    completionBlocker="OPERATOR_OVERRIDE", controlOverrideActive=True)
    assert lower.metrics["progress"] == .65
    assert lower.metrics["taskProgress"] == .1
    assert lower.terminalStatus is None
    withdrawn = measure(subject, task=.2, returnedDeviceCount=1,
                        completionBlocker="INSUFFICIENT_ACTIVE_FORCE")
    assert withdrawn.metrics["progress"] == .65
    assert withdrawn.terminalStatus is None


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf"), True, "0.5"])
def test_invalid_values_cannot_produce_nan_or_invent_stage_completion(value):
    subject = adapter()
    subject.set_mission_active(True)
    frame = measure(subject, deployment=value, assembly=value, task=value)
    assert frame.metrics["missionProgress"] == frame.metrics["taskProgress"] == 0
    assert frame.metrics["progressComponentsKnown"] == {"deployment": False, "escortAssembly": False, "task": False}
    assert frame.terminalStatus is None


def test_finite_out_of_range_task_values_are_normalized_and_nonterminal_is_capped():
    subject = adapter()
    subject.set_mission_active(True)
    assert measure(subject, deployment=-3, assembly=-2, task=-1).metrics["progress"] == 0
    frame = measure(subject, deployment=5, assembly=4, task=3)
    assert frame.metrics["taskProgress"] == 1
    assert frame.metrics["progress"] == .99
    assert frame.terminalStatus is None


def test_missing_assembly_requires_explicit_legacy_readiness_not_elapsed_time():
    subject = adapter()
    subject.set_mission_active(True)
    unknown = measure(subject, assembly=None, task=.5, simulationElapsedSeconds=999)
    assert unknown.metrics["progress"] == .45
    assert not unknown.metrics["progressComponentsKnown"]["escortAssembly"]
    ready = measure(subject, assembly=None, task=.5, escortAssemblyState="READY")
    assert ready.metrics["progress"] == .65
    assert ready.metrics["progressComponentsKnown"]["escortAssembly"]
    # This layer changes no startup or movement permission even when unknown.
    assert subject.mission_active and not subject._deployment_complete


def test_raw_progress_fallback_and_invalid_mission_value_use_valid_solver_progress():
    subject = adapter()
    subject.set_mission_active(True)
    frame = measure(subject, task=None, progress=.5)
    assert frame.metrics["taskProgress"] == .5
    assert frame.metrics["progress"] == .65


@pytest.mark.parametrize("terminal", [None, "STOPPED", "FAILED", "CANCELED", "COMPLETED"])
def test_only_authoritative_completion_reaches_one_hundred_percent(terminal):
    subject = adapter()
    subject.set_mission_active(True)
    frame = measure(subject, task=1, terminal=terminal, missionStage="COMPLETED")
    assert frame.metrics["progress"] == (1 if terminal == "COMPLETED" else .99)
    assert frame.terminalStatus == terminal


def test_new_run_has_no_old_high_water_mark_and_offshore_runs_keep_legacy_metrics():
    first = adapter(run_id=1)
    first.set_mission_active(True)
    assert measure(first, task=.9).metrics["progress"] == .93
    second = adapter(run_id=2)
    second.set_mission_active(True)
    assert measure(second, deployment=.5, assembly=0).metrics["progress"] == .05
    offshore = adapter(startFromShore=False)
    offshore.set_mission_active(True)
    frame = measure(offshore, task=.5, progress=.3)
    assert frame.metrics["missionProgress"] == .5 and frame.metrics["progress"] == .3
    assert "taskProgress" not in frame.metrics
    assert "progressCompositionVersion" not in frame.metrics


@pytest.mark.parametrize("mode", ["SAFE_RELEASE", "FULL_ASSEMBLY"])
def test_real_wrapper_departure_and_paused_step_snapshots_keep_task_source(mode):
    subject = adapter(shoreDeploymentMode=mode)
    initial = subject.step()
    assert initial.phase == "PARKED" and initial.metrics["progress"] == 0
    subject.set_mission_active(True)
    for _ in range(20):
        frame = subject.step()
    assert 0 < frame.metrics["progress"] < .1
    assert frame.metrics["progress"] == pytest.approx(.1 * frame.metrics["deploymentProgress"], abs=1e-6)
    assert frame.metrics["taskProgress"] == 0
    before = deepcopy(frame)
    subject.set_mission_active(False)
    for _ in range(20):
        frame = subject.step()
        assert frame.metrics["progress"] == before.metrics["progress"]
        assert frame.metrics["taskProgress"] == 0
        assert frame.agents == before.agents and frame.targets == before.targets


def test_real_post_departure_pause_uses_task_progress_not_already_composed_value():
    subject = adapter()
    # Isolate the post-release snapshot branch without spending this unit test
    # on trajectories, which are already covered by test_shore_departure.
    subject._deployment_complete = True
    subject.set_mission_active(True)
    frame = subject.step()
    raw = frame.metrics["taskProgress"]
    expected = frame.metrics["progress"]
    assert raw > 0 and expected != raw
    assert subject.base.sequence == 1
    subject.set_mission_active(False)
    for _ in range(30):
        paused = subject.step()
        assert paused.metrics["taskProgress"] == raw
        assert paused.metrics["progress"] == expected
        assert subject.base.sequence == 1
