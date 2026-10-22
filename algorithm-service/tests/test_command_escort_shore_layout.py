"""The command edition must respect the same real-base geometry as escort."""
import math

import numpy as np
import pytest

from app.adapters.command_escort import PROFILE
from runner import build_adapter
from test_command_escort_clearance import assert_clear_frame
from test_return_infrastructure import native_anchor_layout
from test_shore_departure import shore_config


def config(**overrides):
    return shore_config(algorithmCommandProfile=PROFILE, escortStartAtBerth=True,
                        targetShoreDistanceRatio=2 / 3, threatMinDistanceM=170,
                        threatCount=2, **overrides)


@pytest.mark.parametrize("seed", [1, 7, 42, 99])
@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("origin_up", [0, 7.5])
def test_all_enemies_start_farther_seaward_than_escort_and_base(seed, count, origin_up):
    settings = config(seed=seed)
    settings["threatCount"] = count
    settings["fleetOrigin"]["upM"] = origin_up
    settings["returnInfrastructure"] = native_anchor_layout(origin_up)
    adapter = build_adapter("ESCORT_GUARD", 1, settings)
    initial = adapter.step()
    layout = adapter.base.berth_layout
    axis = np.array(layout.direction)
    base = np.array([layout.east, layout.north])
    protected = next(t for t in initial.targets if t.code == "PROTECTED-001")
    own = np.array([protected.x, protected.y])
    assert np.dot(own - base, axis) == pytest.approx(70)
    assert np.linalg.norm(own - base) < 120
    enemies = [t for t in initial.targets if t.code.startswith("THREAT-")]
    assert len(enemies) == count and all(t.visible for t in enemies)
    for enemy in enemies:
        point = np.array([enemy.x, enemy.y])
        assert np.linalg.norm(point - own) >= 224 - 1e-7
        assert np.dot(point - own, axis) > 210
        for agent in initial.agents:
            assert math.hypot(enemy.x - agent.x, enemy.y - agent.y) > 200
        assert enemy.z == -origin_up
    assert_clear_frame(initial)
    # Idle/preview frames cannot advance the tactical encounter.
    later = adapter.step()
    assert [(t.x, t.y, t.z) for t in later.targets] == [(t.x, t.y, t.z) for t in initial.targets]


@pytest.mark.parametrize("seed", [7, 42])
def test_real_shore_encounter_deploys_detects_captures_and_returns_to_escort(seed):
    adapter = build_adapter("ESCORT_GUARD", 1, config(seed=seed))
    initial = adapter.step()
    parked = {a.code: a for a in initial.agents}
    adapter.set_mission_active(True)
    previous = initial
    capture_at = None
    deployed_at = None
    for tick in range(6000):
        current = adapter.step()
        for agent in current.agents:
            old = next(a for a in previous.agents if a.code == agent.code)
            assert math.dist((old.x, old.y, old.z), (agent.x, agent.y, agent.z)) <= (
                .5 if agent.type == "UAV" else .3) + 1e-6
        if current.metrics["deploymentState"] == "COMPLETE":
            deployed_at = tick if deployed_at is None else deployed_at
            assert_clear_frame(current)
            if capture_at is None and all(t.state in {"detected", "forming", "orbiting"}
                                          for t in adapter.base.sim.threats):
                for preset in (13, 14, 20):
                    adapter.execute_preset(preset, PROFILE)
                capture_at = tick
        else:
            assert [(t.x, t.y, t.z) for t in current.targets] == [(t.x, t.y, t.z) for t in initial.targets]
        previous = current
        if current.metrics["captureComplete"]:
            break
    assert deployed_at is not None and capture_at is not None
    assert current.metrics["captureComplete"], (seed, tick, current.metrics)
    assert all(math.dist((a.x, a.y), (parked[a.code].x, parked[a.code].y)) > 30 for a in current.agents)
    adapter.execute_preset(0, PROFILE)
    for _ in range(50):
        current = adapter.step()
        assert_clear_frame(current)
    assert current.metrics["algorithmMode"] == "ESCORT"
    assert current.metrics["fixedAssignments"] == {}
    assert current.metrics["capturedThreatCount"] == 0
    assert current.metrics["missionProgress"] == pytest.approx(.1)
    print(f"seed={seed}: deployed={deployed_at / 10:.1f}s capture_started={capture_at / 10:.1f}s "
          f"captured=2/2 at {tick / 10:.1f}s; withdrawal verified")


@pytest.mark.parametrize("seed", [7, 42])
def test_long_guard_orbits_do_not_cut_toward_escort_before_capture(seed):
    adapter = build_adapter("ESCORT_GUARD", 1, config(seed=seed))
    previous = adapter.step()
    adapter.set_mission_active(True)
    orbiting = set()
    headings = set()
    minimum = math.inf
    for tick in range(10000):
        if tick == 6000:
            for preset in (13, 14, 20):
                adapter.execute_preset(preset, PROFILE)
        frame = adapter.step()
        own = frame.targets[0]
        for target in frame.targets[1:]:
            old = next(t for t in previous.targets if t.code == target.code)
            step = math.hypot(target.x - old.x, target.y - old.y)
            assert step <= 1.2 + 1e-6
            turn = abs((target.heading - old.heading + 180) % 360 - 180)
            assert turn <= 4 + 1e-6
            if step < 1e-8:
                assert turn < 1e-8
            if target.state == "ORBITING":
                orbiting.add(target.code)
                headings.add(round(target.heading))
            if tick < 6000:
                radius = math.hypot(target.x - own.x, target.y - own.y)
                minimum = min(minimum, radius)
                # The intended ring is 8 native units = 112 m. Before the
                # angular-rate conversion, orbit pursuit cut inward to 17 m.
                assert radius >= 110, (seed, tick, target.code, radius)
        if frame.metrics["deploymentState"] == "COMPLETE":
            assert_clear_frame(frame)
        previous = frame
        if tick > 6000 and frame.metrics["captureComplete"]:
            break
    # Guard formation readiness is seed-dependent; a target may remain in
    # FORMING until capture is explicitly requested. At least one must have
    # exercised sustained orbiting, while the radius check covers both.
    assert orbiting and len(headings) > 20
    assert frame.metrics["captureComplete"]
    assert frame.metrics["algorithmMode"] == "CAPTURE"
    print(f"seed={seed}: 600s guard + capture; minimum enemy/escort distance={minimum:.3f}m")
