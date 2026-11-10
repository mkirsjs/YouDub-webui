"""Write speech-clip receipts only after a checksum backup matches the files.

This does not generate audio and does not change wav bytes. A wav without a
receipt is left untouched. Retry still will not treat it as finished.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from backend.app.v1.tts_process import _inside, _sha256, _write_receipt, complete_wav


def _fail(message: str) -> int:
    print(json.dumps({"code": "RESUME_NOT_VERIFIED", "message": message}), file=sys.stderr)
    return 1


def read_checksums(path: Path, task_root: Path) -> dict[Path, str]:
    found = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"Checksum line {line_number} must be '<sha256> <path>'.")
        digest, name = parts
        candidate = Path(name)
        resolved = candidate.resolve() if candidate.is_absolute() else (task_root / candidate).resolve()
        if not _inside(resolved, task_root) or not resolved.is_file():
            raise ValueError(f"Checksum path is not a file inside the task: {name}")
        if resolved in found:
            raise ValueError(f"Checksum path is repeated: {name}")
        found[resolved] = digest.lower()
    if not found:
        raise ValueError("The checksum file is empty.")
    return found


def _load_request(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    clips = payload.get("clips") if isinstance(payload, dict) else None
    if not isinstance(clips, list) or not clips:
        raise ValueError("request.json has no clips.")
    return clips


def plan_receipts(task_root: Path, model_path: Path, checksums: dict[Path, str]) -> list[tuple[Path, dict, int, str]]:
    """Return receipt writes, or raise before any file is changed."""
    requests = [path for path in checksums if path.name == "request.json" and path.parent.name == "tts"]
    if len(requests) != 1:
        raise ValueError("The checksum file must list exactly one tts/request.json.")
    request_path = requests[0]
    if _sha256(request_path) != checksums[request_path]:
        raise ValueError("request.json does not match the checksum backup.")
    clips = _load_request(request_path)
    request_sha = checksums[request_path]
    planned = []
    for wav, digest in checksums.items():
        if wav.suffix != ".wav" or wav.parent.name != "tts":
            continue
        if not wav.stem.isdigit() or len(wav.stem) != 6:
            raise ValueError(f"{wav.name} is not a six-digit cue ordinal.")
        ordinal = int(wav.stem)
        if ordinal < 1 or ordinal > len(clips) or not isinstance(clips[ordinal - 1], dict):
            raise ValueError(f"{wav.name} has no clip {ordinal} in the checksummed request.")
        clip = clips[ordinal - 1]
        if Path(str(clip.get("output_path", ""))).name != wav.name:
            raise ValueError(
                f"Checksummed request clip {ordinal} names "
                f"{Path(str(clip.get('output_path', ''))).name or 'no file'}, not {wav.name}."
            )
        if _sha256(wav) != digest:
            raise ValueError(f"{wav.name} does not match the checksum backup.")
        if not complete_wav(wav):
            raise ValueError(f"{wav.name} is not a complete mono WAV.")
        reference = Path(str(clip.get("reference_path", ""))).resolve()
        if not _inside(reference, task_root) or reference not in checksums:
            raise ValueError(f"Reference audio for {wav.name} is not in the checksum backup.")
        if _sha256(reference) != checksums[reference]:
            raise ValueError(f"Reference audio for {wav.name} does not match the checksum backup.")
        if clip.get("text") is None or clip.get("reference_text") is None or clip.get("segment_id") is None:
            raise ValueError(f"request.json identity for {wav.name} is incomplete.")
        planned.append((wav, clip, ordinal, request_sha))
    if not planned:
        raise ValueError("The checksum file lists no tts wav clips.")
    return planned


def adopt(task_root: Path, model_path: Path, checksum_path: Path) -> int:
    task_root = task_root.resolve()
    model_path = model_path.resolve()
    if not task_root.is_dir() or not model_path.is_dir() or not checksum_path.is_file():
        return _fail("Task root, model directory, and checksum file must all exist.")
    try:
        checksums = read_checksums(checksum_path, task_root)
        planned = plan_receipts(task_root, model_path, checksums)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _fail(str(exc))
    # Every check above succeeded. Receipts are the only files written.
    for wav, clip, ordinal, request_sha in planned:
        before = wav.read_bytes()
        _write_receipt(
            wav, clip, model_path, None, legacy=True, ordinal=ordinal, request_sha256=request_sha,
        )
        if wav.read_bytes() != before:
            return _fail(f"Refusing to continue because {wav.name} changed while writing its receipt.")
    print(json.dumps({"receipts": len(planned), "wav_bytes_changed": False}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    options = parser.parse_args(argv)
    return adopt(options.task_root, options.model_path, options.checksums)


if __name__ == "__main__":
    raise SystemExit(main())
