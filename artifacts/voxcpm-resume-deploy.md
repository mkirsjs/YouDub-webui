# VoxCPM resume and CUDA check

Deployed base: `59ef4b4599927f7ee139fb4e1d836ac7d533d0f3`. Do not drop the HTTPS patch. No GPU benchmark and no backup were done from this workspace. The coordinator server was not contacted.

## What the code does today

`speech.synthesize/v1` runs `tts_process.py` once. `VoxCPM.from_pretrained(..., device=...)` is one call, then `generate` runs once per cue. This repository does not reload the model per cue. It also never reads the parameter device after load, and the UI stays on `Generating translated speech with VoxCPM2` until the process exits.

Cancel leaves synthesize incomplete. `POST /api/youdub/v2` `retry` keeps completed steps, increments the attempt, and runs synthesize in a new directory `tasks/<id>/attempts/<attempt>/<invocationId>/`. It does not delete older attempt directories (`delete` is the call that removes the task tree). It also does not read the 54 wavs. `request.json` is written for every cue before generation, so that file does not prove a wav was produced from that cue. There is no per-cue receipt. A wav without a receipt must not be reused.

## Before any retry

When the server is reachable again, copy first. Do not retry, rerun, or delete task `9280e54b-89e9-42e4-b871-b96ca69734fe` before the two copies match.

```sh
TASK=9280e54b-89e9-42e4-b871-b96ca69734fe
# data root is the YouDub data directory (Files root), not the git checkout
find "$YOUDUB_DATA" -type d -path "*/tasks/$TASK/attempts" -print
SRC=$(find "$YOUDUB_DATA" -type d -path "*/tasks/$TASK/attempts" -print -quit | sed 's:/attempts$::')
BACKUP=/workspace/youdub-backup/$TASK-$(date +%Y%m%d%H%M%S)
mkdir -p "$BACKUP"
cp -a "$SRC" "$BACKUP/task"
( cd "$SRC" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$BACKUP/live.sha256"
( cd "$BACKUP/task" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$BACKUP/copy.sha256"
diff -u "$BACKUP/live.sha256" "$BACKUP/copy.sha256"
```

Stop if `diff` is not empty, if fewer than 54 `tts/*.wav` files are listed, or if any file is empty. Keep `$BACKUP` outside the task directory.

## Adopt only the checksummed clips

Receipts are written only after every listed wav, its reference wav, and `tts/request.json` match the checksum file. Wav bytes are not modified. A mismatch writes nothing.

```sh
MODEL=${YOUDUB_VOXCPM_MODEL_DIR:-$YOUDUB_DATA/models/voxcpm/VoxCPM2}
python -m backend.app.v1.tts_resume \
  --task-root "$SRC" \
  --model-path "$MODEL" \
  --checksums "$BACKUP/live.sha256"
```

`--model-path` must be the same directory the host will pass to VoxCPM. The command refuses references that are outside the task or absent from the checksum file.

## Then retry once

Retry does not delete the old attempt. After receipts exist, the new synthesize copies a clip only when the receipt matches the current text, reference checksum, model path, and wav checksum. Other cues are generated. An unreceipted wav is never treated as finished.

```sh
# read attempt from the task, then:
curl -sS -X POST "$ORIGIN/api/youdub/v2" \
  -H "content-type: application/json" \
  --cookie "$COOKIE" \
  -d "{\"method\":\"POST\",\"path\":\"/api/v2/tasks/$TASK/retry\",\"body\":{\"expectedAttempt\":ATTEMPT}}"
```

Do not call `rerun` or `delete` for this recovery. `rerun` starts another task and does not carry an unfinished synthesize directory.

## GPU check while synthesize is running

This repository's tests mock VoxCPM. They do not measure a GPU. On the server:

```sh
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv
pgrep -af tts_process.py
```

The task log should show `Loading VoxCPM2 once on cuda:0` and then `VoxCPM2 loaded once; requested cuda:0; tensors on cuda:0`. If parameters are not on `cuda:0`, the process exits `MODEL_NOT_READY` and does not generate. Later lines are `generated cue N/129 in Xs` or `reused verified cue N/129`. A legacy receipt proves checksum identity, not that the original 54 clips ran on CUDA.

## Apply

Already on `59ef4b4`:

```sh
git apply --check artifacts/youdub-voxcpm-resume-since-59ef4b4.patch
git apply artifacts/youdub-voxcpm-resume-since-59ef4b4.patch
```

Fresh `a270415` tree, including the HTTPS fix:

```sh
git apply --check artifacts/youdub-since-a270415.patch
git apply artifacts/youdub-since-a270415.patch
```

Python only. `npm run build:framework` is not required for this change. Restart the host after the patch so the worker loads it. No model download.
