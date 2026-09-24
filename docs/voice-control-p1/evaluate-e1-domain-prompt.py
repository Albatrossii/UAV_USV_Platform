"""Compare domain prompting profiles without storing reference or recognized text."""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import time

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("e1_corpus_evaluator", HERE / "evaluate-e1-corpus.py")
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)

DOMAIN_TERMS = EVALUATOR.PRODUCTION_TRANSCRIBE_OPTIONS.get("initial_prompt", "")


def evaluate_profile(model, samples, name, additions):
    rows = []
    total_edits = total_chars = term_hits = term_total = 0
    options = {**EVALUATOR.PRODUCTION_TRANSCRIBE_OPTIONS, **additions}
    for item in samples:
        audio = decode_audio(item["path"], sampling_rate=16000)
        started = time.perf_counter()
        segments, _ = model.transcribe(audio, **options)
        hypothesis = "".join(segment.text for segment in segments)
        elapsed = time.perf_counter() - started
        reference, predicted = EVALUATOR.normalize(item["reference"]), EVALUATOR.normalize(hypothesis)
        edits = EVALUATOR.edit_distance(reference, predicted)
        terms = [EVALUATOR.normalize(term) for term in item.get("terms", []) if EVALUATOR.normalize(term)]
        hits = sum(term in predicted for term in terms)
        total_edits += edits
        total_chars += len(reference)
        term_hits += hits
        term_total += len(terms)
        rows.append({
            "id": item["id"], "edits": edits, "referenceChars": len(reference),
            "cer": round(edits / len(reference), 6), "termHits": hits, "termTotal": len(terms),
            "elapsedSeconds": round(elapsed, 3),
            "hypothesisSha256": hashlib.sha256(hypothesis.encode()).hexdigest(),
        })
    return {
        "profile": name, "samples": len(rows), "cer": round(total_edits / total_chars, 6),
        "termHitRate": round(term_hits / term_total, 6), "termHits": term_hits, "termTotal": term_total,
        "elapsedSeconds": round(sum(row["elapsedSeconds"] for row in rows), 3), "sampleDetails": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--scope", choices=("terminology", "all"), default="terminology")
    parser.add_argument("--profiles", default="baseline,initial_prompt,hotwords,combined")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    manifest_path = pathlib.Path(args.manifest)
    samples = EVALUATOR.validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8-sig")))
    if args.scope == "terminology":
        samples = [sample for sample in samples if sample["group"] == "terminology"]
    available = {
        "baseline": {"initial_prompt": None},
        "initial_prompt": {"initial_prompt": DOMAIN_TERMS},
        "hotwords": {"initial_prompt": None, "hotwords": DOMAIN_TERMS},
        "combined": {"initial_prompt": DOMAIN_TERMS, "hotwords": DOMAIN_TERMS},
    }
    names = [name.strip() for name in args.profiles.split(",") if name.strip()]
    if not names or any(name not in available for name in names):
        raise ValueError("profiles must be selected from baseline,initial_prompt,hotwords,combined")
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads,
                         num_workers=1, local_files_only=True)
    results = [evaluate_profile(model, samples, name, available[name]) for name in names]
    report = {
        "datasetId": "e1-zh-60-v1", "scope": args.scope,
        "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "modelRevision": EVALUATOR.REVISION, "threads": args.threads,
        "productionProfile": EVALUATOR.PRODUCTION_TRANSCRIBE_OPTIONS,
        "domainTermsSha256": hashlib.sha256(DOMAIN_TERMS.encode()).hexdigest(),
        "results": results,
        "interpretationLimit": "Profile selection on this corpus is tuning evidence, not an independent accuracy acceptance result.",
        "privacy": "No audio, reference text, recognized text, private path, or credential is included.",
    }
    pathlib.Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"scope": args.scope, "profiles": names, "samples": len(samples)}))


if __name__ == "__main__":
    main()
