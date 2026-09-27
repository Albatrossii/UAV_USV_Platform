"""D3 real-HTTP checks for the local LLM intent adapter.

Credentials are read from a local ignored file and are never written to the report.
The script only talks to localhost and creates no P0 proposal or execution.
"""
import argparse
import http.cookiejar
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:5175")
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.base)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("Local HTTP only")

    secrets = json.loads(pathlib.Path(args.credentials).read_text(encoding="utf-8-sig"))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    report = {
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base": args.base,
        "scope": "D3 local LLM real HTTP classification and idempotency",
        "cases": [],
        "result": "FAIL",
    }

    def call(path, body=None, headers=None, timeout=30):
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            args.base.rstrip("/") + path,
            data=raw,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    status, login = call(
        "/api/auth/login",
        {"username": "p0_admin", "password": secrets["adminPassword"]},
    )
    if status != 200:
        raise AssertionError("Login failed; credentials are not printed")
    status, csrf_body = call("/api/auth/csrf")
    if status != 200:
        raise AssertionError("CSRF bootstrap failed")
    csrf = csrf_body["data"]

    def interpret(text, request_id=None):
        request_id = request_id or str(uuid.uuid4())
        body = {
            "requestId": request_id,
            "text": text,
            "locale": "zh-CN",
            "allowedActions": ["START", "PAUSE", "RESUME", "STOP"],
            "availableDeviceCodes": [],
            "runtimeContext": None,
        }
        headers = {
            "X-Request-ID": request_id,
            "Idempotency-Key": request_id,
            csrf["headerName"]: csrf["token"],
        }
        return request_id, body, call(
            "/api/voice/intelligence/interpretations", body, headers
        )

    matrix = [
        ("D3-01", "请让整个编队进入工作状态", "CANDIDATE", "action", "START"),
        ("D3-02", "先暂停一下", "CANDIDATE", "action", "PAUSE"),
        ("D3-03", "恢复刚才的任务", "CANDIDATE", "action", "RESUME"),
        ("D3-04", "结束当前任务", "CANDIDATE", "action", "STOP"),
        (
            "D3-05",
            "暂停然后继续",
            "NEEDS_CLARIFICATION",
            "reason",
            "AMBIGUOUS_ACTION",
        ),
        (
            "D3-06",
            "今天天气怎么样",
            "NEEDS_CLARIFICATION",
            "reason",
            "NO_SUPPORTED_ACTION",
        ),
        (
            "D3-07",
            "让无人机攻击目标",
            "UNSUPPORTED",
            "reason",
            "UNSUPPORTED_CAPABILITY",
        ),
        (
            "D3-08",
            "让USV-001停止任务",
            "UNSUPPORTED",
            "reason",
            "UNSUPPORTED_TARGETING",
        ),
        ("D3-09", "不要停止任务", "NOT_ACTIONABLE", "reason", "NEGATED_ACTION"),
    ]
    try:
        for case_id, text, expected_status, field, expected_value in matrix:
            request_id, _, (status, response) = interpret(text)
            data = response.get("data") or {}
            assert status == 200, (case_id, status, response)
            assert data.get("status") == expected_status, (case_id, response)
            assert data.get(field) == expected_value, (case_id, response)
            assert data.get("provider") == "local-llm", (case_id, response)
            assert data.get("model") == "qwen2.5-1.5b-instruct-q4_k_m", (
                case_id,
                response,
            )
            report["cases"].append(
                {
                    "id": case_id,
                    "result": "PASS",
                    "requestId": request_id,
                    "classification": expected_status,
                    field: expected_value,
                }
            )

        replay_id = str(uuid.uuid4())
        _, body, first = interpret("恢复刚才的任务", replay_id)
        _, _, replay = interpret("恢复刚才的任务", replay_id)
        assert first == replay and first[0] == 200
        body["text"] = "停止任务"
        headers = {
            "X-Request-ID": replay_id,
            "Idempotency-Key": replay_id,
            csrf["headerName"]: csrf["token"],
        }
        conflict = call("/api/voice/intelligence/interpretations", body, headers)
        assert conflict[0] == 409 and conflict[1].get("code") == "IDEMPOTENCY_CONFLICT"
        report["cases"].append(
            {
                "id": "D3-10",
                "result": "PASS",
                "requestId": replay_id,
                "identicalReplay": True,
                "changedBody": "IDEMPOTENCY_CONFLICT",
            }
        )
        report["result"] = "PASS"
    finally:
        report["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"result": report["result"], "cases": len(report["cases"])}))


if __name__ == "__main__":
    main()
