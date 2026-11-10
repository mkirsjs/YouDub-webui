"""Group short subtitle cues into one source-clone phrase.

Whisper tiny does not identify speakers. A missing speaker id is not treated as
one speaker. Known different speakers are never joined. A join also stops at a
sentence ending, a pause longer than 300 ms, or a 12 second span. The reference
interval is the untouched span from the first cue start to the last cue end.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import soundfile as sf

MAX_GAP_MS = 300
MAX_SPAN_MS = 12_000
SENTENCE_END = set(".?!。！？")


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


def _can_append(current, segment) -> bool:
    previous = current[-1]
    gap = segment.start_ms - previous.end_ms
    span = segment.end_ms - current[0].start_ms
    if gap < 0 or gap > MAX_GAP_MS or span > MAX_SPAN_MS or _ends_sentence(previous.text):
        return False
    left = getattr(previous, "speaker_id", None)
    right = getattr(segment, "speaker_id", None)
    if left and right:
        return left == right
    if left or right:
        return False
    return True


def group_segments(segments, translations: dict[str, str], *, enabled: bool) -> list[Phrase]:
    """Return one phrase per cue, or merged phrases when ``enabled`` is true."""
    if not segments:
        return []

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
        if _can_append(groups[-1], segment):
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return [phrase(members) for members in groups]


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
