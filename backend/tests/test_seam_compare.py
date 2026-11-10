from __future__ import annotations

import json

import numpy as np
import soundfile as sf

from backend.app.v1.seam_compare import compare


def test_short_interval_is_copied_outside_the_task_and_the_task_is_unchanged(tmp_path):
    task = tmp_path / "task" / "attempts" / "1" / "mix"
    task.mkdir(parents=True)
    (task / "adjusted").mkdir()
    rate = 16000
    tone = np.full((rate, 1), 0.2, dtype="float32")
    sf.write(task / "adjusted" / "0001.wav", tone, rate)
    sf.write(task / "mixed.wav", np.full((2 * rate, 1), 0.1), rate)
    (task / "alignment.json").write_text(json.dumps({"segments": [
        {"segment_id": "a", "source_start_ms": 0, "source_end_ms": 1000, "dubbed_start_ms": 0, "dubbed_end_ms": 1000},
        {"segment_id": "b", "source_start_ms": 1000, "source_end_ms": 2000, "dubbed_start_ms": 1000, "dubbed_end_ms": 2000},
    ]}))
    before = (task / "alignment.json").read_bytes()
    output = tmp_path / "compare"
    summary = compare(task.parents[2], output, 0, 500)
    assert (task / "alignment.json").read_bytes() == before
    assert summary["cue_count_in_alignment"] == 2
    assert summary["cues"][0]["neighbor"] is False
    assert summary["cues"][1]["neighbor"] is True
    assert (output / "comparison.wav").is_file()
    assert summary["wrote_inside_task"] is False
