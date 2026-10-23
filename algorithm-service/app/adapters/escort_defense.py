"""One fleet-wide defence contract: allocate, align, reverse, hand over.

Counts create slots; they never select a different behaviour implementation.
The capture planner remains responsible for pursuit and the final rings.
"""
from dataclasses import dataclass, field
import math

DT = 0.1
REVERSE_SPEED = 0.8
REVERSE_FRAMES = 40
DEPARTURE_DISTANCE = 8.0


def inner_slots(count):
    """Concentric protected berths with hull-scale spacing, for any count."""
    points, radius = [], 20.0
    while count > 0:
        layer_count = min(count, int(math.tau*radius/14.0))
        points.extend((radius*math.cos(math.tau*i/layer_count),
                       radius*math.sin(math.tau*i/layer_count)) for i in range(layer_count))
        count -= layer_count
        radius += 14.0
    return points


def unit(x, y):
    length = math.hypot(x, y)
    return (1.0, 0.0) if length < 1e-8 else (x / length, y / length)


@dataclass
class Screen:
    index: int
    codes: list[str] = field(default_factory=list)
    phase: str = "ASSEMBLING"
    alignment_frames: int = 0
    reverse_frames: int = 0
    distance: float = 0.0
    slots: dict = field(default_factory=dict)
    origins: dict = field(default_factory=dict)


class EscortDefenseCoordinator:
    def __init__(self, adapter):
        self.a = adapter
        self.screens: dict[int, Screen] = {}
        self.mode = "ESCORT"
        self.retreat = (0.0, 0.0)
        self.reversing = False
        self.released = False
        self.reverse_elapsed = 0
        self.capacity_ok = True
        self.recognition_radius = adapter._rendezvous_radius
        remaining = adapter.plan.uav_count + adapter.plan.usv_count - 4*adapter.plan.threat_count
        self.screen_radius = max(40.0, max((math.hypot(*p) for p in inner_slots(max(0, remaining))), default=20)+18)
        self.inner_berths = {}
        self.withdrawal_origin = None

    def withdrawing(self, v):
        return (self.mode == "WITHDRAWAL" and not self.released
                and v.code in self.inner_berths and v.assigned_threat is None)

    def withdrawal_distance(self):
        if self.withdrawal_origin is None:
            return 0.0
        p = self.a.protected[0]
        return max(0.0, (p.x-self.withdrawal_origin[0])*self.retreat[0]
                   + (p.y-self.withdrawal_origin[1])*self.retreat[1])

    def forward_heading(self, v):
        # Steer before translating. Never turn back to chase a berth behind us.
        p = self.a.protected[0]
        x, y = self.inner_berths[v.code]
        lateral = -(p.x+x-v.x)*self.retreat[1] + (p.y+y-v.y)*self.retreat[0]
        desired = math.degrees(math.atan2(self.retreat[1], self.retreat[0]))
        desired += max(-15.0, min(15.0, lateral*2.0))
        previous = self.a._stable_headings.get(v.code, desired)
        delta = (desired-previous+180)%360-180
        return (previous+max(-3.0, min(3.0, delta)))%360

    def forward_motion(self, v):
        heading = self.forward_heading(v)
        base = math.degrees(math.atan2(self.retreat[1], self.retreat[0]))
        error = abs((heading-base+180)%360-180)
        p = self.a.protected[0]
        x, y = self.inner_berths[v.code]
        along = (p.x+x-v.x)*self.retreat[0] + (p.y+y-v.y)*self.retreat[1]
        desired_speed = max(0.0, min(1.25, p.vx*self.retreat[0]+p.vy*self.retreat[1]+along*.65))
        if error > 20.0:
            desired_speed = 0.0
        previous_speed = max(0.0, v.vx*math.cos(math.radians(heading))+v.vy*math.sin(math.radians(heading)))
        speed = max(0.0, previous_speed+max(-.15, min(.10, desired_speed-previous_speed)))
        return (v.x+math.cos(math.radians(heading))*speed*DT,
                v.y+math.sin(math.radians(heading))*speed*DT, v.z)

    def pending(self):
        return [(i, t) for i, t in enumerate(self.a.threats)
                if not t.forced and t.state not in {"WAITING", "CAPTURED", "SECURED", "ESCAPED"}]

    def recognition_ready(self):
        pending = self.pending()
        return bool(pending) and self.a._escort_departure_distance() >= DEPARTURE_DISTANCE and all(
            self.a._distance_to_protected(t) <= self.recognition_radius + 4.0 for _, t in pending)

    def approach(self, threat):
        a = self.a
        target = a.protected[threat.protected_index]
        dx, dy = target.x - threat.x, target.y - threat.y
        ux, uy = unit(dx, dy)
        if getattr(a, "_shore_assembly_pending", False) and threat.detected_frame is None:
            # The established encounter starts when the escorted hull departs.
            # While its detail is arriving, contacts keep navigating around
            # their original encounter distance with bounded real motion.
            # No target is teleported, frozen, or granted detection by a timer.
            radius = a._shore_encounter_radii[threat.code]
            radial = max(-.8, min(.8, (math.hypot(dx, dy) - radius) / 12.0))
            side = 1.0 if a.threats.index(threat) % 2 == 0 else -1.0
            px, py = unit(ux * radial - uy * side, uy * radial + ux * side)
            return px, py, min(2.2, threat.cruise_speed)
        # Shared time-to-boundary feedback; closer attackers slow down without
        # receiving a fabricated detection event or teleporting onto a circle.
        closure = (threat.cruise_speed if self.screens else
                   max(0.6, min(2.2, (math.hypot(dx, dy) - self.recognition_radius) / 7.0)))
        speed = max(.5, min(2.2, closure + target.vx*ux + target.vy*uy))
        return ux, uy, speed

    def screen_for(self, vehicle):
        if vehicle.assigned_threat is not None:
            return None
        return next((s for s in self.screens.values()
                     if vehicle.code in s.codes and not self.a.threats[s.index].forced), None)

    def _pick_mode(self):
        a = self.a
        target = a.protected[0]
        candidates = []
        for sample in range(48):
            angle = math.tau * sample / 48
            dx, dy = math.cos(angle), math.sin(angle)
            clearance = min((dx * unit(target.x - t.x, target.y - t.y)[0]
                             + dy * unit(target.x - t.x, target.y - t.y)[1]
                             for _, t in self.pending()), default=1.0)
            x, y = target.x + dx * 18, target.y + dy * 18
            left, right, bottom, top = a.safe_bounds
            room = min(x-left, right-x, y-bottom, top-y)
            if clearance >= 0.12 and room >= 55.0:
                candidates.append((clearance, room, dx, dy))
        if candidates:
            _, _, dx, dy = max(candidates)
            self.mode, self.retreat = "WITHDRAWAL", (dx, dy)
        else:
            self.mode, self.retreat = "CENTER_DEFENSE", (0.0, 0.0)

    def synchronize(self):
        a = self.a
        incidents = [(i, t) for i, t in self.pending() if t.detected_frame is not None]
        if incidents and len(incidents) == len(self.pending()) and not self.screens:
            self._pick_mode()
            self.withdrawal_origin = (a.protected[0].x, a.protected[0].y)
            self.screens = {i: Screen(i) for i, _ in incidents}
            # Fill every sector's first berth before assigning its second.
            # Nearest feasible craft win regardless of their former berth.
            # Thus 3 USVs => two blockers + one inner guard.
            for kind, quota in (("USV", 2), ("UAV", min(2, a.plan.uav_count // len(incidents)))):
                for berth in range(quota):
                    remaining = set(self.screens)
                    while remaining:
                        assigned = {code for s in self.screens.values() for code in s.codes}
                        pool = [v for v in a.vehicles if v.kind == kind and v.assigned_threat is None
                                and v.code not in a._unavailable_codes
                                and v.code not in assigned and v.role not in {"CAPTURE", "CONTAINMENT"}]
                        if not pool:
                            self.capacity_ok = False
                            break
                        choices = []
                        for index in remaining:
                            t = a.threats[index]
                            p = a.protected[t.protected_index]
                            ux, uy = unit(t.x-p.x, t.y-p.y)
                            lead = self.screen_radius + (12 if kind == "UAV" else 0)
                            gx, gy = p.x+ux*lead, p.y+uy*lead
                            for v in pool:
                                heading = a._stable_headings.get(v.code, 0.0)
                                facing = math.degrees(math.atan2(t.y-v.y, t.x-v.x))
                                turn = abs((facing-heading+180)%360-180) / 35.0
                                cost = math.hypot(v.x-gx, v.y-gy) / max(.2, a.usv_cruise if kind == "USV" else a.uav_cruise)
                                cost += turn
                                choices.append((cost, v.code, index, v))
                        _, _, index, v = min(choices, key=lambda row: row[:3])
                        s = self.screens[index]
                        s.codes.append(v.code)
                        s.origins[v.code] = (v.x, v.y)
                        v.role = "BLOCKER" if kind == "USV" else "CONFRONT"
                        v.group_id = f"{'BLOCK' if kind == 'USV' else 'WATCH'}-{index+1:03d}"
                        remaining.remove(index)
            # Assign the lateral berths in current angular order, so two
            # neighbouring craft never exchange sides through each other.
            for s in self.screens.values():
                t, p = a.threats[s.index], a.protected[0]
                ux, uy = unit(t.x-p.x, t.y-p.y)
                for kind in ("USV", "UAV"):
                    members = sorted((v for v in a.vehicles if v.code in s.codes and v.kind == kind),
                                     key=lambda v: -(v.x-p.x)*uy+(v.y-p.y)*ux)
                    for slot, v in enumerate(members):
                        lateral = (slot-(len(members)-1)/2) * (18 if kind == "USV" else 22)
                        lead = self.screen_radius+(12 if kind == "UAV" else 0)
                        s.slots[v.code] = (lead, lateral)
            assigned = {c for s in self.screens.values() for c in s.codes}
            inner = [v for v in a.vehicles if v.code not in assigned and v.assigned_threat is None]
            berths = inner_slots(len(inner))
            while inner:
                _, code, berth, v = min((math.hypot(v.x-a.protected[0].x-x, v.y-a.protected[0].y-y),
                                        v.code, i, v) for v in inner for i, (x,y) in enumerate(berths))
                self.inner_berths[code] = berths.pop(berth)
                inner.remove(v)
            if self.mode == "WITHDRAWAL":
                # Preserve each escort's current berth while the convoy turns;
                # rotating/reassigning slots here makes boats cut across the hull.
                self.inner_berths = {v.code: (v.x-a.protected[0].x, v.y-a.protected[0].y)
                                     for v in a.vehicles if v.code in self.inner_berths}
            for kind in ("USV", "UAV"):
                available = sorted((v for v in a.vehicles if v.kind == kind
                                    and v.code not in assigned and v.assigned_threat is None),
                                   key=lambda v: (v.role != "CLOSE_GUARD",
                                                  math.hypot(v.x-a.protected[0].x, v.y-a.protected[0].y)))
                needed = a._guard_count(sum(v.kind == kind for v in a.vehicles))
                for v in available[:needed]:
                    v.role, v.group_id = "CLOSE_GUARD", "GUARD-001"
            a._convoy_guard_slot_by_code = {v.code: i for i, v in enumerate(
                sorted((v for v in a.vehicles if v.role == "CLOSE_GUARD"), key=lambda v: v.code))}
        for v in a.vehicles:
            if v.assigned_threat is None and v.role in {"BLOCKER", "CONFRONT"} and self.screen_for(v) is None:
                v.role, v.group_id = "FORMATION_GUARD", f"ESCORT-{v.kind}"

    def goal(self, v):
        s = self.screen_for(v)
        if self.screens and not self.released and v.code in self.inner_berths and v.assigned_threat is None:
            x,y = self.inner_berths[v.code]
            p = self.a.protected[0]
            return p.x+x, p.y+y, v.z
        if s is None or s.phase == "RELEASED":
            return None
        t, p = self.a.threats[s.index], self.a.protected[0]
        ux, uy = unit(t.x-p.x, t.y-p.y)
        lead, lateral = s.slots[v.code]
        if s.phase in {"REVERSING", "READY"}:
            return (*s.origins[v.code], v.z)
        return (p.x+ux*lead-uy*lateral, p.y+uy*lead+ux*lateral, v.z)

    def heading(self, v):
        if self.withdrawing(v):
            return self.forward_heading(v)
        s = self.screen_for(v)
        if s is None:
            return None
        t = self.a.threats[s.index]
        desired = math.degrees(math.atan2(t.y-v.y, t.x-v.x)) % 360
        previous = self.a._stable_headings.get(v.code, desired)
        delta = (desired-previous+180)%360-180
        return (previous+max(-3.5, min(3.5, delta))) % 360

    def motion(self, v):
        if self.withdrawing(v):
            return self.forward_motion(v)
        s = self.screen_for(v)
        goal = self.goal(v)
        if s is None or goal is None:
            return None
        t = self.a.threats[s.index]
        toward = unit(t.x-v.x, t.y-v.y)
        heading = self.heading(v)
        error = abs((heading-math.degrees(math.atan2(toward[1], toward[0]))+180)%360-180)
        dx, dy = goal[0]-v.x, goal[1]-v.y
        distance = math.hypot(dx, dy)
        if s.phase in {"REVERSING", "READY"}:
            feed = REVERSE_SPEED if s.phase == "REVERSING" else 0.0
            vx, vy = dx*.8-toward[0]*feed, dy*.8-toward[1]*feed
            cap = 1.0
        else:
            cap = min(self.a.usv_cruise if v.kind == "USV" else self.a.uav_cruise, distance*.9)
            if error > 20:
                cap *= max(0.0, 1.0-error/70)
            vx, vy = unit(dx, dy)
            vx, vy = vx*cap, vy*cap
        length = math.hypot(vx, vy)
        if length > cap > 0:
            vx, vy = vx*cap/length, vy*cap/length
        ax, ay = vx-v.vx, vy-v.vy
        acceleration = math.hypot(ax, ay)
        if acceleration > .12:
            ax, ay = ax*.12/acceleration, ay*.12/acceleration
        return (v.x+(v.vx+ax)*DT, v.y+(v.vy+ay)*DT, v.z)

    def advance_convoy(self):
        a = self.a
        if not self.pending() or (self.released and self.mode != "CENTER_DEFENSE"):
            return False
        p = a.protected[0]
        forward_withdrawal = bool(self.screens) and self.mode == "WITHDRAWAL"
        if not self.screens:
            speed = min(1.5, a.usv_cruise*.55) if a._escort_departure_distance() < DEPARTURE_DISTANCE else 0.0
            route_x, route_y = a._escort_route_axis()
            vx, vy = speed * route_x, speed * route_y
            p.state = "ESCORTING"
        else:
            # Keep withdrawing while the screen is being established/reverses.
            # The old 6 m stop completed before slow-arriving guards began
            # reversing, making the two coordinated actions look sequential.
            speed = .65 if forward_withdrawal and self.withdrawal_distance() < 18.0 else 0.0
            vx, vy = self.retreat[0]*speed, self.retreat[1]*speed
            p.state = "CENTER_DEFENSE" if self.mode == "CENTER_DEFENSE" else "COVERED_WITHDRAWAL"
        if forward_withdrawal:
            desired_heading = math.degrees(math.atan2(self.retreat[1], self.retreat[0]))
            delta = (desired_heading-p.heading+180)%360-180
            p.heading = (p.heading+max(-3.0,min(3.0,delta)))%360
            escorts_aligned = all(abs((a._stable_headings.get(v.code, 0)-desired_heading+180)%360-180) <= 20
                                  for v in a.vehicles if v.kind == "USV" and self.withdrawing(v))
            if abs(delta) > 15 or not escorts_aligned:
                speed = 0.0
            radians = math.radians(p.heading)
            prior_speed = max(0.0, p.vx*math.cos(radians)+p.vy*math.sin(radians))
            speed = max(0.0, prior_speed+max(-.15,min(.10,speed-prior_speed)))
            vx, vy = math.cos(radians)*speed, math.sin(radians)*speed
        ax, ay = vx-p.vx, vy-p.vy
        magnitude = math.hypot(ax, ay)
        if magnitude > .10 and not forward_withdrawal:
            ax, ay = ax*.10/magnitude, ay*.10/magnitude
        safe = a.safety.constrain((p.x,p.y,0), (p.x+(p.vx+ax)*DT,p.y+(p.vy+ay)*DT,0), "ESCORT_TARGET", (), 0)
        p.vx, p.vy = (safe.x-p.x)/DT, (safe.y-p.y)/DT
        p.x, p.y = safe.x, safe.y
        if math.hypot(p.vx,p.vy) > .1 and not forward_withdrawal:
            desired = math.degrees(math.atan2(p.vy,p.vx))
            p.heading = (p.heading+max(-3.0,min(3.0,(desired-p.heading+180)%360-180)))%360
        for s in self.screens.values():
            if s.phase == "REVERSING":
                t = a.threats[s.index]
                for code, (x,y) in list(s.origins.items()):
                    ux, uy = unit(t.x-x,t.y-y)
                    s.origins[code] = (x-ux*REVERSE_SPEED*DT,y-uy*REVERSE_SPEED*DT)
        return True

    def observe(self):
        a = self.a
        if self.reversing and not self.released:
            self.reverse_elapsed += 1
            # A blocked screen must reform, never retreat indefinitely through
            # the inner layer or gain completion credit from elapsed time.
            if self.reverse_elapsed > 100 and any(s.phase == "REVERSING" for s in self.screens.values()):
                self.reversing = False
                self.reverse_elapsed = 0
                for s in self.screens.values():
                    s.phase, s.alignment_frames = "ASSEMBLING", 0
                    s.reverse_frames, s.distance = 0, 0.0
                    a.threats[s.index].cover_verified = False
        for s in self.screens.values():
            t = a.threats[s.index]
            if t.forced or s.phase == "RELEASED":
                continue
            members = [v for v in a.vehicles if v.code in s.codes]
            if not t.response_dispatched and members and all(math.hypot(v.x-s.origins[v.code][0],v.y-s.origins[v.code][1]) >= 2 for v in members):
                t.response_dispatched, t.response_motion_frames = True, 5
                a._emit_tactical_event("GUARD_RESPONSE_DISPATCHED",t,"守卫组已出动",
                                      "、".join(s.codes)+" 正在前出建立本方向屏障。")
            valid = (bool(members) and not any(v.code in a._unavailable_codes for v in members)
                     and len([v for v in members if v.kind=="USV"]) >= 2)
            for v in members:
                g = self.goal(v)
                facing = math.degrees(math.atan2(t.y-v.y,t.x-v.x))
                error = abs((a._stable_headings.get(v.code,0)-facing+180)%360-180)
                valid = valid and math.hypot(v.x-g[0],v.y-g[1]) <= 2.0 and error <= 10.0
            if s.phase in {"ASSEMBLING", "ALIGNING"}:
                s.alignment_frames = s.alignment_frames+1 if valid else 0
                if s.alignment_frames >= 5:
                    s.phase = "ALIGNING"
                    t.screen_established, t.intercept_stage_frames = True, 5
                    t.mission_stage = "GUARD_RECONFIGURATION"
                    a._emit_tactical_event("GUARD_SCREEN_ESTABLISHED",t,"分向守卫就位","守卫艇已经朝敌对准，保持攻击通道阻挡。")
            elif s.phase == "REVERSING":
                reverse = []
                for v in members:
                    if v.kind == "USV":
                        ux,uy=unit(t.x-v.x,t.y-v.y)
                        reverse.append(-(v.vx*ux+v.vy*uy))
                valid = valid and bool(reverse) and min(reverse)>=.4
                if valid:
                    s.reverse_frames += 1
                    s.distance += min(reverse)*DT
                else:
                    s.reverse_frames, s.distance = 0, 0.0
                t.cover_frames, t.cover_distance = s.reverse_frames, s.distance
                if s.reverse_frames == 5:
                    a._emit_tactical_event("COVER_RETREAT_OBSERVED",t,"正在倒航守卫","本方向全部守卫艇船头朝敌，持续倒退掩护。")
                if s.reverse_frames >= REVERSE_FRAMES and s.distance >= 2.5:
                    s.phase, t.cover_verified = "READY", True
        if self.screens and self.capacity_ok and not self.reversing and all(s.phase=="ALIGNING" and s.alignment_frames >= 5 for s in self.screens.values()):
            self.reversing=True
            self.reverse_elapsed=0
            for s in self.screens.values():
                s.phase="REVERSING"
                s.origins={v.code:(v.x,v.y) for v in a.vehicles if v.code in s.codes}
                a.threats[s.index].cover_origin=(a.protected[0].x,a.protected[0].y)
        safe_center = all(a._distance_to_protected(t) >= 52.0 for _, t in self.pending())
        withdrawal_verified = self.mode != "WITHDRAWAL" or self.withdrawal_distance() >= 4.0
        if self.screens and not self.released and safe_center and withdrawal_verified and all(s.phase=="READY" for s in self.screens.values()):
            self.released=True
            for s in self.screens.values():
                s.phase="RELEASED"
                t=a.threats[s.index]
                t.cover_released=True
                t.mission_stage="INTERCEPT"
                a._emit_tactical_event("DEFENSE_HANDOFF_CONFIRMED",t,"分向守卫完成","所有攻击方向完成倒航守卫，转入前出拦截；保留内层保护。")

    def metrics(self):
        return {"mode":self.mode,"capacityReady":self.capacity_ok,
                "withdrawalDistanceM":round(self.withdrawal_distance(),2),
                "directions":len(self.screens),"readyDirections":sum(s.phase in {"READY","RELEASED"} for s in self.screens.values()),
                "released":self.released,
                "screens":[{"threatCode":self.a.threats[s.index].code,"phase":s.phase,"members":s.codes,
                            "reverseSeconds":round(s.reverse_frames*DT,1),"reverseDistanceM":round(s.distance,2)}
                           for s in self.screens.values()]}
