"""E1 real Java -> Python audio matrix. Keeps generated audio and transcripts in memory."""
import argparse
import hashlib
import http.cookiejar
import io
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
import uuid

import av
import numpy as np
from faster_whisper.audio import decode_audio


def encode(samples, container, codec, rate=48000, layout="mono"):
    output = io.BytesIO()
    channels = len(av.AudioLayout(layout).channels)
    with av.open(output, mode="w", format=container) as target:
        stream = target.add_stream(codec, rate=rate)
        stream.layout = layout
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
        for start in range(0, len(samples), 1600):
            mono = samples[start : start + 1600]
            values = np.repeat(mono.reshape(1, -1), channels, axis=0)
            frame = av.AudioFrame.from_ndarray(values, format="fltp", layout=layout)
            frame.sample_rate = 16000
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    target.mux(packet)
        for converted in resampler.resample(None):
            for packet in stream.encode(converted):
                target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
    return output.getvalue()


def silence(seconds, container="mp3", codec="libmp3lame", rate=48000, layout="mono"):
    return encode(np.zeros(round(seconds * 16000), dtype=np.float32), container, codec, rate, layout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    source = pathlib.Path(args.source).read_bytes()
    samples = decode_audio(io.BytesIO(source), sampling_rate=16000)
    secrets = json.loads(pathlib.Path(args.credentials).read_text(encoding="utf-8-sig"))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )

    def request(path, body=None, headers=None):
        req = urllib.request.Request(args.base.rstrip("/") + path, data=body, headers=headers or {})
        try:
            response = opener.open(req, timeout=140)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), dict(response.headers)

    status, _, _ = request(
        "/api/auth/login",
        json.dumps({"username": "p0_admin", "password": secrets["adminPassword"]}).encode(),
        {"Content-Type": "application/json"},
    )
    if status != 200:
        raise SystemExit("Login failed; credentials are not printed")
    status, csrf, _ = request("/api/auth/csrf")
    if status != 200:
        raise SystemExit("CSRF acquisition failed")

    def transcribe(name, audio, mime, expected_status, expected_code):
        request_id = str(uuid.uuid4())
        boundary = "e1-" + uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="requestId"\r\n\r\n{request_id}'
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="locale"\r\n\r\nzh-CN'
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="sample"'
            f'\r\nContent-Type: {mime}\r\n\r\n'
        ).encode() + audio + f"\r\n--{boundary}--\r\n".encode()
        headers = {
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "X-Request-ID": request_id,
            "Idempotency-Key": request_id,
            csrf["data"]["headerName"]: csrf["data"]["token"],
        }
        started = time.perf_counter()
        actual_status, payload, _ = request("/api/voice/intelligence/transcriptions", body, headers)
        elapsed = time.perf_counter() - started
        if actual_status != expected_status or payload.get("code") != expected_code:
            raise AssertionError((name, actual_status, payload.get("code")))
        row = {
            "case": name,
            "requestId": request_id,
            "mime": mime,
            "audioSha256": hashlib.sha256(audio).hexdigest(),
            "audioBytes": len(audio),
            "status": actual_status,
            "code": payload.get("code"),
            "elapsedSeconds": round(elapsed, 3),
        }
        if actual_status == 200:
            data = payload["data"]
            row.update(
                durationMs=data["durationMs"],
                textCodePoints=len(data["text"]),
                transcriptSha256=hashlib.sha256(data["text"].encode()).hexdigest(),
            )
        return row

    formats = (
        ("webm-opus", "webm", "libopus", "audio/webm"),
        ("ogg-opus", "ogg", "libopus", "audio/ogg"),
        ("mp4-aac", "mp4", "aac", "audio/mp4"),
        ("wav-pcm", "wav", "pcm_s16le", "audio/wav"),
        ("mp3", "mp3", "libmp3lame", "audio/mpeg"),
    )
    rows = []
    for name, container, codec, mime in formats:
        rows.append(transcribe(name, encode(samples, container, codec), mime, 200, "SUCCESS"))
    rows.extend(
        (
            transcribe("rate-96000", silence(1, "wav", "pcm_s16le", 96000), "audio/wav", 415, "VOICE_AUDIO_FORMAT_UNSUPPORTED"),
            transcribe("three-channels", silence(1, "wav", "pcm_s16le", 48000, "2.1"), "audio/wav", 415, "VOICE_AUDIO_FORMAT_UNSUPPORTED"),
            transcribe("damaged-container", b"not an audio container", "audio/ogg", 415, "VOICE_AUDIO_FORMAT_UNSUPPORTED"),
            transcribe("decoded-over-60s", silence(60.01), "audio/mpeg", 413, "VOICE_AUDIO_TOO_LONG"),
        )
    )
    # The tenth request proves bounded rejects did not exhaust the service.
    rows.append(transcribe("recovery-after-rejects", encode(samples, "webm", "libopus"), "audio/webm", 200, "SUCCESS"))
    report = {
        "evidenceId": "E1-C06-C12-AUDIO-MATRIX-20260924",
        "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceSha256": hashlib.sha256(source).hexdigest(),
        "sourceUse": "Authorized Chinese recording decoded in memory; source and generated audio are not written to evidence.",
        "results": rows,
        "assertions": {
            "fiveContainersPassed": all(row["status"] == 200 for row in rows[:5]),
            "resourceLimitsRejected": all(row["status"] in (413, 415) for row in rows[5:9]),
            "serviceRecovered": rows[-1]["status"] == 200,
            "pass": True,
        },
        "privacy": "No transcript, audio, password, token, cookie, or key is stored.",
    }
    pathlib.Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evidenceId": report["evidenceId"], "assertions": report["assertions"]}))


if __name__ == "__main__":
    main()
