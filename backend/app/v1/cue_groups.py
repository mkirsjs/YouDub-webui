"""Group short subtitle cues into one source-clone phrase.

Whisper tiny does not identify speakers. Missing speaker ids stay separate
unless the user sets speaker scope to ``single``. The same known speaker may
join. Different speakers never join. A join also stops at a sentence ending,
a pause longer than 300 ms, or a 12 second span.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import numpy as np
import soundfile as sf

MAX_GAP_MS = 300
MAX_SPAN_MS = 12_000
SENTENCE_END = set(".?!。！？")
SCOPES = ("unknown", "single", "multi")

# Short sample shared by the boundary tests and `python -m backend.app.v1.cue_groups`.
# It is not a finished video and it does not call a model.
SHORT_SAMPLE = {
    "segments": [
        {"id": "q1", "start_ms": 0, "end_ms": 700, "text": "What happened,", "speaker_id": None},
        {"id": "q2", "start_ms": 800, "end_ms": 1400, "text": "exactly?", "speaker_id": None},
        {"id": "a1", "start_ms": 1800, "end_ms": 2400, "text": "I waited,", "speaker_id": "guest"},
        {"id": "a2", "start_ms": 2500, "end_ms": 3100, "text": "then I left.", "speaker_id": "guest"},
        {"id": "h1", "start_ms": 3400, "end_ms": 3900, "text": "Thanks,", "speaker_id": "host"},
        {"id": "h2", "start_ms": 4000, "end_ms": 4500, "text": "next question.", "speaker_id": "host"},
    ],
    "translations": {
        "q1": "发生了什么，", "q2": "具体呢？", "a1": "我等了，", "a2": "然后离开了。",
        "h1": "谢谢，", "h2": "下一个问题。",
    },
}


@dataclass(frozen=True)
class Phrase:
    member_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    reference_text: str
    translation_text: str
    speaker_known: bool

    @property
    def segment_id(self) -> str:
        return self.member_ids[0]


def resolve_speaker_scope(selection) -> str:
    """Unknown stays conservative. ``singleSpeaker`` confirms one person only."""
    raw = getattr(selection, "speaker_scope", None) or "unknown"
    scope = raw if raw in SCOPES else "unknown"
    if scope == "unknown" and bool(getattr(selection, "single_speaker", False)):
        return "single"
    return scope


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_END


def _join(parts: list[str]) -> str:
    joined = parts[0]
    for part in parts[1:]:
        if not part:
            continue
        if joined.endswith((" ", "\n")) or part.startswith((" ", "\n")):
            joined += part
        else:
            joined += " " + part
    return joined


def _timing_allows(current, segment) -> bool:
    previous = current[-1]
    gap = segment.start_ms - previous.end_ms
    span = segment.end_ms - current[0].start_ms
    return gap >= 0 and gap <= MAX_GAP_MS and span <= MAX_SPAN_MS and not _ends_sentence(previous.text)


def _can_append(current, segment, *, allow_unknown: bool) -> bool:
    if not _timing_allows(current, segment):
        return False
    left = getattr(current[-1], "speaker_id", None)
    right = getattr(segment, "speaker_id", None)
    if left and right:
        return left == right
    if left or right:
        return False
    return allow_unknown


def group_segments(segments, translations: dict[str, str], *, enabled: bool, speaker_scope: str = "unknown") -> list[Phrase]:
    """Return one phrase per cue, or merged phrases when ``enabled`` is true."""
    if not segments:
        return []
    scope = speaker_scope if speaker_scope in SCOPES else "unknown"
    allow_unknown = bool(enabled) and scope == "single"

    def phrase(members) -> Phrase:
        return Phrase(
            member_ids=tuple(item.id for item in members),
            start_ms=members[0].start_ms,
            end_ms=members[-1].end_ms,
            reference_text=_join([item.text for item in members]),
            translation_text=_join([translations[item.id] for item in members]),
            speaker_known=all(getattr(item, "speaker_id", None) for item in members),
        )

    if not enabled:
        return [phrase([item]) for item in segments]
    groups: list[list] = [[segments[0]]]
    for segment in segments[1:]:
        if _can_append(groups[-1], segment, allow_unknown=allow_unknown):
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return [phrase(members) for members in groups]


def unknown_blocks(segments, *, speaker_scope: str = "unknown") -> list[list[str]]:
    """Adjacent cues that timing would join, but unlabeled speakers keep apart."""
    if speaker_scope == "single":
        return []
    blocked = []
    current = [segments[0]] if segments else []
    for segment in segments[1:]:
        previous = current[-1]
        left = getattr(previous, "speaker_id", None)
        right = getattr(segment, "speaker_id", None)
        if _timing_allows(current, segment) and not left and not right:
            blocked.append([previous.id, segment.id])
            current.append(segment)
            continue
        current = [segment]
    return blocked


def plans(segments, translations: dict[str, str]) -> dict:
    """Reproducible A/B membership without reading audio or calling a model."""
    per_cue = group_segments(segments, translations, enabled=False)
    single = group_segments(segments, translations, enabled=True, speaker_scope="single")
    unknown = group_segments(segments, translations, enabled=True, speaker_scope="unknown")
    multi = group_segments(segments, translations, enabled=True, speaker_scope="multi")
    return {
        "diarization": False,
        "per_cue": [list(item.member_ids) for item in per_cue],
        "single_speaker": [list(item.member_ids) for item in single],
        "unknown": [list(item.member_ids) for item in unknown],
        "multi": [list(item.member_ids) for item in multi],
        "unknown_not_joined": unknown_blocks(segments, speaker_scope="unknown"),
        "note": "Whisper tiny has no speaker ids. unknown and multi do not join those cues. single joins them only after the user confirms one speaker. Same known ids can join. Different ids never join.",
    }


def write_span_reference(members, references: dict, destination) -> None:
    """Write the vocals span covering every member, including short internal pauses."""
    pieces = []
    rate = None
    for index, member in enumerate(members):
        samples, file_rate = sf.read(references[member.id]["path"], dtype="float32", always_2d=False)
        if getattr(samples, "ndim", 1) == 2:
            samples = samples.mean(axis=1)
        if rate is None:
            rate = file_rate
        elif file_rate != rate:
            raise ValueError("Reference clips do not share a sample rate")
        if index:
            gap_ms = member.start_ms - members[index - 1].end_ms
            if gap_ms > 0:
                pieces.append(np.zeros(round(gap_ms * rate / 1000), dtype=np.float32))
        pieces.append(np.asarray(samples, dtype=np.float32))
    audio = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    sf.write(destination, audio, rate or 16000, subtype="PCM_16")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    payload = SHORT_SAMPLE
    if args:
        payload = json.loads(open(args[0], encoding="utf-8").read())
    segments = []
    for item in payload["segments"]:
        segments.append(type("Cue", (), item))
    print(json.dumps(plans(segments, payload["translations"]), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
