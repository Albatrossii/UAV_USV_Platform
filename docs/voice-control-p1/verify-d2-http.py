"""D2 real-HTTP acceptance against the local isolated environment.

The script never prints credentials. It temporarily enables the existing peer test
account to prove role and ownership boundaries, then restores its original role and
enabled flag in a finally block.
"""
import argparse
import http.cookiejar
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--peer-credentials", required=True)
    parser.add_argument("--mysql", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base", default="http://localhost:5175")
    args = parser.parse_args()

    parsed = urllib.parse.urlparse(args.base)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("Local HTTP only")

    secrets = json.loads(pathlib.Path(args.credentials).read_text(encoding="utf-8-sig"))
    peer = json.loads(pathlib.Path(args.peer_credentials).read_text(encoding="utf-8-sig"))
    mysql_env = {**os.environ, "MYSQL_PWD": secrets["dbPassword"]}
    report = {
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base": args.base,
        "scope": "D2-01..D2-17 real HTTP plus database evidence; D2-18 references browser evidence",
        "cases": {},
        "result": "FAIL",
    }

    def sql(statement):
        result = subprocess.run(
            [
                args.mysql,
                "--host=127.0.0.1",
                "--port=3306",
                "--user=p0_integration",
                "--database=uav_usv_p0_integration",
                "--batch",
                "--skip-column-names",
                "--raw",
                "--default-character-set=utf8mb4",
                "--execute",
                statement,
            ],
            env=mysql_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        if result.returncode:
            raise RuntimeError("Local read/write test SQL failed")
        return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]

    def set_peer(role, enabled):
        if role not in ("ADMIN", "VIEWER") or enabled not in (0, 1):
            raise ValueError("Unsafe peer state")
        username = peer["username"].replace("'", "''")
        sql(f"UPDATE app_user SET role='{role}',enabled={enabled} WHERE username='{username}'")

    class Session:
        def __init__(self):
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            )
            self.csrf = None

        def call(self, path, body=None, headers=None, timeout=20):
            raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                args.base.rstrip("/") + path,
                data=raw,
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                payload = json.loads(response.read().decode("utf-8"))
                return response.status, payload, dict(response.headers)

        def login(self, username, password):
            status, body, _ = self.call(
                "/api/auth/login", {"username": username, "password": password}
            )
            if status != 200:
                raise AssertionError("Login failed; credentials are not printed")
            status, body, _ = self.call("/api/auth/csrf")
            if status != 200:
                raise AssertionError("CSRF bootstrap failed")
            self.csrf = body["data"]

        def write_headers(self, request_id, include_csrf=True):
            headers = {"X-Request-ID": request_id, "Idempotency-Key": request_id}
            if include_csrf and self.csrf:
                headers[self.csrf["headerName"]] = self.csrf["token"]
            return headers

        def interpret(self, text, request_id=None, **overrides):
            request_id = request_id or str(uuid.uuid4())
            body = {
                "requestId": request_id,
                "text": text,
                "locale": "zh-CN",
                "allowedActions": ["START", "PAUSE", "RESUME", "STOP"],
                "availableDeviceCodes": [],
                "runtimeContext": None,
            }
            body.update(overrides)
            return request_id, body, self.call(
                "/api/voice/intelligence/interpretations",
                body,
                self.write_headers(request_id),
            )

    def expect(case_id, status, body, expected_status, expected_code=None, data=None):
        assert status == expected_status, (case_id, status, body)
        if expected_code is not None:
            assert body.get("code") == expected_code, (case_id, body)
        if data:
            for key, value in data.items():
                assert body["data"].get(key) == value, (case_id, key, body)
        report["cases"][case_id] = {
            "result": "PASS",
            "httpStatus": status,
            "code": body.get("code"),
            **({"data": data} if data else {}),
        }

    original = sql(
        "SELECT role,enabled FROM app_user WHERE username='"
        + peer["username"].replace("'", "''")
        + "'"
    )
    if len(original) != 1:
        raise SystemExit("Peer test account missing")
    original_role, original_enabled = original[0][0], int(original[0][1])

    admin = Session()
    proposal_id = None
    try:
        # D2-01: unauthenticated request.
        anonymous = Session()
        rid = str(uuid.uuid4())
        body = {
            "requestId": rid,
            "text": "开始任务",
            "locale": "zh-CN",
            "allowedActions": ["START"],
            "availableDeviceCodes": [],
            "runtimeContext": None,
        }
        status, response, _ = anonymous.call(
            "/api/voice/intelligence/interpretations",
            body,
            {"X-Request-ID": rid, "Idempotency-Key": rid},
        )
        expect("D2-01", status, response, 401, "UNAUTHORIZED")

        admin.login("p0_admin", secrets["adminPassword"])

        # D2-02: authenticated but missing/wrong CSRF.
        rid = str(uuid.uuid4())
        body["requestId"] = rid
        status, response, _ = admin.call(
            "/api/voice/intelligence/interpretations",
            body,
            {"X-Request-ID": rid, "Idempotency-Key": rid},
        )
        expect("D2-02", status, response, 403, "CSRF_INVALID")

        # D2-03: enabled non-ADMIN account.
        set_peer("VIEWER", 1)
        viewer = Session()
        viewer.login(peer["username"], peer["password"])
        rid, body, (status, response, _) = viewer.interpret("开始任务")
        expect("D2-03", status, response, 403, "FORBIDDEN")

        # D2-04: request IDs disagree.
        rid = str(uuid.uuid4())
        wrong = str(uuid.uuid4())
        body["requestId"] = rid
        status, response, _ = admin.call(
            "/api/voice/intelligence/interpretations",
            body,
            {**admin.write_headers(rid), "X-Request-ID": wrong},
        )
        expect("D2-04", status, response, 400, "VOICE_INVALID_REQUEST")

        # D2-05: representative malformed bodies.
        invalid_results = []
        for mutation in ("unknown", "blank", "too_long", "duplicate_action", "bad_device"):
            rid = str(uuid.uuid4())
            candidate = {
                "requestId": rid,
                "text": "开始任务",
                "locale": "zh-CN",
                "allowedActions": ["START"],
                "availableDeviceCodes": [],
                "runtimeContext": None,
            }
            if mutation == "unknown":
                candidate["unknown"] = True
            elif mutation == "blank":
                candidate["text"] = "   "
            elif mutation == "too_long":
                candidate["text"] = "开" * 201
            elif mutation == "duplicate_action":
                candidate["allowedActions"] = ["START", "START"]
            else:
                candidate["availableDeviceCodes"] = ["bad device"]
            status, response, _ = admin.call(
                "/api/voice/intelligence/interpretations",
                candidate,
                admin.write_headers(rid),
            )
            assert status == 400 and response.get("code") == "VOICE_INVALID_REQUEST", (
                mutation,
                status,
                response,
            )
            invalid_results.append(mutation)
        report["cases"]["D2-05"] = {
            "result": "PASS",
            "variants": invalid_results,
        }

        # D2-06..D2-11 classification matrix.
        matrix = {
            "D2-06-START": ("开始任务", "CANDIDATE", None, "START"),
            "D2-06-PAUSE": ("暂停任务", "CANDIDATE", None, "PAUSE"),
            "D2-06-RESUME": ("继续任务", "CANDIDATE", None, "RESUME"),
            "D2-06-STOP": ("停止任务", "CANDIDATE", None, "STOP"),
            "D2-07": ("不要停止任务", "NOT_ACTIONABLE", "NEGATED_ACTION", None),
            "D2-08": ("暂停然后继续", "NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION", None),
            "D2-09": ("报告当前情况", "NEEDS_CLARIFICATION", "NO_SUPPORTED_ACTION", None),
            "D2-10": ("攻击目标", "UNSUPPORTED", "UNSUPPORTED_CAPABILITY", None),
            "D2-11": ("让USV-001停止任务", "UNSUPPORTED", "UNSUPPORTED_TARGETING", None),
        }
        for case_id, (text, result_status, reason, action) in matrix.items():
            rid, _, (status, response, _) = admin.interpret(text)
            assert status == 200 and response["data"]["status"] == result_status
            if reason:
                assert response["data"]["reason"] == reason
            if action:
                assert response["data"]["action"] == action
            report["cases"][case_id] = {
                "result": "PASS",
                "requestId": rid,
                "classification": result_status,
                **({"reason": reason} if reason else {"action": action}),
            }

        status, contexts, _ = admin.call("/api/voice/contexts")
        assert status == 200
        active = next(
            item
            for item in contexts["data"]
            if item["state"] == "PREPARED"
            and item["sceneReady"]
            and "START" in item["capabilities"]
        )
        runtime_hint = {
            "runtimeRef": active["runtimeRef"],
            "runtimeGeneration": active["runtimeGeneration"],
            "contextVersion": active["contextVersion"] + 1,
        }
        rid, _, (status, response, _) = admin.interpret(
            "开始任务", runtimeContext=runtime_hint
        )
        expect("D2-12", status, response, 409, "VOICE_CONTEXT_CHANGED")

        # D2-13 and D2-14: same-key replay and conflict.
        rid = str(uuid.uuid4())
        rid, body, first = admin.interpret("暂停任务", rid)
        _, _, replay = admin.interpret("暂停任务", rid)
        assert first[0] == 200 and replay[0] == 200 and first[1] == replay[1]
        report["cases"]["D2-13"] = {
            "result": "PASS",
            "requestId": rid,
            "identicalReplay": True,
        }
        changed = dict(body)
        changed["text"] = "停止任务"
        status, response, _ = admin.call(
            "/api/voice/intelligence/interpretations",
            changed,
            admin.write_headers(rid),
        )
        expect("D2-14", status, response, 409, "IDEMPOTENCY_CONFLICT")

        # D2-15 ownership and action mismatch.
        set_peer("ADMIN", 1)
        peer_admin = Session()
        peer_admin.login(peer["username"], peer["password"])
        peer_id, _, (status, peer_candidate, _) = peer_admin.interpret("开始任务")
        assert status == 200 and peer_candidate["data"]["status"] == "CANDIDATE"

        admin_pause_id, _, (status, pause_candidate, _) = admin.interpret("暂停任务")
        assert status == 200 and pause_candidate["data"]["action"] == "PAUSE"
        proposal_body = {
            "runtimeRef": active["runtimeRef"],
            "runtimeGeneration": active["runtimeGeneration"],
            "expectedContextVersion": active["contextVersion"],
            "intent": "MISSION_START",
        }
        invalid_sources = []
        for label, source_id in (("other_user", peer_id), ("action_mismatch", admin_pause_id)):
            key = str(uuid.uuid4())
            status, response, _ = admin.call(
                "/api/voice/commands/proposals",
                proposal_body,
                {
                    **admin.write_headers(key),
                    "X-Voice-Interpretation-ID": source_id,
                },
            )
            assert status == 409 and response.get("code") == "VOICE_INTERPRETATION_INVALID"
            invalid_sources.append(label)
        report["cases"]["D2-15"] = {
            "result": "PASS",
            "realHttpVariants": invalid_sources,
            "expiredVariant": "covered by deterministic service test; no 30-minute wait in this run",
        }

        # D2-16 and D2-17: source participates in proposal idempotency and no execution exists.
        source_one, _, (status, _, _) = admin.interpret("开始任务")
        assert status == 200
        source_two, _, (status, _, _) = admin.interpret("开始任务")
        assert status == 200
        proposal_key = str(uuid.uuid4())
        status, created, _ = admin.call(
            "/api/voice/commands/proposals",
            proposal_body,
            {
                **admin.write_headers(proposal_key),
                "X-Voice-Interpretation-ID": source_one,
            },
        )
        assert status == 201 and created["data"]["status"] == "AWAITING_CONFIRMATION"
        proposal_id = created["data"]["proposalId"]
        status, conflict, _ = admin.call(
            "/api/voice/commands/proposals",
            proposal_body,
            {
                **admin.write_headers(proposal_key),
                "X-Voice-Interpretation-ID": source_two,
            },
        )
        expect("D2-16", status, conflict, 409, "IDEMPOTENCY_CONFLICT")
        execution_rows = sql(
            "SELECT COUNT(*) FROM voice_execution WHERE proposal_id='"
            + proposal_id.replace("'", "''")
            + "'"
        )
        assert execution_rows == [["0"]]
        status, fetched, _ = admin.call("/api/voice/commands/" + proposal_id)
        assert (
            status == 200
            and fetched["data"]["status"] == "AWAITING_CONFIRMATION"
            and fetched["data"]["executionId"] is None
            and fetched["data"]["interpretationId"] == source_one
        )
        report["cases"]["D2-17"] = {
            "result": "PASS",
            "proposalId": proposal_id,
            "interpretationId": source_one,
            "proposalStatus": "AWAITING_CONFIRMATION",
            "executionCountBeforeConfirmation": 0,
        }

        # Leave the prepared run reusable and remove only this test proposal.
        cancel_key = str(uuid.uuid4())
        status, cancelled, _ = admin.call(
            f"/api/voice/commands/{proposal_id}/cancel",
            {
                "expectedPlanVersion": created["data"]["planVersion"],
                "expectedPlanHash": created["data"]["planHash"],
            },
            admin.write_headers(cancel_key),
        )
        assert status == 200 and cancelled["data"]["status"] == "CANCELLED"
        report["cleanup"] = {"proposalId": proposal_id, "status": "CANCELLED"}

        report["cases"]["D2-18"] = {
            "result": "PASS",
            "evidence": "D2-LOCAL-BROWSER-ACCEPTANCE-20260924.md",
            "executionId": "87e1e64f-34a3-437d-af5f-919f12fe6c73",
            "terminal": "SUCCEEDED/SUCCESS/REPORTED_APPLIED",
        }
        report["result"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            set_peer(original_role, original_enabled)
            report["peerAccountRestored"] = {
                "role": original_role,
                "enabled": bool(original_enabled),
            }
        except Exception as restore_error:
            report["peerAccountRestoreError"] = type(restore_error).__name__
            report["result"] = "FAIL"
        report["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"result": report["result"], "cases": len(report["cases"])}))


if __name__ == "__main__":
    main()
