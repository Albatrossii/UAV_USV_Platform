"""Isolated runner smoke; stdin is the JSON output of the frontend layout builder.

No HTTP service or existing Java-owned runtime is contacted. Every spawned
runner is owned by this script and is closed after its checks complete.
"""
from __future__ import annotations

import json
import math
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


def check_variant(layout: dict, algorithm: str, adaptive: bool) -> dict:
    reference, generation = str(uuid.uuid4()), str(uuid.uuid4())
    config = {"uavCount": 3, "usvCount": 3, "targetCount": 1, "seed": 42,
              "adaptiveMultiTarget": adaptive,
              "fleetOrigin": {"eastM": -360, "northM": -285, "upM": 0},
              "returnInfrastructure": layout}
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "runner.py"),
         "--algorithm", algorithm, "--run-id", "990432", "--config", json.dumps(config),
         "--command-protocol", "v1", "--runtime-ref", reference, "--runtime-generation", generation, "--fps", "20"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        encoding="utf-8", text=True, bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    events: queue.Queue[dict | None] = queue.Queue()

    def read_output() -> None:
        try:
            for line in process.stdout:
                events.put(json.loads(line))
        finally:
            events.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    latest_frame = None

    def wait_for(predicate, timeout: float = 60.0) -> dict:
        nonlocal latest_frame
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{algorithm}/{adaptive}: timed out waiting for runner event")
            event = events.get(timeout=remaining)
            if event is None:
                raise AssertionError(f"{algorithm}/{adaptive}: runner exited before expected event")
            if event.get("event") == "frame":
                latest_frame = event["payload"]
                assert latest_frame["metrics"]["returnInfrastructure"] == layout
            if predicate(event):
                return event

    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    try:
        ready = wait_for(lambda event: event.get("kind") == "RUNTIME_READY")
        assert ready["state"] == "PREPARED"
        wait_for(lambda event: event.get("event") == "frame")
        assert len(latest_frame["agents"]) == 6
        assert {a["code"] for a in latest_frame["agents"]} == {slot["deviceCode"] for slot in layout["slots"]}
        start_id = str(uuid.uuid4())
        send({"protocolVersion": "algorithm.command.v1", "runtimeRef": reference, "runtimeGeneration": generation,
              "kind": "COMMAND", "commandId": start_id, "commandSequence": 1, "expectedStateVersion": 0,
              "action": "START", "parameters": {}})
        started = wait_for(lambda event: event.get("kind") == "COMMAND_RESULT" and event.get("commandId") == start_id
                           and event.get("status") in {"SUCCEEDED", "REJECTED", "FAILED"})
        assert started["status"] == "SUCCEEDED", started
        initial = {agent["code"]: (agent["x"], agent["y"], agent["z"]) for agent in latest_frame["agents"]}
        receipts = []
        for code in ("UAV-001", "USV-001"):
            request_id = str(uuid.uuid4())
            send({"kind": "DEVICE_COMMAND", "requestId": request_id, "deviceCode": code, "commandType": f"{code[:3]}_RETURN"})
            receipt = wait_for(lambda event: event.get("event") == "deviceCommandResult" and event.get("requestId") == request_id)
            assert receipt["success"] and receipt["deviceStatus"] == "RETURNING", receipt
            receipts.append({"deviceCode": code, "status": receipt["deviceStatus"]})
        for _ in range(8):
            wait_for(lambda event: event.get("event") == "frame")
        for code in ("UAV-001", "USV-001"):
            current = next(a for a in latest_frame["agents"] if a["code"] == code)
            assert math.dist(initial[code], (current["x"], current["y"], current["z"])) > .01
            assert current["status"] == "RETURNING"
        return {"algorithm": algorithm, "adaptive": adaptive, "state": ready["state"],
                "metadataMatchesFrontend": True, "returnReceipts": receipts,
                "returnStages": {code: latest_frame["metrics"]["deviceControlStates"][code]["returnStage"]
                                 for code in ("UAV-001", "USV-001")}}
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        reader.join(timeout=2)
        process.stdout.close()


if __name__ == "__main__":
    native_layout = json.load(sys.stdin)
    assert native_layout["version"] == "scene-existing-v1"
    for variant in (("ESCORT_GUARD", False), ("ESCORT_GUARD", True), ("GB_SFLA_CS", False)):
        print(json.dumps(check_variant(native_layout, *variant)), flush=True)
