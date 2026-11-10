# Speaker scope for phrase grouping

Apply this after `17e43453b678a423fa0baacb6d9eb60d53f2e2cb`. Do not replace or reorder the silence, Index, upload, or phrase-seam patches.

Whisper tiny does not label speakers. Grouping no longer treats two unlabeled cues as one person. The「视频配音」template still shows short-cue grouping, with **说话人范围** set to **未知说话人：不跨句合并**. That choice is on the synthesize card. It does not enable speaker detection and it does not download a speaker model.

| 说话人范围 | Unlabeled cues | Same known speaker | Different speakers |
|---|---|---|---|
| `unknown` (default) | stay separate | may join | never join |
| `multi` | stay separate; the label says there is no speaker detection | may join | never join |
| `single` | may join after the user confirms the whole source is one person | may join | never join |

A saved graph that only has `groupCues: true` from the previous patch also stays on `unknown`, because a missing scope is not treated as single. Joining still stops at 300 ms, 12 seconds, or a sentence ending. One side labeled and the other unlabeled never join.

`tts/grouping.json` lists the phrases and `unknown_not_joined`. A receipt from another scope or another member list is not reused.

## Short-sample A/B, no video and no model

```sh
python -m backend.app.v1.cue_groups
```

`per_cue` is one cue per clone. `single_speaker` joins the unlabeled question pair. `unknown` and `multi` leave that pair split and still join the two lines that already share a speaker id. The sample is in `backend/app/v1/cue_groups.py` (`SHORT_SAMPLE`). It is not task `9280e54b` or `59a78441`.

## Apply

```sh
git apply --check artifacts/youdub-speaker-scope-since-17e4345.patch
git apply artifacts/youdub-speaker-scope-since-17e4345.patch
npm --prefix apps/harness run build
```

Restart `youdub-host` only when no task is running. The rebuild is what shows the new labels. The worker default already refuses unlabeled joins before that rebuild.
