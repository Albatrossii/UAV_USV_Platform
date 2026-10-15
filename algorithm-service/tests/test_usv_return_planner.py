"""Independent safety/time contracts for original-scene surface return routes."""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from app.adapters.return_infrastructure import ParkingSlot
from app.navigation.usv_return import NativeReturnPlanner, NativeWaterGrid, ReturnPathUnavailable


def slot() -> ParkingSlot:
    return ParkingSlot("USV-002", "BERTH", 0, 0, 0, 90, -132, -30, 0)


def planner(predicate=lambda x, y: True, **changes):
    return NativeReturnPlanner({}, predicate, water_bounds=(-170, 50, -80, 80), **changes)


def distance(start, route):
    result = 0
    for _, point, _ in route:
        result += math.dist(start, point)
        start = point
    return result


def duration(start, route):
    result = 0
    for _, point, speed in route:
        result += math.dist(start, point) / speed
        start = point
    return result


def assert_route_safe(subject, start, route, obstacles=()):
    for _, point, _ in route:
        assert subject.route_clear(start, point, obstacles), (start, point)
        start = point


def test_nearshore_return_approaches_current_berth_without_visiting_old_offshore_entrance():
    subject, start, destination = planner(), (-34.25, 18.93, 0), slot()
    route = subject.plan(start, destination, 3)
    assert route[-1] == ("DOCKING", (0, 0, 0), .6)
    assert distance(start, route) < 50
    assert duration(start, route) < 40
    assert all(point != (-132, -30, 0) for _, point, _ in route)
    assert math.dist(route[0][1], destination.position) < math.dist(start, destination.position)
    assert_route_safe(subject, start, route)


def test_near_port_and_last_eight_metres_have_separate_speed_limits():
    subject, destination, start = planner(), slot(), (-145, -33, 0)
    route = subject.plan(start, destination, 12)
    previous = start
    saw_cruise = saw_approach = False
    for stage, point, speed in route:
        midpoint = tuple((a + b) / 2 for a, b in zip(previous, point))
        assert speed <= 4
        if stage == "DOCKING":
            assert math.dist(previous, destination.position) == pytest.approx(8)
            assert speed == .6
        elif math.hypot(midpoint[0], midpoint[1]) <= 32 + 1e-7:
            saw_approach = True
            assert speed == 1.5
        else:
            saw_cruise = True
            assert speed == 4
        previous = point
    assert saw_cruise and saw_approach


def test_already_at_berth_is_stationary_and_does_not_sail_out_again():
    assert planner().plan((0, 0, 0), slot(), 3) == []


def test_already_at_berth_still_requires_certified_static_water():
    with pytest.raises(ReturnPathUnavailable, match="current position is not certified clear"):
        planner(lambda x, y: False).plan((0, 0, 0), slot(), 3)


def test_already_at_berth_still_rejects_a_dynamic_hull_overlap():
    with pytest.raises(ReturnPathUnavailable, match="current position is not certified clear"):
        planner().plan((0, 0, 0), slot(), 3, [(0, 0, 12)])


def test_hold_then_return_inside_final_docking_segment_continues_forward():
    destination = slot()
    length = math.hypot(destination.approachEastM, destination.approachNorthM)
    start = destination.approachEastM * 3 / length, destination.approachNorthM * 3 / length, 0
    subject = planner()
    route = subject.plan(start, destination, 3)
    assert route == [("DOCKING", (0, 0, 0), .6)]
    assert duration(start, route) == pytest.approx(5)
    assert subject.plan(start, destination, 3) == route  # No time/history-based drift.


def test_hold_during_approach_replans_from_the_new_real_pose_without_resetting_to_entry():
    subject, destination = planner(), slot()
    route = subject.plan((-34.25, 18.93, 0), destination, 3)
    current = route[-2][1]
    resumed = subject.plan(current, destination, 3)
    assert distance(current, resumed) <= 8 + 1e-6
    assert resumed[-1] == ("DOCKING", (0, 0, 0), .6)


def test_circle_obstacle_uses_a_safe_detour_instead_of_an_unsafe_direct_fallback():
    subject, destination, start = planner(), slot(), (-80, 18, 0)
    obstacles = [(-43, 9, 14)]
    docking = (-132 * 8 / math.hypot(132, 30), -30 * 8 / math.hypot(132, 30), 0)
    assert not subject.route_clear(start, docking, obstacles)
    route = subject.plan(start, destination, 3, obstacles)
    assert len(route) >= 3
    assert_route_safe(subject, start, route, obstacles)


def test_static_island_requires_a_real_grid_detour():
    subject = planner(lambda x, y: not (-54 <= x <= -35 and -15 <= y <= 22))
    start = (-80, 10, 0)
    route = subject.plan(start, slot(), 3)
    assert any(point[1] > 22 or point[1] < -15 for _, point, _ in route)
    assert_route_safe(subject, start, route)


def test_blocked_docking_segment_and_separating_land_fail_closed():
    with pytest.raises(ReturnPathUnavailable, match="docking corridor is blocked"):
        planner().plan((-50, 0, 0), slot(), 3, [(-4, -1, 8)])
    with pytest.raises(ReturnPathUnavailable, match="no safe harbour route"):
        planner(lambda x, y: not -26 <= x <= -20).plan((-50, 0, 0), slot(), 3)


def test_unknown_out_of_bounds_positions_and_nonfinite_obstacles_do_not_create_routes():
    subject = planner()
    assert not subject.route_clear((-180, 0, 0), (-30, 0, 0))
    with pytest.raises(ReturnPathUnavailable):
        subject.plan((-180, 0, 0), slot(), 3)
    assert not subject.route_clear((-1e20, 0, 0), (-30, 0, 0))
    assert not subject.route_clear((-10, 0, 0), (-5, 0, 0), [(math.nan, 0, 2)])
    with pytest.raises(ValueError):
        subject.plan((-50, 0, 0), slot(), 3, [(0, 0, -2)])


def test_fleet_origin_translation_and_nonzero_sea_level_preserve_global_path():
    original = planner()
    destination = slot()
    start = (-34.25, 18.93, 0)
    offset = (350, 285, 7.5)
    shifted = NativeReturnPlanner({"fleetOrigin": dict(zip(("eastM", "northM", "upM"), offset))},
                                  lambda x, y: True, water_bounds=(-170, 50, -80, 80))
    shifted_slot = replace(destination, eastM=-350, northM=-285, upM=-7.5,
                           approachEastM=-482, approachNorthM=-315, approachUpM=-7.5)
    shifted_start = tuple(a - b for a, b in zip(start, offset))
    route = original.plan(start, destination, 3)
    translated = shifted.plan(shifted_start, shifted_slot, 3)
    assert len(route) == len(translated)
    for (stage, point, speed), (other_stage, other_point, other_speed) in zip(route, translated):
        assert stage == other_stage and speed == other_speed
        assert point == pytest.approx(tuple(a + b for a, b in zip(other_point, offset)))
    assert not shifted.route_clear(shifted_start, (-350, -285, 0))
    obstacle = (-22, 9, 3)
    shifted_obstacle = (obstacle[0] - offset[0], obstacle[1] - offset[1], obstacle[2])
    assert original.route_clear(start, route[-2][1], [obstacle]) == shifted.route_clear(
        shifted_start, translated[-2][1], [shifted_obstacle])


def grid(rows, corridors=()):
    return NativeWaterGrid({"version": "native-berth-water-v1", "coordinateFrame": "GLOBAL_ENU",
                           "resolutionM": 2, "eastMinM": 0, "northMinM": 0,
                           "width": len(rows[0]), "height": len(rows), "hullRadiusM": 6,
                           "safeRows": rows, "corridors": list(corridors)})


def test_grid_checks_short_blocked_corner_crossings_not_just_regular_samples():
    subject = NativeReturnPlanner({}, water_grid=grid(["111", "101", "111"]))
    assert not subject.route_clear((1, 3.01, 0), (3.01, .99, 0))
    assert subject.route_clear((.1, .1, 0), (5.9, .1, 0))
    assert not subject.route_clear((.1, .1, 0), (6, .1, 0))


def test_verified_corridor_supplement_is_only_a_centreline_not_a_six_metre_band():
    corridor = {"fromEastM": .5, "fromNorthM": 1, "toEastM": 5.5, "toNorthM": 1, "clearanceM": 6}
    subject = NativeReturnPlanner({}, water_grid=grid(["000", "000", "000"], [corridor]))
    assert subject.route_clear((.5, 1, 0), (5.5, 1, 0))
    assert not subject.route_clear((.5, 1.001, 0), (5.5, 1.001, 0))
    assert not subject.route_clear((1, .5, 0), (1, 1.5, 0))


def test_crossing_a_corridor_at_one_midpoint_does_not_certify_a_blocked_cell():
    corridor = {"fromEastM": .5, "fromNorthM": 3, "toEastM": 5.5, "toNorthM": 3, "clearanceM": 6}
    subject = NativeReturnPlanner({}, water_grid=grid(["111", "101", "111"], [corridor]))
    assert not subject.route_clear((3, 1, 0), (3, 5, 0))


@pytest.mark.parametrize("speed", [0, -1, math.nan, math.inf])
def test_invalid_speed_is_rejected(speed):
    with pytest.raises(ValueError):
        planner().plan((-50, 0, 0), slot(), speed)


def test_low_speed_configuration_is_never_increased_by_port_limits():
    route = planner().plan((-50, 0, 0), slot(), .3)
    assert all(speed == .3 for _, _, speed in route)


def test_circle_clearance_is_checked_analytically_between_static_samples():
    subject = planner()
    assert subject.route_clear((-60, 0, 0), (-40, 0, 0), [(-49.99, 3, 3)])
    assert not subject.route_clear((-60, 0, 0), (-40, 0, 0), [(-49.99, 2.999, 3)])


def test_whole_cell_certificate_rejects_maps_with_a_smaller_hull_or_bad_rows():
    data = {"version": "native-berth-water-v1", "coordinateFrame": "GLOBAL_ENU",
            "resolutionM": 2, "eastMinM": 0, "northMinM": 0,
            "width": 2, "height": 1, "hullRadiusM": 5.9, "safeRows": ["11"]}
    with pytest.raises(ValueError, match="hull clearance"):
        NativeWaterGrid(data)
    with pytest.raises(ValueError, match="safeRows"):
        NativeWaterGrid({**data, "hullRadiusM": 6, "safeRows": ["1X"]})
