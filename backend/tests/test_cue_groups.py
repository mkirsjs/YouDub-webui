from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from backend.app.v1.cue_groups import group_segments, write_span_reference
from backend.app.v1.tts_process import _content_matches, _write_receipt


def _cue(identifier, start, end, text, speaker=None):
    return SimpleNamespace(id=identifier, start_ms=start, end_ms=end, text=text, speaker_id=speaker)


def test_unknown_speakers_join_only_across_a_short_pause_and_keep_every_word():
    cues = [
        _cue("a", 0, 800, "Hello,"),
        _cue("b", 900, 1600, "there."),
        _cue("c", 2500, 3200, "Next line."),
    ]
    translations = {"a": "你好，", "b": "那里。", "c": "下一句。"}
    separate = group_segments(cues, translations, enabled=False)
    grouped = group_segments(cues, translations, enabled=True)
    assert [item.member_ids for item in separate] == [("a",), ("b",), ("c",)]
    assert [item.member_ids for item in grouped] == [("a", "b"), ("c",)]
    assert grouped[0].reference_text == "Hello, there."
    assert grouped[0].translation_text == "你好， 那里。"
    assert grouped[0].start_ms == 0 and grouped[0].end_ms == 1600
    assert grouped[0].speaker_known is False
    assert "Hello," in grouped[0].reference_text and "there." in grouped[0].reference_text
    assert "你好，" in grouped[0].translation_text and "那里。" in grouped[0].translation_text


def test_known_speakers_do_not_cross_and_a_long_pause_stays_split():
    cues = [
        _cue("a", 0, 500, "One,", "s1"),
        _cue("b", 600, 1000, "two.", "s2"),
        _cue("c", 1100, 1500, "same,", "s1"),
        _cue("d", 1600, 2000, "speaker.", "s1"),
    ]
    translations = {item.id: item.text for item in cues}
    groups = group_segments(cues, translations, enabled=True)
    assert [item.member_ids for item in groups] == [("a",), ("b",), ("c", "d")]
    assert groups[-1].speaker_known is True


def test_span_reference_keeps_the_internal_pause_and_old_receipt_does_not_match(tmp_path):
    rate = 16000
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(first, np.full(round(0.8 * rate), 0.2), rate)
    sf.write(second, np.full(round(0.7 * rate), 0.2), rate)
    members = [_cue("a", 0, 800, "Hello,"), _cue("b", 900, 1600, "there.")]
    destination = tmp_path / "span.wav"
    write_span_reference(members, {"a": {"path": first}, "b": {"path": second}}, destination)
    info = sf.info(destination)
    assert abs(info.frames / info.samplerate - 1.6) < 0.02
    model = tmp_path / "model"
    model.mkdir()
    wav = tmp_path / "000001.wav"
    sf.write(wav, np.full(rate, 0.2), rate)
    clip = {"segment_id": "a", "text": "你好， 那里。", "reference_text": "Hello, there.",
            "reference_path": str(destination), "member_ids": ["a", "b"]}
    _write_receipt(wav, {"segment_id": "a", "text": "你好，", "reference_text": "Hello,",
                         "reference_path": str(first)}, model, None, legacy=True, ordinal=1)
    assert _content_matches(json.loads((wav.parent / "000001.wav.receipt.json").read_text()), clip, model, wav, 1) is False
