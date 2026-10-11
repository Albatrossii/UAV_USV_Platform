"""Pose-based guard acceptance, independent of coordinator completion flags."""
import math
import unittest

from app.adapters.adaptive_escort import AdaptiveEscortAdapter
from app.adapters.escort_defense import Screen


class EscortDefenseTests(unittest.TestCase):
    def test_hostile_trajectory_never_teleports_or_slides_at_any_stage(self):
        for count in (3, 5, 10, 15, 30):
            with self.subTest(count=count):
                a = AdaptiveEscortAdapter(9910, {"uavCount": count, "usvCount": count, "seed": 20260814})
                a.set_mission_active(True)
                for _ in range(6000):
                    old = {t.code: (t.x, t.y, t.heading) for t in a.threats}
                    frame = a.step()
                    for t in a.threats:
                        x, y, heading = old[t.code]
                        dx, dy = t.x-x, t.y-y
                        angle = math.radians(t.heading)
                        why = (count, frame.sequence, t.code, t.state)
                        self.assertLessEqual(math.hypot(dx, dy), t.maximum_speed*.1+.002, why)
                        self.assertLessEqual(abs((t.heading-heading+180)%360-180), 7.01, why)
                        self.assertGreaterEqual(dx*math.cos(angle)+dy*math.sin(angle), -.002, why)
                        self.assertLessEqual(abs(-dx*math.sin(angle)+dy*math.cos(angle)), .002, why)
                    if frame.terminalStatus:
                        break
                self.assertEqual(frame.terminalStatus, "COMPLETED")

    def test_withdrawing_convoy_moves_bow_first_while_guards_reverse(self):
        for count in (3, 5, 10):
            with self.subTest(count=count):
                a = AdaptiveEscortAdapter(9920, {"uavCount": count, "usvCount": count, "seed": 20260814})
                forward_distance, reverse_seen, turning_seen = {}, False, False
                concurrent_frames = 0
                for _ in range(1800):
                    old = {v.code:(v.x,v.y) for v in a.vehicles+a.protected}
                    frame = a.step()
                    if a.defense.mode != "WITHDRAWAL" or a.defense.released:
                        if a.defense.released:
                            break
                        continue
                    poses = {v.code:v for v in frame.agents+frame.targets}
                    codes = [v.code for v in a.vehicles if v.kind == "USV" and a.defense.withdrawing(v)] + [a.protected[0].code]
                    for code in codes:
                        v = poses[code]
                        dx,dy = v.x-old[code][0], v.y-old[code][1]
                        angle = math.radians(v.heading)
                        forward = dx*math.cos(angle)+dy*math.sin(angle)
                        lateral = -dx*math.sin(angle)+dy*math.cos(angle)
                        self.assertGreaterEqual(forward, -.002, (count,frame.sequence,code,"backward",forward))
                        self.assertLessEqual(abs(lateral), .012, (count,frame.sequence,code,"lateral",lateral))
                        forward_distance[code] = forward_distance.get(code,0)+max(0,forward)
                        if code == a.protected[0].code and a.defense.reversing and forward > .03:
                            concurrent_frames += 1
                    turning_seen |= not a.defense.reversing and a.defense.withdrawal_distance() > .5
                    reverse_seen |= a.defense.reversing
                self.assertTrue(a.defense.released)
                self.assertTrue(turning_seen, "withdrawal waited for reverse phase")
                self.assertTrue(reverse_seen)
                self.assertGreaterEqual(concurrent_frames, 20, "convoy stopped before guards reversed")
                self.assertTrue(all(d >= 3.5 for d in forward_distance.values()), forward_distance)

    def test_all_directions_show_two_bows_and_four_seconds_of_real_reverse(self):
        for uav, usv in ((3, 3), (10, 10), (15, 15), (20, 25), (30, 30)):
            with self.subTest(fleet=(uav, usv)):
                a = AdaptiveEscortAdapter(9910, {"uavCount": uav, "usvCount": usv, "seed": 20260814})
                best, streak, assignment = {}, {}, {}
                detection_distances = {}
                for _ in range(1800):
                    previous = {v.code: (v.x, v.y) for v in a.vehicles}
                    frame = a.step()
                    poses = {v.code: v for v in frame.agents}
                    for t in a.threats:
                        if t.detected_frame is not None and t.code not in detection_distances:
                            detection_distances[t.code] = a._distance_to_protected(t)
                        if t.forced:
                            self.assertTrue(t.cover_released, (frame.sequence, t.code, "guard bypass"))
                    for index, s in a.defense.screens.items():
                        if index in assignment:
                            self.assertEqual(assignment[index], s.codes)
                        assignment[index] = s.codes[:]
                        boats = [poses[c] for c in s.codes if c.startswith("USV-")]
                        self.assertEqual(2, len(boats))
                        t = a.threats[index]
                        good = True
                        for v in boats:
                            angle = math.atan2(t.y-v.y, t.x-v.x)
                            delta = (v.x-previous[v.code][0], v.y-previous[v.code][1])
                            reverse = -(delta[0]*math.cos(angle)+delta[1]*math.sin(angle))/.1
                            bow_error = abs((v.heading-math.degrees(angle)+180)%360-180)
                            good = good and reverse >= .39 and bow_error <= 10.01
                        streak[index] = streak.get(index, 0)+1 if good else 0
                        best[index] = max(best.get(index, 0), streak[index])
                    if a.defense.released:
                        break
                self.assertTrue(a.defense.released, a.defense.metrics())
                self.assertEqual(len(a.threats), len(assignment))
                self.assertTrue(all(best[i] >= 40 for i in assignment), best)
                self.assertLessEqual(max(detection_distances.values())-min(detection_distances.values()), 8)
                if usv >= 15:
                    self.assertEqual("CENTER_DEFENSE", a.defense.mode)
                if usv == 3:
                    self.assertEqual(1, sum(v.kind == "USV" and v.role == "CLOSE_GUARD" for v in a.vehicles))
                    for _ in range(1000):
                        a.step()
                        if a.threats[0].forced:
                            break
                    self.assertEqual(2, sum(v.kind == "USV" and v.assigned_threat == 0 for v in a.vehicles))
                    self.assertEqual(1, sum(v.kind == "UAV" and v.role == "CLOSE_GUARD" for v in a.vehicles))

    def test_simultaneous_detection_is_required_before_allocating_cohort(self):
        a = AdaptiveEscortAdapter(1, {"uavCount": 30, "usvCount": 30})
        a.threats[0].detected_frame = 1
        a.defense.synchronize()
        self.assertFalse(a.defense.screens)
        for t in a.threats:
            t.detected_frame = 2
        a.defense.synchronize()
        self.assertEqual(len(a.threats), len(a.defense.screens))
        members = [c for s in a.defense.screens.values() for c in s.codes]
        self.assertEqual(len(members), len(set(members)))

    def test_reverse_commands_never_face_the_protected_target(self):
        a = AdaptiveEscortAdapter(1, {"uavCount": 3, "usvCount": 3})
        v = next(v for v in a.vehicles if v.kind == "USV")
        t, p = a.threats[0], a.protected[0]
        p.x, p.y, t.x, t.y, v.x, v.y = 0, 0, 80, 0, 40, 0
        a.defense.screens[0] = Screen(0, [v.code], phase="REVERSING",
                                     slots={v.code: (40, 0)}, origins={v.code: (39, 0)})
        a._stable_headings[v.code] = 0
        v.vx, v.vy = -.8, 0
        for _ in range(60):
            heading = a.defense.heading(v)
            point = a.defense.motion(v)
            self.assertEqual(0, heading)
            self.assertLess(point[0], v.x)

    def test_stationary_screen_cannot_complete_from_elapsed_time(self):
        a = AdaptiveEscortAdapter(1, {"uavCount": 3, "usvCount": 3})
        for t in a.threats:
            t.detected_frame = 1
        a.defense.synchronize()
        for _ in range(2000):
            a.defense.observe()
        self.assertFalse(a.defense.released)
        self.assertFalse(any(t.cover_released for t in a.threats))


if __name__ == "__main__":
    unittest.main()
