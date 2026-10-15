"""Time-weighted return routes through the original scene's verified water.

The public interface uses fleet-local ENU, like the authoritative runtime.
The immutable water map and optional test predicate use global ENU.  A map
cell certifies the *whole cell* for a six-metre-radius surface craft; native
dock corridors certify only their centreline, never a six-metre-wide band.
"""
from __future__ import annotations

import heapq
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping, Sequence


Point = tuple[float, float]
Obstacle = tuple[float, float, float]
Route = list[tuple[str, tuple[float, float, float], float]]
DEFAULT_MAP = Path(__file__).with_name("data") / "native_berth_water.json"
NEAR_BERTH_M = 32.0
DOCKING_M = 8.0
EPS = 1e-7


class ReturnPathUnavailable(ValueError):
    """No certified route exists; the caller must hold, not use a direct fallback."""


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("native return geometry must be finite")
    return float(value)


def _segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2)) if length2 else 0.0
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy)


class NativeWaterGrid:
    def __init__(self, data: Mapping[str, object]) -> None:
        if data.get("version") != "native-berth-water-v1" or data.get("coordinateFrame") != "GLOBAL_ENU":
            raise ValueError("native return requires the certified global water map")
        self.resolution = _finite(data.get("resolutionM"))
        self.east = _finite(data.get("eastMinM"))
        self.north = _finite(data.get("northMinM"))
        self.width, self.height = data.get("width"), data.get("height")
        if (self.resolution <= 0 or _finite(data.get("hullRadiusM")) < 6.0
                or type(self.width) is not int or type(self.height) is not int
                or not 1 <= self.width <= 4096 or not 1 <= self.height <= 4096):
            raise ValueError("native return water map has invalid dimensions or hull clearance")
        rows = data.get("safeRows")
        if (not isinstance(rows, list) or len(rows) != self.height
                or any(not isinstance(row, str) or len(row) != self.width or set(row) - {"0", "1"} for row in rows)):
            raise ValueError("native return water map has invalid safeRows")
        self.rows = tuple(rows)
        self.bounds = (self.east, self.east + self.width * self.resolution,
                       self.north, self.north + self.height * self.resolution)
        self.corridors = []
        for corridor in data.get("corridors", []):
            if not isinstance(corridor, dict) or _finite(corridor.get("clearanceM")) < 6.0:
                raise ValueError("native return corridor lacks hull clearance")
            start = (_finite(corridor.get("fromEastM")), _finite(corridor.get("fromNorthM")))
            end = (_finite(corridor.get("toEastM")), _finite(corridor.get("toNorthM")))
            if math.dist(start, end) < EPS:
                raise ValueError("native return corridor must have positive length")
            self.corridors.append((start, end))
        self.corridors = tuple(self.corridors)

    def cell_safe(self, point: Point) -> bool:
        x, y = point
        if not (self.bounds[0] <= x < self.bounds[1] and self.bounds[2] <= y < self.bounds[3]):
            return False
        col, row = math.floor((x - self.east) / self.resolution), math.floor((y - self.north) / self.resolution)
        return self.rows[row][col] == "1"

    def contains(self, point: Point) -> bool:
        if not (self.bounds[0] <= point[0] < self.bounds[1] and self.bounds[2] <= point[1] < self.bounds[3]):
            return False
        return self.cell_safe(point) or self.corridor_segment(point, point)

    def corridor_segment(self, start: Point, end: Point) -> bool:
        return any(_segment_distance(start, a, b) <= 1e-6 and _segment_distance(end, a, b) <= 1e-6
                   for a, b in self.corridors)


@lru_cache(maxsize=1)
def _default_grid() -> NativeWaterGrid:
    try:
        with DEFAULT_MAP.open(encoding="utf-8") as stream:
            return NativeWaterGrid(json.load(stream))
    except (OSError, ValueError, TypeError) as error:
        raise ReturnPathUnavailable("NATIVE_RETURN_MAP_UNAVAILABLE: certified water map unavailable") from error


class NativeReturnPlanner:
    def __init__(self, config: Mapping[str, object],
                 water_predicate: Callable[[float, float], bool] | None = None, *,
                 water_bounds: tuple[float, float, float, float] = (-700, -40, -600, 100),
                 resolution_m: float = 2.0, water_grid: NativeWaterGrid | None = None) -> None:
        origin = config.get("fleetOrigin", {})
        if not isinstance(origin, dict):
            raise ValueError("native return requires a finite fleet origin")
        self.origin = tuple(_finite(origin.get(key, 0.0)) for key in ("eastM", "northM", "upM"))
        self.sea_level = -self.origin[2]
        if water_predicate is not None and water_grid is not None:
            raise ValueError("provide one static water authority")
        self.grid = water_grid if water_grid is not None else None if water_predicate is not None else _default_grid()
        self.bounds = self.grid.bounds if self.grid else tuple(_finite(v) for v in water_bounds)
        self.resolution = self.grid.resolution if self.grid else _finite(resolution_m)
        if (self.resolution <= 0 or len(self.bounds) != 4 or self.bounds[0] >= self.bounds[1]
                or self.bounds[2] >= self.bounds[3]):
            raise ValueError("native return requires valid water bounds and resolution")
        self._predicate = water_predicate

    def _global(self, position: Sequence[float]) -> Point:
        if len(position) not in (2, 3):
            raise ValueError("native return pose must contain two or three coordinates")
        values = tuple(_finite(value) for value in position)
        if len(values) == 3 and abs(values[2] - self.sea_level) > 1e-6:
            raise ValueError("native return pose must lie at sea level")
        return values[0] + self.origin[0], values[1] + self.origin[1]

    def _obstacles(self, obstacles: Sequence[Obstacle]) -> tuple[Obstacle, ...]:
        result = []
        for obstacle in obstacles:
            if len(obstacle) != 3:
                raise ValueError("native return obstacle requires x, y and centre clearance")
            x, y, radius = (_finite(value) for value in obstacle)
            if radius < 0:
                raise ValueError("native return obstacle clearance must be nonnegative")
            result.append((x + self.origin[0], y + self.origin[1], radius))
        return tuple(result)

    def _water(self, point: Point) -> bool:
        if not (self.bounds[0] <= point[0] < self.bounds[1] and self.bounds[2] <= point[1] < self.bounds[3]):
            return False
        return self.grid.contains(point) if self.grid else bool(self._predicate(*point))

    @lru_cache(maxsize=32768)
    def _static_clear(self, start: Point, end: Point) -> bool:
        # Check every grid boundary and the interior of every crossed cell.
        # This is not ray sampling: a short unsafe corner crossing cannot hide
        # between samples. Certified centreline supplements remain zero-width.
        if not self._water(start) or not self._water(end):
            return False
        fractions = {0.0, 1.0}
        for axis, minimum in ((0, self.bounds[0]), (1, self.bounds[2])):
            delta = end[axis] - start[axis]
            if abs(delta) < EPS:
                continue
            low, high = sorted((start[axis], end[axis]))
            for index in range(math.floor((low - minimum) / self.resolution) + 1,
                               math.ceil((high - minimum) / self.resolution)):
                fractions.add((minimum + index * self.resolution - start[axis]) / delta)
        if not self.grid:
            # Injected test predicates have no whole-cell certificate.
            count = max(1, math.ceil(math.dist(start, end) / .25))
            fractions.update(index / count for index in range(1, count))
        cuts = sorted(fractions)
        point_at = lambda t: (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        if self.grid:
            if not all(self._water(point_at(t)) for t in cuts):
                return False
            return all(self.grid.cell_safe(point_at((a + b) / 2))
                       or self.grid.corridor_segment(point_at(a), point_at(b))
                       for a, b in zip(cuts, cuts[1:]))
        checks = cuts + [(a + b) / 2 for a, b in zip(cuts, cuts[1:])]
        return all(self._water(point_at(t)) for t in checks)

    def _clear(self, start: Point, end: Point, obstacles: tuple[Obstacle, ...]) -> bool:
        return (all(_segment_distance((x, y), start, end) + EPS >= radius for x, y, radius in obstacles)
                and self._static_clear(start, end))

    def route_clear(self, start: Sequence[float], end: Sequence[float], obstacles: Sequence[Obstacle] = ()) -> bool:
        """Whether the whole segment is certified, including dynamic hull gaps."""
        try:
            return self._clear(self._global(start), self._global(end), self._obstacles(obstacles))
        except (ValueError, TypeError, OverflowError):
            return False

    @staticmethod
    def _speed_parts(start: Point, end: Point, berth: Point, cruise: float) -> list[tuple[Point, float]]:
        """Split at the near-port boundary, so speed and time use the same rule."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        length2 = dx * dx + dy * dy
        if length2 <= EPS * EPS:
            return []
        px, py = start[0] - berth[0], start[1] - berth[1]
        b = 2 * (px * dx + py * dy)
        disc = b * b - 4 * length2 * (px * px + py * py - NEAR_BERTH_M ** 2)
        cuts = [0.0, 1.0]
        if disc > 0:
            cuts.extend(t for t in ((-b - math.sqrt(disc)) / (2 * length2),
                                   (-b + math.sqrt(disc)) / (2 * length2)) if EPS < t < 1 - EPS)
        cuts.sort()
        result = []
        for a, b in zip(cuts, cuts[1:]):
            midpoint = start[0] + dx * (a + b) / 2, start[1] + dy * (a + b) / 2
            speed = min(cruise, 1.5) if math.dist(midpoint, berth) <= NEAR_BERTH_M + EPS else cruise
            result.append(((start[0] + dx * b, start[1] + dy * b), speed))
        return result

    def _time(self, start: Point, end: Point, berth: Point, cruise: float) -> float:
        total, previous = 0.0, start
        for point, speed in self._speed_parts(start, end, berth, cruise):
            total += math.dist(previous, point) / speed
            previous = point
        return total

    def _node(self, point: Point) -> tuple[int, int]:
        return (math.floor((point[0] - self.bounds[0]) / self.resolution),
                math.floor((point[1] - self.bounds[2]) / self.resolution))

    def _point(self, node: tuple[int, int]) -> Point:
        return (self.bounds[0] + (node[0] + .5) * self.resolution,
                self.bounds[2] + (node[1] + .5) * self.resolution)

    def _near_nodes(self, point: Point):
        col, row = self._node(point)
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                node = col + dx, row + dy
                if self._water(self._point(node)):
                    yield node

    def plan(self, start_xyz: Sequence[float], slot: object, speed: float,
             obstacles: Sequence[Obstacle] = ()) -> Route:
        """Find the least-time route in the direct/centreline/grid graph.

        Obstacles are fleet-local circles whose radius is the required centre
        separation (our hull plus theirs), not only the obstacle's own radius.
        No planner state, elapsed time or prior mission target affects the route.
        """
        start = self._global(start_xyz)
        berth = self._global((slot.eastM, slot.northM, slot.upM))
        approach = self._global((slot.approachEastM, slot.approachNorthM, slot.approachUpM))
        cruise = min(4.0, _finite(speed))
        if cruise <= 0 or getattr(slot, "kind", None) != "BERTH":
            raise ValueError("native return requires a berth and positive speed")
        obstacles_global = self._obstacles(obstacles)
        if not self._clear(start, start, obstacles_global):
            raise ReturnPathUnavailable("NATIVE_RETURN_NO_SAFE_PATH: current position is not certified clear")
        if math.dist(start, berth) <= EPS:
            return []
        length = math.dist(berth, approach)
        if length < DOCKING_M:
            raise ValueError("native berth corridor must include the last eight metres")
        axis = ((approach[0] - berth[0]) / length, (approach[1] - berth[1]) / length)
        docking = berth[0] + axis[0] * DOCKING_M, berth[1] + axis[1] * DOCKING_M
        # A hold inside the final docking segment resumes forward, not by first
        # sailing out to its eight-metre beginning or the old offshore entrance.
        if (_segment_distance(start, berth, docking) <= 1e-6
                and self._clear(start, berth, obstacles_global)):
            return [("DOCKING", (slot.eastM, slot.northM, slot.upM), min(cruise, .6))]
        if not self._clear(docking, berth, obstacles_global):
            raise ReturnPathUnavailable("NATIVE_RETURN_NO_SAFE_PATH: docking corridor is blocked")
        final_time = DOCKING_M / min(cruise, .6)
        projection = (start[0] - berth[0]) * axis[0] + (start[1] - berth[1]) * axis[1]
        distances = {DOCKING_M, length, max(DOCKING_M, min(length, projection))}
        distances.update(float(value) for value in range(16, math.ceil(length), 8))
        candidates = []
        for distance in sorted(distances):
            point = berth[0] + axis[0] * distance, berth[1] + axis[1] * distance
            if self._clear(point, docking, obstacles_global):
                candidates.append((point, self._time(point, docking, berth, cruise) + final_time))
        best_cost, best_path = math.inf, None
        for point, tail in candidates:
            if self._clear(start, point, obstacles_global):
                cost = self._time(start, point, berth, cruise) + tail
                if cost < best_cost:
                    best_cost, best_path = cost, [start, point]

        # Connections to exact corridor points keep the narrow, verified berth
        # centreline reachable even where its containing grid cell is blocked.
        terminals = {}
        for point, tail in candidates:
            for node in self._near_nodes(point):
                position = self._point(node)
                if self._clear(position, point, obstacles_global):
                    cost = self._time(position, point, berth, cruise) + tail
                    if node not in terminals or cost < terminals[node][0]:
                        terminals[node] = cost, point
        costs, previous, queue = {}, {}, []
        heuristic = lambda point: math.dist(point, docking) / cruise + final_time
        for node in self._near_nodes(start):
            point = self._point(node)
            if self._clear(start, point, obstacles_global):
                cost = self._time(start, point, berth, cruise)
                costs[node], previous[node] = cost, None
                heapq.heappush(queue, (cost + heuristic(point), cost, node))
        best_node = None
        while queue:
            estimate, cost, node = heapq.heappop(queue)
            if estimate >= best_cost - EPS:
                break
            if cost > costs[node] + EPS:
                continue
            point = self._point(node)
            if node in terminals and cost + terminals[node][0] < best_cost:
                best_cost, best_node = cost + terminals[node][0], node
                best_path = None
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                neighbor = node[0] + dx, node[1] + dy
                endpoint = self._point(neighbor)
                if not self._water(endpoint) or not self._clear(point, endpoint, obstacles_global):
                    continue
                next_cost = cost + self._time(point, endpoint, berth, cruise)
                if next_cost + heuristic(endpoint) >= best_cost - EPS or next_cost >= costs.get(neighbor, math.inf) - EPS:
                    continue
                costs[neighbor], previous[neighbor] = next_cost, node
                heapq.heappush(queue, (next_cost + heuristic(endpoint), next_cost, neighbor))
        if best_node is not None:
            reverse, node = [], best_node
            while node is not None:
                reverse.append(self._point(node))
                node = previous[node]
            best_path = [start, *reversed(reverse), terminals[best_node][1]]
        if best_path is None:
            raise ReturnPathUnavailable("NATIVE_RETURN_NO_SAFE_PATH: no safe harbour route")
        # Visibility simplification is accepted only if it preserves safety and
        # does not increase travel time by cutting across the slow harbour zone.
        simplified, index = [best_path[0]], 0
        while index < len(best_path) - 1:
            accumulated, chosen = 0.0, index + 1
            for candidate in range(index + 1, len(best_path)):
                accumulated += self._time(best_path[candidate - 1], best_path[candidate], berth, cruise)
                if (self._time(best_path[index], best_path[candidate], berth, cruise) <= accumulated + EPS
                        and self._clear(best_path[index], best_path[candidate], obstacles_global)):
                    chosen = candidate
            simplified.append(best_path[chosen])
            index = chosen
        if math.dist(simplified[-1], docking) > EPS:
            simplified.append(docking)
        route = []
        for begin, end in zip(simplified, simplified[1:]):
            for point, limit in self._speed_parts(begin, end, berth, cruise):
                route.append(("APPROACHING", (point[0] - self.origin[0], point[1] - self.origin[1], self.sea_level), limit))
        route.append(("DOCKING", (slot.eastM, slot.northM, slot.upM), min(cruise, .6)))
        return route
