import pytest
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from app.adapters.command_escort import PROFILE
from runner import build_adapter
from test_shore_departure import shore_config


def departing(threat_count=2):
    adapter = build_adapter('ESCORT_GUARD', 1, shore_config(
        algorithmCommandProfile=PROFILE, threatCount=threat_count))
    adapter.step()
    adapter.set_mission_active(True)
    return adapter


def finish_departure(adapter):
    for _ in range(600):
        frame = adapter.step()
        if frame.metrics['deploymentState'] == 'COMPLETE':
            return frame
    pytest.fail('departure did not complete')


def test_departure_queues_then_executes_once_and_pause_retains_request():
    a = departing()
    pending = a.execute_preset(1, PROFILE, 'first')
    assert pending['status'] == 'WAITING_DEPLOYMENT'
    assert not a.base.sim.pursuit_assignments
    a.set_mission_active(False)
    for _ in range(5): a.step()
    assert a._preset_commands['first']['status'] == 'WAITING_DEPLOYMENT'
    a.set_mission_active(True)
    finish_departure(a)
    frame = a.step()
    assert frame.metrics['presetCommands']['first']['status'] == 'EXECUTED'
    assert frame.metrics['pendingAlgorithmPresets'] == []
    assert next(p for p in frame.agents if p.code == 'UAV-001').assignedTargetCode == 'THREAT-001'
    assert a.execute_preset(1, PROFILE, 'first')['status'] == 'EXECUTED'
    assert not a._pending_presets


def test_latest_conflicting_chase_replaces_old_but_preserves_other_device():
    a = departing()
    a.execute_preset(1, PROFILE, 'old')
    a.execute_preset(2, PROFILE, 'boat')
    a.execute_preset(7, PROFILE, 'new')
    assert a._preset_commands['old']['status'] == 'SUPERSEDED'
    finish_departure(a)
    a.step(); a.step()
    assigned = {p.code: p.assignedTargetCode for p in a.step().agents if p.role == 'PURSUIT'}
    assert assigned == {'USV-001': 'THREAT-001', 'UAV-001': 'THREAT-002'}


def test_bindings_execute_before_waiting_capture_and_independent_groups_survive():
    a = departing()
    a.execute_preset(20, PROFILE, 'capture')
    a.execute_preset(13, PROFILE, 'air')
    a.execute_preset(14, PROFILE, 'sea')
    assert [p['presetId'] for p in a._pending_presets] == [13, 14, 20]
    finish_departure(a)
    # Capture prerequisites are assessed at execution, not while parked.
    for target in a.base.sim.threats: target.state = 'detected'
    a.step(); a.step(); frame = a.step()
    assert all(r['status'] == 'EXECUTED' for r in frame.metrics['presetCommands'].values())
    assert len(a.base.sim.manual_capture_assignments) == 6


def test_invalid_replacement_preserves_pending_and_stop_cancels():
    a = departing(1)
    a.execute_preset(1, PROFILE, 'valid')
    with pytest.raises(ValueError, match='PRESET_TARGET_MISSING'):
        a.execute_preset(7, PROFILE, 'invalid')
    assert [p['commandId'] for p in a._pending_presets] == ['valid']
    a.cancel_pending_presets()
    finish_departure(a)
    assert a.step().metrics['presetCommands']['valid']['status'] == 'CANCELLED'
    assert not a.base.sim.pursuit_assignments
    assert not departing()._pending_presets


def test_deferred_business_failure_is_reported_without_killing_simulation():
    a = departing()
    a.execute_preset(20, PROFILE, 'capture')
    finish_departure(a)
    for target in a.base.sim.threats: target.state = 'undetected'
    result = a.step().metrics['presetCommands']['capture']
    assert result['status'] == 'FAILED'
    assert result['errorCode'] == 'PRESET_CAPTURE_NOT_READY'
    a.execute_preset(1, PROFILE, 'next')
    assert a.step().metrics['presetCommands']['next']['status'] == 'EXECUTED'


def test_real_protocol_accepts_pending_replay_pause_resume_and_stop():
    ref, generation = str(uuid.uuid4()), str(uuid.uuid4())
    config = shore_config(algorithmCommandProfile=PROFILE, threatCount=2)
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / 'runner.py'),
        '--algorithm', 'ESCORT_GUARD', '--run-id', '981235', '--config', json.dumps(config),
        '--command-protocol', 'v1', '--runtime-ref', ref, '--runtime-generation', generation, '--fps', '100'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    events = queue.Queue()
    threading.Thread(target=lambda: [events.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    def read(predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            event = events.get(timeout=max(.01, deadline-time.monotonic()))
            if predicate(event): return event
        pytest.fail('missing protocol event')
    def send(number, action, parameters=None):
        command = dict(protocolVersion='algorithm.command.v1', kind='COMMAND', runtimeRef=ref,
            runtimeGeneration=generation, commandId=str(uuid.uuid4()), commandSequence=number,
            expectedStateVersion=number-1, action=action, parameters=parameters or {})
        process.stdin.write(json.dumps(command)+'\n'); process.stdin.flush()
        result = read(lambda e: e.get('commandId') == command['commandId'] and e.get('status') in {'SUCCEEDED', 'FAILED', 'REJECTED'})
        assert result['status'] == 'SUCCEEDED', result
        return command, result
    try:
        read(lambda e: e.get('kind') == 'RUNTIME_READY')
        send(1, 'START')
        command, pending = send(2, 'ALGORITHM_PRESET', {'presetId': 1, 'presetVersion': PROFILE})
        assert pending['presetExecution']['status'] == 'WAITING_DEPLOYMENT'
        process.stdin.write(json.dumps(command)+'\n'); process.stdin.flush()
        assert read(lambda e: e.get('commandId') == command['commandId'] and e.get('status') == 'SUCCEEDED') == pending
        _, paused = send(3, 'PAUSE')
        assert paused['runtimeState'] == 'PAUSED'
        send(4, 'RESUME')
        new, _ = send(5, 'ALGORITHM_PRESET', {'presetId': 7, 'presetVersion': PROFILE})
        frame = read(lambda e: e.get('event') == 'frame' and e['payload'].get('metrics', {}).get('presetCommands', {}).get(new['commandId'], {}).get('status') == 'EXECUTED')['payload']
        assert frame['metrics']['presetCommands'][command['commandId']]['status'] == 'SUPERSEDED'
        assert frame['metrics']['pendingAlgorithmPresets'] == []
        assert next(a for a in frame['agents'] if a.get('deviceCode') == 'UAV-001')['assignedTargetCode'] == 'THREAT-002'
        _, stopped = send(6, 'STOP')
        assert stopped['runtimeState'] == 'STOPPED'
    finally:
        process.stdin.close()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
