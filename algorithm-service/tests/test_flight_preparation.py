import math
import pytest
from app.adapters.command_escort import CommandEscortAdapter
from runner import build_adapter

PROFILE = "escort-20261027"

def make(seed=42):
    return build_adapter("ESCORT_GUARD", 1, {"algorithmCommandProfile": PROFILE, "threatCount": 2, "seed": seed, "previewEnabled": True})

@pytest.mark.parametrize("unit", [1, 2, 3])
def test_vertical_takeoff_hover_land_before_escort(unit):
    a = make()
    a.set_mission_active(False)
    first = a.step()
    assert first.metrics["startupState"] == "standby"
    assert all(p.z == 0 for p in first.agents)
    initial = {p.code: (p.x, p.y, p.z) for p in first.agents}
    a.execute_preset(21 + (unit - 1)*3, PROFILE, "takeoff")
    frames = [a.step() for _ in range(8)]
    code = f"UAV-{unit:03d}"
    pos = next(p for p in frames[-1].agents if p.code == code)
    assert pos.z > 0
    assert (pos.x, pos.y) == initial[code][:2]
    assert all((p.x,p.y,p.z) == initial[p.code] for p in frames[-1].agents if p.code != code)
    a.execute_preset(22 + (unit - 1)*3, PROFILE, "hover")
    for _ in range(8):
        p = next(p for p in a.step().agents if p.code == code)
        assert (p.x,p.y,p.z) == pytest.approx((pos.x,pos.y,pos.z))
    a.execute_preset(23 + (unit - 1)*3, PROFILE, "land")
    for _ in range(12): frame = a.step()
    p = next(p for p in frame.agents if p.code == code)
    assert (p.x,p.y,p.z) == pytest.approx(initial[code])
    assert all((t.x,t.y,t.z) == (first.targets[i].x,first.targets[i].y,first.targets[i].z) for i,t in enumerate(frame.targets))

def test_staging_line_random_heights_and_h_continuous_assembly():
    a = make()
    b = make(7)
    first = a.step()
    assert len({round(p.x,6) for p in first.agents}) == 1
    assert len({round(p.y,6) for p in first.agents}) == 6
    assert a.base.sim._escort_cruise_altitudes != b.base.sim._escort_cruise_altitudes
    with pytest.raises(ValueError): a.execute_preset(22, PROFILE)
    a.base.start_escort()
    a.set_mission_active(True)
    previous = {p.code:(p.x,p.y,p.z) for p in first.agents}
    for _ in range(2500):
        frame = a.step()
        for p in frame.agents:
            assert math.dist(previous[p.code],(p.x,p.y,p.z)) <= .5 + 1e-7
        previous = {p.code:(p.x,p.y,p.z) for p in frame.agents}
        if frame.metrics["startupState"] == "active": break
    assert frame.metrics["startupState"] == "active"
    with pytest.raises(ValueError): a.execute_preset(21, PROFILE)
    a.execute_preset(1, PROFILE)
    assert a.base.sim.pursuit_assignments
    a.execute_preset(0, PROFILE)
    assert not a.base.sim.pursuit_assignments
