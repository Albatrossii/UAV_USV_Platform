from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Dict, Mapping, Sequence

from app.schemas import AgentFrame, RuntimeFrame


class AlgorithmAdapter(ABC):
    code: str
    version = "1.0.0"

    def __init__(self, run_id: int, config: Dict[str, object] | None = None) -> None:
        self.run_id = int(run_id)
        self.config = config or {}
        self.sequence = 0
        self.mission_active = True
        self._stable_headings: Dict[str, float] = {}

    def set_mission_active(self, active: bool) -> None:
        """Switch between ambient preview and the scored mission.

        Adapters that support preview can override this hook to preserve the
        current poses while resetting only mission counters at START.
        """
        self.mission_active = bool(active)

    def reconcile_execution(
        self,
        agents: Sequence[AgentFrame],
        previous_agents: Mapping[str, AgentFrame],
        unavailable_codes: Sequence[str] = (),
    ) -> None:
        """Receive authoritative local poses after deployment/operator control.

        Planning adapters override this to keep their next decision grounded in
        the emitted state. Unavailable devices must not supply capture or guard
        evidence. This hook never starts a new mission or resets target motion.
        """

    def rejoin_target(self, device_code: str) -> tuple[float, float, float] | None:
        """Return a live task waypoint for an operator-controlled rejoin."""
        return None

    def stabilize_heading(
        self,
        code: str,
        previous: Sequence[float] | None,
        current: Sequence[float],
        fallback: float,
        max_turn_degrees: float,
    ) -> float:
        """Return a turn-rate-limited heading derived from actual motion.

        Vendor algorithms may change their desired waypoint sharply, while the
        final safety layer changes the executed displacement again.  Driving a
        Unity model with the vendor's instantaneous heading therefore makes it
        shake even when its real path is smooth.
        """
        old_heading = self._stable_headings.get(code, float(fallback) % 360.0)
        target_heading = old_heading
        if previous is not None:
            dx = float(current[0]) - float(previous[0])
            dy = float(current[1]) - float(previous[1])
            if math.hypot(dx, dy) >= 0.025:
                target_heading = math.degrees(math.atan2(dy, dx)) % 360.0
        delta = (target_heading - old_heading + 180.0) % 360.0 - 180.0
        limited = max(-max_turn_degrees, min(max_turn_degrees, delta))
        heading = (old_heading + limited) % 360.0
        self._stable_headings[code] = heading
        return heading

    def initial_pose_map(self) -> Dict[str, Mapping[str, object]]:
        """Return scenario poses supplied by Unity, keyed by device code."""
        raw_poses = self.config.get("initialPoses", [])
        if not isinstance(raw_poses, list):
            return {}
        result: Dict[str, Mapping[str, object]] = {}
        for raw_pose in raw_poses:
            if not isinstance(raw_pose, dict):
                continue
            code = str(raw_pose.get("deviceCode", "")).strip().upper()
            if code:
                result[code] = raw_pose
        return result

    def initial_pose_to_local(
        self,
        pose: Mapping[str, object],
    ) -> tuple[float, float, float]:
        """Convert the Unity scenario pose to the algorithm local frame."""
        east = float(pose.get("eastM", 0.0))
        north = float(pose.get("northM", 0.0))
        up = float(pose.get("upM", 0.0))
        frame = str(
            self.config.get("initialPosesCoordinateFrame", "GLOBAL_ENU")
        ).upper()
        if frame == "GLOBAL_ENU":
            origin = self.config.get("fleetOrigin", {})
            if isinstance(origin, dict):
                east -= float(origin.get("eastM", 0.0))
                north -= float(origin.get("northM", 0.0))
                up -= float(origin.get("upM", 0.0))
        return east, north, up

    @abstractmethod
    def step(self) -> RuntimeFrame:
        raise NotImplementedError

    def place_threat(self, x: float, y: float) -> None:
        raise ValueError(f"{self.code} does not support interactive threat placement")

    def activate_capture(self, threat_code: str | None = None) -> str:
        raise ValueError(f"{self.code} does not support active capture")
