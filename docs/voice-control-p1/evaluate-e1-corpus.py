"""Evaluate the frozen 60-recording E1 corpus. Never invents reference text."""
import argparse
import hashlib
import json
import math
import os
import pathlib
import statistics
import threading
import time
import unicodedata

import psutil
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("P", "Z"))


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


class PeakMemory:
    def __init__(self):
        self.stop = threading.Event()
        self.peak = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        process = psutil.Process()
        while not self.stop.wait(0.05):
            try:
                rss = process.memory_info().rss
                for child in process.children(recursive=True):
                    rss += child.memory_info().rss
                self.peak = max(self.peak, rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def validate_manifest(document):
    if document.get("normalizationRevision") != "nfkc-lower-remove-punctuation-and-separators-v1":
        raise ValueError("normalizationRevision is not frozen v1")
    samples = document.get("samples")
    if not isinstance(samples, list) or len(samples) != 60:
        raise ValueError("E1 requires exactly 60 samples")
    if len({row.get("speakerId") for row in samples}) != 3:
        raise ValueError("E1 requires exactly 3 speakers")
    counts = {group: sum(row.get("group") == group for row in samples) for group in ("quiet", "noise", "terminology")}
    if counts != {"quiet": 40, "noise": 10, "terminology": 10}:
        raise ValueError(f"invalid group counts: {counts}")
    if len({row.get("id") for row in samples}) != 60:
        raise ValueError("sample ids must be unique")
    for row in samples:
        path = pathlib.Path(row.get("path", ""))
        reference = row.get("reference")
        if not path.is_file() or not isinstance(reference, str) or not normalize(reference):
            raise ValueError(f"missing audio or human reference: {row.get('id')}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != row.get("audioSha256"):
            raise ValueError(f"audio hash mismatch: {row.get('id')}")
        if not isinstance(row.get("terms", []), list):
            raise ValueError(f"terms must be a list: {row.get('id')}")
    return samples


def evaluate(model_name, model_path, samples, threads):
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    started = time.perf_counter()
    with PeakMemory() as memory:
        model = WhisperModel(model_path, device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True)
        load_seconds = time.perf_counter() - started
        rows = []
        total_edits = total_chars = term_hits = term_total = 0
        for index, item in enumerate(samples):
            audio = decode_audio(item["path"], sampling_rate=16000)
            duration = len(audio) / 16000
            inference_started = time.perf_counter()
            segments, _ = model.transcribe(audio, language="zh", beam_size=5, vad_filter=False)
            hypothesis = "".join(segment.text for segment in segments)
            elapsed = time.perf_counter() - inference_started
            reference, predicted = normalize(item["reference"]), normalize(hypothesis)
            edits = edit_distance(reference, predicted)
            terms = [normalize(term) for term in item.get("terms", []) if normalize(term)]
            hits = sum(term in predicted for term in terms)
            total_edits += edits
            total_chars += len(reference)
            term_hits += hits
            term_total += len(terms)
            rows.append({
                "id": item["id"], "speakerId": item["speakerId"], "group": item["group"],
                "durationSeconds": round(duration, 3), "elapsedSeconds": round(elapsed, 3),
                "rtf": round(elapsed / duration, 4), "referenceChars": len(reference), "edits": edits,
                "cer": round(edits / len(reference), 6), "termHits": hits, "termTotal": len(terms),
                "hypothesisSha256": hashlib.sha256(hypothesis.encode()).hexdigest(),
                "phase": "cold" if index == 0 else "warm",
            })
    warm = rows[1:]
    groups = {}
    for group in ("quiet", "noise", "terminology"):
        selected = [row for row in rows if row["group"] == group]
        groups[group] = {
            "samples": len(selected),
            "cer": round(sum(row["edits"] for row in selected) / sum(row["referenceChars"] for row in selected), 6),
        }
    return {
        "model": model_name, "modelPathSha256": hashlib.sha256(str(pathlib.Path(model_path).resolve()).encode()).hexdigest(),
        "loadSeconds": round(load_seconds, 3), "coldInferenceSeconds": rows[0]["elapsedSeconds"],
        "warmP50Seconds": round(statistics.median(row["elapsedSeconds"] for row in warm), 3),
        "warmP95Seconds": round(percentile([row["elapsedSeconds"] for row in warm], 0.95), 3),
        "overallRtf": round(sum(row["elapsedSeconds"] for row in rows) / sum(row["durationSeconds"] for row in rows), 4),
        "peakProcessTreeRssBytes": memory.peak, "timeoutRate": 0.0,
        "cer": round(total_edits / total_chars, 6),
        "termHitRate": round(term_hits / term_total, 6) if term_total else None,
        "groups": groups, "samples": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--model", action="append", required=True, help="name=local_model_path; pass base and small")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8-sig"))
    samples = validate_manifest(manifest)
    models = []
    for value in args.model:
        name, separator, path = value.partition("=")
        if not separator or not pathlib.Path(path).is_dir():
            raise ValueError("model must be name=existing_local_directory")
        models.append(evaluate(name, path, samples, args.threads))
    if {model["model"] for model in models} != {"base", "small"}:
        raise ValueError("Q02 requires both base and small on the same frozen corpus")
    report = {
        "datasetId": manifest["datasetId"], "datasetManifestSha256": hashlib.sha256(pathlib.Path(args.manifest).read_bytes()).hexdigest(),
        "normalizationRevision": manifest["normalizationRevision"], "threads": args.threads,
        "models": models, "privacy": "No audio, reference text, or recognized text is copied into this report.",
    }
    pathlib.Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"datasetId": report["datasetId"], "models": [row["model"] for row in models], "samples": len(samples)}))


if __name__ == "__main__":
    main()
