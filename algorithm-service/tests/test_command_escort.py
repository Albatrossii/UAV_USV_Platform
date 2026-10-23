import copy
import json
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from app.adapters.command_escort import CommandEscortAdapter, PROFILE, source
from runner import build_adapter


@pytest.mark.parametrize('number', range(1, 19))
def test_presets_match_vendor_semantics(number):
    adapter = CommandEscortAdapter(1, {'threatCount': 2})
    adapter.execute_preset(number, PROFILE)
    words = source.PRESET_CAPTURE_COMMANDS[str(number)].split()
    frame = adapter.step()
    if number <= 12:
        agent = next(a for a in frame.agents if a.code == adapter.code_for(words[1]))
        assert agent.role == 'PURSUIT'
        assert agent.assignedTargetCode == f'THREAT-{int(words[2][1:]):03d}'
    else:
        assert not adapter.sim.gbsflacs_active
        assert set(frame.metrics['fixedAssignments']) == {adapter.code_for(p) for p in words[2:]}


def test_rejection_does_not_replace_existing_pursuit():
    a = CommandEscortAdapter(1, {'threatCount': 1})
    a.execute_preset(1, PROFILE)
    before = dict(a.sim.pursuit_assignments)
    with pytest.raises(ValueError): a.execute_preset(7, PROFILE)
    assert a.sim.pursuit_assignments == before
    with pytest.raises(ValueError, match='PRESET_CAPTURE_NOT_READY'): a.execute_preset(20, PROFILE)
    assert a.sim.pursuit_assignments == before
    a.execute_preset(0, PROFILE)
    assert not a.sim.pursuit_assignments and a.sim.escort_only
    a.execute_preset(19, PROFILE)
    assert not a.sim.escort_only


def test_configured_speed_is_respected_in_scene_coordinates():
    a = CommandEscortAdapter(1, {'threatCount': 2, 'uavSpeedMps': 1.0, 'usvSpeedMps': .5})
    a.set_mission_active(True)
    a.execute_preset(1, PROFILE)
    previous = {p.identifier: a.to_scene(p.position).copy() for p in a.sim.platforms}
    for _ in range(30):
        a.step()
        for p in a.sim.platforms:
            current = a.to_scene(p.position)
            assert sum((current - previous[p.identifier]) ** 2) ** .5 <= (1.0 if p.kind == 'UAV' else .5) * .1 + 1e-8
            previous[p.identifier] = current.copy()


@pytest.mark.parametrize('include_protected_pose', [False, True])
def test_global_enu_sea_level_preserves_fleet_origin_altitude(include_protected_pose):
    config = {'threatCount': 2, 'fleetOrigin': {'eastM': 100, 'northM': 200, 'upM': 7.5},
              'initialPosesCoordinateFrame': 'GLOBAL_ENU',
              'initialPoses': [{'deviceCode': 'USV-001', 'eastM': 120, 'northM': 230, 'upM': 0}]}
    if include_protected_pose:
        config['initialPoses'].append({'deviceCode': 'PROTECTED-001', 'eastM': 110, 'northM': 220, 'upM': 0})
    a = CommandEscortAdapter(1, config)
    frame = a.step()
    assert all(agent.z == -7.5 for agent in frame.agents if agent.type == 'USV')
    assert all(target.z == -7.5 for target in frame.targets)
    boat = next(agent for agent in frame.agents if agent.code == 'USV-001')
    assert (boat.x, boat.y, boat.z) == pytest.approx((20, 30, -7.5))


def test_capture_starts_only_after_detection_and_keeps_binding():
    a = CommandEscortAdapter(1, {'threatCount': 2})
    a.execute_preset(13, PROFILE)
    a.execute_preset(14, PROFILE)
    for target in a.sim.threats: target.state = 'detected'
    a.execute_preset(20, PROFILE)
    assert a.sim.gbsflacs_active
    assert len(a.sim.manual_capture_assignments) == 6
    a.set_mission_active(True)
    for _ in range(3):
        f = a.step()
        assert all(agent.z == 0 for agent in f.agents if agent.type == 'USV')


def test_shore_release_handoff_and_operator_override():
    from test_shore_departure import shore_config
    config = shore_config(algorithmCommandProfile=PROFILE, threatCount=2)
    a = build_adapter('ESCORT_GUARD', 1, config)
    a.step()
    a.set_mission_active(True)
    for _ in range(500):
        f = a.step()
        if f.metrics['deploymentState'] == 'COMPLETE': break
    assert f.metrics['deploymentState'] == 'COMPLETE'
    a.execute_preset(1, PROFILE)
    a.control_device('UAV-001', 'UAV_HOVER')
    with pytest.raises(ValueError, match='PRESET_DEVICE_OVERRIDE'): a.execute_preset(7, PROFILE)
    f = a.step()
    assert f.metrics['algorithmCommandProfile'] == PROFILE


def test_real_runner_idempotency_and_context_version():
    ref, generation = str(uuid.uuid4()), str(uuid.uuid4())
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1]/'runner.py'),
        '--algorithm','ESCORT_GUARD','--run-id','981234','--config',json.dumps({'algorithmCommandProfile':PROFILE,'threatCount':2}),
        '--command-protocol','v1','--runtime-ref',ref,'--runtime-generation',generation],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    events = queue.Queue()
    threading.Thread(target=lambda: [events.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    def read(predicate):
        for _ in range(200):
            event = events.get(timeout=25)
            if predicate(event): return event
        raise AssertionError('missing result')
    def send(number, version, action, parameters):
        body = dict(protocolVersion='algorithm.command.v1', kind='COMMAND', runtimeRef=ref, runtimeGeneration=generation,
                    commandId=str(uuid.uuid4()), commandSequence=number, expectedStateVersion=version, action=action, parameters=parameters)
        process.stdin.write(json.dumps(body)+'\n'); process.stdin.flush()
        return body
    try:
        ready = read(lambda e:e.get('kind')=='RUNTIME_READY')
        assert 'ALGORITHM_PRESET' in ready['capabilities']
        send(1,0,'START',{})
        read(lambda e:e.get('status')=='SUCCEEDED')
        cmd = send(2,1,'ALGORITHM_PRESET',{'presetId':13,'presetVersion':PROFILE})
        result = read(lambda e:e.get('commandId')==cmd['commandId'] and e.get('status')=='SUCCEEDED')
        process.stdin.write(json.dumps(cmd)+'\n'); process.stdin.flush()
        replay = read(lambda e:e.get('commandId')==cmd['commandId'] and e.get('status')=='SUCCEEDED')
        assert replay == result and result['runtimeState']=='RUNNING' and result['stateVersion']==2
        bad = send(3,1,'ALGORITHM_PRESET',{'presetId':0,'presetVersion':PROFILE})
        assert read(lambda e:e.get('commandId')==bad['commandId'])['errorCode']=='STATE_VERSION_MISMATCH'
    finally:
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
