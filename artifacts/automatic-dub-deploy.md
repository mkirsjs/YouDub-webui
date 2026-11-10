# Automatic dubbing on the current host

Baseline: `44a98e30c4108cc6bc05cce1b10f96db5fafda6d`. This patch does not change the public origin, the launch-token file, nginx, or task `9280e54b-89e9-42e4-b871-b96ca69734fe`.

## What the author actually ships

`README.md` (author tree `a270415`, still the product text) says the desk imports a video, recognizes speech, translates it, clones each sentence, and can keep separated background. The first-run step is explicit: add a provider, an API key, and a translation model (`README.md` lines 100 and 129). Translation is a Harness model call, not a weight file in this repo.

The built-in dubbing graph is `packages/builtin/src/workflow-templates/source-clone-dubbing.json`, published as `youdub.dubbing` by `packages/builtin/src/workflow-templates.ts`. Its nodes are prepare, Demucs `htdemucs`, Whisper `tiny`, translate, reference, VoxCPM2 `source_clone`, mix with `keepBackground: true`, and export without burned-in subtitles. `packages/builtin/src/setup-models.json` lists local files for Whisper tiny, VoxCPM2, Demucs htdemucs, and Qwen3 Forced Aligner 0.6B. It lists no translation weights.

`apps/harness/src/backend/llm.mjs` `translateWithHarness` sends each batch to the configured provider and requires every segment id back. The author's recorded runs use an OpenAI-compatible endpoint (`doubao-seed-evolving` in `docs/validation/mvp-runtime.md`) with `max_completion_tokens` 65535. There is no offline translator in the execution code. A task cannot invent the Chinese lines.

Whisper may keep a `speaker_id` when the recognizer returns one (`backend/app/v1/asr.py`). Stock Whisper tiny does not diarize. `backend/app/v1/mix.py` rejects overlapping source segments with `UNSUPPORTED_OVERLAPPING_SPEECH`. This build does not claim multi-speaker dubbing or a perfect vocal split. Demucs `htdemucs` is one vocals stem and one background stem. Mix adds that background at gain `0.3`.

## What this deployment already changed

HTTPS exact origin, the mode-0600 launch token, CUDA placement for Demucs and VoxCPM, CPU `libx264` because NVENC session creation failed, and receipt resume are on `44a98e30`. The long task used imported SRT files because no translation credential was configured. That import path is `youdub.importSubtitles`. It is not the default dubbing graph.

Attempt 2 of `9280e54b` already finished 129 clips, mix, and export. Do not retry it, delete it, or replace its wavs. The backup stays untouched.

## Silence is now a failure

A generated clip whose peak is below `1e-3` (-60 dBFS), or an adjusted mix clip that quiet, raises `INVALID_PROVIDER_RESULT` and does not write the mix. Reused receipt bytes are not rewritten. The scheduler still does not trim, fade, or overlap speech. `backend/app/v1/mix.py` writes each complete clip with a hard sample boundary and can leave a gap when a clip is shorter than the source interval. Those gaps, a reference slice that includes a breath, and residual voice in the background stem are the code paths that can sound like a break at a joint. This patch does not cut heads or tails to chase that symptom. No private audio was available here, and this VM has no GPU, so this is not a listening test.

## Apply

On `44a98e30`:

```sh
git apply --check artifacts/youdub-silence-gate-since-44a98e30.patch
git apply artifacts/youdub-silence-gate-since-44a98e30.patch
npm --prefix apps/harness run build
```

Restart only `youdub-host` through the existing supervisor config. Keep `YOUDUB_PUBLIC_ORIGIN` and `YOUDUB_LAUNCH_TOKEN_FILE=/var/lib/youdub/launch-token`. Do not rebuild the framework unless `vendor/deepseek-harness` changes. Do not open ports 8888 or 8889.

To run the author's dubbing graph, the host still needs an OpenAI-compatible translation endpoint that the machine can reach, a stored API key, and a model whose output limit is at least 65535 tokens. This patch does not add a key, a tunnel, or a local translation model. Until that endpoint exists, a new video cannot produce Chinese speech. Importing two SRT files remains the only path that does not call a translator.

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
