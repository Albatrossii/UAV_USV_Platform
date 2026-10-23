"""Opt-in, one-time target placement relative to the existing shore berths."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class ShoreTargetLayout:
    ratio: float
    east: float
    north: float

    def position(self, east: float, north: float) -> tuple[float, float]:
        return (
            self.east + (east - self.east) * self.ratio,
            self.north + (north - self.north) * self.ratio,
        )


@dataclass(frozen=True)
class EscortBerthLayout:
    east: float
    north: float
    direction: tuple[float, float]
    shore_bounds: tuple[float, float, float, float]
    berth_positions: tuple[tuple[float, float], ...]
    approach_positions: tuple[tuple[float, float], ...]


def read_escort_berth_layout(config: Mapping[str, object]) -> EscortBerthLayout | None:
    """Read the real berth/outbound axis without applying target scaling."""
    if config.get("escortStartAtBerth") is not True:
        return None
    if config.get("startFromShore") is not True or config.get("shoreDeploymentMode") != "SAFE_RELEASE":
        raise ValueError("escortStartAtBerth requires startFromShore and SAFE_RELEASE")
    infrastructure = config.get("returnInfrastructure")
    if not isinstance(infrastructure, dict) or not isinstance(infrastructure.get("slots"), list):
        raise ValueError("escortStartAtBerth requires actual berth infrastructure")
    frame = infrastructure.get("coordinateFrame")
    if frame not in {"FLEET_LOCAL_ENU", "GLOBAL_ENU"}:
        raise ValueError("escort berth layout requires an explicit ENU coordinate frame")
    origin = config.get("fleetOrigin") if frame == "GLOBAL_ENU" else {}
    if not isinstance(origin, dict):
        raise ValueError("global escort berths require fleetOrigin")
    east_origin = _finite(origin.get("eastM"), "fleetOrigin.eastM") if frame == "GLOBAL_ENU" else 0.0
    north_origin = _finite(origin.get("northM"), "fleetOrigin.northM") if frame == "GLOBAL_ENU" else 0.0
    berths = [slot for slot in infrastructure["slots"]
              if isinstance(slot, dict) and slot.get("kind") == "BERTH"]
    if not berths:
        raise ValueError("escortStartAtBerth requires at least one berth")
    positions = tuple((_finite(slot.get("eastM"), "berth.eastM") - east_origin,
                       _finite(slot.get("northM"), "berth.northM") - north_origin)
                      for slot in berths)
    east = sum(point[0] for point in positions) / len(positions)
    north = sum(point[1] for point in positions) / len(positions)
    approaches = tuple((_finite(slot.get("approachEastM"), "berth.approachEastM") - east_origin,
                        _finite(slot.get("approachNorthM"), "berth.approachNorthM") - north_origin)
                       for slot in berths)
    approach_east = sum(point[0] for point in approaches) / len(approaches)
    approach_north = sum(point[1] for point in approaches) / len(approaches)
    dx, dy = approach_east - east, approach_north - north
    distance = math.hypot(dx, dy)
    if distance < 1.0:
        raise ValueError("escort berths require a nonzero outward approach direction")
    shore = infrastructure.get("shore")
    if not isinstance(shore, dict):
        raise ValueError("escortStartAtBerth requires shore bounds")
    bounds = (_finite(shore.get("eastMinM"), "shore.eastMinM") - east_origin,
              _finite(shore.get("eastMaxM"), "shore.eastMaxM") - east_origin,
              _finite(shore.get("northMinM"), "shore.northMinM") - north_origin,
              _finite(shore.get("northMaxM"), "shore.northMaxM") - north_origin)
    if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
        raise ValueError("escort shore bounds must be ordered")
    return EscortBerthLayout(east, north, (dx / distance, dy / distance), bounds, positions, approaches)


def read_shore_target_layout(config: Mapping[str, object]) -> ShoreTargetLayout | None:
    """Return a local-frame transform; omitted config preserves legacy geometry.

    The reference is the mean of the *actual configured berths*, not the pad,
    world origin or an approximate shoreline. Only target initialization uses
    this transform: it must never scale subsequent motion or fleet home slots.
    """
    if "targetShoreDistanceRatio" not in config:
        return None
    ratio = _finite(config["targetShoreDistanceRatio"], "targetShoreDistanceRatio")
    if not 0 < ratio <= 1:
        raise ValueError("targetShoreDistanceRatio must be in (0, 1]")
    if config.get("startFromShore") is not True:
        raise ValueError("targetShoreDistanceRatio requires startFromShore")
    infrastructure = config.get("returnInfrastructure")
    if not isinstance(infrastructure, dict) or not isinstance(infrastructure.get("slots"), list):
        raise ValueError("targetShoreDistanceRatio requires shore berth slots")
    frame = infrastructure.get("coordinateFrame")
    if frame not in {"FLEET_LOCAL_ENU", "GLOBAL_ENU"}:
        raise ValueError("shore target layout requires an explicit ENU coordinate frame")
    berths = [slot for slot in infrastructure["slots"] if isinstance(slot, dict) and slot.get("kind") == "BERTH"]
    if not berths:
        raise ValueError("targetShoreDistanceRatio requires at least one berth")
    east = sum(_finite(slot.get("eastM"), "berth.eastM") for slot in berths) / len(berths)
    north = sum(_finite(slot.get("northM"), "berth.northM") for slot in berths) / len(berths)
    if frame == "GLOBAL_ENU":
        origin = config.get("fleetOrigin")
        if not isinstance(origin, dict):
            raise ValueError("global shore berths require fleetOrigin")
        east -= _finite(origin.get("eastM"), "fleetOrigin.eastM")
        north -= _finite(origin.get("northM"), "fleetOrigin.northM")
    return ShoreTargetLayout(ratio, east, north)
