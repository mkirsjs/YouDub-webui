"""Loopback OpenAI chat server for a local Index-Translate-2B directory.

The process loads BF16 weights, forces ``enable_thinking=False``, and exits
when the parent stops it. Tests can pass a fake generator and load no weights.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "IndexTeam/Index-Translate-2B"
THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def strip_think(text: str) -> str:
    return THINK_BLOCK.sub("", text, count=1).strip()


def render_prompt(tokenizer, messages: list[dict]) -> str:
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False,
    )


def sse(payload: dict | None) -> bytes:
    if payload is None:
        return b"data: [DONE]\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def completion_body(text: str, finish: str, *, index: int = 0) -> list[dict]:
    return [
        {"id": "index-translate", "object": "chat.completion.chunk", "choices": [
            {"index": index, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
        {"id": "index-translate", "object": "chat.completion.chunk", "choices": [
            {"index": index, "delta": {}, "finish_reason": finish}]},
    ]


class TranslateHandler(BaseHTTPRequestHandler):
    generate = None
    model_id = MODEL_ID

    def log_message(self, _format, *_args) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("content-length", "0"))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
            messages = request.get("messages")
            if request.get("model") not in {None, self.model_id} or not isinstance(messages, list):
                raise ValueError("model or messages are invalid")
            text, finish = self.generate(messages, int(request.get("max_tokens") or 1024))
            text = strip_think(text)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in completion_body(text, finish):
            self.wfile.write(sse(event))
        self.wfile.write(sse(None))


def serve(generate, host: str, port: int, model_id: str = MODEL_ID) -> ThreadingHTTPServer:
    handler = type("BoundTranslateHandler", (TranslateHandler,), {"generate": staticmethod(generate), "model_id": model_id})
    server = ThreadingHTTPServer((host, port), handler)
    return server


def load_generator(model_dir: str):
    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("Index-Translate-2B requires CUDA. Refusing to load it on CPU.")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(model_dir, dtype=torch.bfloat16, local_files_only=True)
    model.to("cuda").eval()

    def generate(messages: list[dict], max_tokens: int) -> tuple[str, str]:
        prompt = render_prompt(tokenizer, messages)
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        new_tokens = output[0, inputs["input_ids"].shape[-1]:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        finish = "length" if len(new_tokens) >= max_tokens else "stop"
        return text, finish

    return generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--echo", action="store_true")
    options = parser.parse_args(argv)
    if options.host not in {"127.0.0.1", "::1"}:
        raise SystemExit("The translation server binds loopback only.")

    def echo(_messages: list[dict], _max_tokens: int) -> tuple[str, str]:
        text = json.dumps({"segments": [{"segment_id": "echo", "text": "回声"}]}, ensure_ascii=False)
        return text, "stop"

    generate = echo if options.echo else load_generator(options.model_dir)
    server = serve(generate, options.host, options.port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
