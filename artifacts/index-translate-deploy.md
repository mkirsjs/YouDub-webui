# Local Index-Translate-2B

This is the translation stage only. It does not change clip timing, mixing, or the unfinished edge-sound investigation. Do not retry task `9280e54b-89e9-42e4-b871-b96ca69734fe` or `59a78441-20c7-44b1-82d1-fadb946bdb62`.

## Choice

`IndexTeam/Index-Translate-2B` is the model. The checkpoint `config.json` says `Qwen3_5ForConditionalGeneration`. The project license is Apache-2.0 (`https://github.com/bilibili/Index-Translate`). Official inference memory for BF16 2B is about 8 GB plus KV cache (`inference/llm/serve_vllm.sh`). The 9B preset is about 24 GB and is not used. FP8 is not assumed to work on this 4090.

The official server examples use vLLM. A vLLM install is large enough to threaten the remaining disk, so this host does not install it. A separate venv reuses the existing Torch 2.8 CUDA wheel and adds Transformers 5.6.2, which contains the `qwen3_5` implementation. The process speaks the same OpenAI chat-completions stream the Harness translator already calls. It always applies the chat template with `enable_thinking=False`. YouDub still checks that every segment id comes back, and a `max-tokens` finish splits that batch instead of keeping a partial body.

## Disk and GPU

Weights are about 4.57 GB from ModelScope. The extra Python packages are the pins in `backend/requirements/index-translate.txt`, not a second CUDA Torch. Keep the download and the venv under the free space reported on the root disk; do not treat the 24 GB figure as exact.

Translation loads only while a translate node is running. The last translate node to finish stops the process, which releases the GPU before VoxCPM. If two tasks are inside translation at the same time, the second stop does not kill the process the first task still holds. On this 24 GB card, run one task at a time so translation (about 8 GB) and VoxCPM (measured near 9 GB) do not overlap.

## Install

```sh
DIR=/var/lib/youdub/models/Index-Translate-2B
PY=/var/lib/youdub/index-translate
"$YOUDUB_PYTHON" -m venv --system-site-packages "$PY"
"$PY/bin/pip" install -r backend/requirements/index-translate.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
"$PY/bin/pip" install modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple
"$PY/bin/modelscope" download --model IndexTeam/Index-Translate-2B --local_dir "$DIR"
```

`YOUDUB_PYTHON` is the interpreter that already runs VoxCPM. The ModelScope id is `IndexTeam/Index-Translate-2B`.

Add these to the existing `youdub-host` supervisor environment and restart only that program after the host bundle is rebuilt:

```sh
YOUDUB_MEDIA_ROOT=/path/to/YouDub-webui
YOUDUB_TRANSLATION_MODEL_DIR=/var/lib/youdub/models/Index-Translate-2B
YOUDUB_TRANSLATION_PYTHON=/var/lib/youdub/index-translate/bin/python
YOUDUB_LOCAL_TRANSLATION_KEY=local
```

The model id defaults to `IndexTeam/Index-Translate-2B` and the base URL to `http://127.0.0.1:8091/v1`. A public URL is rejected. `8091` is not 8888 or 8889. The key value `local` is a placeholder for the loopback server, not a paid credential.

## Check before a real video

Echo mode does not load weights:

```sh
"$PY/bin/python" -m backend.app.v1.index_translate_server --echo --model-dir "$DIR" --port 8091
```

From another shell, a completion request to `127.0.0.1:8091` should return a short JSON chunk and `finish_reason` `stop`. Stop the echo process. Then start one short new video from the built-in「视频配音」graph. Do not use the finished long videos. If the output cap is hit, the batch splits and every segment id must still be present. A single segment that still stops for `max-tokens` fails the node.

## Apply

On `8c5395b3cf1f6c42c1e8c29eae86571c0a07e961`:

```sh
git apply --check artifacts/youdub-index-translate-since-8c5395b.patch
git apply artifacts/youdub-index-translate-since-8c5395b.patch
npm --prefix apps/harness run build
```

Restart `youdub-host` when no task is running. Keep the public origin and `/var/lib/youdub/launch-token`.

## Rollback

Unset the four translation variables, restart `youdub-host` from commit `8c5395b`, and leave the weight directory in place. The dubbing graph then blocks again until a translation provider exists. Do not delete the weights as part of rollback unless disk space requires it.
