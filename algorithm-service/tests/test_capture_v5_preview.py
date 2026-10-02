import contextlib
import io
import math
import unittest

from app.adapters import CaptureAdapter


def frontend_capture_config(uav_count=4, usv_count=5):
    origin = {"eastM": -210.0, "northM": -330.0, "upM": 0.0}
    poses = []
    state = 20260814

    def random_value():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 0x100000000

    corridor = 55 + max(0, math.ceil(math.sqrt(max(uav_count, usv_count))) - 2) * 4
    for kind, count, spacing in (("UAV", uav_count, 10.0), ("USV", usv_count, 14.0)):
        columns = math.ceil(math.sqrt(count))
        rows = math.ceil(count / columns)
        for index in range(count):
            row, column = divmod(index, columns)
            east = -corridor - column * spacing + (random_value() - 0.5) * spacing * 0.18
            north = (row - (rows - 1) / 2) * spacing + (random_value() - 0.5) * spacing * 0.18
            poses.append({
                "deviceCode": f"{kind}-{index + 1:03d}",
                "deviceType": kind,
                "eastM": origin["eastM"] + east,
                "northM": origin["northM"] + north,
                "upM": 20.0 + index % 4 * 2.0 if kind == "UAV" else 0.0,
                "headingDeg": random_value() * 360.0,
                "valid": True,
            })
    poses.append({
        "deviceCode": "TARGET-001",
        "deviceType": "TARGET",
        "eastM": origin["eastM"] + corridor,
        "northM": origin["northM"],
        "upM": 0.0,
        "headingDeg": 0.0,
        "valid": True,
    })
    return {
        "seed": 20260814,
        "uavCount": uav_count,
        "usvCount": usv_count,
        "uavSpeedMps": 5.0,
        "usvSpeedMps": 2.0,
        "initialPoses": poses,
        "initialPosesCoordinateFrame": "GLOBAL_ENU",
        "fleetOrigin": origin,
    }


class CaptureV5PreviewTests(unittest.TestCase):
    def test_resume_preserves_progress_instead_of_reinitializing_the_mission(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            adapter = CaptureAdapter(5100, frontend_capture_config())
            adapter.set_mission_active(False)
            adapter.step()
            adapter.set_mission_active(True)
            adapter.step()
            adapter.target_travelled_distance = 42.0
            adapter.display_progress = 0.37
            adapter.set_mission_active(False)
            adapter.set_mission_active(True)

        self.assertEqual(42.0, adapter.target_travelled_distance)
        self.assertEqual(0.37, adapter.display_progress)

    def test_preview_moves_without_consuming_mission_distance(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            adapter = CaptureAdapter(5101, frontend_capture_config())
            adapter.set_mission_active(False)
            first = adapter.step()
            first_target = first.targets[0]
            first_agents = {item.code: (item.x, item.y) for item in first.agents}
            frame = first
            for _ in range(100):
                frame = adapter.step()
        target = frame.targets[0]
        self.assertEqual("PREVIEW", frame.phase)
        self.assertEqual(0.0, frame.metrics["progress"])
        self.assertEqual(0.0, frame.metrics["targetTravelDistanceM"])
        self.assertGreater(math.hypot(target.x - first_target.x, target.y - first_target.y), 5.0)
        self.assertTrue(any(
            math.hypot(agent.x - first_agents[agent.code][0], agent.y - first_agents[agent.code][1]) > 1.0
            for agent in frame.agents
        ))

    def test_start_continues_preview_pose_and_completes_real_frontend_4_plus_5(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            adapter = CaptureAdapter(5102, frontend_capture_config())
            adapter.set_mission_active(False)
            preview = adapter.step()
            for _ in range(100):
                preview = adapter.step()
            preview_target = preview.targets[0]
            adapter.set_mission_active(True)
            frames = []
            final = adapter.step()
            frames.append(final)
            for _ in range(2199):
                if final.terminalStatus:
                    break
                final = adapter.step()
                frames.append(final)
        first_target = frames[0].targets[0]
        self.assertLess(math.hypot(first_target.x - preview_target.x, first_target.y - preview_target.y), 0.2)
        self.assertEqual("COMPLETED", final.terminalStatus, final.metrics)
        self.assertGreaterEqual(final.metrics["targetTravelDistanceM"], 100.0)
        self.assertGreaterEqual(final.metrics["targetNetDisplacementM"], 80.0)
        self.assertGreaterEqual(min(frame.metrics["operationalBoundaryClearanceM"] for frame in frames), 0.0)
        moving_frames = [
            frame for frame in frames[20:]
            if not frame.metrics["formationReady"]
            and frame.metrics["targetTravelDistanceM"] <= frame.metrics["requiredPursuitDistanceM"] + 20.0
        ]
        self.assertTrue(moving_frames)
        self.assertGreater(min(frame.metrics["targetSpeedMps"] for frame in moving_frames), 0.3)
        self.assertEqual("NONE", final.metrics["captureBlocker"])

    def test_target_turns_along_operational_edge_without_stopping(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            adapter = CaptureAdapter(5103, frontend_capture_config())
            right = adapter.safety.bounds[1] - adapter.operational_inset
            previous = (right - 4.0, 0.0, 0.0)
            adapter.target_escape_direction = (1.0, 0.0)
            safe = adapter._advance_capture_target(previous)
        self.assertEqual("COAST_AVOID", adapter.target_behavior_state)
        self.assertGreater(math.hypot(*adapter.target_velocity), 0.0)
        self.assertGreaterEqual(adapter._operational_clearance(safe.x, safe.y), 0.0)
        self.assertLess(adapter.target_escape_direction[0], 0.95)


if __name__ == "__main__":
    unittest.main()
