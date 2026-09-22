"""Exercise real host hook dispatch in a fresh, disposable CI account.

Model responses and the upload endpoint are deterministic loopback fixtures.
This proves hook trust, advisory dispatch and authenticated MCP tool discovery
in both real hosts. Model judgment and Jev are outside this fixture.
Never run against a developer's configured home directory.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tomlkit

from litellm_memory.connector import LocalCapture, configure_hosts
from litellm_memory.connector_hooks import trust_reviewed_codex_hooks

PROMPT = "LITELLM_MEMORY_HOST_USER_CANARY: Reply briefly. Do not use tools."
ANSWER = "LITELLM_MEMORY_HOST_ASSISTANT_CANARY: Capture verified."
records = []
model_inputs = []


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for name, value in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(value)}\n\n".encode())
            self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path == "/mcp/":
            assert self.headers.get("Authorization") == "Bearer disposable-fixture"
            method = body.get("method")
            if "id" not in body:
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": body["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "litellm-memory", "version": "1.0.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "record_work", "description": "Save useful work privately.",
                          "inputSchema": {"type": "object", "properties": {"source_id": {"type": "string"},
                          "content": {"type": "string"}}, "required": ["source_id", "content"]}}]}
            else:
                result = {}
            return self.respond({"jsonrpc": "2.0", "id": body["id"], "result": result})
        if self.path == "/api/memories":
            assert self.headers.get("Authorization") == "Bearer disposable-fixture"
            records.append(body)
            return self.respond({"id": body["source_id"]})
        if self.path.split("?")[0].endswith("/responses"):
            model_inputs.append(body)
            message = {"id": "msg_fixture", "type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": ANSWER, "annotations": []}]}
            response = {"id": "resp_fixture", "object": "response", "created_at": int(time.time()),
                        "status": "completed", "model": "fixture", "output": [message],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            return self.stream([
                ("response.created", {"type": "response.created", "response": {
                    **response, "status": "in_progress", "output": []}}),
                ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                    "item": {**message, "status": "in_progress", "content": []}}),
                ("response.content_part.added", {"type": "response.content_part.added", "item_id": message["id"],
                    "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}}),
                ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": message["id"],
                    "output_index": 0, "content_index": 0, "delta": ANSWER}),
                ("response.output_text.done", {"type": "response.output_text.done", "item_id": message["id"],
                    "output_index": 0, "content_index": 0, "text": ANSWER}),
                ("response.content_part.done", {"type": "response.content_part.done", "item_id": message["id"],
                    "output_index": 0, "content_index": 0, "part": message["content"][0]}),
                ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": message}),
                ("response.completed", {"type": "response.completed", "response": response}),
            ])
        if "count_tokens" in self.path:
            return self.respond({"input_tokens": 1})
        if self.path.split("?")[0].endswith("/messages"):
            model_inputs.append(body)
            message = {"id": "msg_fixture", "type": "message", "role": "assistant", "model": body.get("model"),
                       "content": [], "stop_reason": None, "stop_sequence": None,
                       "usage": {"input_tokens": 1, "output_tokens": 0}}
            if not body.get("stream"):
                return self.respond({**message, "content": [{"type": "text", "text": ANSWER}], "stop_reason": "end_turn"})
            return self.stream([
                ("message_start", {"type": "message_start", "message": message}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": ANSWER}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1}}),
                ("message_stop", {"type": "message_stop"}),
            ])
        self.send_error(404)


def main():
    if os.environ.get("LITELLM_MEMORY_DISPOSABLE_HOST_TEST") != "1":
        raise SystemExit("Run only in the disposable host-verification CI job.")
    host = sys.argv[1]
    base = Path.home()
    # Refuse rather than alter any existing host configuration or credentials.
    for name in (".codex", ".claude", ".claude.json"):
        if (base / name).exists():
            raise SystemExit(f"Expected a clean CI account; {name} already exists.")
    with tempfile.TemporaryDirectory(prefix="litellm-memory-host-") as directory:
        root = Path(directory)
        state = root / "capture"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
        configure_hosts(base, state, url, [host])
        local = LocalCapture(state)
        local.set("account_binding", {"url": url, "owner": "T:OWNER"})
        local.set("connection", {"url": url, "owner": "T:OWNER", "access_token": "disposable-fixture", "expires_at": time.time() + 3600})
        local.set_paused(False)
        local.db.commit()
        local.close()
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        if host == "codex":
            path = base / ".codex/config.toml"
            config = tomlkit.parse(path.read_text())
            config.update(model="fixture", model_provider="fixture", web_search="disabled")
            config["model_providers"] = {"fixture": {"name": "Disposable fixture", "base_url": url + "/v1",
                                                  "wire_api": "responses", "requires_openai_auth": False}}
            config["features"] = {"hooks": True}
            path.write_text(tomlkit.dumps(config))
            # Only the two exact installer commands in this disposable account are reviewed.
            trust_reviewed_codex_hooks(base, state)
            command = ["codex", "exec", "--skip-git-repo-check",
                       "--sandbox", "read-only", "--json", PROMPT]
        else:
            env.update(ANTHROPIC_API_KEY="disposable-fixture", ANTHROPIC_BASE_URL=url,
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            command = ["claude", "-p", "--model", "claude-sonnet-4-6", "--tools", "",
                       "--output-format", "json", PROMPT]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=90)
        if result.returncode or ANSWER not in result.stdout:
            print(result.stdout[-4000:])
            print(result.stderr[-4000:])
            raise AssertionError(f"{host} fixture response failed (exit {result.returncode})")
        assert any('record_work' in json.dumps(body.get('tools', [])) for body in model_inputs), 'Memory MCP tools did not reach the host model'
        assert not records, 'A reminder hook uploaded conversation text'
        assert any('Keep a low bar' in json.dumps(body) and 'Skip saving' in json.dumps(body)
                   for body in model_inputs), 'Memory guidance did not reach the host model'
        local = LocalCapture(state)
        assert local.status()['queued'] == 0, 'A reminder hook buffered conversation text'
        local.close()
        version = subprocess.check_output([host, "--version"], text=True).strip()
        print(json.dumps({"host": version, "transcripts_uploaded": len(records), "guidance_received": True, "mcp_tools_available": True}))
        server.shutdown()


if __name__ == "__main__":
    main()
