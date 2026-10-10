"""Bounded isolated real-time runner soak (no HTTP, cloud ASR, or real devices).

Run with the test dependency environment, e.g. python tests/soak_real_runner.py
--seconds 600. Original-scene coordinates come from the regression fixture.
Results are JSON on stdout; nonzero exit means no acceptance.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from test_return_infrastructure import native_anchor_layout


def run(seconds: int) -> dict:
    reference, generation = str(uuid.uuid4()), str(uuid.uuid4())
    layout = native_anchor_layout()
    config = {"standaloneVirtualSimulation": True, "seed": 42,
              "uavCount": 3, "usvCount": 3, "targetCount": 1,
              "fleetOrigin": {"eastM": -360, "northM": -285, "upM": 0},
              "returnInfrastructure": layout}
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "runner.py"),
         "--algorithm", "GB_SFLA_CS", "--run-id", "990601", "--config", json.dumps(config),
         "--fps", "20", "--command-protocol", "v1", "--runtime-ref", reference,
         "--runtime-generation", generation],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    events = queue.Queue(maxsize=2000)
    errors = []
    stop_reading = threading.Event()

    def read():
        try:
            for line in process.stdout:
                event = json.loads(line)
                while not stop_reading.is_set():
                    try:
                        events.put(event, timeout=.2)
                        break
                    except queue.Full:
                        pass
        except Exception as error:
            errors.append(str(error))
        finally:
            if not stop_reading.is_set():
                try:
                    events.put(None, timeout=1)
                except queue.Full:
                    pass

    def read_errors():
        for line in process.stderr:
            errors.append(line.strip())
            del errors[:-20]

    readers = [threading.Thread(target=read, daemon=True), threading.Thread(target=read_errors, daemon=True)]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    last_beat = started
    stats = {"frames": 0, "heartbeats": 0, "commands": 0, "completedReturnRejoinCycles": 0,
             "maxHeartbeatGapSeconds": 0.0}
    latest = None
    version = sequence = 0

    def wait_for(predicate, timeout=12):
        nonlocal latest, last_beat
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = time.monotonic()
            assert now - last_beat < 8, "Runner heartbeat stale"
            try:
                event = events.get(timeout=min(1, max(.01, deadline - now)))
            except queue.Empty:
                continue
            assert event is not None, f"Unexpected runner exit: {errors}"
            assert event.get("kind") != "PROTOCOL_ERROR", event
            if event.get("kind") == "HEARTBEAT":
                stats["maxHeartbeatGapSeconds"] = max(stats["maxHeartbeatGapSeconds"], time.monotonic() - last_beat)
                last_beat = time.monotonic()
                stats["heartbeats"] += 1
            if event.get("event") == "frame":
                frame = event["payload"]
                assert latest is None or frame["sequence"] > latest["sequence"]
                assert frame["metrics"]["returnInfrastructure"] == layout
                assert len(frame["agents"]) == 6
                latest = frame
                stats["frames"] += 1
            if predicate(event):
                return event
        raise AssertionError(f"Timed out; last frame sequence: {latest and latest['sequence']}")

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def fleet(action):
        nonlocal version, sequence
        sequence += 1
        cid = str(uuid.uuid4())
        message = {"protocolVersion": "algorithm.command.v1", "runtimeRef": reference,
                   "runtimeGeneration": generation, "kind": "COMMAND", "commandId": cid,
                   "commandSequence": sequence, "expectedStateVersion": version,
                   "action": action, "parameters": {}}
        send(message)
        result = wait_for(lambda e: e.get("commandId") == cid and e.get("status") in {"SUCCEEDED", "REJECTED", "FAILED"})
        assert result["status"] == "SUCCEEDED", result
        version = result["stateVersion"]
        stats["commands"] += 1
        if action != "STOP":
            # Recover via a read-only status query; never resend an uncertain action.
            qid = str(uuid.uuid4())
            send({"protocolVersion": "algorithm.command.v1", "runtimeRef": reference,
                  "runtimeGeneration": generation, "kind": "STATUS_QUERY", "queryId": qid,
                  "commandId": cid})
            reply = wait_for(lambda e: e.get("kind") == "STATUS_REPLY" and e.get("queryId") == qid)
            assert reply["known"] and reply["result"] == result
        return result

    def device(code, action):
        rid = str(uuid.uuid4())
        send({"kind": "DEVICE_COMMAND", "requestId": rid, "deviceCode": code, "commandType": action})
        receipt = wait_for(lambda e: e.get("event") == "deviceCommandResult" and e.get("requestId") == rid)
        assert receipt["success"], receipt
        stats["commands"] += 1

    def pair_at(status):
        return latest is not None and all(next(a for a in latest["agents"] if a["code"] == code)["status"] == status
                                          for code in ("UAV-001", "USV-001"))

    try:
        wait_for(lambda e: e.get("kind") == "RUNTIME_READY")
        wait_for(lambda e: e.get("event") == "frame")
        fleet("START")
        device("UAV-003", "UAV_HOVER")  # Prevent algorithm terminal completion during the soak.
        for cycle in range(2):
            device("UAV-001", "UAV_RETURN" if cycle == 0 else "UAV_LAND")
            device("USV-001", "USV_RETURN")
            wait_for(lambda e: e.get("event") == "frame" and pair_at("RETURNED"), timeout=180)
            for slot in (layout["slots"][0], layout["slots"][3]):
                agent = next(a for a in latest["agents"] if a["code"] == slot["deviceCode"])
                assert (agent["x"], agent["y"], agent["z"]) == (slot["eastM"], slot["northM"], slot["upM"])
            fleet("PAUSE")
            paused_sequence = latest["sequence"]
            wait_for(lambda e: e.get("kind") == "HEARTBEAT" and e.get("runtimeState") == "PAUSED")
            assert latest["sequence"] == paused_sequence
            fleet("RESUME")
            device("UAV-001", "UAV_RESUME")
            device("USV-001", "USV_RESUME")
            wait_for(lambda e: e.get("event") == "frame" and pair_at("ACTIVE"), timeout=180)
            stats["completedReturnRejoinCycles"] += 1
        while time.monotonic() - started < seconds:
            fleet("PAUSE")
            wait_for(lambda e: e.get("kind") == "HEARTBEAT" and e.get("runtimeState") == "PAUSED")
            fleet("RESUME")
            until = min(started + seconds, time.monotonic() + 10)
            while time.monotonic() < until:
                wait_for(lambda e: e.get("event") == "frame")
        fleet("STOP")
        assert process.wait(timeout=10) == 0
        return {"result": "PASS", "wallClockSeconds": round(time.monotonic() - started, 2), **stats}
    finally:
        stop_reading.set()
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    if not 600 <= args.seconds <= 3600:
        parser.error("--seconds must be between 600 and 3600")
    print(json.dumps(run(args.seconds)), flush=True)
