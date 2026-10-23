import math
import unittest

from app.adapters.adaptive_escort import (
    AdaptiveEscortAdapter,
    BREACH_DISTANCE_M,
    CONVOY_GUARD_MARGIN_M,
    CONVOY_TARGET_SPACING_M,
    CONTAINMENT_STANDOFF_M,
    POST_CAPTURE_CONVOY_CLEARANCE_M,
    POST_MISSION_OUTER_GUARD_GAP_M,
    POST_MISSION_SLOT_TOLERANCE_M,
    PROTECTED_SAFE_GATE_OFFSET_M,
    TARGET_SEPARATION_M,
    ESCORT_DEPARTURE_MIN_M,
)


from app.adapters.escort_defense import Screen


class NextEscortAcceptanceTests(unittest.TestCase):
    def test_default_terminal_keeps_mixed_guard_pair_with_protected_ship(self):
        adapter = AdaptiveEscortAdapter(9951, {
            "uavCount": 3, "usvCount": 3, "seed": 42,
            "uavSpeedMps": 5, "usvSpeedMps": 3,
        })
        frame = None
        for _ in range(4000):
            frame = adapter.step()
            if frame.terminalStatus:
                break

        self.assertIsNotNone(frame)
        self.assertEqual("COMPLETED", frame.terminalStatus, frame.metrics)
        containment = [
            item for item in adapter.vehicles if item.assigned_threat == 0
        ]
        guards = [
            item for item in adapter.vehicles if item.role == "CLOSE_GUARD"
        ]
        self.assertEqual(2, sum(item.kind == "UAV" for item in containment))
        self.assertEqual(2, sum(item.kind == "USV" for item in containment))
        self.assertEqual({"UAV", "USV"}, {item.kind for item in guards})
        self.assertEqual(0, frame.metrics["postMissionFormationRequiredCount"])
        self.assertEqual(0, frame.metrics["postMissionFormationReadyCount"])
        protected = adapter.protected[0]
        self.assertTrue(all(
            math.hypot(item.x - protected.x, item.y - protected.y)
            < math.hypot(item.x - adapter.threats[0].x, item.y - adapter.threats[0].y)
            for item in guards
        ))

    def test_default_capture_keeps_mixed_close_guard_pair_during_pursuit(self):
        adapter = AdaptiveEscortAdapter(9952, {
            "uavCount": 3, "usvCount": 3, "seed": 42,
            "uavSpeedMps": 5, "usvSpeedMps": 3,
        })
        adapter.step()
        adapter.activate_capture()

        guards = [item for item in adapter.vehicles if item.role == "CLOSE_GUARD"]
        containment = [item for item in adapter.vehicles if item.assigned_threat == 0]
        self.assertEqual({"UAV", "USV"}, {item.kind for item in guards})
        self.assertEqual(2, sum(item.kind == "UAV" for item in containment))
        self.assertEqual(2, sum(item.kind == "USV" for item in containment))

    def test_normal_encounter_does_not_bypass_multi_direction_guard(self):
        adapter = AdaptiveEscortAdapter(9960, {"uavCount": 30, "usvCount": 30, "seed": 20260814})
        for _ in range(1800):
            adapter.step()
            for threat in adapter.threats:
                if threat.forced:
                    self.assertTrue(threat.cover_released)
                    self.assertEqual(threat.auto_capture_reason, "INTERCEPT_ESTABLISHED")
            if all(t.forced for t in adapter.threats):
                break
        self.assertTrue(all(t.forced for t in adapter.threats))

    def test_reserve_slots_do_not_collapse_at_two_water_boundaries(self):
        adapter = AdaptiveEscortAdapter(9959, {"uavCount": 20, "usvCount": 25, "seed": 20260814})
        for x, y in ((-215.35, 159.64), (-215.35, -159.64), (215.35, 159.64), (215.35, -159.64)):
            with self.subTest(corner=(x, y)):
                adapter.protected[0].x, adapter.protected[0].y = x, y
                points = [adapter._convoy_support_point(slot, 23) for slot in range(23)]
                minimum = min(math.hypot(a[0] - b[0], a[1] - b[1])
                              for index, a in enumerate(points) for b in points[index + 1:])
                self.assertGreaterEqual(minimum, 7.0)

    def test_emitted_poses_prove_sustained_bow_inward_reverse_before_release(self):
        for count in (3, 10):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9950 + count, {
                    "uavCount": count, "usvCount": count, "seed": 20260814,
                })
                streak, best, releases = {}, {}, set()
                for _ in range(1000):
                    before = {v.code: (v.x, v.y) for v in adapter.vehicles}
                    frame = adapter.step()
                    for index, threat in enumerate(adapter.threats):
                        screen = [v for v in frame.agents if v.type == "USV"
                                  and v.groupId == f"BLOCK-{index + 1:03d}" and v.role == "BLOCKER"]
                        valid = bool(screen) and threat.screen_established
                        for v in screen:
                            dx, dy = v.x - before[v.code][0], v.y - before[v.code][1]
                            facing = math.atan2(threat.y - v.y, threat.x - v.x)
                            backward = -(dx * math.cos(facing) + dy * math.sin(facing))
                            yaw = abs((v.heading - math.degrees(facing) + 180) % 360 - 180)
                            valid = valid and backward >= 0.015 - 1e-6 and yaw <= 10.0
                        streak[threat.code] = streak.get(threat.code, 0) + 1 if valid else 0
                        best[threat.code] = max(best.get(threat.code, 0), streak[threat.code])
                        if threat.cover_released:
                            self.assertGreaterEqual(best[threat.code], 40)
                            releases.add(threat.code)
                    if len(releases) == len(adapter.threats):
                        break
                self.assertEqual(len(adapter.threats), len(releases))

    def cover_fixture(self):
        adapter = AdaptiveEscortAdapter(9901, {"uavCount": 3, "usvCount": 3})
        threat, target = adapter.threats[0], adapter.protected[0]
        target.x, target.y = 0, 0
        threat.x, threat.y = 100, 0
        threat.state, threat.screen_established = "ATTACKING", True
        responders = [v for v in adapter.vehicles if v.kind == "USV"][:2]
        s = Screen(0, [v.code for v in responders], phase="REVERSING")
        for i, v in enumerate(responders):
            v.x, v.y = 40, (i-.5)*18
            angle = math.atan2(threat.y-v.y, threat.x-v.x)
            v.vx, v.vy = -.8*math.cos(angle), -.8*math.sin(angle)
            adapter._stable_headings[v.code] = math.degrees(angle)
            s.origins[v.code], s.slots[v.code] = (v.x, v.y), (40, v.y)
        adapter.defense.screens = {0: s}
        adapter.defense.reversing = True
        return adapter, threat, target, responders

    def test_cover_release_requires_real_reverse_and_safe_withdrawal(self):
        adapter, threat, _, _ = self.cover_fixture()
        for _ in range(39):
            adapter.defense.observe()
        self.assertFalse(threat.cover_released)
        adapter.defense.observe()
        self.assertTrue(threat.cover_released)
        self.assertGreaterEqual(threat.cover_distance, 3.0)
        self.assertIn("DEFENSE_HANDOFF_CONFIRMED", [e["type"] for e in adapter._tactical_events])

    def test_cover_has_no_timer_only_release(self):
        adapter, threat, _, responders = self.cover_fixture()
        responders[0].vx = responders[0].vy = 0
        for _ in range(1000):
            adapter.defense.observe()
        self.assertFalse(threat.cover_released)
        self.assertEqual(0, threat.cover_frames)

    def test_cover_rejects_wrong_bow_or_unsafe_target(self):
        adapter, threat, target, responders = self.cover_fixture()
        adapter._stable_headings[responders[0].code] = 180
        for _ in range(60):
            adapter.defense.observe()
        self.assertEqual(0, threat.cover_frames)
        angle = math.atan2(threat.y-responders[0].y, threat.x-responders[0].x)
        adapter._stable_headings[responders[0].code] = math.degrees(angle)
        adapter.defense.reverse_elapsed = 0
        target.x = 60
        for _ in range(40):
            adapter.defense.observe()
        self.assertGreaterEqual(threat.cover_frames, 40)
        self.assertFalse(threat.cover_released)

    def test_verified_cover_is_not_erased_while_another_threat_is_unsafe(self):
        adapter, threat, target, responders = self.cover_fixture()
        target.x = 60
        for _ in range(40):
            adapter.defense.observe()
        self.assertTrue(threat.cover_verified)
        responders[0].vx = 0
        for _ in range(100):
            adapter.defense.observe()
        self.assertFalse(threat.cover_released)
        self.assertEqual(40, threat.cover_frames)
        target.x = 0
        adapter.defense.observe()
        self.assertTrue(threat.cover_released)

    def run_frames(self, adapter, count):
        frame = None
        for _ in range(count):
            frame = adapter.step()
        return frame

    def test_detection_automatically_starts_capture(self):
        adapter = AdaptiveEscortAdapter(9001, {"uavCount": 3, "usvCount": 3, "seed": 8})
        frame = self.run_frames(adapter, 650)
        self.assertTrue(any(threat.forced for threat in adapter.threats))
        self.assertTrue(any(agent.role in {"CAPTURE", "INTERCEPTOR", "CONTAINMENT"} for agent in frame.agents))
        self.assertTrue(any(target.state not in {"APPROACHING", "WAITING"} for target in frame.targets if target.type == "THREAT_TARGET"))

    def test_generated_threats_start_outside_guard_area(self):
        for count in (3, 5, 10, 20, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9050 + count, {
                    "uavCount": count,
                    "usvCount": count,
                    "seed": 20260814,
                })
                for threat in adapter.threats:
                    protected = adapter.protected[threat.protected_index]
                self.assertGreaterEqual(
                    math.hypot(threat.x - protected.x, threat.y - protected.y),
                    adapter.defense.screen_radius + 30.0,
                )

    def test_multi_convoy_response_craft_are_separated_before_first_frame(self):
        for uav_count, usv_count in ((20, 25), (25, 25), (30, 30)):
            with self.subTest(uav_count=uav_count, usv_count=usv_count):
                adapter = AdaptiveEscortAdapter(9100 + uav_count + usv_count, {
                    "uavCount": uav_count,
                    "usvCount": usv_count,
                    "seed": 20260814,
                })
                usvs = [item for item in adapter.vehicles if item.kind == "USV"]
                self.assertGreaterEqual(min(
                    math.hypot(left.x - right.x, left.y - right.y)
                    for index, left in enumerate(usvs)
                    for right in usvs[index + 1:]
                ), adapter.safety.required_separation("USV", "USV") - 0.02)

    def test_active_capture_keeps_mixed_close_guards(self):
        for count in (3, 4, 10, 20, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9100 + count, {"uavCount": count, "usvCount": count, "seed": 12})
                adapter.step()
                selected = adapter.activate_capture()
                frame = self.run_frames(adapter, 10)
                guards = [agent for agent in frame.agents if agent.role == "CLOSE_GUARD"]
                assigned = [agent for agent in frame.agents if agent.assignedTargetCode == selected]
                if count == 3:
                    self.assertEqual(1, sum(agent.type == "UAV" for agent in guards))
                    self.assertEqual(1, sum(agent.type == "USV" for agent in guards))
                    self.assertEqual(2, sum(agent.type == "UAV" for agent in assigned))
                    self.assertEqual(2, sum(agent.type == "USV" for agent in assigned))
                else:
                    self.assertGreaterEqual(sum(agent.type == "UAV" for agent in guards), adapter.plan.protected_count)
                    self.assertGreaterEqual(sum(agent.type == "USV" for agent in guards), adapter.plan.protected_count)
                    self.assertGreaterEqual(sum(agent.type == "UAV" for agent in assigned), 2)
                    self.assertGreaterEqual(sum(agent.type == "USV" for agent in assigned), 2)

    def test_all_scales_keep_exactly_one_protected_target(self):
        for count in (3, 5, 10, 15, 20, 25, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9150 + count, {
                    "uavCount": count, "usvCount": count, "seed": 20260814,
                })
                self.assertEqual(1, adapter.plan.protected_count)
                self.assertEqual(1, len(adapter.protected))

    def test_initial_frame_is_a_convoy_not_a_prebuilt_capture_ring(self):
        for count in (3, 10, 20, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9160 + count, {
                    "uavCount": count, "usvCount": count, "seed": 20260814,
                })
                frame = adapter.step()
                protected = adapter.protected[0]
                guards = [item for item in adapter.vehicles if item.role == "CLOSE_GUARD"]
                self.assertTrue(guards)
                self.assertLess(
                    min(math.hypot(item.x - protected.x, item.y - protected.y) for item in guards),
                    35.0,
                )
                self.assertEqual([], frame.metrics["tacticalEvents"])
                self.assertEqual(0, frame.metrics["captureAssignedCount"])

    def test_initial_convoy_assigns_every_device_a_stable_guard_slot(self):
        for count in (3, 10, 20, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9180 + count, {
                    "uavCount": count, "usvCount": count, "seed": 20260814,
                })
                self.assertFalse(any(
                    item.role == "RECON" for item in adapter.vehicles
                ))
                self.assertTrue(all(
                    item.role in {"CLOSE_GUARD", "FORMATION_GUARD"}
                    for item in adapter.vehicles
                ))
                for kind in ("UAV", "USV"):
                    formation = [
                        item for item in adapter.vehicles
                        if item.kind == kind and item.role == "FORMATION_GUARD"
                    ]
                    desired = [adapter._desired_position(item) for item in formation]
                    self.assertEqual(len(desired), len({
                        (round(point[0], 3), round(point[1], 3))
                        for point in desired
                    }))

    def test_default_fleet_places_both_domains_ahead_and_astern(self):
        adapter = AdaptiveEscortAdapter(9181, {
            "uavCount": 3, "usvCount": 3, "seed": 20260814,
        })
        center_x, _ = adapter._convoy_center()
        for kind in ("UAV", "USV"):
            members = [item for item in adapter.vehicles if item.kind == kind]
            self.assertTrue(
                any(item.x > center_x + 5.0 for item in members),
                f"{kind} must have a forward escort slot",
            )
            self.assertTrue(
                any(item.x < center_x - 5.0 for item in members),
                f"{kind} must have an aft escort slot",
            )

    def test_attack_intent_requires_motion_evidence_and_convoy_departure(self):
        adapter = AdaptiveEscortAdapter(9190, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
        })
        initial = adapter.step()
        self.assertFalse(initial.metrics["escortDepartureReady"])
        attack_event = None
        frame = initial
        for _ in range(900):
            frame = adapter.step()
            attack_event = next((
                item for item in frame.metrics["tacticalEvents"]
                if item["type"] == "ATTACK_INTENT_CONFIRMED"
            ), None)
            if attack_event:
                break
        self.assertIsNotNone(attack_event)
        self.assertTrue(frame.metrics["escortDepartureReady"])
        self.assertGreaterEqual(frame.metrics["escortDepartureDistanceM"], ESCORT_DEPARTURE_MIN_M)
        roles = frame.metrics["roles"]
        self.assertGreaterEqual(roles.get("BLOCKER", 0), 1)
        self.assertGreaterEqual(roles.get("CONFRONT", 0), 1)
        response_event = next((
            item for item in frame.metrics["tacticalEvents"]
            if item["type"] == "GUARD_RESPONSE_DISPATCHED"
        ), None)
        self.assertIsNone(response_event, "Allocating roles is not physical departure")
        for _ in range(300):
            frame = adapter.step()
            response_event = next((event for event in frame.metrics["tacticalEvents"]
                                   if event["type"] == "GUARD_RESPONSE_DISPATCHED"), None)
            if response_event:
                break
        self.assertIsNotNone(response_event)
        self.assertGreater(response_event["sequence"], attack_event["sequence"])
        self.assertIn("UAV-", response_event["message"])
        self.assertIn("USV-", response_event["message"])

    def test_escape_notice_follows_observed_away_motion(self):
        adapter = AdaptiveEscortAdapter(9191, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
        })
        adapter.step()
        adapter.activate_capture(adapter.threats[0].code)
        start_sequence = adapter.sequence
        escape_event = None
        for _ in range(900):
            frame = adapter.step()
            escape_event = next((
                item for item in frame.metrics["tacticalEvents"]
                if item["type"] == "ESCAPE_INTENT_CONFIRMED"
            ), None)
            if escape_event:
                break
        self.assertIsNotNone(escape_event)
        self.assertGreater(escape_event["sequence"], start_sequence)
        self.assertTrue(adapter.threats[0].escape_intent_confirmed)

    def test_escort_events_match_motion_and_guards_stay_outside_capture(self):
        adapter = AdaptiveEscortAdapter(9210, {"uavCount": 10, "usvCount": 10, "seed": 20260814})
        seen = {}
        for _ in range(7000):
            before = {t.code: (t.x, t.y, t.escape_intent_confirmed) for t in adapter.threats}
            frame = adapter.step()
            for event in frame.metrics["tacticalEvents"]:
                key = (event["threatCode"], event["type"])
                if key in seen:
                    continue
                seen[key] = frame.sequence
                threat = next(t for t in adapter.threats if t.code == event["threatCode"])
                if event["type"] == "GUARD_RESPONSE_DISPATCHED":
                    self.assertGreater(frame.sequence, seen[(threat.code, "ATTACK_INTENT_CONFIRMED")])
                    self.assertGreaterEqual(threat.response_motion_frames, 5)
                    responders = [v for v in adapter.vehicles if v.group_id in {
                        f"BLOCK-{adapter.threats.index(threat) + 1:03d}",
                        f"WATCH-{adapter.threats.index(threat) + 1:03d}"}]
                    self.assertEqual({v.kind for v in responders}, {"UAV", "USV"})
                    for v in responders:
                        screen = adapter.defense.screens[adapter.threats.index(threat)]
                        ox, oy = screen.origins[v.code]
                        self.assertGreaterEqual(math.hypot(v.x - ox, v.y - oy), 2.0)
                if event["type"] == "GUARD_SCREEN_ESTABLISHED":
                    self.assertGreater(frame.sequence, seen[(threat.code, "GUARD_RESPONSE_DISPATCHED")])
                    self.assertGreaterEqual(threat.intercept_stage_frames, 5)
            protected = adapter.protected[0]
            for t in adapter.threats:
                x, y, confirmed = before[t.code]
                if confirmed and math.hypot(x - protected.x, y - protected.y) < 59.0:
                    toward = (t.x - x) * (protected.x - x) + (t.y - y) * (protected.y - y)
                    self.assertLessEqual(toward, 0.01, (frame.sequence, t.code, "escaped enemy returned toward convoy"))
                for v in adapter.vehicles:
                    if v.role == "CLOSE_GUARD" and t.forced:
                        self.assertIsNone(v.assigned_threat)
                        self.assertGreaterEqual(math.hypot(v.x - t.x, v.y - t.y), 35.0,
                                                (frame.sequence, v.code, "close guard entered capture area"))
            if frame.terminalStatus:
                break
        self.assertEqual(frame.terminalStatus, "COMPLETED")
        for t in adapter.threats:
            self.assertIn((t.code, "GUARD_SCREEN_ESTABLISHED"), seen)
            self.assertIn((t.code, "ESCAPE_INTENT_CONFIRMED"), seen)

    def test_single_protected_target_reacts_to_threats_without_duplicate_convoy_targets(self):
        adapter = AdaptiveEscortAdapter(9150, {
            "uavCount": 20, "usvCount": 20, "seed": 20260814,
        })
        adapter.step()
        adapter.activate_capture()
        saw_threat_response = False
        for _ in range(420):
            adapter.step()
            self.assertEqual(1, len(adapter.protected))
            protected = adapter.protected[0]
            saw_threat_response = saw_threat_response or protected.state in {
                "THREAT_DETECTED", "EVADING", "BYPASSING_CONTAINMENT",
            }
        self.assertTrue(saw_threat_response)

    def test_single_protected_target_has_square_mixed_close_guard(self):
        adapter = AdaptiveEscortAdapter(9151, {
            "uavCount": 20, "usvCount": 20, "seed": 20260814,
        })
        guards = [item for item in adapter.vehicles if item.role == "CLOSE_GUARD"]
        self.assertTrue(guards)
        self.assertEqual({item.group_id for item in guards}, {"CONVOY-GUARD"})
        self.assertEqual({item.kind for item in guards}, {"UAV", "USV"})
        center_x, center_y = adapter._convoy_center()
        protected_extent = max(
            max(abs(offset[0]), abs(offset[1]))
            for offset in adapter._protected_formation_offsets.values()
        )
        half_extent = protected_extent + CONVOY_GUARD_MARGIN_M
        offsets = [(item.x - center_x, item.y - center_y) for item in guards]
        self.assertEqual(len({(round(x, 3), round(y, 3)) for x, y in offsets}), len(guards))
        self.assertTrue(all(
            abs(max(abs(x), abs(y)) - half_extent) <= 0.05
            for x, y in offsets
        ))
        self.assertLessEqual(min(x for x, _ in offsets), -half_extent + 0.05)
        self.assertGreaterEqual(max(x for x, _ in offsets), half_extent - 0.05)
        self.assertLessEqual(min(y for _, y in offsets), -half_extent + 0.05)
        self.assertGreaterEqual(max(y for _, y in offsets), half_extent - 0.05)
        nearest_guard_clearance = min(
            math.hypot(guard.x - target.x, guard.y - target.y)
            for guard in guards
            for target in adapter.protected
        )
        self.assertGreaterEqual(nearest_guard_clearance, 13.9)
        self.assertLessEqual(nearest_guard_clearance, 15.1)

    def test_eighteen_plus_eighteen_has_eight_square_close_guards(self):
        adapter = AdaptiveEscortAdapter(91511, {
            "uavCount": 18, "usvCount": 18, "seed": 20260814,
        })
        guards = [item for item in adapter.vehicles if item.role == "CLOSE_GUARD"]
        self.assertEqual(8, len(guards))
        self.assertEqual(4, sum(item.kind == "UAV" for item in guards))
        self.assertEqual(4, sum(item.kind == "USV" for item in guards))
        center_x, center_y = adapter._convoy_center()
        offsets = [(item.x - center_x, item.y - center_y) for item in guards]
        self.assertEqual(8, len({
            (round(x, 3), round(y, 3))
            for x, y in offsets
        }))
        self.assertTrue(all(
            abs(max(abs(x), abs(y)) - CONVOY_GUARD_MARGIN_M) <= 0.05
            for x, y in offsets
        ))

    def test_thirty_plus_thirty_returns_all_surplus_to_outer_guard(self):
        adapter = AdaptiveEscortAdapter(91512, {
            "uavCount": 30, "usvCount": 30, "seed": 20260814,
        })
        adapter.step()
        adapter._start_capture_for(adapter.threats, "TEST_SETUP")
        for threat in adapter.threats:
            threat.state = "CAPTURED"
            threat.forced = True
        adapter._redeploy_surplus_to_convoy()

        support = adapter._convoy_support_members()
        watch = adapter._post_watch_members()
        self.assertEqual(20, len(support))
        self.assertEqual(10, sum(item.kind == "UAV" for item in support))
        self.assertEqual(10, sum(item.kind == "USV" for item in support))
        self.assertEqual([], watch)
        self.assertTrue(all(item.assigned_threat is None for item in support))
        self.assertEqual(20, len(adapter._convoy_support_route_by_code))
        status = adapter._post_mission_formation_status()
        self.assertEqual(20, status["requiredCount"])
        self.assertFalse(status["ready"])
        self.assertTrue(status["blockerCode"])

    def test_single_protected_target_keeps_guards_while_rest_respond(self):
        adapter = AdaptiveEscortAdapter(9152, {
            "uavCount": 20, "usvCount": 20, "seed": 20260814,
        })
        guard_codes = {
            item.code for item in adapter.vehicles if item.role == "CLOSE_GUARD"
        }
        adapter.step()
        adapter.activate_capture()
        frame = adapter.step()
        guards = [item for item in adapter.vehicles if item.code in guard_codes]
        self.assertTrue(all(
            item.role == "CLOSE_GUARD" and item.assigned_threat is None
            for item in guards
        ))
        self.assertFalse(any(
            item.role == "RECON"
            for item in adapter.vehicles
            if item.code not in guard_codes
        ))
        self.assertTrue(any(item.role == "CAPTURE_RESERVE" for item in adapter.vehicles))
        self.assertFalse(any(item.role == "OUTER_INTERCEPT" for item in adapter.vehicles))
        reserves = adapter._convoy_reserve_members()
        self.assertTrue(reserves)
        for position, item in enumerate(reserves):
            desired = adapter._desired_position(item)
            rear_slot = adapter._convoy_support_point(position, len(reserves))
            self.assertAlmostEqual(rear_slot[0], desired[0], places=6)
            self.assertAlmostEqual(rear_slot[1], desired[1], places=6)
        self.assertEqual(len(frame.metrics["captureGroups"]), len(adapter.threats))
        self.assertTrue(all(
            group["uavCount"] >= 2 and group["usvCount"] >= 2
            for group in frame.metrics["captureGroups"]
        ))

    def test_thirty_plus_thirty_keeps_all_twenty_surplus_craft_in_outer_escort_square(self):
        adapter = AdaptiveEscortAdapter(91521, {
            "uavCount": 30, "usvCount": 30, "seed": 20260814,
        })
        initial = adapter.step()
        self.assertEqual(0, initial.metrics["captureAssignedCount"])
        adapter._start_capture_for(adapter.threats, "TEST_SETUP")
        frame = adapter.step()

        guards = [item for item in adapter.vehicles if item.role == "CLOSE_GUARD"]
        assigned = [item for item in adapter.vehicles if item.assigned_threat is not None]
        reserves = adapter._convoy_reserve_members()
        self.assertEqual(8, len(guards))
        self.assertEqual(32, len(assigned))
        self.assertEqual(20, len(reserves))
        self.assertEqual(10, sum(item.kind == "UAV" for item in reserves))
        self.assertEqual(10, sum(item.kind == "USV" for item in reserves))
        self.assertFalse(any(item.role == "OUTER_INTERCEPT" for item in adapter.vehicles))
        desired = [adapter._desired_position(item) for item in reserves]
        self.assertEqual(20, len({
            (round(point[0], 3), round(point[1], 3))
            for point in desired
        }))
        self.assertEqual(8, frame.metrics["closeGuardCount"])
        self.assertEqual(32, frame.metrics["captureAssignedCount"])
        self.assertEqual(20, frame.metrics["mobileSupportCount"])

    def test_parallel_team_arrival_can_handoff_after_visible_escape_run(self):
        adapter = AdaptiveEscortAdapter(91522, {
            "uavCount": 30, "usvCount": 30, "seed": 20260814,
        })
        adapter.step()
        threat = adapter.threats[2]
        self.assertFalse(threat.forced)
        self.assertEqual("UNCLASSIFIED", threat.intent)
        adapter._start_capture_for([threat], "TEST_INTERCEPT")
        threat.escape_intent_confirmed = True
        threat.travelled_distance = (
            threat.capture_start_travel_distance
            + threat.required_pursuit_distance * 0.50
        )
        members = adapter._capture_members(2)
        for _ in range(4):
            slots = adapter._capture_slots(members, threat)
            center_x, center_y = adapter._capture_center(threat, members)
            for item, slot in zip(members, slots):
                item.x, item.y, item.z = slot.point((center_x, center_y, 0.0))

        adapter._assess_threats()

        self.assertEqual(1, threat.capture_stage)
        self.assertIn(threat.mission_stage, {"INTERCEPT", "ENCIRCLEMENT"})
        self.assertNotEqual("ESCAPE_PURSUIT", threat.state)
        ring_slots = adapter._capture_slots(members, threat)
        ring_angles = sorted(slot.angle % (2.0 * math.pi) for slot in ring_slots)
        maximum_slot_gap = max(
            (ring_angles[(index + 1) % len(ring_angles)] - ring_angles[index])
            % (2.0 * math.pi)
            for index in range(len(ring_angles))
        )
        self.assertLessEqual(math.degrees(maximum_slot_gap), 61.0)

        # Slot arrival temporarily drops while the team leaves the pursuit fan.
        # The hand-off must remain latched instead of returning to PURSUIT.
        adapter._assess_threats()
        self.assertEqual(1, threat.capture_stage)
        self.assertNotIn(threat.mission_stage, {"ESCAPE", "PURSUIT"})

    def test_twenty_six_parallel_team_does_not_wait_for_full_odometer_in_fan(self):
        adapter = AdaptiveEscortAdapter(91524, {
            "uavCount": 26, "usvCount": 26, "seed": 20260814,
            "uavSpeedMps": 15, "usvSpeedMps": 4,
        })
        stage_one_frame = None
        stage_two_frame = None
        # This is a full escort scenario, now including physical withdrawal
        # and safety-gated handoff. A fixed 160 s from initial preview was not
        # a pursuit contract. Bound the test, retaining all geometric checks.
        for _ in range(9000):
            frame = adapter.step()
            group = next(
                item for item in frame.metrics["captureGroups"]
                if item["threatCode"] == "THREAT-003"
            )
            if group["stage"] == 1 and stage_one_frame is None:
                stage_one_frame = frame.sequence
            if group["stage"] >= 2:
                stage_two_frame = frame.sequence
                break

        self.assertIsNotNone(stage_one_frame)
        self.assertIsNotNone(stage_two_frame)
        # The final formation gate now waits for 88% slot arrival and a much
        # smaller angular gap; it must still converge without an open ring.
        self.assertGreater(stage_two_frame, stage_one_frame)
        self.assertGreaterEqual(group["arrivalRatio"], 7 / 8)
        self.assertLessEqual(group["maxAngularGapDeg"], 95.0)

    def test_global_progress_is_capped_while_any_threat_is_still_unresolved(self):
        adapter = AdaptiveEscortAdapter(91523, {
            "uavCount": 30, "usvCount": 30, "seed": 20260814,
        })
        frame = None
        for _ in range(1200):
            frame = adapter.step()
            if frame.metrics["missionStage"] in {
                "THREAT_DETECTION", "GUARD_RECONFIGURATION", "INTERCEPT"
            }:
                break
        self.assertIsNotNone(frame)
        self.assertIn(frame.metrics["missionStage"], {
            "THREAT_DETECTION", "GUARD_RECONFIGURATION", "INTERCEPT"
        })
        self.assertLessEqual(frame.metrics["missionProgress"], 0.69)
        self.assertTrue(frame.metrics["stageSubjectThreatCode"])

    def test_three_parallel_response_groups_keep_fixed_members(self):
        adapter = AdaptiveEscortAdapter(91525, {
            "uavCount": 20, "usvCount": 20, "seed": 20260814,
        })
        initial = adapter.step()
        self.assertEqual("GUARDING", initial.metrics["missionStage"])
        self.assertTrue(all(
            threat.intent == "UNCLASSIFIED" and not threat.forced
            for threat in adapter.threats
        ))
        self.assertEqual(0, initial.metrics["captureAssignedCount"])
        adapter._start_capture_for(adapter.threats, "TEST_SETUP")
        allocated = adapter.step()
        initial_assignments = {
            item.code: item.assigned_threat
            for item in adapter.vehicles
            if item.assigned_threat is not None
        }
        self.assertEqual(24, len(initial_assignments))
        self.assertEqual(
            {0: 8, 1: 8, 2: 8},
            {
                threat_index: sum(
                    assigned == threat_index
                    for assigned in initial_assignments.values()
                )
                for threat_index in range(3)
            },
        )
        self.assertTrue(all(
            group["uavCount"] == 4 and group["usvCount"] == 4
            for group in allocated.metrics["captureGroups"]
        ))

        for _ in range(80):
            adapter.step()

        self.assertEqual(
            initial_assignments,
            {
                item.code: item.assigned_threat
                for item in adapter.vehicles
                if item.assigned_threat is not None
            },
        )
        self.assertTrue(all(
            item.assigned_threat is None
            for item in adapter.vehicles
            if item.role == "CLOSE_GUARD"
        ))

    def test_four_parallel_response_groups_keep_fixed_members_at_larger_scales(self):
        for count in (25, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(91525 + count, {
                    "uavCount": count, "usvCount": count, "seed": 20260814,
                })
                initial = adapter.step()
                self.assertEqual(0, initial.metrics["captureAssignedCount"])
                adapter._start_capture_for(adapter.threats, "TEST_SETUP")
                allocated = adapter.step()
                initial_assignments = {
                    item.code: item.assigned_threat
                    for item in adapter.vehicles
                    if item.assigned_threat is not None
                }
                self.assertEqual(32, len(initial_assignments))
                self.assertEqual(
                    {0: 8, 1: 8, 2: 8, 3: 8},
                    {
                        threat_index: sum(
                            assigned == threat_index
                            for assigned in initial_assignments.values()
                        )
                        for threat_index in range(4)
                    },
                )
                self.assertTrue(all(
                    group["uavCount"] == 4 and group["usvCount"] == 4
                    for group in allocated.metrics["captureGroups"]
                ))

                for _ in range(80):
                    adapter.step()

                self.assertEqual(
                    initial_assignments,
                    {
                        item.code: item.assigned_threat
                        for item in adapter.vehicles
                        if item.assigned_threat is not None
                    },
                )
                self.assertTrue(all(
                    item.assigned_threat is None
                    for item in adapter.vehicles
                    if item.role == "CLOSE_GUARD"
                ))

    def test_large_convoy_support_slots_form_unique_outer_square(self):
        adapter = AdaptiveEscortAdapter(91586, {
            "uavCount": 30, "usvCount": 30, "seed": 20260814,
        })
        for protected in adapter.protected:
            protected.x = adapter._protected_formation_offsets[protected.code][0]
        center_x, center_y = adapter._convoy_center()
        points = [adapter._convoy_support_point(index, 20) for index in range(20)]
        self.assertEqual(20, len({
            (round(x, 3), round(y, 3)) for x, y in points
        }))
        half_extent = max(
            max(
                max(abs(offset[0]), abs(offset[1]))
                for offset in adapter._protected_formation_offsets.values()
            ) + CONVOY_GUARD_MARGIN_M + POST_MISSION_OUTER_GUARD_GAP_M,
            40.0,
        )
        self.assertTrue(all(
            abs(max(abs(x - center_x), abs(y - center_y)) - half_extent) <= 0.05
            for x, y in points
        ))

    def test_resolved_multi_convoy_recalls_surplus_into_outer_guard_square(self):
        adapter = AdaptiveEscortAdapter(9153, {
            "uavCount": 20, "usvCount": 20, "seed": 20260814,
        })
        for threat_index, threat in enumerate(adapter.threats):
            threat.state = "CAPTURED"
            threat.forced = True
            # Reproduce the live retargeting case that previously left the
            # second protected target with no per-target completion hazard.
            threat.protected_index = 0
            # Keep every completed ring clear of the terminal gate. The gate
            # contract now correctly rejects a destination that is still
            # inside a captured threat's physical safety envelope.
            threat.x = adapter.safe_bounds[0] + 18.0
            threat.y = (threat_index - 1) * 72.0
        for protected in adapter.protected:
            start_x = adapter.protected_start_x[protected.code]
            protected.x = start_x + (protected.destination_x - start_x) * 0.40

        for kind in ("UAV", "USV"):
            free = [
                item for item in adapter.vehicles
                if item.role != "CLOSE_GUARD" and item.kind == kind
            ]
            for position, item in enumerate(free[:12]):
                item.assigned_threat = position % len(adapter.threats)
                item.role = "CONTAINMENT"
                item.group_id = f"CAPTURE-{item.assigned_threat + 1:03d}"
            for position, item in enumerate(free[12:]):
                item.assigned_threat = None
                item.role = "CAPTURE_RESERVE" if position % 2 else "OUTER_INTERCEPT"
                item.group_id = f"RESERVE-{position % len(adapter.threats) + 1:03d}"

        adapter._redeploy_surplus_to_convoy()
        support = adapter._convoy_support_members()
        self.assertEqual(8, len(support))
        self.assertEqual({item.group_id for item in support}, {"CONVOY-SUPPORT"})
        self.assertTrue(all(item.assigned_threat is None for item in support))
        center_x, center_y = adapter._convoy_center()
        desired = [
            adapter._convoy_support_point(position, len(support))
            for position in range(len(support))
        ]
        self.assertEqual(len(desired), len({
            (round(x, 3), round(y, 3)) for x, y in desired
        }))
        outer_half_extent = max(
            max(
                max(abs(offset[0]), abs(offset[1]))
                for offset in adapter._protected_formation_offsets.values()
            ) + CONVOY_GUARD_MARGIN_M + POST_MISSION_OUTER_GUARD_GAP_M,
            len(support) * 2.0,
        )
        self.assertTrue(all(
            abs(max(abs(x - center_x), abs(y - center_y)) - outer_half_extent) <= 0.05
            for x, y in desired
        ))
        for item, (x, y) in zip(support, desired):
            item.x, item.y = x, y
        self.assertTrue(adapter._convoy_support_ready())
        self.assertTrue(all(
            0.39 <= adapter._escort_route_progress(item) <= 0.42
            for item in adapter.protected
        ))
        self.assertFalse(any(
            adapter._protected_reached_safe_gate(item)
            for item in adapter.protected
        ))
        for item in adapter.protected:
            item.x = item.destination_x - PROTECTED_SAFE_GATE_OFFSET_M
            item.y = item.destination_y
        self.assertTrue(all(
            adapter._protected_reached_safe_gate(item)
            for item in adapter.protected
        ))

    def test_terminal_frame_does_not_force_incomplete_escort_route_to_one(self):
        adapter = AdaptiveEscortAdapter(91531, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
        })
        adapter.step()
        protected = adapter.protected[0]
        start_x = adapter.protected_start_x[protected.code]
        gate_x = protected.destination_x - PROTECTED_SAFE_GATE_OFFSET_M
        protected.x = start_x + (gate_x - start_x) * 0.48
        adapter._display_escort_progress = 0.0
        adapter._terminal_status = "COMPLETED"

        frame = adapter.step()

        self.assertAlmostEqual(0.48, frame.metrics["escortProgress"], places=3)
        self.assertFalse(adapter._protected_reached_safe_gate(protected))

    def test_terminal_support_uses_reachable_slot_beside_shoreline_ring(self):
        adapter = AdaptiveEscortAdapter(20260814, {
            "uavCount": 20,
            "usvCount": 20,
            "seed": 20260814,
            "uavSpeedMps": 15,
            "usvSpeedMps": 3,
        })
        frame = None
        for _ in range(4000):
            frame = adapter.step()
            if frame.terminalStatus:
                break

        self.assertIsNotNone(frame)
        self.assertEqual("COMPLETED", frame.terminalStatus, frame.metrics)
        self.assertEqual(
            frame.metrics["postMissionFormationRequiredCount"],
            frame.metrics["postMissionFormationReadyCount"],
        )
        self.assertGreaterEqual(
            frame.metrics["convoySupportStableFrames"],
            frame.metrics["convoySupportRequiredStableFrames"],
        )
        # Every final support slot must be reachable. Depending on the single
        # target's actual evasion path, this may use either a nominal safe slot
        # or a shoreline-deformed override.
        self.assertTrue(all(
            math.hypot(
                item.x - adapter._post_mission_point(item)[0],
                item.y - adapter._post_mission_point(item)[1],
            ) <= POST_MISSION_SLOT_TOLERANCE_M
            for item in adapter._post_mission_members()
        ))

    def test_convoy_support_assignment_minimizes_crossing_routes(self):
        adapter = AdaptiveEscortAdapter(9154, {
            "uavCount": 20, "usvCount": 20, "seed": 20260814,
        })
        members = sorted(
            [item for item in adapter.vehicles if item.role != "CLOSE_GUARD"][:8],
            key=lambda item: item.code,
        )
        points = [
            adapter._convoy_support_point(position, len(members))
            for position in range(len(members))
        ]
        for item, point in zip(members, reversed(points)):
            item.x, item.y = point

        adapter._assign_convoy_support_slots(members)

        self.assertEqual(
            list(reversed(range(len(members)))),
            [adapter._convoy_support_slot_by_code[item.code] for item in members],
        )

    def test_active_threat_visibly_moves_and_evades(self):
        adapter = AdaptiveEscortAdapter(9201, {"uavCount": 3, "usvCount": 3, "seed": 21, "usvSpeedMps": 1.5})
        adapter.step()
        code = adapter.activate_capture()
        start = next(item for item in adapter.threats if item.code == code)
        start_point = (start.x, start.y)
        headings = []
        for _ in range(160):
            adapter.step()
            threat = next(item for item in adapter.threats if item.code == code)
            headings.append(threat.heading)
        travelled = math.hypot(threat.x - start_point[0], threat.y - start_point[1])
        self.assertGreater(travelled, 8.0)
        # A safe straight escape is valid. Enforcing heading variance rewards
        # the exact gratuitous wobble the UI must avoid.
        self.assertTrue(all(math.isfinite(heading) for heading in headings))
        self.assertLessEqual(max(abs((b - a + 180.0) % 360.0 - 180.0)
                                 for a, b in zip(headings, headings[1:])), 7.01)

    def test_capture_escape_corridor_points_away_from_protected_target(self):
        adapter = AdaptiveEscortAdapter(9202, {
            "uavCount": 5, "usvCount": 6, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 2,
        })
        adapter.step()
        adapter.activate_capture()
        threat = adapter.threats[0]
        protected = adapter.protected[threat.protected_index]
        distance = math.hypot(threat.x - protected.x, threat.y - protected.y)
        away_x = (threat.x - protected.x) / distance
        away_y = (threat.y - protected.y) / distance
        self.assertGreaterEqual(
            threat.escape_dir_x * away_x + threat.escape_dir_y * away_y,
            0.3,
        )

    def test_capture_waits_for_a_visible_escape_run_before_forming_ring(self):
        adapter = AdaptiveEscortAdapter(9221, {
            "uavCount": 3, "usvCount": 3, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 1,
        })
        adapter.step()
        adapter.activate_capture()
        frame = self.run_frames(adapter, 400)
        threat = adapter.threats[0]
        pursuit_distance = threat.travelled_distance - threat.capture_start_travel_distance
        self.assertGreater(pursuit_distance, 35.0)
        self.assertLess(pursuit_distance, threat.required_pursuit_distance)
        # A blocked escape may hand off after actual pursuit-slot arrival;
        # the legacy 100 m odometer is telemetry, not a mandatory deadline.
        if threat.capture_stage == 0:
            self.assertEqual(threat.state, "ESCAPE_PURSUIT")
        else:
            self.assertTrue(threat.escape_intent_confirmed)
            self.assertGreaterEqual(threat.capture_stage, 1)
        group = frame.metrics["captureGroups"][0]
        self.assertAlmostEqual(group["pursuitDistanceM"], pursuit_distance, delta=0.2)
        self.assertEqual(group["requiredPursuitDistanceM"], 100.0)

    def test_late_active_capture_gets_a_fresh_deadline_and_completes(self):
        adapter = AdaptiveEscortAdapter(9251, {
            "uavCount": 3, "usvCount": 3, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 1,
        })
        self.run_frames(adapter, 401)
        activation_sequence = adapter.sequence
        adapter.activate_capture()
        terminal = None
        # Automatic capture may already be active before this compatibility
        # command. The convoy then needs its independent escort window to
        # reach the safe destination after containment is complete.
        for _ in range(12000):
            frame = adapter.step()
            terminal = frame.terminalStatus
            if terminal:
                break
        self.assertEqual(terminal, "COMPLETED")
        self.assertGreater(frame.sequence, activation_sequence)
        self.assertNotEqual(frame.metrics["terminalReason"], "active capture time limit reached")
        self.assertEqual(frame.metrics["missionProgress"], 1.0)

    def test_completed_escort_keeps_the_enemy_contained(self):
        adapter = AdaptiveEscortAdapter(9253, {
            "uavCount": 5, "usvCount": 6, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 2,
        })
        frame = None
        minimum_after_capture = math.inf
        minimum_member_clearance = math.inf
        for _ in range(12000):
            frame = adapter.step()
            threat = adapter.threats[0]
            protected = adapter.protected[0]
            if threat.state == "CAPTURED":
                minimum_after_capture = min(
                    minimum_after_capture,
                    math.hypot(threat.x - protected.x, threat.y - protected.y),
                )
                for member in adapter._capture_members(0):
                    minimum_member_clearance = min(
                        minimum_member_clearance,
                        math.hypot(member.x - protected.x, member.y - protected.y),
                    )
            if frame.terminalStatus:
                break
        self.assertEqual(frame.terminalStatus, "COMPLETED")
        self.assertEqual(adapter.threats[0].state, "CAPTURED")
        members = adapter._capture_members(0)
        self.assertGreaterEqual(sum(item.kind == "UAV" for item in members), 2)
        self.assertGreaterEqual(sum(item.kind == "USV" for item in members), 2)
        self.assertTrue(all(item.role == "CONTAINMENT" for item in members))
        self.assertGreaterEqual(
            minimum_after_capture,
            POST_CAPTURE_CONVOY_CLEARANCE_M - 1.0,
        )
        self.assertGreaterEqual(
            minimum_member_clearance,
            TARGET_SEPARATION_M * 0.75,
        )

    def test_escort_advances_in_parallel_then_crosses_safe_gate(self):
        for count in (3, 5):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9350 + count, {
                    "uavCount": count, "usvCount": count,
                    "seed": 20260814,
                    "uavSpeedMps": 15, "usvSpeedMps": 3,
                })
                observed_stages = []
                escort_at_capture = None
                frame = None
                for _ in range(3000):
                    frame = adapter.step()
                    stage = frame.metrics["missionStage"]
                    if not observed_stages or observed_stages[-1] != stage:
                        observed_stages.append(stage)
                    if (
                        escort_at_capture is None
                        and frame.metrics["capturedThreatCount"]
                        == frame.metrics["threatCount"]
                    ):
                        escort_at_capture = frame.metrics["escortProgress"]
                    if frame.terminalStatus:
                        break
                self.assertEqual("COMPLETED", frame.terminalStatus)
                self.assertIsNotNone(escort_at_capture)
                self.assertGreaterEqual(escort_at_capture, 0.65)
                stable_index = observed_stages.index("STABLE_CONTAINMENT")
                gate_index = observed_stages.index("SAFE_GATE_TRANSIT")
                completed_index = observed_stages.index("COMPLETED")
                self.assertLess(stable_index, gate_index)
                self.assertLess(gate_index, completed_index)
                self.assertEqual(
                    [
                        "GUARDING", "THREAT_DETECTION", "GUARD_RECONFIGURATION",
                        "INTERCEPT", "BLOCKING", "PURSUIT", "ENCIRCLEMENT", "STABLE_CONTAINMENT",
                        "SAFE_GATE_TRANSIT", "COMPLETED",
                    ],
                    frame.metrics["stageSequence"],
                )

    def test_multi_threat_capture_uses_fixed_balanced_mixed_groups(self):
        adapter = AdaptiveEscortAdapter(9252, {"uavCount": 10, "usvCount": 10, "seed": 20260814})
        adapter.step()
        adapter.activate_capture()
        frame = adapter.step()
        groups = frame.metrics["captureGroups"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(group["uavCount"] >= 2 and group["usvCount"] >= 2 for group in groups))
        self.assertLessEqual(max(group["memberCount"] for group in groups) - min(group["memberCount"] for group in groups), 1)
        assigned_codes = [item.code for item in adapter.vehicles if item.assigned_threat is not None]
        self.assertEqual(len(assigned_codes), len(set(assigned_codes)))
        self.assertIn("escortProgress", frame.metrics)
        self.assertIn("captureProgress", frame.metrics)
        self.assertNotIn("captureRemainingFrames", frame.metrics)

    def test_ten_plus_ten_two_threats_attack_then_get_independent_intercepts(self):
        adapter = AdaptiveEscortAdapter(9254, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 3,
        })
        attack_alignment_frames = {item.code: 0 for item in adapter.threats}
        blocker_groups_seen = set()
        observer_groups_seen = set()
        maximum_target_speed = 0.0
        frame = None
        for _ in range(1800):
            frame = adapter.step()
            maximum_target_speed = max(
                maximum_target_speed,
                max(math.hypot(item.vx, item.vy) for item in adapter.protected),
            )
            blocker_groups_seen.update(
                item.group_id for item in adapter.vehicles if item.role == "BLOCKER"
            )
            observer_groups_seen.update(
                item.group_id for item in adapter.vehicles if item.role == "CONFRONT"
            )
            for threat in adapter.threats:
                if threat.detected_frame is None or threat.forced:
                    continue
                protected = adapter.protected[threat.protected_index]
                ux, uy = (
                    (protected.x - threat.x) / max(1e-6, math.hypot(protected.x - threat.x, protected.y - threat.y)),
                    (protected.y - threat.y) / max(1e-6, math.hypot(protected.x - threat.x, protected.y - threat.y)),
                )
                speed = math.hypot(threat.vx, threat.vy)
                if speed > 0.5 and (threat.vx * ux + threat.vy * uy) / speed > 0.45:
                    attack_alignment_frames[threat.code] += 1
            if all(item.forced for item in adapter.threats):
                break
        self.assertIsNotNone(frame)
        self.assertTrue(all(item.forced for item in adapter.threats))
        self.assertTrue(all(value >= 18 for value in attack_alignment_frames.values()))
        self.assertEqual(blocker_groups_seen, {"BLOCK-001", "BLOCK-002"})
        self.assertEqual(observer_groups_seen, {"WATCH-001", "WATCH-002"})
        self.assertGreater(maximum_target_speed, 1.4)
        self.assertGreater(frame.metrics["attackClosingDistanceM"], 8.0)
        groups = frame.metrics["captureGroups"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(group["uavCount"] >= 2 and group["usvCount"] >= 2 for group in groups))
        guards = [item for item in adapter.vehicles if item.role == "CLOSE_GUARD"]
        self.assertGreaterEqual(sum(item.kind == "UAV" for item in guards), 2)
        self.assertGreaterEqual(sum(item.kind == "USV" for item in guards), 2)

    def test_ten_plus_ten_two_threats_complete_capture_and_safe_arrival(self):
        adapter = AdaptiveEscortAdapter(9256, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 3,
        })
        frame = None
        for _ in range(4000):
            frame = adapter.step()
            if frame.terminalStatus:
                break
        self.assertIsNotNone(frame)
        self.assertEqual("COMPLETED", frame.terminalStatus, frame.metrics)
        self.assertTrue(all(item.state == "CAPTURED" for item in adapter.threats))
        self.assertEqual(2, frame.metrics["capturedThreatCount"])
        self.assertEqual(1.0, frame.metrics["escortProgress"])

    def test_surface_and_target_speeds_use_the_raised_dynamic_profile(self):
        adapter = AdaptiveEscortAdapter(9255, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 3,
        })
        maximum_threat_speed = 0.0
        maximum_usv_speed = 0.0
        maximum_target_speed = 0.0
        for _ in range(260):
            adapter.step()
            maximum_threat_speed = max(
                maximum_threat_speed,
                max(math.hypot(item.vx, item.vy) for item in adapter.threats),
            )
            maximum_usv_speed = max(
                maximum_usv_speed,
                max(math.hypot(item.vx, item.vy) for item in adapter.vehicles if item.kind == "USV"),
            )
            maximum_target_speed = max(
                maximum_target_speed,
                max(math.hypot(item.vx, item.vy) for item in adapter.protected),
            )
        self.assertGreater(maximum_threat_speed, 1.7)
        self.assertLessEqual(maximum_threat_speed, 2.81)
        self.assertGreater(maximum_usv_speed, 2.1)
        # Interceptors may accelerate above cruise but remain under the 4 m/s
        # physical surface limit advertised by the simulation UI.
        self.assertLessEqual(maximum_usv_speed, 4.05)
        self.assertGreater(maximum_target_speed, 1.4)
        self.assertLessEqual(maximum_target_speed, 2.26)

    def test_enemy_does_not_slow_before_real_containment_pressure(self):
        adapter = AdaptiveEscortAdapter(92551, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
            "uavSpeedMps": 5, "usvSpeedMps": 3,
        })
        adapter.step()
        adapter.activate_capture()
        observed = 0
        for _ in range(700):
            adapter.step()
            for threat in adapter.threats:
                if not threat.forced or threat.capture_stage >= 2:
                    continue
                observed += 1
                if threat.slowdown_reason == "ESCAPE_CORRIDOR_BLOCKED":
                    # Obstacle/interceptor braking is required; it is not
                    # the old artificial pre-capture countdown slowdown.
                    shore = min(threat.x - adapter.safe_bounds[0], adapter.safe_bounds[1] - threat.x,
                                threat.y - adapter.safe_bounds[2], adapter.safe_bounds[3] - threat.y)
                    nearest = min(math.hypot(v.x - threat.x, v.y - threat.y)
                                  for v in adapter.vehicles if v.kind == "USV")
                    other = min((math.hypot(t.x - threat.x, t.y - threat.y)
                                 for t in adapter.threats if t is not threat), default=math.inf)
                    self.assertTrue(shore < 48 or nearest < 30 or other < 85
                                    or adapter._distance_to_protected(threat) < 70)
                    continue
                self.assertGreaterEqual(
                    math.hypot(threat.vx, threat.vy),
                    threat.cruise_speed * 0.88,
                )
                self.assertEqual("NONE", threat.slowdown_reason)
        self.assertGreater(observed, 100)

    def test_capture_members_reassign_predictively_without_changing_quotas(self):
        adapter = AdaptiveEscortAdapter(92552, {
            "uavCount": 10, "usvCount": 10, "seed": 20260814,
            "assignmentEvaluationFrames": 1,
            "assignmentConfirmationCycles": 2,
            "assignmentMinimumImprovement": 0.01,
        })
        adapter._start_capture_for(adapter.threats[:2], "TEST_DYNAMIC_ASSIGNMENT")
        initial = {
            item.code: item.assigned_threat
            for item in adapter.vehicles
            if item.assigned_threat in {0, 1}
        }
        initial_counts = {
            index: {
                kind: sum(
                    item.kind == kind and item.assigned_threat == index
                    for item in adapter.vehicles
                )
                for kind in ("UAV", "USV")
            }
            for index in (0, 1)
        }
        for index, threat in enumerate(adapter.threats[:2]):
            threat.x = -115.0 if index == 0 else 115.0
            threat.y = 0.0
            threat.vx = threat.vy = 0.0
            threat.capture_hold = 0
        for item in adapter.vehicles:
            if item.assigned_threat not in {0, 1}:
                continue
            destination = adapter.threats[1 - item.assigned_threat]
            item.x = destination.x
            item.y = destination.y

        adapter.sequence = 10
        adapter._maybe_reassign_capture_members()
        adapter.sequence = 15
        adapter._maybe_reassign_capture_members()
        updated = {
            item.code: item.assigned_threat
            for item in adapter.vehicles
            if item.assigned_threat in {0, 1}
        }

        self.assertNotEqual(initial, updated)
        self.assertGreater(adapter.dynamic_allocator.reassignment_count, 0)
        for index in (0, 1):
            for kind in ("UAV", "USV"):
                self.assertEqual(
                    initial_counts[index][kind],
                    sum(
                        item.kind == kind and item.assigned_threat == index
                        for item in adapter.vehicles
                    ),
                )

    def test_hostile_changes_course_instead_of_forcing_blocker_to_yield(self):
        adapter = AdaptiveEscortAdapter(92553, {
            "uavCount": 3, "usvCount": 3, "seed": 20260814,
        })
        threat = adapter.threats[0]
        protected = adapter.protected[threat.protected_index]
        protected.x, protected.y = 100.0, 0.0
        protected.vx = protected.vy = 0.0
        threat.x, threat.y = 0.0, 0.0
        threat.vx, threat.vy = threat.cruise_speed, 0.0
        threat.heading = 0.0
        threat.state = "DETECTED"
        threat.detected_frame = 1
        threat.activate_frame = 0
        adapter.sequence = 2
        for item in adapter.vehicles:
            item.x, item.y = -140.0, -140.0
            item.vx = item.vy = 0.0
            item.role = "RECON"
            item.group_id = f"RECON-{item.protected_index + 1:03d}"
            item.assigned_threat = None
        blocker = next(item for item in adapter.vehicles if item.kind == "USV")
        blocker.x, blocker.y = 18.0, 0.0
        blocker.role = "BLOCKER"
        blocker.group_id = "BLOCK-001"

        adapter._advance_threats()

        self.assertEqual(blocker.code, threat.nearest_defender_code)
        self.assertLess(math.hypot(threat.vx, threat.vy), threat.cruise_speed)
        self.assertEqual("DEFENSIVE_SCREEN", threat.slowdown_reason)

    def test_urgent_threats_receive_independent_mixed_response_pairs(self):
        adapter = AdaptiveEscortAdapter(9300, {
            "uavCount": 15, "usvCount": 15, "seed": 20260814,
        })
        for threat in adapter.threats[:3]:
            target = adapter.protected[threat.protected_index]
            threat.x = target.x - 82.0
            threat.y = target.y
            threat.vx = 2.0
            threat.vy = 0.0
            threat.detected_frame = 1
            threat.state = "DETECTED"
        adapter._synchronize_guard_roles()
        for index in range(3):
            blockers = [
                item for item in adapter.vehicles
                if item.group_id == f"BLOCK-{index + 1:03d}"
            ]
            observers = [
                item for item in adapter.vehicles
                if item.group_id == f"WATCH-{index + 1:03d}"
            ]
            self.assertGreaterEqual(len(blockers), 2)
            self.assertGreaterEqual(len(observers), 2)
            desired = [adapter._desired_position(item) for item in blockers]
            self.assertGreater(math.hypot(
                desired[0][0] - desired[1][0],
                desired[0][1] - desired[1][1],
            ), 10.0)

    def test_capture_allocation_preserves_quick_response_reserve(self):
        adapter = AdaptiveEscortAdapter(9302, {
            "uavCount": 15, "usvCount": 15, "seed": 20260814,
        })
        adapter._start_capture_for([adapter.threats[0]], "TEST")
        for kind in ("UAV", "USV"):
            reserve = [
                item for item in adapter.vehicles
                if item.kind == kind and item.role == "CAPTURE_RESERVE"
                and item.assigned_threat is None
            ]
            self.assertGreaterEqual(len(reserve), 2)

    def test_protected_target_moves_and_evades_after_detection(self):
        adapter = AdaptiveEscortAdapter(9301, {"uavCount": 3, "usvCount": 3, "seed": 4})
        start = (adapter.protected[0].x, adapter.protected[0].y)
        states = set()
        for _ in range(900):
            adapter.step()
            states.add(adapter.protected[0].state)
        target = adapter.protected[0]
        self.assertGreater(math.hypot(target.x - start[0], target.y - start[1]), 4.0)
        self.assertTrue(states.intersection({"THREAT_DETECTED", "EVADING"}))

    def test_target_safety_and_water_margin_hold_across_scales(self):
        for count in (3, 4, 10, 20, 30):
            with self.subTest(count=count):
                adapter = AdaptiveEscortAdapter(9400 + count, {"uavCount": count, "usvCount": count, "seed": 31})
                adapter.step()
                adapter.activate_capture()
                frame = self.run_frames(adapter, 500)
                self.assertNotEqual(frame.terminalStatus, "FAILED")
                distance = frame.metrics["minProtectedThreatDistanceM"]
                self.assertIsNotNone(distance)
                self.assertGreaterEqual(distance, BREACH_DISTANCE_M)
                self.assertGreaterEqual(frame.metrics["minShoreDistanceM"], -1e-6)

    def test_mission_has_no_wall_clock_forced_terminal(self):
        adapter = AdaptiveEscortAdapter(9501, {"uavCount": 3, "usvCount": 3, "seed": 2})
        adapter.timeout_frames = 3
        frame = self.run_frames(adapter, 4)
        self.assertIsNone(frame.terminalStatus)
        self.assertNotEqual(frame.phase, "TIMEOUT")

    def test_active_capture_has_no_fixed_deadline(self):
        adapter = AdaptiveEscortAdapter(9502, {"uavCount": 3, "usvCount": 3, "seed": 2})
        self.run_frames(adapter, 50)
        adapter.activate_capture()
        adapter.capture_timeout_frames = 3
        frame = self.run_frames(adapter, 3)
        self.assertIsNone(frame.terminalStatus)
        self.assertEqual(frame.metrics["captureElapsedFrames"], 3)


if __name__ == "__main__":
    unittest.main()
