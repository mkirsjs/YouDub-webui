# CUDA inference, CPU video encode, stable launch token

Deployed HTTPS base: `59ef4b4599927f7ee139fb4e1d836ac7d533d0f3`. Keep that exact public origin. This workspace has no GPU. Python tests mock Demucs and VoxCPM. They do not measure CUDA, NVENC, or the saved backup.

The target measurement supplied with this change: RTX 4090, driver 610.57.04, torch 2.8.0+cu126, CUDA allocation succeeds. `ffmpeg -encoders` lists `h264_nvenc`, `hevc_nvenc`, and cuvid, and a one-second NVENC session fails with `OpenEncodeSessionEx failed: unsupported device (2)`. Video encode therefore stays on CPU libx264. Do not turn NVENC on from the encoder list.

Supervisor already starts `youdub-host` and nginx. A service restart was tested for auth, API, and WebSocket. A cold reboot was not tested. The launch URL still changes because the host mints a new `?token=` on every process. Set `YOUDUB_LAUNCH_TOKEN_FILE` so the next restart reuses one mode-0600 file.

Task `9280e54b-89e9-42e4-b871-b96ca69734fe` has 129 cues and 54 wavs. Those files were reported backed up and checksummed. This workspace did not re-read them. Do not retry, rerun, or delete until adopt has written receipts against that checksum file.

## GPU stages and CPU stages

Use CUDA for a stage only when that stage already has a model device and the placement below succeeds. Do not add a second model load, a hardware-decode filter graph, or an NVENC attempt.

| Stage | Device | What the code does |
| --- | --- | --- |
| Demucs separation | CUDA when `separation.device` is `cuda` or `cuda:0` | Load on CPU, call `model.to(device)`, then require every parameter and buffer on that device. `apply_model` still receives `device`, `shifts=1`, `split=True`, `overlap=0.25`. A CUDA request that stays on CPU exits `MODEL_NOT_READY` before separation. |
| VoxCPM synthesize | CUDA when `tts.device` is `cuda` or `cuda:0` | `VoxCPM.from_pretrained(..., device=)` for pinned `voxcpm==2.0.3`, then `tts_model.to(device)`. Parameters and buffers must land on that device or no cue is generated. One load. `generate` keeps `source_clone` (`normalize=False`, `denoise=False`, `retry_badcase=False`). |
| Whisper ASR | CUDA when that stage runs and its device is CUDA | Existing `whisper.load_model(..., device=)`. The imported EN+ZH SRT task does not run this stage. |
| Qwen forced aligner | CUDA when that stage runs and its device is CUDA | Existing `model.to(device)` and `inputs.to(device)`. Imported subtitle timing is not rewritten by this patch. |
| Video export | CPU libx264 | `-c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p`, then AAC. The subtitle `subtitles` filter and audio map are unchanged. No `-hwaccel`, no NVENC, no encoder-list probe. |
| Prepare, Demucs WAV extract, reference slice, mix, BGM, SRT timing | CPU | PCM, `atempo`, and cue timing. These steps have no CUDA model. |

## Stable launch token

There is no upstream flag that keeps the `?token=` value. The cookie signing secret is already stored in the credential directory. The URL token was only in memory.

Set an absolute path in the `youdub-host` supervisor environment. Do not put the token itself in the supervisor file, on the command line, or in nginx. The host creates the file on first start with mode `0600`, or reads it and tightens the mode if it already exists. A symlink or a non-token file stops startup instead of minting a replacement. A missing token, a wrong token, and a foreign origin are still rejected. `YOUDUB_PUBLIC_ORIGIN` stays the same exact origin.

```sh
# directory must exist and be owned by the host user; do not pre-create a world-readable file
YOUDUB_PUBLIC_ORIGIN=https://7860-cpod-1xhxbe8kv5n4.pod.compshare.cn
YOUDUB_LAUNCH_TOKEN_FILE=/var/lib/youdub/launch-token
```

The host still prints one URL at startup so the existing access-link sync can read it. That print is the product URL, not a new log line, and this patch does not write the token anywhere else. After the rebuilt host restarts twice, both printed URLs must carry the same token and the same origin. Confirm with `stat -c %a` that the file is `600`. Do not copy the token into chat.

This token change is host TypeScript. Apply the patch and run `npm run build:framework` before restarting `youdub-host`. The Python worker picks up Demucs, VoxCPM, and libx264 from the checkout on its next process. No model download.

## Before retry

Confirm the reported backup still matches the live task. Do not replace that backup, and do not retry if the checksums differ or fewer than 54 `tts/*.wav` files are present.

```sh
TASK=9280e54b-89e9-42e4-b871-b96ca69734fe
find "$YOUDUB_DATA" -type d -path "*/tasks/$TASK/attempts" -print
SRC=$(find "$YOUDUB_DATA" -type d -path "*/tasks/$TASK/attempts" -print -quit | sed 's:/attempts$::')
# BACKUP is the existing checksummed copy, not a new one
( cd "$SRC" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > /tmp/live.sha256
( cd "$BACKUP/task" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > /tmp/copy.sha256
diff -u /tmp/live.sha256 /tmp/copy.sha256
```

## Adopt, then retry once

`00000N.wav` is clip N of the checksummed `tts/request.json`. The Nth `clips` entry must name that filename, and its reference wav must be in the same checksum file. A different `request.json` hash makes adopt write nothing. Receipts store the ordinal, segment id, text, reference checksum, wav checksum, and request checksum. Resume copies a wav only when the new cue matches that ordinal, segment id, text, and reference checksum. Wav bytes are not modified. A legacy receipt does not prove the clip ran on CUDA.

```sh
MODEL=${YOUDUB_VOXCPM_MODEL_DIR:-$YOUDUB_DATA/models/voxcpm/VoxCPM2}
python -m backend.app.v1.tts_resume \
  --task-root "$SRC" \
  --model-path "$MODEL" \
  --checksums /tmp/live.sha256
```

`--model-path` must be the directory the host passes to VoxCPM. Then one retry. It keeps the old attempt and does not delete it.

```sh
curl -sS -X POST "$ORIGIN/api/youdub/v2" \
  -H "content-type: application/json" \
  --cookie "$COOKIE" \
  -d "{\"method\":\"POST\",\"path\":\"/api/v2/tasks/$TASK/retry\",\"body\":{\"expectedAttempt\":ATTEMPT}}"
```

Do not call `rerun` or `delete`.

## What to watch on the target GPU

```sh
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv
pgrep -af 'tts_process.py|separate_process.py'
```

VoxCPM logs `Loading VoxCPM2 once on cuda:0` and `tensors on cuda:0` only after `tts_model.to`. Demucs prints a `tensor_devices` line only after `model.to`. Either process exits `MODEL_NOT_READY` if a CUDA request is still on CPU, and does not continue that stage. Export stays `libx264`. No GPU run was measured in this workspace.

## Apply

On `59ef4b4`:

```sh
git apply --check artifacts/youdub-voxcpm-resume-since-59ef4b4.patch
git apply artifacts/youdub-voxcpm-resume-since-59ef4b4.patch
```

On `a270415`, including the HTTPS origin fix:

```sh
git apply --check artifacts/youdub-since-a270415.patch
git apply artifacts/youdub-since-a270415.patch
```
