"""Fleet and device commands share the same live runner, including legacy IDs."""
import uuid
import unittest
import test_runner_protocol


class UnifiedControlTests(unittest.TestCase):
    def test_fleet_pause_preserves_device_override(self):
        for algorithm in ['ESCORT_GUARD', 'GB_SFLA_CS', 'ESCORT_GUARD_SINGLE_DEVICE', 'GB_SFLA_CS_SINGLE_DEVICE']:
            with self.subTest(algorithm=algorithm):
                check_fleet_and_device_commands(algorithm)


def check_fleet_and_device_commands(algorithm):
    runner = test_runner_protocol.RunnerProtocolTest()
    runner.algorithm = algorithm
    runner.config = {'uavCount': 3, 'usvCount': 3, 'targetCount': 1, 'seed': 42}
    runner.setUp()
    try:
        ready = runner.read_protocol_event('RUNTIME_READY')
        assert 'DEVICE_COMMAND' in ready['capabilities']
        sequence = 0
        version = 0

        def fleet(action):
            nonlocal sequence, version
            sequence += 1
            cid = str(uuid.uuid4())
            runner.send(runner.command(cid, sequence, version, action))
            result = runner.read_protocol_event('COMMAND_RESULT', commandId=cid, status='SUCCEEDED')
            version = result['stateVersion']
            return result

        def device(command):
            rid = str(uuid.uuid4())
            runner.send({'kind': 'DEVICE_COMMAND', 'requestId': rid,
                         'deviceCode': 'UAV-001', 'commandType': command})
            return runner.read_event(lambda e: e.get('event') == 'deviceCommandResult' and e.get('requestId') == rid)

        assert not device('UAV_HOVER')['success']
        fleet('START')
        assert device('UAV_HOVER')['deviceStatus'] == 'HOLDING'
        paused = fleet('PAUSE')
        assert not device('UAV_RETURN')['success']
        heartbeat = runner.read_protocol_event('HEARTBEAT', runtimeState='PAUSED')
        assert heartbeat['lastFrameSequence'] == paused['lastFrameSequence']
        fleet('RESUME')
        frame = runner.read_event(lambda e: e.get('event') == 'frame')['payload']
        assert next(a for a in frame['agents'] if a['deviceCode'] == 'UAV-001')['status'] == 'HOLDING'
        assert device('UAV_RESUME')['success']
        assert device('UAV_RETURN')['success']
        fleet('STOP')
        assert runner.process.wait(timeout=10) == 0
    finally:
        runner.tearDown()
