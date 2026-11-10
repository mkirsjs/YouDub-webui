"""Generate a Task's speech clips with one local VoxCPM2 model instance.

A clip is reused only when a receipt written after a successful generation, or
by the checksum-gated adopt command, matches this request. A wav with no
receipt is not evidence and is never overwritten in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path


def _fail(code: str, message: str) -> int:
    print(json.dumps({"code": code, "message": message}), file=sys.stderr)
    return 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def complete_wav(path: Path) -> bool:
    """True when the file is a finished mono RIFF/WAVE, not a truncated write."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    riff_size = int.from_bytes(data[4:8], "little")
    if riff_size < 36 or riff_size + 8 > len(data):
        return False
    offset = 12
    limit = riff_size + 8
    fmt_ok = False
    channels = rate = bits = None
    data_bytes = 0
    while offset + 8 <= limit:
        chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
        payload = offset + 8
        end = payload + chunk_size
        if chunk_size < 0 or end > len(data):
            return False
        chunk_id = data[offset:offset + 4]
        if chunk_id == b"fmt " and chunk_size >= 16:
            fmt_ok = True
            channels = int.from_bytes(data[payload + 2:payload + 4], "little")
            rate = int.from_bytes(data[payload + 4:payload + 8], "little")
            bits = int.from_bytes(data[payload + 14:payload + 16], "little")
        elif chunk_id == b"data":
            data_bytes = chunk_size
        offset = end + (chunk_size & 1)
    if not fmt_ok or channels != 1 or not rate or rate <= 0 or bits not in {16, 32} or data_bytes <= 0:
        return False
    width = bits // 8
    return data_bytes % width == 0 and data_bytes // width > 0


def _publish(progress_path: Path, payload: dict) -> None:
    temporary = progress_path.with_suffix(".json.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, progress_path)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _preserve(path: Path) -> None:
    """Move an unusable wav aside. The bytes stay in preserved/ and are not deleted."""
    if not path.exists():
        return
    folder = path.parent / "preserved"
    folder.mkdir(exist_ok=True)
    index = 1
    while True:
        target = folder / f"{path.name}.{index}"
        if not target.exists():
            path.replace(target)
            return
        index += 1


def _receipt_path(wav: Path) -> Path:
    return wav.with_name(wav.name + ".receipt.json")


def _load_receipt(wav: Path) -> dict | None:
    path = _receipt_path(wav)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _content_matches(receipt: dict, clip: dict, model_path: Path, wav: Path, ordinal: int) -> bool:
    reference = Path(clip["reference_path"]).resolve()
    if receipt.get("version") != 1 or receipt.get("segment_id") != clip["segment_id"]:
        return False
    if receipt.get("ordinal") != ordinal:
        return False
    if wav.stem.isdigit() and int(wav.stem) != ordinal:
        return False
    if receipt.get("text") != clip["text"] or receipt.get("reference_text") != clip["reference_text"]:
        return False
    if receipt.get("model_path") != str(model_path):
        return False
    try:
        if receipt.get("reference_sha256") != _sha256(reference) or receipt.get("wav_sha256") != _sha256(wav):
            return False
    except OSError:
        return False
    return complete_wav(wav)


def _receipt_allows(receipt: dict, device: str) -> bool:
    legacy = receipt.get("legacy") is True and receipt.get("device") is None
    return legacy or receipt.get("device") == device


def _write_receipt(
    wav: Path, clip: dict, model_path: Path, device: str | None, *,
    legacy: bool, ordinal: int, request_sha256: str | None = None,
) -> None:
    reference = Path(clip["reference_path"]).resolve()
    payload = {
        "version": 1,
        "ordinal": ordinal,
        "segment_id": clip["segment_id"],
        "text": clip["text"],
        "reference_text": clip["reference_text"],
        "reference_sha256": _sha256(reference),
        "wav_sha256": _sha256(wav),
        "model_path": str(model_path),
        "device": device,
        "legacy": legacy,
        "request_sha256": request_sha256,
    }
    path = _receipt_path(wav)
    temporary = path.with_suffix(".json.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _copy_wav(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".partial.wav")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def _prior_wavs(task_root: Path, destination: Path) -> list[Path]:
    attempts = task_root / "attempts"
    if not attempts.is_dir():
        return []
    found = []
    for path in attempts.glob("*/*/tts/*.wav"):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved == destination.resolve() or not _inside(resolved, task_root):
            continue
        if resolved.parent.name != "tts":
            continue
        found.append(resolved)
    return found


def _reuse_in_place(clip: dict, ordinal: int, model_path: Path, device: str) -> bool:
    wav = Path(clip["output_path"])
    receipt = _load_receipt(wav)
    if receipt is None:
        return False
    return _receipt_allows(receipt, device) and _content_matches(receipt, clip, model_path, wav, ordinal)


def _adopt_prior(clip: dict, ordinal: int, model_path: Path, device: str, task_root: Path | None) -> bool:
    """Copy one already-receipted wav of the same ordinal. The source file stays put."""
    if task_root is None:
        return False
    destination = Path(clip["output_path"])
    if _reuse_in_place(clip, ordinal, model_path, device):
        return True
    if not destination.stem.isdigit() or int(destination.stem) != ordinal:
        return False
    for source in _prior_wavs(task_root, destination):
        if source.name != destination.name:
            continue
        receipt = _load_receipt(source)
        if receipt is None or not _receipt_allows(receipt, device):
            continue
        if not _content_matches(receipt, clip, model_path, source, ordinal):
            continue
        if destination.exists():
            _preserve(destination)
        _copy_wav(source, destination)
        _write_receipt(
            destination, clip, model_path, receipt.get("device"),
            legacy=receipt.get("legacy") is True, ordinal=ordinal,
            request_sha256=receipt.get("request_sha256"),
        )
        return _reuse_in_place(clip, ordinal, model_path, device)
    return False


def _tensor_devices(model) -> list[str]:
    tts = getattr(model, "tts_model", None)
    found = []
    for method in ("parameters", "buffers"):
        values = getattr(tts, method, None)
        if not callable(values):
            continue
        for tensor in values():
            device = getattr(tensor, "device", None)
            if device is None:
                continue
            text = str(device)
            if text not in found:
                found.append(text)
    return found


def _place_tts(model, requested: str) -> list[str]:
    """Move the loaded VoxCPM2 module onto the requested device.

    voxcpm 2.0.3's public ``from_pretrained(..., device=)`` forwards that
    string to ``VoxCPM2Model.from_local``, which ends in ``module.to(device)``.
    The wrapper class itself has no ``to``. Call the module method again so a
    load that stayed on CPU is actually placed before any cue is generated.
    """
    tts = getattr(model, "tts_model", None)
    move = getattr(tts, "to", None)
    if requested.startswith("cuda") or requested == "mps":
        if not callable(move):
            raise RuntimeError(f"VoxCPM2 tts_model has no to(); cannot place it on {requested}.")
        move(requested)
    elif callable(move):
        move(requested)
    return _tensor_devices(model)


def _require_cuda(requested: str, devices: list[str]) -> str | None:
    if not requested.startswith("cuda"):
        return None
    if not devices or any(device != requested for device in devices):
        visible = ", ".join(devices) if devices else "unknown"
        return f"VoxCPM2 was requested on {requested} but its parameters are on {visible}."
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--request-path", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--task-root", type=Path)
    options = parser.parse_args(argv)
    model_path = options.model_path.resolve()
    if not model_path.is_dir():
        return _fail("MODEL_NOT_READY", "The local VoxCPM2 model directory is missing.")
    try:
        config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        if str(config.get("architecture", "")).lower() != "voxcpm2":
            raise ValueError("VoxCPM2 architecture is required")
    except (OSError, ValueError, AttributeError):
        return _fail("MODEL_NOT_READY", "The selected model is not a valid local VoxCPM2 model.")
    try:
        clips = json.loads(options.request_path.read_text(encoding="utf-8"))["clips"]
        if not isinstance(clips, list) or not clips:
            raise ValueError("Speech clips are required")
        ids = set()
        for clip in clips:
            if not isinstance(clip["text"], str) or not clip["text"].strip() or clip["segment_id"] in ids:
                raise ValueError("Invalid speech clip")
            ids.add(clip["segment_id"])
            if not Path(clip["reference_path"]).is_file():
                raise ValueError("Reference audio is missing")
            if not isinstance(clip["reference_text"], str) or not clip["reference_text"].strip():
                raise ValueError("Reference audio transcript is missing")
    except (OSError, ValueError, KeyError, TypeError):
        return _fail("INPUT_MISSING", "The speech generation request or reference audio is invalid.")
    task_root = options.task_root.resolve() if options.task_root else None
    if task_root is not None and not task_root.is_dir():
        return _fail("INPUT_MISSING", "The task root for speech resume is missing.")
    progress_path = options.request_path.resolve().parent / "progress.json"
    total = len(clips)
    pending = []
    for index, clip in enumerate(clips, start=1):
        if _reuse_in_place(clip, index, model_path, options.device) or _adopt_prior(
            clip, index, model_path, options.device, task_root,
        ):
            _publish(progress_path, {
                "event": "cue", "index": index, "total": total, "reused": True, "seconds": 0,
                "message": f"VoxCPM2 reused verified cue {index}/{total}",
            })
        else:
            pending.append((index, clip))

    if not pending:
        _publish(progress_path, {
            "event": "done", "index": total, "total": total, "reused": True,
            "message": f"VoxCPM2 reused {total}/{total} verified clips; the model was not loaded",
        })
        return 0

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from backend.app.config import configure_windows_ffmpeg_dll_directory
    configure_windows_ffmpeg_dll_directory()
    _publish(progress_path, {
        "event": "load", "index": 0, "total": total,
        "message": f"Loading VoxCPM2 once on {options.device}",
    })
    try:
        import numpy as np
        import soundfile as sf
        from voxcpm import VoxCPM
    except ImportError:
        return _fail("MODEL_NOT_READY", "Install VoxCPM2 and its audio dependencies in the backend environment.")
    try:
        model = VoxCPM.from_pretrained(
            str(model_path), local_files_only=True, load_denoiser=False, optimize=False, device=options.device,
        )
        sample_rate = model.tts_model.sample_rate
        if type(sample_rate) is not int or sample_rate <= 0:
            raise ValueError("Invalid model output sample rate")
        devices = _place_tts(model, options.device)
        refused = _require_cuda(options.device, devices)
        if refused:
            return _fail("MODEL_NOT_READY", refused)
    except Exception as exc:
        traceback.print_exc()
        return _fail("MODEL_NOT_READY", f"The local VoxCPM2 model could not be loaded ({type(exc).__name__}).")
    visible = ", ".join(devices) if devices else "unverified"
    _publish(progress_path, {
        "event": "device", "index": 0, "total": total, "requested_device": options.device,
        "tensor_devices": devices,
        "message": f"VoxCPM2 loaded once; requested {options.device}; tensors on {visible}",
    })
    try:
        for index, clip in pending:
            if Path(clip["output_path"]).exists() and not _reuse_in_place(clip, index, model_path, options.device):
                _preserve(Path(clip["output_path"]))
            started = time.perf_counter()
            waveform = np.asarray(model.generate(
                text=clip["text"], reference_wav_path=clip["reference_path"],
                prompt_wav_path=clip["reference_path"], prompt_text=clip["reference_text"],
                normalize=False, denoise=False, retry_badcase=False,
            ))
            peak = float(np.max(np.abs(waveform))) if waveform.size else 0.0
            if (waveform.ndim != 1 or waveform.size == 0 or not np.isfinite(waveform).all()
                    or peak > 1 or peak < 1e-3):
                raise ValueError("VoxCPM2 returned an empty, silent, or invalid mono waveform")
            destination = Path(clip["output_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(".partial.wav")
            sf.write(partial, waveform, sample_rate, format="WAV", subtype="PCM_16")
            os.replace(partial, destination)
            _write_receipt(
                destination, clip, model_path, options.device, legacy=False, ordinal=index,
                request_sha256=_sha256(options.request_path.resolve()),
            )
            elapsed = round(time.perf_counter() - started, 3)
            _publish(progress_path, {
                "event": "cue", "index": index, "total": total, "reused": False, "seconds": elapsed,
                "message": f"VoxCPM2 {options.device} generated cue {index}/{total} in {elapsed}s",
            })
    except Exception as exc:
        traceback.print_exc()
        return _fail("INVALID_PROVIDER_RESULT", f"VoxCPM2 speech generation failed ({type(exc).__name__}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
