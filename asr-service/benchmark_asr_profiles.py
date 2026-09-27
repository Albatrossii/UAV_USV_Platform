"""Compare ASR decoding profiles against the same local audio without saving text."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from asr_server import (
    AsrError,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the same local audio through several beam sizes; emit timings and text fingerprints only."
    )
    parser.add_argument("audio", type=Path, help="Local audio file; it is read once and never copied or uploaded.")
    parser.add_argument("--model-path", type=Path, default=os.environ.get("ASR_MODEL_PATH"))
    parser.add_argument("--threads", type=int, default=int(os.environ.get("ASR_CPU_THREADS", "6")))
    parser.add_argument("--beams", default="5,2", help="Comma-separated supported sizes; beam 5 is required as reference.")
    return parser.parse_args()


def normalize(text: str) -> str:
    return "".join(text.casefold().split())


def main() -> int:
    args = parse_args()
    mime = MIME_BY_SUFFIX.get(args.audio.suffix.lower())
    if not mime:
        raise SystemExit("Unsupported extension; use MP3, WAV, WebM, Ogg, MP4, or M4A.")
    if not args.audio.is_file() or args.model_path is None:
        raise SystemExit("Provide an existing local audio file and --model-path (or ASR_MODEL_PATH).")
    if not 1 <= args.threads <= 16:
        raise SystemExit("--threads must be between 1 and 16.")
    try:
        beams = [int(value.strip()) for value in args.beams.split(",")]
    except ValueError as error:
        raise SystemExit("--beams must be comma-separated integers.") from error
    if not beams or 5 not in beams or any(size not in SUPPORTED_BEAMS for size in beams):
        raise SystemExit("Include reference beam 5; supported sizes are 1, 2, 3, and 5.")
    if len(set(beams)) != len(beams):
        raise SystemExit("Do not repeat a beam size.")

    audio = args.audio.read_bytes()
    decoded_at = time.perf_counter()
    samples, duration_ms = decode_audio(audio, mime, time.monotonic() + 120)
    decode_ms = round((time.perf_counter() - decoded_at) * 1000)
    del audio

    # One model instance and one decoded waveform keep profile comparisons controlled.
    engine = LocalEngine(args.model_path, args.threads)
    results: list[dict[str, object]] = []
    transcripts: dict[int, list[str]] = {}
    for beam in beams:
        options = {**PRODUCTION_TRANSCRIBE_OPTIONS, "beam_size": beam}
        started = time.perf_counter()
        deadline = time.monotonic() + 120
        try:
            segments, _ = engine.model.transcribe(samples, **options)
            pieces: list[str] = []
            char_count = 0
            for segment in segments:
                if time.monotonic() >= deadline:
                    raise TimeoutError("profile exceeded 120 seconds")
                pieces.append(segment.text)
                char_count += len(segment.text)
                if char_count > 500:
                    raise ValueError("transcript exceeded 500 characters")
            transcript = "".join(pieces).strip()
            if not transcript:
                raise ValueError("no speech detected")
        except (AsrError, TimeoutError, ValueError) as error:
            raise SystemExit(f"beam {beam} failed: {type(error).__name__}") from None
        inference_ms = round((time.perf_counter() - started) * 1000)
        transcripts[beam] = pieces
        results.append({
            "beamSize": beam,
            "inferenceMs": inference_ms,
            "transcriptCharacters": len(transcript),
        })
        del segments, pieces, transcript

    reference = normalize("".join(transcripts[5]))
    reference_ms = next(int(item["inferenceMs"]) for item in results if item["beamSize"] == 5)
    for result in results:
        beam = int(result["beamSize"])
        result["sameAsBeam5"] = normalize("".join(transcripts[beam])) == reference
        result["speedupVsBeam5"] = round(reference_ms / int(result["inferenceMs"]), 2)

    print(json.dumps({
        "modelRevision": MODEL_REVISION,
        "threads": args.threads,
        "audioDurationMs": duration_ms,
        "decodeMs": decode_ms,
        "sameAudioForAllProfiles": True,
        "transcriptTextSaved": False,
        "profiles": results,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
