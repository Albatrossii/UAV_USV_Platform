"""Rendered hulls must remain disjoint throughout command-driven capture."""
import math
from itertools import combinations

import pytest

from app.adapters.command_escort import CommandEscortAdapter, PROFILE
from app.navigation.safety import SceneSafetyFilter


def assert_clear_frame(frame, previous=None):
    safety = SceneSafetyFilter()
    objects = list(frame.agents) + [t for t in frame.targets if t.visible]
    for a, b in combinations(objects, 2):
        required = safety.required_separation(a.type, b.type, a.z, b.z)
        distance = math.hypot(a.x - b.x, a.y - b.y)
        assert distance >= required - 1e-6, (frame.sequence, a.code, b.code, distance, required)
    if previous:
        old = {a.code: a for a in [*previous.agents, *previous.targets]}
        for a, b in combinations(objects, 2):
            pa, pb = old[a.code], old[b.code]
            swept = safety.swept_distance((pa.x, pa.y, pa.z), (a.x, a.y, a.z),
                                          (pb.x, pb.y, pb.z), (b.x, b.y, b.z))
            assert swept >= safety.required_separation(a.type, b.type) - 1e-6
        for agent in frame.agents:
            start = old[agent.code]
            assert math.dist((start.x, start.y, start.z), (agent.x, agent.y, agent.z)) <= (0.5 if agent.type == 'UAV' else 0.3) + 1e-6


@pytest.mark.parametrize('seed', [7, 42])
@pytest.mark.parametrize('count,bindings', [(1, ()), (2, ()), (2, (13, 14)), (2, (17, 18))])
def test_capture_preserves_hull_clearance_and_can_complete(count, bindings, seed):
    adapter = CommandEscortAdapter(1, {'threatCount': count, 'seed': seed})
    adapter.set_mission_active(True)
    previous = adapter.step()
    assert_clear_frame(previous)
    started = False
    completed_at = None
    for step in range(3500):
        if not started and all(t.state in {'detected', 'forming', 'orbiting'} for t in adapter.sim.threats):
            for preset in bindings:
                adapter.execute_preset(preset, PROFILE)
            adapter.execute_preset(20, PROFILE)
            started = True
        frame = adapter.step()
        assert_clear_frame(frame, previous)
        previous = frame
        if frame.metrics['captureComplete']:
            completed_at = step if completed_at is None else completed_at
            if step - completed_at >= 40:
                break
    assert started and completed_at is not None, 'Collision avoidance must not permanently block capture'


def test_capture_uses_executed_positions_not_overlapping_vendor_proposals(monkeypatch):
    adapter = CommandEscortAdapter(1, {'threatCount': 1})
    adapter.sim.threats[0].state = 'detected'
    adapter.execute_preset(13, PROFILE)
    adapter.execute_preset(20, PROFILE)
    adapter.set_mission_active(True)

    def unsafe_vendor_motion():
        for index in (0, 2, 4):
            adapter.sim.platforms[index].position = adapter.sim.threats[0].position.copy()

    monkeypatch.setattr(adapter.sim, '_step_gbsflacs_capture', unsafe_vendor_motion)
    frame = adapter.step()
    assert_clear_frame(frame)
    assert frame.metrics['capturedThreatCount'] == 0
    assert not adapter.sim.permanently_captured
    assert frame.metrics['hullSafetyAdjustedCount'] > 0


@pytest.mark.parametrize('origin_up', [0.0, 7.5])
def test_capture_with_real_shore_layout_and_local_sea_altitude(origin_up):
    from test_shore_departure import shore_config
    from test_return_infrastructure import native_anchor_layout
    from runner import build_adapter

    config = shore_config(algorithmCommandProfile=PROFILE, threatCount=2,
                          fleetOrigin={'eastM': -360, 'northM': -285, 'upM': origin_up},
                          returnInfrastructure=native_anchor_layout(origin_up))
    adapter = build_adapter('ESCORT_GUARD', 1, config)
    adapter.step()
    adapter.set_mission_active(True)
    started = False
    previous = None
    for _ in range(5000):
        frame = adapter.step()
        if frame.metrics['deploymentState'] != 'COMPLETE':
            continue
        assert_clear_frame(frame, previous)
        previous = frame
        if not started and all(t.state in {'detected', 'forming', 'orbiting'} for t in adapter.base.sim.threats):
            for preset in (13, 14, 20):
                adapter.execute_preset(preset, PROFILE)
            started = True
        if frame.metrics['captureComplete']:
            break
    assert started and frame.metrics['captureComplete']
