"""Validated, fleet-local fixed shore destinations shared with the renderer."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Mapping


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"returnInfrastructure.{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1_000_000:
        raise ValueError(f"returnInfrastructure.{field} must be finite and within local bounds")
    return number


@dataclass(frozen=True)
class Shore:
    eastMinM: float
    eastMaxM: float
    northMinM: float
    northMaxM: float
    surfaceUpM: float


@dataclass(frozen=True)
class ParkingSlot:
    deviceCode: str
    kind: str
    eastM: float
    northM: float
    upM: float
    headingDeg: float
    approachEastM: float
    approachNorthM: float
    approachUpM: float

    @property
    def position(self) -> tuple[float, float, float]:
        return self.eastM, self.northM, self.upM


@dataclass(frozen=True)
class ReturnInfrastructure:
    shore: Shore
    slots: tuple[ParkingSlot, ...]
    version: str = "fixed-shore-v1"
    sea_level: float = 0.0

    def metadata(self) -> dict[str, object]:
        # Return fresh dictionaries: consumers cannot mutate the authority's
        # frozen geometry through a prior RuntimeFrame or the input config.
        return {
            "version": self.version,
            "coordinateFrame": "FLEET_LOCAL_ENU",
            "shore": asdict(self.shore),
            "slots": [asdict(slot) for slot in self.slots],
        }


def validate_return_infrastructure(config: Mapping[str, object]) -> ReturnInfrastructure | None:
    if "returnInfrastructure" not in config:
        return None  # Backward compatibility: the first-frame home is retained.
    raw = config["returnInfrastructure"]
    if not isinstance(raw, dict) or raw.get("version") not in {"fixed-shore-v1", "scene-existing-v1"}:
        raise ValueError("returnInfrastructure requires a supported version")
    version = raw["version"]
    existing_scene = version == "scene-existing-v1"
    origin = config.get("fleetOrigin", {})
    if existing_scene and not isinstance(origin, dict):
        raise ValueError("returnInfrastructure native scene requires a finite fleetOrigin")
    sea_level = -_number(origin.get("upM", 0.0), "fleetOrigin.upM") if existing_scene else 0.0
    if raw.get("coordinateFrame") != "FLEET_LOCAL_ENU":
        raise ValueError("returnInfrastructure requires FLEET_LOCAL_ENU coordinates")
    raw_shore = raw.get("shore")
    if not isinstance(raw_shore, dict):
        raise ValueError("returnInfrastructure.shore is required")
    shore = Shore(**{
        field: _number(raw_shore.get(field), f"shore.{field}")
        for field in Shore.__dataclass_fields__
    })
    if shore.eastMinM >= shore.eastMaxM or shore.northMinM >= shore.northMaxM:
        raise ValueError("returnInfrastructure shore bounds must have positive area")
    if not existing_scene and not math.isclose(shore.surfaceUpM, 1.4, abs_tol=1e-6):
        raise ValueError("returnInfrastructure fixed shore surfaceUpM must be 1.4")
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list) or not 1 <= len(raw_slots) <= 256:
        raise ValueError("returnInfrastructure requires one slot per fleet device")
    slots: list[ParkingSlot] = []
    codes: set[str] = set()
    positions: set[tuple[float, float, float]] = set()
    for index, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            raise ValueError(f"returnInfrastructure.slots[{index}] must be an object")
        code = item.get("deviceCode")
        if not isinstance(code, str) or not re.fullmatch(r"(?:UAV|USV)-\d{3}", code):
            raise ValueError("returnInfrastructure slot deviceCode must be canonical UAV/USV-NNN")
        if code in codes or not 1 <= int(code[4:]) <= 128:
            raise ValueError("returnInfrastructure slot device codes must be valid and unique")
        kind = "HELIPAD" if code.startswith("UAV-") else "BERTH"
        if item.get("kind") != kind:
            raise ValueError("returnInfrastructure slot kind does not match its device type")
        slot = ParkingSlot(deviceCode=code, kind=kind, **{
            field: _number(item.get(field), f"slots[{index}].{field}")
            for field in ParkingSlot.__dataclass_fields__
            if field not in {"deviceCode", "kind"}
        })
        if not 0 <= slot.headingDeg < 360:
            raise ValueError("returnInfrastructure headingDeg must be in [0, 360)")
        if (not existing_scene or kind == "HELIPAD") and not shore.eastMinM <= slot.eastM <= shore.eastMaxM:
            raise ValueError("returnInfrastructure slot must be within the shore's east span")
        if kind == "HELIPAD":
            if not (shore.eastMinM < slot.eastM < shore.eastMaxM
                    and shore.northMinM < slot.northM < shore.northMaxM
                    and (slot.upM > sea_level if existing_scene else math.isclose(slot.upM, shore.surfaceUpM, abs_tol=1e-6))):
                raise ValueError("returnInfrastructure helipad must lie on the shore surface")
            if not (math.isclose(slot.approachEastM, slot.eastM, abs_tol=1e-6)
                    and math.isclose(slot.approachNorthM, slot.northM, abs_tol=1e-6)
                    and slot.approachUpM + 1e-6 >= slot.upM + 20.0):
                raise ValueError("returnInfrastructure helipad approach must be at least 20m directly above it")
        elif existing_scene:
            # This bounding box describes the original base, not its water
            # boundary. Native verified berths have independent oblique water
            # approaches, rather than the generated shore's north/east lanes.
            if not (math.isclose(slot.upM, sea_level, abs_tol=1e-6)
                    and math.isclose(slot.approachUpM, sea_level, abs_tol=1e-6)
                    and math.isclose(slot.headingDeg, 90.0, abs_tol=1e-6)
                    and math.hypot(slot.eastM - slot.approachEastM, slot.northM - slot.approachNorthM) >= 8.0):
                raise ValueError("returnInfrastructure native berth requires a sea-level approach at least 8m from the berth")
        elif not (slot.northM > shore.northMaxM
                  and math.isclose(slot.upM, 0.0, abs_tol=1e-6)
                  and math.isclose(slot.approachUpM, 0.0, abs_tol=1e-6)
                  and math.isclose(slot.headingDeg, 90.0, abs_tol=1e-6)
                  and math.isclose(slot.approachEastM, slot.eastM + 8.0, abs_tol=1e-6)
                  and slot.approachNorthM > slot.northM):
            raise ValueError("returnInfrastructure berth/approach must be in northern water with an 8m east channel")
        position = tuple(round(value, 6) for value in slot.position)
        if position in positions:
            raise ValueError("returnInfrastructure parking slots must not overlap")
        positions.add(position)
        codes.add(code)
        slots.append(slot)

    berths = [slot for slot in slots if slot.kind == "BERTH"]
    if existing_scene and (len(berths) > 3 or sum(slot.kind == "HELIPAD" for slot in slots) > 3):
        raise ValueError("returnInfrastructure original scene has only 3 verified helipads and 3 berths")
    if not existing_scene and berths and (any(not math.isclose(slot.approachNorthM, berths[0].approachNorthM, abs_tol=1e-6) for slot in berths)
                   or berths[0].approachNorthM <= max(slot.northM for slot in berths)):
        raise ValueError("returnInfrastructure berths require a shared entrance north of every berth")
    for kind, count_key in (("UAV", "uavCount"), ("USV", "usvCount")):
        if count_key not in config:
            continue
        count = config[count_key]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 128:
            raise ValueError(f"returnInfrastructure requires a valid {count_key}")
        expected = {f"{kind}-{index:03d}" for index in range(1, count + 1)}
        assigned = {code for code in codes if code.startswith(f"{kind}-")}
        valid_assignment = assigned.issubset(expected) if existing_scene else assigned == expected
        if not valid_assignment:
            detail = "reference only configured devices" if existing_scene else "cover every configured device"
            raise ValueError(f"returnInfrastructure {kind} slots must {detail}")
    return ReturnInfrastructure(shore, tuple(slots), version, sea_level)
