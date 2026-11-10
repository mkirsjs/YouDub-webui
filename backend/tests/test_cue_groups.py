from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from backend.app.v1.cue_groups import SHORT_SAMPLE, group_segments, plans, resolve_speaker_scope, unknown_blocks, write_span_reference
from backend.app.v1.tts_process import _content_matches, _write_receipt


def _cue(identifier, start, end, text, speaker=None):
    return SimpleNamespace(id=identifier, start_ms=start, end_ms=end, text=text, speaker_id=speaker)


def _ids(segments, **kwargs):
    translations = {item.id: item.text for item in segments}
    return [item.member_ids for item in group_segments(segments, translations, **kwargs)]


def test_unlabeled_cues_stay_separate_until_the_user_confirms_one_speaker():
    cues = [
        _cue("a", 0, 800, "Hello,"),
        _cue("b", 900, 1600, "there."),
        _cue("c", 2500, 3200, "Next line."),
    ]
    translations = {"a": "你好，", "b": "那里。", "c": "下一句。"}
    separate = group_segments(cues, translations, enabled=False, speaker_scope="single")
    unknown = group_segments(cues, translations, enabled=True, speaker_scope="unknown")
    multi = group_segments(cues, translations, enabled=True, speaker_scope="multi")
    single = group_segments(cues, translations, enabled=True, speaker_scope="single")
    assert [item.member_ids for item in separate] == [("a",), ("b",), ("c",)]
    assert [item.member_ids for item in unknown] == [("a",), ("b",), ("c",)]
    assert [item.member_ids for item in multi] == [("a",), ("b",), ("c",)]
    assert [item.member_ids for item in single] == [("a", "b"), ("c",)]
    assert unknown_blocks(cues, speaker_scope="unknown") == [["a", "b"]]
    assert unknown_blocks(cues, speaker_scope="multi") == [["a", "b"]]
    assert unknown_blocks(cues, speaker_scope="single") == []
    assert single[0].reference_text == "Hello, there."
    assert single[0].translation_text == "你好， 那里。"
    assert single[0].speaker_known is False


def test_known_speakers_join_without_single_mode_and_different_speakers_never_join():
    cues = [
        _cue("a", 0, 500, "One,", "s1"),
        _cue("b", 600, 1000, "two.", "s2"),
        _cue("c", 1100, 1500, "same,", "s1"),
        _cue("d", 1600, 2000, "speaker.", "s1"),
        _cue("e", 2100, 2500, "unknown,", None),
    ]
    for scope in ("unknown", "single", "multi"):
        groups = _ids(cues, enabled=True, speaker_scope=scope)
        assert ("a",) in groups and ("b",) in groups
        assert ("c", "d") in groups
        assert ("d", "e") not in groups and ("c", "d", "e") not in groups
    assert _ids(cues, enabled=True, speaker_scope="single")[-1] == ("e",)


def test_timing_boundaries_and_a_missing_label_on_one_side():
    gap_ok = [_cue("a", 0, 500, "Hello,"), _cue("b", 800, 1200, "there")]
    gap_over = [_cue("a", 0, 500, "Hello,"), _cue("b", 801, 1200, "there")]
    span_ok = [_cue("a", 0, 500, "Hello,"), _cue("b", 600, 12_000, "there")]
    span_over = [_cue("a", 0, 500, "Hello,"), _cue("b", 600, 12_001, "there")]
    sentence = [_cue("a", 0, 500, "Hello."), _cue("b", 600, 1000, "Next")]
    mixed = [_cue("a", 0, 500, "Hello,", "s1"), _cue("b", 600, 1000, "there")]
    assert _ids(gap_ok, enabled=True, speaker_scope="single") == [("a", "b")]
    assert _ids(gap_over, enabled=True, speaker_scope="single") == [("a",), ("b",)]
    assert _ids(span_ok, enabled=True, speaker_scope="single") == [("a", "b")]
    assert _ids(span_over, enabled=True, speaker_scope="single") == [("a",), ("b",)]
    assert _ids(sentence, enabled=True, speaker_scope="single") == [("a",), ("b",)]
    assert _ids(mixed, enabled=True, speaker_scope="single") == [("a",), ("b",)]
    assert _ids(gap_ok, enabled=True, speaker_scope="unknown") == [("a",), ("b",)]


def test_short_sample_reproduces_per_cue_and_single_speaker_plans():
    segments = [SimpleNamespace(**item) for item in SHORT_SAMPLE["segments"]]
    result = plans(segments, SHORT_SAMPLE["translations"])
    assert result["diarization"] is False
    assert result["per_cue"] == [["q1"], ["q2"], ["a1"], ["a2"], ["h1"], ["h2"]]
    assert result["unknown"] == result["multi"] == [["q1"], ["q2"], ["a1", "a2"], ["h1", "h2"]]
    assert result["single_speaker"] == [["q1", "q2"], ["a1", "a2"], ["h1", "h2"]]
    assert result["unknown_not_joined"] == [["q1", "q2"]]
    assert resolve_speaker_scope(SimpleNamespace(speaker_scope="unknown", single_speaker=True)) == "single"
    assert resolve_speaker_scope(SimpleNamespace(speaker_scope="multi", single_speaker=True)) == "multi"
    assert resolve_speaker_scope(SimpleNamespace(speaker_scope="nope")) == "unknown"


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
            "reference_path": str(destination), "member_ids": ["a", "b"], "speaker_scope": "single"}
    _write_receipt(wav, {"segment_id": "a", "text": "你好，", "reference_text": "Hello,",
                         "reference_path": str(first)}, model, None, legacy=True, ordinal=1)
    receipt = json.loads((wav.parent / "000001.wav.receipt.json").read_text())
    assert _content_matches(receipt, clip, model, wav, 1) is False
    same = {"segment_id": "a", "text": "你好，", "reference_text": "Hello,", "reference_path": str(first),
            "member_ids": ["a"], "speaker_scope": "unknown"}
    _write_receipt(wav, same, model, None, legacy=False, ordinal=1)
    changed_scope = {**same, "speaker_scope": "single"}
    assert _content_matches(json.loads((wav.parent / "000001.wav.receipt.json").read_text()), changed_scope, model, wav, 1) is False
