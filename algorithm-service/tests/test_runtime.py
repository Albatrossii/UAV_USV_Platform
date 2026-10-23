import contextlib
import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters import CaptureAdapter, EscortAdapter
from app.capture import build_formation_slots
from app.navigation import TASK_CENTER_SCENE_MAP, SceneSafetyFilter


class AlgorithmRuntimeTests(unittest.TestCase):
    @staticmethod
    def heading_delta(first, second):
        return abs((second - first + 180.0) % 360.0 - 180.0)

    @staticmethod
    def capture_random_staging_config(uav_count, usv_count):
        origin = {"eastM": -150.0, "northM": -275.0, "upM": 0.0}
        poses = []
        occupied = []
        state = 20260814

        def random_value():
            nonlocal state
            state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
            return state / 0x100000000

        for kind, count in (("UAV", uav_count), ("USV", usv_count)):
            for index in range(count):
                accepted = False
                for _ in range(500):
                    east = origin["eastM"] - 85.0 + random_value() * 55.0
                    north = origin["northM"] - 70.0 + random_value() * 140.0
                    spacing = 13.0 if kind == "USV" else 10.0
                    if all(
                        math.hypot(east - point[0], north - point[1]) >= spacing
                        for point in occupied
                    ):
                        accepted = True
                        break
                if not accepted:
                    east = origin["eastM"] - 82.0 + (index % 4) * 16.0
                    north = origin["northM"] - 66.0 + (index // 4) * 36.0
                occupied.append((east, north))
                up = 20.0 + (index % 4) * 2.0 if kind == "UAV" else 0.0
                poses.append({
                    "deviceCode": f"{kind}-{index + 1:03d}",
                    "deviceType": kind,
                    "eastM": east,
                    "northM": north,
                    "upM": up,
                    "headingDeg": random_value() * 360.0,
                    "valid": True,
                })
        poses.append({
            "deviceCode": "TARGET-001",
            "deviceType": "TARGET",
            "eastM": origin["eastM"],
            "northM": origin["northM"],
            "upM": 0.0,
            "headingDeg": 0.0,
            "valid": True,
        })
        return {
            "seed": 20260814,
            "uavCount": uav_count,
            "usvCount": usv_count,
            "targetBehavior": "STATIC",
            "initialPoses": poses,
            "initialPosesCoordinateFrame": "GLOBAL_ENU",
            "fleetOrigin": origin,
        }

    def test_capture_is_exactly_three_plus_three(self):
        frame = CaptureAdapter(1, {"seed": 42, "targetBehavior": "STATIC"}).step()
        self.assertEqual(3, sum(agent.type == "UAV" for agent in frame.agents))
        self.assertEqual(3, sum(agent.type == "USV" for agent in frame.agents))
        self.assertEqual({"CAPTURE_TARGET"}, {target.type for target in frame.targets})
        target = frame.targets[0]
        next_frame = CaptureAdapter(3, {"seed": 42, "targetBehavior": "STATIC"}).step()
        next_target = next_frame.targets[0]
        self.assertEqual((target.x, target.y), (next_target.x, next_target.y))
        self.assertEqual(6, frame.metrics["requiredCaptureAgents"])

    def test_escort_has_moving_protected_and_threat_targets(self):
        adapter = EscortAdapter(2, {"threatFrame": 2})
        first = adapter.step()
        second = adapter.step()
        third = adapter.step()
        self.assertEqual(6, len(third.agents))
        self.assertNotEqual((first.targets[0].x, first.targets[0].y), (third.targets[0].x, third.targets[0].y))
        self.assertEqual({"ESCORT_TARGET", "THREAT_TARGET"}, {target.type for target in third.targets})
        self.assertIn(third.metrics["threatState"], {"APPROACHING", "DETECTED", "FORMING", "ORBITING"})

    def test_escort_manual_threat_uses_new_727_state_machine(self):
        adapter = EscortAdapter(23, {"seed": 42, "threatFrame": 10000})
        waiting = adapter.step()
        self.assertEqual("ESCORTING", waiting.phase)
        self.assertFalse(waiting.metrics["threatVisible"])
        self.assertEqual({"ESCORT_TARGET"}, {target.type for target in waiting.targets})

        adapter.place_threat(21.0, 8.0)
        response = adapter.step()
        self.assertIn(response.phase, {"FORMING", "ORBITING"})
        self.assertTrue(response.metrics["threatActive"])
        self.assertEqual({"ESCORT_TARGET", "THREAT_TARGET"}, {target.type for target in response.targets})
        self.assertEqual(
            {"UAV-001", "UAV-002", "UAV-003", "USV-001", "USV-002", "USV-003"},
            {agent.code for agent in response.agents},
        )

    def test_usv_never_enters_dock(self):
        safety = SceneSafetyFilter()
        safe = safety.constrain((-19.0, 0.0, 0.0), (-25.0, 0.0, 0.0), "USV")
        self.assertTrue(safe.adjusted)
        self.assertFalse(-35.8 <= safe.x <= -19.2 and -9.3 <= safe.y <= 9.3)

    def test_runner_emits_machine_readable_ndjson(self):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "runner.py"), "--algorithm", "ESCORT_GUARD", "--run-id", "9", "--autostart", "--fps", "20"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        try:
            ready = json.loads(process.stdout.readline())
            frame = json.loads(process.stdout.readline())
            self.assertEqual("runtimeReady", ready["event"])
            self.assertEqual("frame", frame["event"])
            self.assertEqual("ESCORT_GUARD", frame["payload"]["algorithmCode"])
        finally:
            process.stdin.write('{"action":"CANCEL"}\n')
            process.stdin.flush()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_both_scenarios_preserve_visual_footprints(self):
        for adapter_type in (CaptureAdapter, EscortAdapter):
            with contextlib.redirect_stdout(sys.stderr):
                adapter = adapter_type(12, {"seed": 42})
            safety = adapter.safety
            for _ in range(300):
                with contextlib.redirect_stdout(sys.stderr):
                    frame = adapter.step()
                self.assertEqual([], frame.obstacles)
                for item in [*frame.agents, *frame.targets]:
                    self.assertTrue(math.isfinite(item.x))
                    self.assertTrue(math.isfinite(item.y))
                    self.assertGreaterEqual(item.x, safety.bounds[0] - 1e-3)
                    self.assertLessEqual(item.x, safety.bounds[1] + 1e-3)
                    self.assertGreaterEqual(item.y, safety.bounds[2] - 1e-3)
                    self.assertLessEqual(item.y, safety.bounds[3] + 1e-3)
                if frame.sequence > 1:
                    for index, first in enumerate(frame.agents):
                        for second in frame.agents[index + 1:]:
                            required = safety.required_separation(first.type, second.type, first.z, second.z)
                            if required <= 0:
                                continue
                            distance = math.hypot(first.x - second.x, first.y - second.y)
                            self.assertGreaterEqual(distance + 1e-3, required)
                    for craft in (item for item in frame.agents if item.type == "USV"):
                        for target in frame.targets:
                            required = safety.required_separation(craft.type, target.type, craft.z, target.z)
                            distance = math.hypot(craft.x - target.x, craft.y - target.y)
                            self.assertGreaterEqual(distance + 1e-3, required)

    def test_capture_completes_after_smooth_long_approach(self):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(21, {"seed": 42, "targetBehavior": "STATIC"})
        frames = []
        for _ in range(1800):
            with contextlib.redirect_stdout(sys.stderr):
                frame = adapter.step()
            frames.append(frame)
            if frame.terminalStatus:
                break
        self.assertEqual("COMPLETED", frames[-1].terminalStatus)
        self.assertEqual(6, frames[-1].metrics["captureAgents"])
        self.assertIn("ESCAPE_PURSUIT", {item.phase for item in frames})
        self.assertGreaterEqual(
            frames[-1].metrics["targetTravelDistanceM"],
            frames[-1].metrics["requiredPursuitDistanceM"],
        )
        travel = {agent.code: 0.0 for agent in frames[0].agents}
        for previous, current in zip(frames, frames[1:]):
            for first in previous.agents:
                second = next(item for item in current.agents if item.code == first.code)
                travel[first.code] += math.hypot(second.x - first.x, second.y - first.y)
                limit = 6.01 if first.type == "UAV" else 3.51
                self.assertLessEqual(self.heading_delta(first.heading, second.heading), limit)
        self.assertGreater(min(travel.values()), 10.0)

    def test_capture_four_plus_four_keeps_every_agent_in_formation(self):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(
                25,
                {
                    "seed": 20260814,
                    "uavCount": 4,
                    "usvCount": 4,
                    "targetBehavior": "STATIC",
                },
            )
        final = None
        for _ in range(1800):
            with contextlib.redirect_stdout(sys.stderr):
                final = adapter.step()
            if final.terminalStatus:
                break

        self.assertIsNotNone(final)
        self.assertEqual("COMPLETED", final.terminalStatus)
        self.assertTrue(final.metrics["formationReady"])
        self.assertEqual(8, final.metrics["captureAgents"])
        self.assertEqual(
            {1000, 1001, 1002, 1003, 2000, 2001, 2002, 2003},
            adapter.env.guarding_agents[0],
        )
        self.assertEqual(4, sum(agent.type == "UAV" for agent in final.agents))
        self.assertEqual(4, sum(agent.type == "USV" for agent in final.agents))
        self.assertLessEqual(final.metrics["usvAngularErrorDeg"], 18.0)
        self.assertLessEqual(final.metrics["uavAngularErrorDeg"], 18.0)

    def test_capture_real_frontend_layout_supports_every_valid_fleet_size(self):
        combinations = (
            (1, 5), (5, 1), (2, 2), (2, 7), (7, 2),
            (3, 3), (4, 4), (6, 6), (8, 8), (12, 12), (15, 15),
        )
        for uav_count, usv_count in combinations:
            with self.subTest(uav_count=uav_count, usv_count=usv_count):
                with contextlib.redirect_stdout(sys.stderr):
                    adapter = CaptureAdapter(
                        1000 + uav_count * 20 + usv_count,
                        self.capture_random_staging_config(uav_count, usv_count),
                    )
                    final = adapter.step()
                target = next(
                    item for item in final.targets
                    if item.type == "CAPTURE_TARGET"
                )
                self.assertTrue(all(
                    agent.x < target.x - 10.0
                    for agent in final.agents
                ))
                for _ in range(1599):
                    with contextlib.redirect_stdout(sys.stderr):
                        final = adapter.step()
                    if final.terminalStatus:
                        break
                expected = uav_count + usv_count
                self.assertEqual("COMPLETED", final.terminalStatus, final.metrics)
                self.assertEqual(expected, final.metrics["captureAgents"])
                self.assertEqual(expected, len(adapter.env.guarding_agents[0]))
                self.assertTrue(final.metrics["targetInsideFormation"])
                self.assertLessEqual(final.metrics["combinedMaxGapDeg"], 151.2)

    def test_two_agents_intercept_without_false_encirclement(self):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(1099, self.capture_random_staging_config(1, 1))
            frames = [adapter.step() for _ in range(300)]
        self.assertTrue(all(frame.terminalStatus is None for frame in frames))
        self.assertTrue(all(not frame.metrics["captured"] for frame in frames))
        self.assertEqual("INTERCEPT_ONLY", frames[-1].metrics["capability"])
        self.assertEqual(3, frames[-1].metrics["requiredCaptureAgents"])

    def test_dynamic_slot_builder_has_no_algorithm_count_ceiling(self):
        usv_slots = build_formation_slots(
            128,
            kind="USV",
            phase=0.0,
            minimum_radius=22.0,
            minimum_spacing=13.8,
        )
        self.assertEqual(128, len(usv_slots))
        self.assertGreater(max(slot.ring for slot in usv_slots), 0)
        self.assertEqual(len(usv_slots), len({(slot.ring, round(slot.angle, 8)) for slot in usv_slots}))

    def test_capture_adapter_supports_phase_one_realtime_fleet_limit(self):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(1100, self.capture_random_staging_config(15, 15))
            frame = adapter.step()
        self.assertEqual(15, sum(agent.type == "UAV" for agent in frame.agents))
        self.assertEqual(15, sum(agent.type == "USV" for agent in frame.agents))
        self.assertGreaterEqual(frame.metrics["usvFormationRings"], 1)
        self.assertGreaterEqual(frame.metrics["uavFormationRings"], 1)
        self.assertEqual(["TARGET-001"], [target.code for target in frame.targets])
        self.assertEqual(["CAPTURE_TARGET"], [target.type for target in frame.targets])

    def test_capture_four_plus_four_usvs_keep_hull_clearance(self):
        safety = SceneSafetyFilter(TASK_CENTER_SCENE_MAP)
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(
                26,
                {
                    "seed": 20260814,
                    "uavCount": 4,
                    "usvCount": 4,
                    "targetBehavior": "STATIC",
                },
            )
        previous = None
        required = safety.required_separation("USV", "USV")
        for _ in range(700):
            with contextlib.redirect_stdout(sys.stderr):
                current = adapter.step()
            if previous is not None and previous.sequence > 1:
                old = {agent.code: agent for agent in previous.agents}
                usvs = [agent for agent in current.agents if agent.type == "USV"]
                for index, left in enumerate(usvs):
                    for right in usvs[index + 1:]:
                        swept = safety.swept_distance(
                            (old[left.code].x, old[left.code].y),
                            (left.x, left.y),
                            (old[right.code].x, old[right.code].y),
                            (right.x, right.y),
                        )
                        self.assertGreaterEqual(
                            swept + 1e-3,
                            required,
                            f"{left.code}/{right.code} hull clearance lost",
                        )
            previous = current
            if current.terminalStatus:
                break

    def test_capture_scale_matrix_keeps_rendered_hull_clearance_while_settling(self):
        safety = SceneSafetyFilter(TASK_CENTER_SCENE_MAP)
        for uav_count, usv_count in ((3, 3), (5, 5), (6, 4), (10, 10), (20, 12)):
            config = self.capture_random_staging_config(uav_count, usv_count)
            with contextlib.redirect_stdout(sys.stderr):
                adapter = CaptureAdapter(2705 + uav_count * 100 + usv_count, config)
            previous = None
            reached_settling = False
            minimum_margin = math.inf
            worst_pair = ""
            for _ in range(900):
                with contextlib.redirect_stdout(sys.stderr):
                    current = adapter.step()
                if current.phase in {"INTERCEPTING", "ENCIRCLEMENT", "CAPTURED"}:
                    reached_settling = True
                if previous is not None and previous.sequence > 1:
                    old = {agent.code: agent for agent in previous.agents}
                    for index, left in enumerate(current.agents):
                        for right in current.agents[index + 1:]:
                            required = safety.required_separation(
                                left.type,
                                right.type,
                                left.z,
                                right.z,
                            )
                            endpoint = math.hypot(left.x - right.x, left.y - right.y)
                            swept = safety.swept_distance(
                                (old[left.code].x, old[left.code].y),
                                (left.x, left.y),
                                (old[right.code].x, old[right.code].y),
                                (right.x, right.y),
                            )
                            margin = min(endpoint, swept) - required
                            if margin < minimum_margin:
                                minimum_margin = margin
                                worst_pair = (
                                    f"{uav_count}+{usv_count} {current.phase} "
                                    f"{left.code}/{right.code} endpoint={endpoint:.3f} "
                                    f"swept={swept:.3f} required={required:.3f}"
                                )
                previous = current
                if current.terminalStatus:
                    break
            with self.subTest(uav_count=uav_count, usv_count=usv_count):
                self.assertTrue(reached_settling)
                self.assertGreaterEqual(minimum_margin, -0.05, worst_pair)

    def test_first_frame_preserves_unity_initial_poses(self):
        initial_poses = [
            {
                "deviceCode": "UAV-001",
                "deviceType": "UAV",
                "eastM": -67.0,
                "northM": -303.0,
                "upM": 26.0,
                "headingDeg": 90.0,
                "valid": True,
            },
            {
                "deviceCode": "UAV-002",
                "deviceType": "UAV",
                "eastM": -59.0,
                "northM": -296.0,
                "upM": 27.0,
                "headingDeg": 90.0,
                "valid": True,
            },
            {
                "deviceCode": "USV-001",
                "deviceType": "USV",
                "eastM": -65.0,
                "northM": -318.0,
                "upM": 0.0,
                "headingDeg": 90.0,
                "valid": True,
            },
            {
                "deviceCode": "USV-002",
                "deviceType": "USV",
                "eastM": -55.0,
                "northM": -326.0,
                "upM": 0.0,
                "headingDeg": 90.0,
                "valid": True,
            },
            {
                "deviceCode": "TARGET-001",
                "deviceType": "TARGET",
                "eastM": -20.0,
                "northM": -310.0,
                "upM": 0.0,
                "headingDeg": 0.0,
                "valid": True,
            },
        ]
        config = {
            "uavCount": 3,
            "usvCount": 3,
            "targetCount": 1,
            "initialPoses": initial_poses,
            "initialPosesCoordinateFrame": "GLOBAL_ENU",
            "fleetOrigin": {"eastM": -75.0, "northM": -310.0, "upM": 0.0},
            "seed": 20260814,
        }

        for adapter_type in (CaptureAdapter, EscortAdapter):
            adapter = adapter_type(34, config)
            frame = adapter.step()
            poses = {agent.code: agent for agent in frame.agents}
            targets = {target.code: target for target in frame.targets}
            self.assertEqual(1, frame.sequence)
            self.assertEqual(5, len(initial_poses))
            for initial in initial_poses:
                code = initial["deviceCode"]
                expected_x = initial["eastM"] - config["fleetOrigin"]["eastM"]
                expected_y = initial["northM"] - config["fleetOrigin"]["northM"]
                current = (
                    targets["TARGET-001"]
                    if adapter_type is CaptureAdapter and code.startswith("TARGET")
                    else targets["ESCORT_TARGET"]
                    if adapter_type is EscortAdapter and code.startswith("ESCORT_TARGET")
                    else targets["TARGET"]
                    if adapter_type is EscortAdapter and code.startswith("TARGET")
                    else poses[code]
                )
                if adapter_type is CaptureAdapter and code.startswith("TARGET"):
                    # A stale/legacy page can supply a capture target only a
                    # few metres from the fleet.  The v4 contract deliberately
                    # relocates that one unsafe pose before frame 1 so the
                    # visible 80 m escape/chase can occur; craft poses remain
                    # exact and are still checked below.
                    self.assertGreaterEqual(
                        min(
                            math.hypot(current.x - agent.x, current.y - agent.y)
                            for agent in poses.values()
                        ),
                        90.0,
                    )
                else:
                    self.assertAlmostEqual(expected_x, current.x, places=3)
                    self.assertAlmostEqual(expected_y, current.y, places=3)
                self.assertAlmostEqual(initial["upM"], current.z, places=3)
                self.assertAlmostEqual(initial["headingDeg"], current.heading, places=3)

            second = adapter.step()
            self.assertEqual(2, second.sequence)
            second_agents = {agent.code: agent for agent in second.agents}
            moved = any(
                math.hypot(
                    second_agents[code].x - current.x,
                    second_agents[code].y - current.y,
                ) > 1e-6
                for code, current in poses.items()
            )
            self.assertTrue(moved, f"{adapter_type.__name__} did not move after the initial frame")

    def test_escort_uses_single_step_long_route_and_stable_heading(self):
        adapter = EscortAdapter(22, {"seed": 42, "threatFrame": 10000, "escortSpeed": "LOW"})
        frames = [adapter.step() for _ in range(240)]
        protected_travel = sum(
            math.hypot(current.targets[0].x - previous.targets[0].x, current.targets[0].y - previous.targets[0].y)
            for previous, current in zip(frames, frames[1:])
        )
        self.assertGreater(protected_travel, 15.0)
        self.assertLess(protected_travel, 24.0)
        for previous, current in zip(frames, frames[1:]):
            for first in previous.agents:
                second = next(item for item in current.agents if item.code == first.code)
                limit = 6.01 if first.type == "UAV" else 3.51
                self.assertLessEqual(self.heading_delta(first.heading, second.heading), limit)

    def test_new_escort_source_preserves_3d_altitudes_at_scale(self):
        adapter = EscortAdapter(
            24,
            {
                "uavCount": 30,
                "usvCount": 30,
                "seed": 20260814,
                "threatFrame": 70,
            },
        )
        first = adapter.step()
        self.assertEqual(60, len(first.agents))
        self.assertEqual(1, len(first.targets))
        uavs = [agent for agent in first.agents if agent.type == "UAV"]
        usvs = [agent for agent in first.agents if agent.type == "USV"]
        self.assertEqual(30, len(uavs))
        self.assertEqual(30, len(usvs))
        self.assertTrue(all(0.0 <= agent.z <= 7.0 for agent in uavs))
        self.assertTrue(all(agent.z == 0.0 for agent in usvs))
        self.assertEqual("ESCORTING", first.phase)

    def test_continuous_paths_respect_rendered_hulls_and_speed_limits(self):
        safety = SceneSafetyFilter(TASK_CENTER_SCENE_MAP)
        scenarios = (
            (CaptureAdapter(31, {"seed": 42, "targetBehavior": "STATIC"}), 260),
            (EscortAdapter(32, {"seed": 42, "threatFrame": 60}), 220),
        )
        for adapter, frame_count in scenarios:
            previous = None
            for _ in range(frame_count):
                with contextlib.redirect_stdout(sys.stderr):
                    current = adapter.step()
                if previous is not None:
                    previous_agents = {item.code: item for item in previous.agents}
                    previous_targets = {item.code: item for item in previous.targets}
                    for item in current.agents:
                        old = previous_agents[item.code]
                        displacement = math.hypot(item.x - old.x, item.y - old.y)
                        if item.type == "UAV":
                            configured = float(adapter.config.get("uavSpeedMps", 5.0))
                            speed_limit = min(0.35, max(0.05, configured * 0.1)) + 0.001
                        else:
                            configured = float(adapter.config.get("usvSpeedMps", 3.0))
                            speed_limit = min(0.32, max(0.04, configured * 0.18)) + 0.001
                        self.assertLessEqual(
                            displacement,
                            speed_limit,
                            f"{adapter.code} {item.code} exceeded per-frame speed",
                        )
                    for left_index, left in enumerate(current.agents):
                        for right in current.agents[left_index + 1:]:
                            if previous.sequence == 1:
                                continue
                            required = safety.required_separation(
                                left.type, right.type, left.z, right.z
                            )
                            if required <= 0:
                                continue
                            swept = safety.swept_distance(
                                (
                                    previous_agents[left.code].x,
                                    previous_agents[left.code].y,
                                ),
                                (left.x, left.y),
                                (
                                    previous_agents[right.code].x,
                                    previous_agents[right.code].y,
                                ),
                                (right.x, right.y),
                            )
                            self.assertGreaterEqual(
                                swept + 1e-3,
                                required,
                                f"{adapter.code} {left.code}/{right.code} crossed between frames",
                            )
                    for craft in (item for item in current.agents if item.type == "USV"):
                        for target in current.targets:
                            if previous.sequence == 1:
                                continue
                            if target.code not in previous_targets:
                                continue
                            required = safety.required_separation(
                                craft.type, target.type, craft.z, target.z
                            )
                            swept = safety.swept_distance(
                                (
                                    previous_agents[craft.code].x,
                                    previous_agents[craft.code].y,
                                ),
                                (craft.x, craft.y),
                                (
                                    previous_targets[target.code].x,
                                    previous_targets[target.code].y,
                                ),
                                (target.x, target.y),
                            )
                            self.assertGreaterEqual(
                                swept + 1e-3,
                                required,
                                f"{adapter.code} {craft.code}/{target.code} crossed between frames",
                            )
                previous = current

    def test_capture_holds_large_domain_specific_formation_before_completion(self):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(33, {"seed": 42, "targetBehavior": "STATIC"})
        final = None
        for _ in range(1800):
            with contextlib.redirect_stdout(sys.stderr):
                final = adapter.step()
            if final.terminalStatus:
                break
        self.assertIsNotNone(final)
        self.assertEqual("COMPLETED", final.terminalStatus)
        target = next(item for item in final.targets if item.type == "CAPTURE_TARGET")
        usv_radii = [
            math.hypot(item.x - target.x, item.y - target.y)
            for item in final.agents
            if item.type == "USV"
        ]
        uav_radii = [
            math.hypot(item.x - target.x, item.y - target.y)
            for item in final.agents
            if item.type == "UAV"
        ]
        # SFLA's radial scale now affects the executed ring. Keep the same
        # arrival tolerance, and independently constrain the selected radius
        # to the research parameter range rather than accepting any self-
        # reported goal.
        self.assertGreaterEqual(final.metrics["usvFormationRadius"], 22.0 * 0.94 - 0.005)
        self.assertLessEqual(final.metrics["usvFormationRadius"], 22.0 * 1.06 + 0.005)
        self.assertGreaterEqual(final.metrics["uavFormationRadius"], 46.0 * 0.94 - 0.005)
        self.assertLessEqual(final.metrics["uavFormationRadius"], 46.0 * 1.06 + 0.005)
        self.assertAlmostEqual(final.metrics["usvFormationRadius"], sum(usv_radii) / len(usv_radii), delta=1.2)
        self.assertAlmostEqual(
            final.metrics["uavFormationRadius"],
            sum(uav_radii) / len(uav_radii),
            delta=1.4,
        )
        self.assertTrue(final.metrics["ringGeometryReady"])
        self.assertTrue(all(
            ring["ready"] for ring in final.metrics["ringDiagnostics"].values()
        ))

    def test_five_plus_five_cannot_complete_with_a_large_visual_gap(self):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = CaptureAdapter(3305, self.capture_random_staging_config(5, 5))
        final = None
        for _ in range(2600):
            with contextlib.redirect_stdout(sys.stderr):
                final = adapter.step()
            if final.terminalStatus:
                break
        self.assertIsNotNone(final)
        self.assertEqual("COMPLETED", final.terminalStatus)
        self.assertTrue(final.metrics["safeContainmentReady"])
        self.assertTrue(final.metrics["domainFormationReady"])
        if final.metrics["ringGeometryReady"]:
            for diagnostic in final.metrics["ringDiagnostics"].values():
                if diagnostic["expected"] >= 3:
                    self.assertLessEqual(
                        diagnostic["maxGapDeg"], diagnostic["maxAllowedGapDeg"] + 1e-6,
                    )


if __name__ == "__main__":
    unittest.main()
