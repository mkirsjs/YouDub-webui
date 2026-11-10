"""Read-only short comparison of one dubbed interval.

Copies slices of vocals, the cue reference, raw TTS, time-adjusted speech,
background and the final mix into a directory outside the task. It does not
modify the task and does not decide why a joint sounds wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def _slice(path: Path, start_ms: int, end_ms: int, destination: Path) -> dict | None:
    if not path.is_file():
        return None
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    start = max(0, round(start_ms * rate / 1000))
    end = min(len(samples), round(end_ms * rate / 1000))
    if end <= start:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, samples[start:end], rate, subtype="PCM_16")
    return {"path": destination.name, "seconds": round((end - start) / rate, 3), "rate": rate}


def _latest_alignment(task_root: Path) -> Path:
    found = [path for path in task_root.glob("attempts/*/*/alignment.json") if path.is_file()]
    if not found:
        raise FileNotFoundError("No alignment.json under the task attempts.")
    return max(found, key=lambda path: path.stat().st_mtime)


def compare(task_root: Path, output_dir: Path, start_ms: int, end_ms: int) -> dict:
    task_root = task_root.resolve()
    output_dir = output_dir.resolve()
    if output_dir == task_root or output_dir.is_relative_to(task_root):
        raise ValueError("The comparison directory must be outside the task.")
    output_dir.mkdir(parents=True, exist_ok=True)
    alignment_path = _latest_alignment(task_root)
    mix_dir = alignment_path.parent
    segments = json.loads(alignment_path.read_text(encoding="utf-8"))["segments"]
    selected = []
    for index, segment in enumerate(segments):
        if segment["dubbed_end_ms"] >= start_ms and segment["dubbed_start_ms"] <= end_ms:
            selected.append(index)
    if not selected:
        raise ValueError("No dubbed cue overlaps the requested interval.")
    first, last = max(0, selected[0] - 1), min(len(segments) - 1, selected[-1] + 1)
    vocals = next(task_root.glob("attempts/*/*/separation/vocals.wav"), None)
    background = next(task_root.glob("attempts/*/*/separation/background.wav"), None)
    cues = []
    pieces = []
    rate = 16000
    for index in range(first, last + 1):
        segment = segments[index]
        ordinal = index + 1
        raw = next(mix_dir.glob(f"tts/{ordinal:06d}.wav"), None) or next(task_root.glob(f"attempts/*/*/tts/{ordinal:06d}.wav"), None)
        reference = next(task_root.glob(f"attempts/*/*/tts/references/{ordinal:06d}.wav"), None)
        adjusted = mix_dir / "adjusted" / f"{ordinal:04d}.wav"
        item = {"index": ordinal, "segment_id": segment["segment_id"], "neighbor": index not in selected,
                "source_ms": [segment["source_start_ms"], segment["source_end_ms"]],
                "dubbed_ms": [segment["dubbed_start_ms"], segment["dubbed_end_ms"]]}
        if raw and raw.is_file() and adjusted.is_file():
            raw_info, adjusted_info = sf.info(raw), sf.info(adjusted)
            item["measured_adjusted_over_raw"] = round(adjusted_info.frames / raw_info.frames, 4) if raw_info.frames else None
            item["time_adjust"] = "ffmpeg atempo. base is clamp(0.99 * source_span / raw_duration, 0.8, 1.2); local is clamp(remaining_source / (raw * base), 0.75, 1.25)."
        for label, path, begin, finish in (
            ("vocals", vocals, segment["source_start_ms"], segment["source_end_ms"]),
            ("reference", reference, 0, 10**9),
            ("raw_tts", raw, 0, 10**9),
            ("adjusted", adjusted if adjusted.is_file() else None, 0, 10**9),
        ):
            if path is None:
                item[label] = None
                continue
            written = _slice(path, begin, finish, output_dir / f"{ordinal:04d}-{label}.wav")
            item[label] = written
            if written and label == "adjusted":
                audio, file_rate = sf.read(output_dir / written["path"], dtype="float32", always_2d=True)
                pieces.append(audio)
                rate = file_rate
        cues.append(item)
    window = _slice(mix_dir / "mixed.wav", start_ms, end_ms, output_dir / "mix-window.wav")
    background_slice = _slice(background, start_ms, end_ms, output_dir / "background-window.wav") if background else None
    if pieces:
        gap = np.zeros((round(0.25 * rate), pieces[0].shape[1]), dtype=np.float32)
        sequence = []
        for audio in pieces:
            if sequence:
                sequence.append(gap)
            if audio.shape[1] != pieces[0].shape[1]:
                audio = np.repeat(audio.mean(axis=1, keepdims=True), pieces[0].shape[1], axis=1)
            sequence.append(audio)
        sf.write(output_dir / "comparison.wav", np.concatenate(sequence), rate, subtype="PCM_16")
    summary = {
        "task_root_name": task_root.name,
        "alignment": str(alignment_path.relative_to(task_root)),
        "window_dubbed_ms": [start_ms, end_ms],
        "cue_count_in_alignment": len(segments),
        "cues": cues,
        "mix_window": window,
        "background_window": background_slice,
        "comparison_wav": "comparison.wav" if pieces else None,
        "wrote_inside_task": False,
        "not_a_listening_result": True,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    options = parser.parse_args(argv)
    try:
        compare(options.task_root, options.output_dir, options.start_ms, options.end_ms)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
