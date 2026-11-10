from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
import soundfile as sf

from backend.app.v1 import media, separate, separate_process
from backend.app.v1.contracts import TaskConfig
from backend.app.v1.errors import ApiError
from backend.app.v1.steps import StageContext
from backend.workers.protocol import WorkerError


@pytest.fixture
def context(tmp_path, monkeypatch, video):
    models = tmp_path / "models"
    models.mkdir()
    (models / separate.MODEL_FILES["htdemucs"]).write_bytes(b"explicit-mock-checkpoint")
    monkeypatch.setenv("YOUDUB_DEMUCS_MODELS_DIR", str(models))
    config = TaskConfig.model_validate({
        "source_language": "en", "target_language": "zh", "output_mode": "both", "keep_background": True,
        "asr": {"adapter": "whisper", "model": "tiny", "device": "cpu"},
        "translation": {"adapter": "openai", "model": "test", "device": "remote"},
        "tts": {"adapter": "voxcpm", "model": "VoxCPM2", "device": "cpu", "voice": {"mode": "source_clone"}},
        "separation": {"adapter": "demucs", "model": "htdemucs", "device": "cpu"},
    })
    return StageContext(stage="separate",
                        config=config, input_files={"video": video}, work_dir=tmp_path / "work")


def test_local_model_metadata_and_missing_asset_fail_before_extraction(context, monkeypatch):
    assert separate.available_models() == ["htdemucs"]
    (separate.model_directory() / separate.MODEL_FILES["htdemucs"]).unlink()
    monkeypatch.setattr(media, "_run_media", lambda *args, **kwargs: pytest.fail("must not start"))
    with pytest.raises(ApiError) as error:
        separate.run(context, lambda *args: None)
    assert error.value.content["error"]["code"] == "MODEL_NOT_READY"


@pytest.mark.parametrize("bad_length", [False, True])
def test_original_stereo_extraction_and_separation_timeline_validation(context, monkeypatch, bad_length):
    real_run = media._run_media
    commands = []
    original_bytes = context.input_files["video"].read_bytes()

    def invoke(command, **kwargs):
        commands.append(command)
        if command[0] != sys.executable:
            return real_run(command, **kwargs)
        assert command[command.index("--device") + 1] == "cpu"
        assert Path(command[command.index("--model-path") + 1]).name == separate.MODEL_FILES["htdemucs"]
        source = Path(command[command.index("--audio-path") + 1])
        samples, rate = sf.read(source, always_2d=True)
        assert rate == 44100 and samples.shape[1] == 2
        for flag in ("--vocals-path", "--background-path"):
            sf.write(command[command.index(flag) + 1], samples[:-1] if bad_length else samples, rate, subtype="FLOAT")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media, "_run_media", invoke)
    if bad_length:
        with pytest.raises(ApiError) as error:
            separate.run(context, lambda *args: None)
        assert error.value.content["error"]["code"] == "INVALID_PROVIDER_RESULT"
    else:
        result = separate.run(context, lambda *args: None)
        assert set(result.output_files) == {"vocals", "background"}
        assert sf.info(result.output_files["vocals"]).frames == sf.info(context.work_dir / "separation/source.wav").frames
    assert context.input_files["video"].read_bytes() == original_bytes
    assert len(commands) == 2


def test_demucs_process_is_reaped_on_cancel(context, monkeypatch):
    real_run, real_popen = media._run_media, subprocess.Popen
    children = []

    def invoke(command, **kwargs):
        if command[0] != sys.executable:
            return real_run(command, **kwargs)

        def child(*args, **kw):
            process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kw)
            children.append(process)
            return process

        with monkeypatch.context() as scoped:
            scoped.setattr(media.subprocess, "Popen", child)
            return real_run(command, **kwargs)

    def cancelled():
        if children:
            raise WorkerError("CANCELLED", "The host cancelled this invocation.")

    monkeypatch.setattr(media, "_run_media", invoke)
    with pytest.raises(WorkerError, match="The host cancelled this invocation") as error:
        separate.run(replace(context, check_cancel=cancelled), lambda *args: None)
    assert error.value.code == "CANCELLED"
    assert children[0].poll() is not None


def test_finished_message_reads_the_device_line_only():
    assert separate.finished_message("") == "Vocals and background are ready"
    stdout = "\n".join([
        "not json",
        json.dumps({"event": "device", "requested_device": "cuda:0", "tensor_devices": ["cuda:0"]}),
    ])
    assert separate.finished_message(stdout) == "Demucs tensors on cuda:0"


def test_separation_progress_reports_cuda_tensors(context, monkeypatch):
    real_run = media._run_media
    data = context.config.model_dump()
    data["separation"]["device"] = "cuda:0"
    configured = replace(context, config=TaskConfig.model_validate(data))
    messages = []

    def invoke(command, **kwargs):
        if command[0] != sys.executable:
            return real_run(command, **kwargs)
        assert command[command.index("--device") + 1] == "cuda:0"
        source = Path(command[command.index("--audio-path") + 1])
        samples, rate = sf.read(source, always_2d=True)
        for flag in ("--vocals-path", "--background-path"):
            sf.write(command[command.index(flag) + 1], samples, rate, subtype="FLOAT")
        line = json.dumps({"event": "device", "requested_device": "cuda:0", "tensor_devices": ["cuda:0"]})
        return subprocess.CompletedProcess(command, 0, line + "\n", "")

    monkeypatch.setattr(media, "_run_media", invoke)
    separate.run(configured, lambda value, message: messages.append((value, message)))
    assert messages[-1] == (1.0, "Demucs tensors on cuda:0")


class _Digest:
    def hexdigest(self):
        return "8726e21a" + "ab" * 28


class _Tensor:
    def __init__(self, array, device="cpu"):
        self.array = array
        self.device = device

    def mean(self, axis=None):
        return _Tensor(self.array.mean(axis=axis), self.device)

    def std(self):
        return float(self.array.std())

    def _other(self, other):
        return other.array if isinstance(other, _Tensor) else other

    def __add__(self, other):
        return _Tensor(self.array + self._other(other), self.device)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        return _Tensor(self.array - self._other(other), self.device)

    def __mul__(self, other):
        return _Tensor(self.array * self._other(other), self.device)

    def __truediv__(self, other):
        return _Tensor(self.array / self._other(other), self.device)

    def __getitem__(self, item):
        return _Tensor(self.array[item], self.device)

    def numpy(self):
        return self.array

    def cpu(self):
        return self


def _demucs_child(monkeypatch, tmp_path, *, device, move):
    import numpy as np

    checkpoint = tmp_path / "955717e8-8726e21a.th"
    checkpoint.write_bytes(b"checkpoint")
    audio = tmp_path / "audio.wav"
    vocals = tmp_path / "vocals.wav"
    background = tmp_path / "background.wav"
    samples = np.linspace(-0.2, 0.2, 4410, dtype="float32").reshape(-1, 1).repeat(2, axis=1)
    sf.write(audio, samples, 44100, subtype="FLOAT")
    events = []

    class Model:
        def __init__(self):
            self.params = [_Tensor(np.zeros(1, dtype="float32"))]
            self.samplerate = 44100
            self.audio_channels = 2
            self.sources = ["drums", "bass", "other", "vocals"]

        def parameters(self):
            return self.params

        def buffers(self):
            return []

        def to(self, requested):
            events.append(("to", requested))
            move(self, requested)
            return self

        def eval(self):
            return self

    model = Model()

    def apply_model(loaded, wav, device=None, **kwargs):
        events.append(("apply", device, loaded.params[0].device, kwargs["shifts"], kwargs["overlap"], kwargs["split"]))
        channels, count = wav.array.shape[-2], wav.array.shape[-1]
        sources = np.zeros((4, channels, count), dtype="float32")
        sources[3] = wav.array[0]
        return [_Tensor(sources)]

    torch = ModuleType("torch")
    torch.load = lambda *args, **kwargs: {"loaded": True}
    torch.from_numpy = lambda array: _Tensor(array)
    torch.isfinite = lambda value: np.isfinite(value)
    class _Inference:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    torch.inference_mode = _Inference
    demucs_apply = ModuleType("demucs.apply")
    demucs_apply.apply_model = apply_model
    demucs_states = ModuleType("demucs.states")
    demucs_states.load_model = lambda package: model
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "demucs", ModuleType("demucs"))
    monkeypatch.setitem(sys.modules, "demucs.apply", demucs_apply)
    monkeypatch.setitem(sys.modules, "demucs.states", demucs_states)
    monkeypatch.setattr(hashlib, "file_digest", lambda *_args, **_kwargs: _Digest())
    code = separate_process.main([
        "--model-path", str(checkpoint), "--audio-path", str(audio),
        "--vocals-path", str(vocals), "--background-path", str(background),
        "--device", device,
    ])
    return code, events, vocals, background


def test_demucs_places_cpu_model_on_cuda_before_apply(monkeypatch, tmp_path, capsys):
    def move(model, requested):
        for parameter in model.params:
            parameter.device = requested

    code, events, vocals, background = _demucs_child(monkeypatch, tmp_path, device="cuda:0", move=move)
    assert code == 0
    assert events[0] == ("to", "cuda:0")
    assert events[1][0:3] == ("apply", "cuda:0", "cuda:0")
    assert events[1][3:] == (1, 0.25, True)
    assert vocals.is_file() and background.is_file()
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["requested_device"] == "cuda:0" and line["tensor_devices"] == ["cuda:0"]


def test_demucs_cuda_request_does_not_separate_when_placement_stays_on_cpu(monkeypatch, tmp_path, capsys):
    code, events, vocals, background = _demucs_child(
        monkeypatch, tmp_path, device="cuda:0", move=lambda model, requested: None,
    )
    assert code == 1
    assert events == [("to", "cuda:0")]
    assert not vocals.exists() and not background.exists()
    error = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert error["code"] == "MODEL_NOT_READY"


def test_child_rejects_invalid_weight_before_importing_demucs(context, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "demucs", None)
    args = []
    for name, path in {"model": separate.model_directory() / separate.MODEL_FILES["htdemucs"],
                       "audio": context.work_dir / "input.wav", "vocals": context.work_dir / "vocals.wav",
                       "background": context.work_dir / "background.wav"}.items():
        args.extend([f"--{name}-path", str(path)])
    assert separate_process.main([*args, "--device", "cpu"]) == 1
    assert "checksum is invalid" in capsys.readouterr().err
