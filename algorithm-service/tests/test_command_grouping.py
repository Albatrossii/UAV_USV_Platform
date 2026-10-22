"""Bindings are persistent capture configuration, independent of current motion."""
import numpy as np
import pytest
from app.adapters.command_escort import CommandEscortAdapter, PROFILE


def bindings():
    return {f'{kind}-{i:03d}': target for kind, target in
            [('UAV', 'THREAT-001'), ('USV', 'THREAT-002')] for i in range(1, 4)}


@pytest.mark.parametrize('seed', [7, 42])
def test_two_groups_coexist_without_changing_current_guard_motion(seed):
    actual = CommandEscortAdapter(1, {'threatCount': 2, 'seed': seed})
    control = CommandEscortAdapter(2, {'threatCount': 2, 'seed': seed})
    for a in (actual, control):
        a.set_mission_active(True)
        a.execute_preset(19, PROFILE)
        for _ in range(120): a.step()
    actual.execute_preset(13, PROFILE)
    first = actual.step()
    control.step()
    assert first.metrics['fixedAssignments'] == {k: v for k, v in bindings().items() if k.startswith('UAV')}
    actual.execute_preset(14, PROFILE)
    for _ in range(150):
        frame, reference = actual.step(), control.step()
        assert frame.metrics['fixedAssignments'] == bindings()
        assert frame.metrics['algorithmMode'] == reference.metrics['algorithmMode'] == 'GUARD'
        for agent, other in zip(frame.agents, reference.agents):
            assert (agent.role, agent.assignedTargetCode) == (other.role, other.assignedTargetCode)
            np.testing.assert_allclose([agent.x, agent.y, agent.z], [other.x, other.y, other.z], atol=1e-9)
    # Once capture is explicitly requested, both saved teams become active.
    for target in actual.sim.threats: target.state = 'detected'
    actual.execute_preset(20, PROFILE)
    for _ in range(100):
        frame = actual.step()
        assert frame.metrics['fixedAssignments'] == bindings()
        assert {a.code: a.assignedTargetCode for a in frame.agents} == bindings()


def test_chase_and_guard_keep_saved_groups_until_explicit_withdrawal():
    a = CommandEscortAdapter(1, {'threatCount': 2})
    a.execute_preset(13, PROFILE)
    a.execute_preset(14, PROFILE)
    a.execute_preset(1, PROFILE)
    assert a.step().metrics['fixedAssignments'] == bindings()
    a.execute_preset(19, PROFILE)
    assert a.step().metrics['fixedAssignments'] == bindings()
    a.execute_preset(0, PROFILE)
    assert a.step().metrics['fixedAssignments'] == {}


def test_binding_does_not_interrupt_existing_chase():
    a = CommandEscortAdapter(1, {'threatCount': 2})
    a.execute_preset(1, PROFILE)
    pursuit = dict(a.sim.pursuit_assignments)
    a.execute_preset(13, PROFILE)
    a.execute_preset(14, PROFILE)
    frame = a.step()
    assert frame.metrics['fixedAssignments'] == bindings()
    assert a.sim.pursuit_assignments == pursuit
    assert frame.metrics['algorithmMode'] == 'PURSUIT'
