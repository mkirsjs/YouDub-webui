from __future__ import annotations

import json

import numpy as np
import soundfile as sf

from backend.app.v1.tts_diagnose import main, report


def _clip(path, samples, rate=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, samples.astype("float32"), rate, subtype="PCM_16")


def test_report_reads_edges_and_gaps_without_writing(tmp_path):
    task = tmp_path / "task"
    work = task / "attempts" / "1" / "invoke"
    rate = 16000
    tone = 0.2 * np.sin(2 * np.pi * 220 * np.arange(rate) / rate)
    tone[0] = tone[-1] = 0.2
    tail = np.concatenate([tone, np.zeros(int(0.2 * rate))])
    lead = np.concatenate([np.zeros(int(0.1 * rate)), tone])
    _clip(work / "tts" / "000001.wav", tail)
    _clip(work / "tts" / "000002.wav", lead)
    (work / "alignment.json").write_text(json.dumps({"segments": [
        {"segment_id": "a", "source_start_ms": 0, "source_end_ms": 1000, "dubbed_start_ms": 0, "dubbed_end_ms": 1000},
        {"segment_id": "b", "source_start_ms": 2500, "source_end_ms": 3500, "dubbed_start_ms": 2800, "dubbed_end_ms": 3600},
    ]}))
    before = sorted(path.relative_to(task).as_posix() for path in task.rglob("*") if path.is_file())
    result = report(task)
    after = sorted(path.relative_to(task).as_posix() for path in task.rglob("*") if path.is_file())
    assert after == before
    assert result["wrote_files"] is False
    assert result["wavs"] == 2 and result["tail_silence_count"] == 1
    assert result["clips"][0]["tail_silence_s"] == 0.2
    assert result["clips"][0]["lead_silence_s"] is None
    assert result["clips"][1]["lead_silence_s"] == 0.1
    assert result["alignments"][0]["gaps_over_0_05s"] == [{"after": "a", "before": "b", "gap_s": 1.8}]
    assert main(["--task-root", str(task), "--limit", "1"]) == 0
