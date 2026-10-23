"""An opt-in berth-side escort starts a real seaward route, never a render warp."""
from __future__ import annotations

import math

import pytest

from app.adapters.adaptive_escort import AdaptiveEscortAdapter
from app.adapters.shore_target_layout import read_escort_berth_layout
from runner import build_adapter
from test_shore_departure import shore_config


def berth_config(**changes):
    return shore_config(escortStartAtBerth=True, targetShoreDistanceRatio=2 / 3, **changes)


def subject(**changes):
    return build_adapter("ESCORT_GUARD", 99310, berth_config(**changes))


def segment_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    fraction = max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(point[0] - start[0] - dx * fraction, point[1] - start[1] - dy * fraction)


def test_authoritative_preview_places_ship_beside_berths_outside_all_departure_corridors():
    adapter = subject()
    frame = adapter.step()
    base, protected = adapter.base, adapter.base.protected[0]
    layout = base._berth_layout
    assert (frame.targets[0].x, frame.targets[0].y) == (protected.x, protected.y)
    assert (protected.x, protected.y) == pytest.approx((201.8456940079747, 168.15231027533656))
    assert 45 <= math.hypot(protected.x - layout.east, protected.y - layout.north) <= 85
    assert min(math.hypot(protected.x - x, protected.y - y) for x, y in layout.berth_positions) < 70
    guards = [v for v in base.vehicles if v.role == "CLOSE_GUARD"]
    points = [(protected.x, protected.y)] + [
        base._convoy_guard_point(base._convoy_guard_slot_by_code[v.code], len(guards)) for v in guards
    ]
    for point in points:
        assert all(segment_distance(point, berth, approach) >= 14
                   for berth, approach in zip(layout.berth_positions, layout.approach_positions))
        assert base._project_to_safe_water(*point) == point
        assert point[0] <= layout.shore_bounds[0] - 20
    assert frame.metrics["escortAssemblyProgress"] == 0
    assert base._escort_route_progress(protected) == 0
    assert not base._protected_reached_safe_gate(protected)


def test_open_water_threat_heading_distance_and_layout_are_not_scaled_twice():
    base = subject().base
    config = berth_config()
    config.pop("targetShoreDistanceRatio")
    unscaled = build_adapter("ESCORT_GUARD", 99311, config).base
    assert [(v.x, v.y) for v in base.protected + base.threats] == [(v.x, v.y) for v in unscaled.protected + unscaled.threats]
    protected, threat = base.protected[0], base.threats[0]
    assert math.hypot(protected.x - threat.x, protected.y - threat.y) == pytest.approx(120)
    ux, uy = base._escort_route_axis()
    assert (threat.x - protected.x) * ux + (threat.y - protected.y) * uy > 110
    heading = math.radians(threat.heading)
    assert math.cos(heading) * ux + math.sin(heading) * uy == pytest.approx(-1)
    assert base._shore_encounter_radii[threat.code] == pytest.approx(120)
    assert base._project_to_safe_water(threat.x, threat.y, 38) == (threat.x, threat.y)


def test_route_progress_gate_and_approach_limit_follow_westbound_route_not_world_x():
    base = subject().base
    protected = base.protected[0]
    start = protected.x, protected.y
    ux, uy = base._escort_route_axis()
    assert ux < -.9 and uy < 0
    assert protected.destination_x < start[0]
    protected.x, protected.y = base._escort_route_station(protected, .5)
    assert base._escort_route_progress(protected) == pytest.approx(.5)
    protected.x, protected.y = start[0] - ux * 10, start[1] - uy * 10
    assert base._escort_route_progress(protected) == 0
    protected.x, protected.y = base._escort_route_station(protected, 1)
    assert base._protected_reached_safe_gate(protected)
    protected.x -= uy * 25
    protected.y += ux * 25
    assert not base._protected_reached_safe_gate(protected)
    protected.vx, protected.vy = ux * 2, uy * 2
    point = base._escort_route_station(protected, 1)
    clipped = base._limit_escort_approach(protected, *point)
    assert clipped == pytest.approx(base._escort_route_station(protected, .96))
    assert protected.vx * ux + protected.vy * uy == pytest.approx(0)


def test_legacy_geometry_is_unchanged_without_explicit_flag():
    adapter = build_adapter("ESCORT_GUARD", 99312, shore_config(targetShoreDistanceRatio=2 / 3)).base
    target = adapter.protected[0]
    assert adapter._berth_layout is None
    assert adapter._escort_route_axis() == (1, 0)
    assert (target.x, target.y, target.destination_x, target.destination_y) == pytest.approx((69.77777777777777, 41.66666666666667, 110, 0))


def test_global_and_local_infrastructure_produce_the_same_berth_start():
    local = subject().base
    config = berth_config()
    infrastructure = config["returnInfrastructure"]
    infrastructure["coordinateFrame"] = "GLOBAL_ENU"
    ox, oy = config["fleetOrigin"]["eastM"], config["fleetOrigin"]["northM"]
    for slot in infrastructure["slots"]:
        for key in ("eastM", "approachEastM"):
            slot[key] += ox
        for key in ("northM", "approachNorthM"):
            slot[key] += oy
    for key in ("eastMinM", "eastMaxM"):
        infrastructure["shore"][key] += ox
    for key in ("northMinM", "northMaxM"):
        infrastructure["shore"][key] += oy
    # Test the layout parser directly: infrastructure normalization is owned
    # by the wrapper and must not be invoked twice for this equivalence check.
    config["shoreDeploymentMode"] = "SAFE_RELEASE"
    layout = read_escort_berth_layout(config)
    assert layout.east == pytest.approx(local._berth_layout.east)
    assert layout.north == pytest.approx(local._berth_layout.north)
    assert layout.direction == pytest.approx(local._escort_route_axis())
    assert layout.shore_bounds == local._berth_layout.shore_bounds


@pytest.mark.parametrize("changes", [{"startFromShore": False}, {"shoreDeploymentMode": "FULL_ASSEMBLY"}])
def test_new_layout_requires_actual_safe_release(changes):
    config = berth_config()
    config["shoreDeploymentMode"] = "SAFE_RELEASE"
    config.update(changes)
    with pytest.raises(ValueError, match="requires startFromShore and SAFE_RELEASE"):
        AdaptiveEscortAdapter(99313, config)


def test_first_physical_convoy_departure_follows_the_configured_outward_axis():
    adapter = subject()
    frame = adapter.step()
    adapter.set_mission_active(True)
    initial = frame.targets[0].x, frame.targets[0].y
    ux, uy = adapter.base._escort_route_axis()
    for _ in range(900):
        previous, frame = frame, adapter.step()
        target = frame.targets[0]
        if math.hypot(target.x - initial[0], target.y - initial[1]) > .1:
            dx, dy = target.x - previous.targets[0].x, target.y - previous.targets[0].y
            assert dx * ux + dy * uy > 0
            assert abs(-dx * uy + dy * ux) < 1e-8
            assert math.hypot(dx, dy) <= .225 + 1e-8
            break
    else:
        pytest.fail("real mixed protection never released the convoy")


def test_high_speed_surface_motion_respects_four_mps_even_during_screen_separation():
    adapter = subject(uavSpeedMps=8, usvSpeedMps=4)
    frame = adapter.step()
    adapter.set_mission_active(True)
    phases = set()
    for _ in range(900):
        previous = {agent.code: agent for agent in frame.agents}
        frame = adapter.step()
        phases.add(frame.phase)
        for agent in frame.agents:
            if agent.type == "USV":
                prior = previous[agent.code]
                assert math.dist((agent.x, agent.y, agent.z), (prior.x, prior.y, prior.z)) <= .4 + 1e-8
    assert "GUARDING" in phases
