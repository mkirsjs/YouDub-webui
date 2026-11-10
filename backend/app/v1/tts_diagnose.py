"""Read-only edge report for dubbed clips already on disk.

Prints peaks, leading and trailing silence, and gaps between dubbed intervals.
It does not open the source video, write inside the task, or change a wav.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def _runs(quiet: np.ndarray) -> list[tuple[int, int]]:
    if not len(quiet):
        return []
    changes = np.flatnonzero(quiet[1:] != quiet[:-1]) + 1
    bounds = np.concatenate(([0], changes, [len(quiet)]))
    return [(int(start), int(end)) for start, end in zip(bounds, bounds[1:]) if quiet[start]]


def silence_report(samples: np.ndarray, rate: int, *, threshold_db: float, min_s: float) -> dict:
    magnitude = np.max(np.abs(samples), axis=1) if samples.ndim == 2 else np.abs(np.asarray(samples))
    floor = 10 ** (threshold_db / 20)
    runs = _runs(magnitude < floor)
    minimum = min_s * rate

    def edge(which: str) -> float | None:
        if not runs:
            return None
        start, end = runs[0] if which == "lead" else runs[-1]
        if which == "lead" and start != 0:
            return None
        if which == "tail" and end != len(magnitude):
            return None
        if end - start < minimum:
            return None
        return round((end - start) / rate, 3)

    interior = [round((end - start) / rate, 3) for start, end in runs
                if start != 0 and end != len(magnitude) and end - start >= minimum]
    peak = float(np.max(magnitude)) if len(magnitude) else 0.0
    return {
        "seconds": round(len(magnitude) / rate, 3),
        "peak": round(peak, 6),
        "lead_silence_s": edge("lead"),
        "tail_silence_s": edge("tail"),
        "interior_silence_s": interior,
    }


def _wavs(task_root: Path) -> list[Path]:
    attempts = task_root / "attempts"
    if not attempts.is_dir():
        return []
    found = []
    for path in attempts.glob("*/*/tts/[0-9][0-9][0-9][0-9][0-9][0-9].wav"):
        resolved = path.resolve()
        if resolved.is_file() and resolved.is_relative_to(task_root.resolve()) and "preserved" not in resolved.parts:
            found.append(resolved)
    return sorted(found)


def _alignments(task_root: Path) -> list[dict]:
    rows = []
    for path in sorted((task_root / "attempts").glob("*/*/alignment.json")):
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(task_root.resolve()):
            continue
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        segments = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(segments, list):
            continue
        ordered = sorted(segments, key=lambda item: (item.get("dubbed_start_ms", 0), item.get("segment_id", "")))
        gaps = []
        for previous, current in zip(ordered, ordered[1:]):
            gap = (current.get("dubbed_start_ms", 0) - previous.get("dubbed_end_ms", 0)) / 1000
            if gap >= 0.05:
                gaps.append({"after": previous.get("segment_id"), "before": current.get("segment_id"), "gap_s": round(gap, 3)})
        rows.append({"path": str(resolved.relative_to(task_root.resolve())), "gaps_over_0_05s": gaps})
    return rows


def report(task_root: Path, *, threshold_db: float = -40, min_s: float = 0.05, limit: int | None = None) -> dict:
    task_root = task_root.resolve()
    if not task_root.is_dir():
        raise FileNotFoundError("Task root is not a directory.")
    paths = _wavs(task_root)
    selected = paths if limit is None else paths[:limit]
    clips = []
    for path in selected:
        samples, rate = sf.read(path, dtype="float32", always_2d=True)
        item = silence_report(samples, rate, threshold_db=threshold_db, min_s=min_s)
        item["path"] = str(path.relative_to(task_root))
        clips.append(item)
    tails = [item["tail_silence_s"] for item in clips if item["tail_silence_s"]]
    return {
        "task_root": str(task_root),
        "threshold_db": threshold_db,
        "min_silence_s": min_s,
        "wavs": len(paths),
        "reported": len(clips),
        "tail_silence_count": len(tails),
        "clips": clips,
        "alignments": _alignments(task_root),
        "wrote_files": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--threshold-db", type=float, default=-40)
    parser.add_argument("--min-silence-s", type=float, default=0.05)
    parser.add_argument("--limit", type=int)
    options = parser.parse_args(argv)
    try:
        print(json.dumps(report(options.task_root, threshold_db=options.threshold_db,
                                min_s=options.min_silence_s, limit=options.limit), ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
