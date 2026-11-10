# Phrase grouping and read-only seam comparison

Baseline: `b0da50bdf1b170f9f134cb4ffe800743faaf06a1` (upload progress). This patch does not change Index-Translate serving, the public origin, or the launch-token file.

## What is still not measured

No GPU run and no listening test were done. Silence detection and this comparison do not show that a joint sound is fixed. Do not rerun task `9280e54b-89e9-42e4-b871-b96ca69734fe` or `59a78441-20c7-44b1-82d1-fadb946bdb62`.

## Read a short interval

```sh
python -m backend.app.v1.seam_compare \
  --task-root "$TASK" \
  --output-dir /tmp/youdub-seam \
  --start-ms 10000 \
  --end-ms 16000
```

`/tmp/youdub-seam` must be outside the task. The command copies only the overlapping cues, one neighbor on each side, and the mix/background window. `summary.json` records source and dubbed times and, when both files exist, the measured adjusted-length divided by the raw TTS length. `comparison.wav` concatenates the adjusted slices. It does not rewrite the task.

## Phrase grouping

The author has no dubbing grouping step. Subtitle punctuation splitting is display-only. Whisper tiny does not set `speaker_id`.

`groupCues` defaults off, so an old saved graph stays one cue per clone. The built-in「视频配音」template sets it on for a new task. Two cues join only when the gap is at most 300 ms, the span stays within 12 seconds, and the previous cue does not end a sentence. Different known speakers never join. If either cue has a speaker id and the other does not, they do not join. If neither has a speaker id, the join uses only the pause, punctuation, and length rules. That is not a claim that the speakers are the same.

The English prompt is the joined source text. The Chinese line is the joined translation. The reference wav is the span from the first cue start through the last cue end, including a short internal pause. It is not a fixed trim of every cue. A receipt without the same `member_ids` is not reused.

## Apply

```sh
git apply --check artifacts/youdub-phrase-seam-since-b0da50b.patch
git apply artifacts/youdub-phrase-seam-since-b0da50b.patch
npm --prefix apps/harness run build
```

Restart `youdub-host` only when no task is running.
