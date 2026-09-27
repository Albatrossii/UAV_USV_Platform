"""Compare ASR CPU/decoder profiles against the same local audio without saving text."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time

from asr_server import (
    LocalEngine,
    MODEL_REVISION,
    PRODUCTION_TRANSCRIBE_OPTIONS,
    decode_audio,
)

MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
}
SUPPORTED_BEAMS = (1, 2, 3, 5)


def parse_ints(value: str, name: str, allowed: tuple[int, ...]) -> list[int]:
    try:
        values = [int(item.strip()) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{name} must be comma-separated integers") from error
    if not values or any(item not in allowed for item in values) or len(set(values)) != len(values):
        choices = ", ".join(str(item) for item in allowed)
        raise argparse.ArgumentTypeError(f"{name} values must be unique members of: {choices}")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the same local audio across CPU thread and beam profiles; never print transcript text."
    )
    parser.add_argument("audio", type=Path, help="Local audio file; read once and never copied or uploaded.")
    parser.add_argument("--model-path", type=Path, default=os.environ.get("ASR_MODEL_PATH"))
    parser.add_argument(
        "--threads", default=os.environ.get("ASR_CPU_THREADS", "6"),
        help="Comma-separated CPU thread candidates, for example 4,6,8.",
    )
    parser.add_argument("--beams", default="5,2", help="Unique beam sizes; beam 5 is required as reference.")
    parser.add_argument("--repeats", type=int, default=2, help="Measured runs per profile (1..5), alternating order.")
    return parser.parse_args()


def normalize_digest(text: str) -> str:
    normalized = "".join(text.casefold().split()).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def run_profile(engine: LocalEngine, samples, beam: int) -> tuple[int, str, int]:
    options = {**PRODUCTION_TRANSCRIBE_OPTIONS, "beam_size": beam}
    started = time.perf_counter()
    segments, _ = engine.model.transcribe(samples, **options)
    pieces: list[str] = []
    char_count = 0
    try:
        for segment in segments:
            if time.perf_counter() - started >= 120:
                raise TimeoutError("profile exceeded 120 seconds")
            pieces.append(segment.text)
            char_count += len(segment.text)
            if char_count > 500:
                raise ValueError("transcript exceeded 500 characters")
    finally:
        close = getattr(segments, "close", None)
        if close:
            close()
    transcript = "".join(pieces).strip()
    if not transcript:
        raise ValueError("no speech detected")
    return round((time.perf_counter() - started) * 1000), normalize_digest(transcript), len(transcript)


def main() -> int:
    args = parse_args()
    mime = MIME_BY_SUFFIX.get(args.audio.suffix.lower())
    if not mime:
        raise SystemExit("Unsupported extension; use MP3, WAV, WebM, Ogg, MP4, or M4A.")
    if not args.audio.is_file() or args.model_path is None:
        raise SystemExit("Provide an existing local audio file and --model-path (or ASR_MODEL_PATH).")
    if not 1 <= args.repeats <= 5:
        raise SystemExit("--repeats must be between 1 and 5.")
    try:
        threads = parse_ints(args.threads, "--threads", tuple(range(1, 17)))
        beams = parse_ints(args.beams, "--beams", SUPPORTED_BEAMS)
    except argparse.ArgumentTypeError as error:
        raise SystemExit(str(error)) from None
    if 5 not in beams:
        raise SystemExit("Include reference beam 5.")

    audio = args.audio.read_bytes()
    decoded_at = time.perf_counter()
    samples, duration_ms = decode_audio(audio, mime, time.monotonic() + 120)
    decode_ms = round((time.perf_counter() - decoded_at) * 1000)
    del audio

    results: list[dict[str, object]] = []
    for thread_count in threads:
        try:
            engine = LocalEngine(args.model_path, thread_count)
            measured: dict[int, list[int]] = {beam: [] for beam in beams}
            digests: dict[int, list[str]] = {beam: [] for beam in beams}
            lengths: dict[int, list[int]] = {beam: [] for beam in beams}
            # Reverse the candidate order each run so later profiles do not always benefit from warm caches.
            for run_index in range(args.repeats):
                order = beams if run_index % 2 == 0 else list(reversed(beams))
                for beam in order:
                    elapsed_ms, digest, length = run_profile(engine, samples, beam)
                    measured[beam].append(elapsed_ms)
                    digests[beam].append(digest)
                    lengths[beam].append(length)

            reference_digest = digests[5][0]
            for beam in beams:
                durations = measured[beam]
                results.append({
                    "threads": thread_count,
                    "beamSize": beam,
                    "runsMs": durations,
                    "medianMs": round(statistics.median(durations)),
                    "sameAsBeam5": all(digest == reference_digest for digest in digests[beam]),
                    "transcriptCharacters": round(statistics.median(lengths[beam])),
                })
            del engine
        except Exception as error:
            raise SystemExit(f"profile failed: {type(error).__name__}") from None

    for thread_count in threads:
        reference = next(
            int(item["medianMs"]) for item in results
            if item["threads"] == thread_count and item["beamSize"] == 5
        )
        for item in results:
            if item["threads"] == thread_count:
                item["speedupVsBeam5"] = round(reference / int(item["medianMs"]), 2)

    print(json.dumps({
        "modelRevision": MODEL_REVISION,
        "audioDurationMs": duration_ms,
        "decodeMs": decode_ms,
        "sameAudioForAllProfiles": True,
        "repeatOrderAlternates": True,
        "transcriptTextSaved": False,
        "transcriptTextPrinted": False,
        "profiles": results,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
