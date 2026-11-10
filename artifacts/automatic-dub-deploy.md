# Automatic dubbing on the current host

Baseline: `44a98e30c4108cc6bc05cce1b10f96db5fafda6d`. This patch does not change the public origin, the launch-token file, nginx, or task `9280e54b-89e9-42e4-b871-b96ca69734fe`.

## What the author actually ships

`README.md` (author tree `a270415`, still the product text) says the desk imports a video, recognizes speech, translates it, clones each sentence, and can keep separated background. The first-run step is explicit: add a provider, an API key, and a translation model (`README.md` lines 100 and 129). Translation is a Harness model call, not a weight file in this repo.

The built-in dubbing graph is `packages/builtin/src/workflow-templates/source-clone-dubbing.json`, published as `youdub.dubbing` by `packages/builtin/src/workflow-templates.ts`. Its nodes are prepare, Demucs `htdemucs`, Whisper `tiny`, translate, reference, VoxCPM2 `source_clone`, mix with `keepBackground: true`, and export without burned-in subtitles. `packages/builtin/src/setup-models.json` lists local files for Whisper tiny, VoxCPM2, Demucs htdemucs, and Qwen3 Forced Aligner 0.6B. It lists no translation weights.

`apps/harness/src/backend/llm.mjs` `translateWithHarness` sends one batch at a time and requires every segment id back. A batch closes at 20 segments or 6000 characters of source text, whichever comes first. The call uses `llm.prepareCall` and does not set `maxTokens` or `maxCompletionTokens`. An old node field named `maxCompletionTokens`, including 65535, is ignored (`apps/harness/test/llm-native.test.mjs`). The output cap is the one already stored for that model. A finish reason other than `stop`, including `max-tokens`, fails that batch. Invalid JSON or a missing segment id also fails it. There is no second attempt with a smaller cap, and there is no offline translator in this repo.

Whisper may keep a `speaker_id` when the recognizer returns one (`backend/app/v1/asr.py`). Stock Whisper tiny does not diarize. `backend/app/v1/mix.py` rejects overlapping source segments with `UNSUPPORTED_OVERLAPPING_SPEECH`. This build does not claim multi-speaker dubbing or a perfect vocal split. Demucs `htdemucs` is one vocals stem and one background stem. Mix adds that background at gain `0.3`.

## What this deployment already changed

HTTPS exact origin, the mode-0600 launch token, CUDA placement for Demucs and VoxCPM, CPU `libx264` because NVENC session creation failed, and receipt resume are on `44a98e30`. The long task used imported SRT files because no translation credential was configured. That import path is `youdub.importSubtitles`. It is not the default dubbing graph.

Attempt 2 of `9280e54b` already finished 129 clips, mix, and export. Do not retry it, delete it, or replace its wavs. The backup stays untouched.

## Silence is a failure, not a joint repair

A generated clip whose peak is below `1e-3` (-60 dBFS), or an adjusted mix clip that quiet, raises `INVALID_PROVIDER_RESULT` and does not write the mix. That check is commit `1d4f80f8b96102fbc1c5f7618a1814338a49b215`. It does not explain or remove the short sounds at clip edges. Reused receipt bytes are not rewritten. The scheduler still does not trim, fade, or overlap speech. `backend/app/v1/mix.py` writes each complete clip with a hard sample boundary and can leave a gap when a clip is shorter than the source interval.

`python -m backend.app.v1.tts_diagnose --task-root <task>` only reads numbered wavs and `alignment.json`. It reports leading and trailing silence at -40 dB for at least 0.05 s, plus dubbed gaps of at least 0.05 s. It writes nothing under the task. Run it on the server. Do not copy the video or the wavs off the machine. A later change still has to be tried on a short copy, not on task `9280e54b` or `59a78441-20c7-44b1-82d1-fadb946bdb62`.

## Apply

On `44a98e30`:

```sh
git apply --check artifacts/youdub-silence-gate-since-44a98e30.patch
git apply artifacts/youdub-silence-gate-since-44a98e30.patch
npm --prefix apps/harness run build
```

Restart only `youdub-host` through the existing supervisor config. Keep `YOUDUB_PUBLIC_ORIGIN` and `YOUDUB_LAUNCH_TOKEN_FILE=/var/lib/youdub/launch-token`. Do not rebuild the framework unless `vendor/deepseek-harness` changes. Do not open ports 8888 or 8889.

The workbench already lists the built-in graphs「视频配音」and「字幕＋配音」. With no translation provider, the translate node stays on the unavailable `openai` placeholder. Creating the task returns 422 `PROVIDER_UNAVAILABLE` and the message `请在模型设置中添加翻译服务。` A saved provider with no key says to configure that credential. After the user selects a reachable endpoint and one of its models in Settings, the same graph runs from one uploaded video. This patch does not add a key, a tunnel, or a local translation model. The endpoint must accept a system message plus one user text message and return one complete JSON object for a batch of at most 20 segments or 6000 source characters. Its own output cap has to be large enough for that JSON to finish with reason `stop`. 65535 is not required. Importing two SRT files remains the path that does not call a translator.

## Rollback

```sh
git checkout 44a98e30c4108cc6bc05cce1b10f96db5fafda6d -- \
  backend/app/v1/audio.py backend/app/v1/mix.py backend/app/v1/tts_process.py \
  packages/builtin/src/builtin-compatibility.json \
  packages/builtin/test/builtin-integrity.test.ts \
  backend/tests/test_v1_mix.py backend/tests/test_v1_tts.py
npm --prefix apps/harness run build
```

Restart `youdub-host`. Leave the finished task, its backup, and the token file as they are.

## Read-only checks for the operator

No private media needs to be copied here. Useful reads, if a joint is investigated later:

- `find` the Whisper and Qwen directories and print file names and sizes only.
- From the finished task's `alignment.json`, the `dubbed_start_ms` and `dubbed_end_ms` of two adjacent cues and the gap between them.
- Whether a translation provider credential is absent. Do not print the key.
