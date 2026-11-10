from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from backend.app.v1.index_translate_server import MODEL_ID, render_prompt, serve, strip_think


class _Tokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True, enable_thinking=True):
        assert tokenize is False and add_generation_prompt is True and enable_thinking is False
        assert messages[0]["role"] == "system"
        return "prompt"


def test_prompt_disables_thinking_and_think_blocks_are_removed():
    assert render_prompt(_Tokenizer(), [{"role": "system", "content": "rules"}]) == "prompt"
    assert strip_think("<think>hidden</think>\n{\"segments\":[]}") == '{"segments":[]}'


def test_echo_server_streams_openai_chunks_and_rejects_another_model():
    def generate(messages, max_tokens):
        assert messages[0]["role"] == "user"
        assert max_tokens == 128
        return '<think>skip</think>{"segments":[{"segment_id":"s1","text":"你好"}]}', "stop"

    server = serve(generate, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        body = json.dumps({
            "model": MODEL_ID,
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        }).encode()
        with urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
            headers={"content-type": "application/json"},
        )) as response:
            payload = response.read().decode()
        assert response.status == 200
        assert '"finish_reason": "stop"' in payload
        assert "<think>" not in payload
        assert "你好" in payload
        assert payload.strip().endswith("data: [DONE]")
        bad = json.dumps({"model": "other", "messages": [{"role": "user", "content": "x"}]}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions", data=bad,
                headers={"content-type": "application/json"},
            ))
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("a different model id was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)
