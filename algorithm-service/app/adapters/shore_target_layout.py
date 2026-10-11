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
