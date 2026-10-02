import json
import subprocess
import sys
import unittest
import uuid
from pathlib import Path


class RunnerProtocolTest(unittest.TestCase):
    algorithm = "GB_SFLA_CS"
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        runtime_ref = str(uuid.uuid4())
        generation = str(uuid.uuid4())
        config = json.dumps(getattr(self, 'config', {"uavCount": 1, "usvCount": 1, "targetCount": 1, "seed": 42}))
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(root / "runner.py"),
                "--algorithm", self.algorithm,
                "--run-id", "990026",
                "--config", config,
                "--fps", "20",
                "--command-protocol", "v1",
                "--runtime-ref", runtime_ref,
                "--runtime-generation", generation,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.runtime_ref = runtime_ref
        self.generation = generation

    def tearDown(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()

    def read_event(self, predicate):
        while True:
            line = self.process.stdout.readline()
            if not line:
                self.fail("Runner exited before emitting the expected event")
            event = json.loads(line)
            if predicate(event):
                return event

    def send(self, command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()

    def send_raw(self, value):
        self.process.stdin.write(value + "\n")
        self.process.stdin.flush()

    def command(self, command_id, sequence, state_version, action, **overrides):
        value = {
            "protocolVersion": "algorithm.command.v1",
            "runtimeRef": self.runtime_ref,
            "runtimeGeneration": self.generation,
            "kind": "COMMAND",
            "commandId": command_id,
            "commandSequence": sequence,
            "expectedStateVersion": state_version,
            "action": action,
            "parameters": {},
        }
        value.update(overrides)
        return value

    def status_query(self, command_id, **overrides):
        value = {
            "protocolVersion": "algorithm.command.v1",
            "runtimeRef": self.runtime_ref,
            "runtimeGeneration": self.generation,
            "kind": "STATUS_QUERY",
            "queryId": str(uuid.uuid4()),
            "commandId": command_id,
        }
        value.update(overrides)
        return value

    def read_protocol_event(self, kind, **matches):
        return self.read_event(
            lambda event: event.get("kind") == kind
            and all(event.get(key) == value for key, value in matches.items())
        )

    def test_unknown_query_preserves_first_command_sequence(self):
        ready=self.read_protocol_event("RUNTIME_READY")
        self.assertEqual("algorithm.command.v1",ready["protocolVersion"])
        self.assertEqual("PREPARED",ready["state"])
        self.assertNotIn("runtimeState",ready)
        query=self.status_query(str(uuid.uuid4()))
        self.send(query)
        reply=self.read_protocol_event("STATUS_REPLY",queryId=query["queryId"])
        self.assertFalse(reply["known"])
        command_id=str(uuid.uuid4())
        self.send(self.command(command_id,1,0,"START"))
        result=self.read_protocol_event("COMMAND_RESULT",commandId=command_id,status="SUCCEEDED")
        self.assertEqual(1,result["stateVersion"])
        stop=str(uuid.uuid4())
        self.send(self.command(stop,2,1,"STOP"))
        self.read_protocol_event("COMMAND_RESULT",commandId=stop,status="SUCCEEDED")
        self.assertEqual(0,self.process.wait(timeout=10))

    def test_long_json_protocol_is_not_a_cli_option(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,str(root/"runner.py"),"--algorithm","GB_SFLA_CS","--run-id","991003","--command-protocol","algorithm.command.v1"],capture_output=True,text=True,encoding="utf-8",timeout=10)
        self.assertNotEqual(0,result.returncode)
        self.assertNotIn('"kind": "RUNTIME_READY"',result.stdout)
        self.assertIn("invalid choice",result.stderr)

    def test_known_status_reply_embeds_protocol_version(self):
        self.read_event(lambda event: event.get("kind") == "RUNTIME_READY")
        frame = self.read_event(lambda event: event.get("event") == "frame")
        self.assertTrue(frame["payload"]["agents"])

        command_id = str(uuid.uuid4())
        self.send(self.command(command_id, 1, 0, "START"))
        self.read_protocol_event("COMMAND_RESULT", commandId=command_id, status="SUCCEEDED")

        query = self.status_query(command_id)
        self.send(query)
        reply = self.read_protocol_event("STATUS_REPLY", queryId=query["queryId"])
        self.assertTrue(reply["known"])
        self.assertEqual("algorithm.command.v1", reply["protocolVersion"])
        self.assertIsNotNone(reply["result"])
        self.assertEqual("algorithm.command.v1", reply["result"]["protocolVersion"])

    def test_malformed_json_returns_protocol_error_without_killing_runner(self):
        self.read_protocol_event("RUNTIME_READY")
        self.send_raw('{"kind":"COMMAND"')
        error = self.read_protocol_event("PROTOCOL_ERROR")
        self.assertEqual("MALFORMED_MESSAGE", error["errorCode"])

        command_id = str(uuid.uuid4())
        self.send(self.command(command_id, 1, 0, "START"))
        self.read_protocol_event("COMMAND_RESULT", commandId=command_id, status="SUCCEEDED")

    def test_wrong_identity_status_query_is_rejected_and_does_not_mutate_state(self):
        self.read_protocol_event("RUNTIME_READY")
        unknown_id = str(uuid.uuid4())
        self.send(self.status_query(unknown_id, runtimeGeneration=str(uuid.uuid4())))
        error = self.read_protocol_event("PROTOCOL_ERROR")
        self.assertEqual("IDENTITY_MISMATCH", error["errorCode"])
        self.assertEqual(unknown_id, error["relatedCommandId"])

        command_id = str(uuid.uuid4())
        self.send(self.command(command_id, 1, 0, "START"))
        succeeded = self.read_protocol_event(
            "COMMAND_RESULT", commandId=command_id, status="SUCCEEDED"
        )
        self.assertEqual(1, succeeded["stateVersion"])

    def test_invalid_numeric_field_returns_error_without_consuming_sequence(self):
        self.read_protocol_event("RUNTIME_READY")
        invalid_id = str(uuid.uuid4())
        self.send(self.command(invalid_id, "1", 0, "START"))
        error = self.read_protocol_event("PROTOCOL_ERROR")
        self.assertEqual("MALFORMED_MESSAGE", error["errorCode"])

        valid_id = str(uuid.uuid4())
        self.send(self.command(valid_id, 1, 0, "START"))
        self.read_protocol_event("COMMAND_RESULT", commandId=valid_id, status="SUCCEEDED")

    def test_wrong_protocol_version_is_rejected(self):
        self.read_protocol_event("RUNTIME_READY")
        command_id = str(uuid.uuid4())
        self.send(self.command(command_id, 1, 0, "START", protocolVersion="legacy"))
        error = self.read_protocol_event("PROTOCOL_ERROR")
        self.assertEqual("MALFORMED_MESSAGE", error["errorCode"])

    def test_duplicate_replay_is_stable_and_changed_body_conflicts(self):
        self.read_protocol_event("RUNTIME_READY")
        command_id = str(uuid.uuid4())
        command = self.command(command_id, 1, 0, "START")
        self.send(command)
        accepted = self.read_protocol_event(
            "COMMAND_RESULT", commandId=command_id, status="ACCEPTED"
        )
        succeeded = self.read_protocol_event(
            "COMMAND_RESULT", commandId=command_id, status="SUCCEEDED"
        )

        self.send(command)
        replayed_accepted = self.read_protocol_event(
            "COMMAND_RESULT", commandId=command_id, status="ACCEPTED"
        )
        replayed_succeeded = self.read_protocol_event(
            "COMMAND_RESULT", commandId=command_id, status="SUCCEEDED"
        )
        self.assertEqual(accepted, replayed_accepted)
        self.assertEqual(succeeded, replayed_succeeded)

        self.send({**command, "action": "STOP"})
        conflict = self.read_protocol_event("PROTOCOL_ERROR")
        self.assertEqual("COMMAND_ID_CONFLICT", conflict["errorCode"])

    def test_sequence_and_state_version_rejections_preserve_contract(self):
        self.read_protocol_event("RUNTIME_READY")
        skipped_id = str(uuid.uuid4())
        self.send(self.command(skipped_id, 2, 0, "START"))
        sequence_error = self.read_protocol_event("PROTOCOL_ERROR")
        self.assertEqual("SEQUENCE_MISMATCH", sequence_error["errorCode"])

        stale_id = str(uuid.uuid4())
        self.send(self.command(stale_id, 1, 9, "START"))
        rejected = self.read_protocol_event(
            "COMMAND_RESULT", commandId=stale_id, status="REJECTED"
        )
        self.assertEqual("STATE_VERSION_MISMATCH", rejected["errorCode"])

        valid_id = str(uuid.uuid4())
        self.send(self.command(valid_id, 2, 0, "START"))
        self.read_protocol_event("COMMAND_RESULT", commandId=valid_id, status="SUCCEEDED")


    def test_business_rejection_consumes_sequence_and_next_command_succeeds(self):
        self.read_protocol_event("RUNTIME_READY")
        rejected_id = str(uuid.uuid4())
        command = self.command(rejected_id, 1, 0, "PAUSE")
        self.send(command)
        rejected = self.read_protocol_event("COMMAND_RESULT", commandId=rejected_id, status="REJECTED")
        self.assertEqual("INVALID_STATE", rejected["errorCode"])
        self.assertEqual(0, rejected["stateVersion"])
        self.assertEqual("PREPARED", rejected["runtimeState"])
        self.send(command)
        self.assertEqual(rejected, self.read_protocol_event("COMMAND_RESULT", commandId=rejected_id, status="REJECTED"))
        self.send(self.command(str(uuid.uuid4()), 1, 0, "START"))
        self.assertEqual("SEQUENCE_MISMATCH", self.read_protocol_event("PROTOCOL_ERROR")["errorCode"])
        valid_id = str(uuid.uuid4())
        self.send(self.command(valid_id, 2, 0, "START"))
        succeeded = self.read_protocol_event("COMMAND_RESULT", commandId=valid_id, status="SUCCEEDED")
        self.assertEqual(1, succeeded["stateVersion"])
        self.assertEqual("RUNNING", succeeded["runtimeState"])


    def test_old_and_skipped_sequences_do_not_advance_command_sequence(self):
        self.read_protocol_event("RUNTIME_READY")
        initial = str(uuid.uuid4())
        self.send(self.command(initial, 1, 0, "START"))
        self.read_protocol_event("COMMAND_RESULT", commandId=initial, status="SUCCEEDED")
        for sequence in (1, 3):
            invalid = str(uuid.uuid4())
            self.send(self.command(invalid, sequence, 1, "PAUSE"))
            error = self.read_protocol_event("PROTOCOL_ERROR")
            self.assertEqual("SEQUENCE_MISMATCH", error["errorCode"])
            self.assertEqual(invalid, error["relatedCommandId"])
        valid = str(uuid.uuid4())
        self.send(self.command(valid, 2, 1, "PAUSE"))
        result = self.read_protocol_event("COMMAND_RESULT", commandId=valid, status="SUCCEEDED")
        self.assertEqual(2, result["stateVersion"])
        self.assertEqual("PAUSED", result["runtimeState"])

    def test_behind_state_version_rejects_action_and_preserves_current_state(self):
        self.read_protocol_event("RUNTIME_READY")
        initial = str(uuid.uuid4())
        self.send(self.command(initial, 1, 0, "START"))
        self.read_protocol_event("COMMAND_RESULT", commandId=initial, status="SUCCEEDED")
        stale = str(uuid.uuid4())
        self.send(self.command(stale, 2, 0, "PAUSE"))
        rejected = self.read_protocol_event("COMMAND_RESULT", commandId=stale, status="REJECTED")
        self.assertEqual("STATE_VERSION_MISMATCH", rejected["errorCode"])
        self.assertEqual("RUNNING", rejected["runtimeState"])
        self.assertEqual(1, rejected["stateVersion"])
        valid = str(uuid.uuid4())
        self.send(self.command(valid, 3, 1, "PAUSE"))
        result = self.read_protocol_event("COMMAND_RESULT", commandId=valid, status="SUCCEEDED")
        self.assertEqual("PAUSED", result["runtimeState"])
        self.assertEqual(2, result["stateVersion"])

    def test_pause_has_six_heartbeats_and_no_new_pose_frames(self):
        self.read_protocol_event("RUNTIME_READY")
        start=str(uuid.uuid4());self.send(self.command(start,1,0,"START"))
        self.read_protocol_event("COMMAND_RESULT",commandId=start,status="SUCCEEDED")
        pause=str(uuid.uuid4());self.send(self.command(pause,2,1,"PAUSE"))
        paused=self.read_protocol_event("COMMAND_RESULT",commandId=pause,status="SUCCEEDED")
        observed=[]
        for _ in range(6):
            def heartbeat(event):
                observed.append(event)
                return event.get("kind")=="HEARTBEAT"
            self.read_event(heartbeat)
        beats=[e for e in observed if e.get("kind")=="HEARTBEAT"]
        self.assertEqual(6,len(beats))
        self.assertFalse(any(e.get("event")=="frame" for e in observed))
        self.assertTrue(all(e["runtimeState"]=="PAUSED" and e["stateVersion"]==2 and e["lastFrameSequence"]==paused["lastFrameSequence"] for e in beats))
        self.assertEqual(sorted(set(e["heartbeatSequence"] for e in beats)),[e["heartbeatSequence"] for e in beats])
        stop=str(uuid.uuid4());self.send(self.command(stop,3,2,"STOP"))
        self.read_protocol_event("COMMAND_RESULT",commandId=stop,status="SUCCEEDED")
        self.assertEqual(0,self.process.wait(timeout=10))


if __name__ == "__main__":
    unittest.main()
