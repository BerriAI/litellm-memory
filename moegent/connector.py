"""Opt-in, write-only host hooks with encrypted durable buffering and OAuth.

The hook never reads remote memories or emits context to the coding agent. Only
visible user/assistant text is captured; tools, system text and reasoning are
excluded. Host transcript formats are versioned adapters, not an MCP feature.
"""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import shlex
import sqlite3
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import tomlkit

from .crypto import KeyVault

MAX_OUTBOX_BYTES = 100_000_000
MAX_LINE_BYTES = 2_000_000
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")


def atomic_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6))
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class LocalCapture:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.vault = KeyVault(self.directory)
        self.db = sqlite3.connect(self.directory / "capture.sqlite3", timeout=10)
        (self.directory / "capture.sqlite3").chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY);
        """)

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return self.vault.open_operational(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, self.vault.seal_operational(value)))

    def enqueue(self, record):
        record_id = digest(record["source_id"])
        if self.db.execute("SELECT 1 FROM receipts WHERE id=?", (record_id,)).fetchone():
            return False
        self.db.execute("INSERT OR IGNORE INTO outbox(id,payload,created) VALUES(?,?,?)", (
            record_id, self.vault.seal_operational(record), time.time()))
        return True

    def capture(self, path, host, session_id, *, historical=False, max_records=250):
        path = Path(path).expanduser()
        if not path.is_file() or path.is_symlink():
            self.set("status", "transcript_unavailable")
            self.db.commit()
            return 0
        # Cursors, paths and payloads are encrypted. One transaction owns a
        # cursor advance and its queued records, so interruption cannot lose work.
        cursor_key = "cursor:" + digest(str(path.resolve()) + ":" + host)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT coalesce(sum(length(payload)),0) FROM outbox").fetchone()[0] >= MAX_OUTBOX_BYTES:
                self.set("status", "outbox_full")
                self.db.commit()
                return 0
            cursor = self.get(cursor_key, {"offset": 0, "generation": 0, "inode": path.stat().st_ino})
            stat = path.stat()
            if stat.st_size < cursor["offset"] or stat.st_ino != cursor["inode"]:
                cursor = {"offset": 0, "generation": cursor["generation"] + 1, "inode": stat.st_ino}
            if self.get("paused", False):
                cursor["offset"] = stat.st_size
                self.set(cursor_key, cursor)
                self.db.commit()
                return 0
            session_id = cursor.get("session_id", session_id)
            count, inspected = 0, 0
            with path.open("rb") as transcript:
                transcript.seek(cursor["offset"])
                while count < max_records and inspected < 2000:
                    offset = transcript.tell()
                    line = transcript.readline(MAX_LINE_BYTES + 1)
                    if not line or not line.endswith(b"\n"):
                        # A partial trailing JSON row is retried on the next hook.
                        if len(line) > MAX_LINE_BYTES:
                            self.set("status", "unsupported_large_transcript_row")
                        break
                    inspected += 1
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        self.set("status", "unsupported_transcript_row")
                        cursor["offset"] = transcript.tell()
                        continue
                    if isinstance(item, dict) and host == "codex" and item.get("type") == "session_meta":
                        session_id = str(item.get("payload", {}).get("id") or session_id)
                    elif isinstance(item, dict) and host == "claude" and item.get("sessionId"):
                        session_id = str(item["sessionId"])
                    cursor["session_id"] = session_id
                    message = visible_message(item, host)
                    if message:
                        role, content, timestamp, message_id = message
                        # A message UUID survives transcript relocation/replay;
                        # fallback offsets distinguish identical repeated messages.
                        identity = message_id or f"{cursor['generation']}:{offset}:{digest(content)}"
                        for part, begin in enumerate(range(0, len(content), 120_000)):
                            record = {"source_id": f"{host}:{digest(session_id)}:{digest(identity)}:{part}",
                                      "source": host, "session_id": session_id[:250],
                                      "content": content[begin:begin + 120_000], "historical": historical,
                                      "metadata": {"role": role, "adapter_version": 1, "message_part": part,
                                                   "evidence_type": "visible_conversation"}}
                            if timestamp:
                                record["occurred_at"] = timestamp
                            self.enqueue(record)
                        count += 1
                    cursor["offset"] = transcript.tell()
                self.set(cursor_key, cursor)
                self.set("last_capture", time.time())
            self.db.commit()
            return inspected
        except BaseException:
            self.db.rollback()
            raise

    def flush(self, *, transport=None, limit=200):
        # Separate lock prevents parallel hook processes rotating the same refresh
        # token. It never holds up another hook's durable enqueue transaction.
        import fcntl
        with (self.directory / "flush.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            config = self.get("connection")
            if not config or self.get("paused", False):
                return 0
            sent = 0
            try:
                with httpx.Client(base_url=config["url"], timeout=12, transport=transport) as client:
                    if config["expires_at"] < time.time() + 60:
                        response = client.post("/oauth/token", data={"grant_type": "refresh_token",
                            "refresh_token": config["refresh_token"], "client_id": config["client_id"],
                            "resource": config["url"] + "/mcp"})
                        if not response.is_success:
                            self.set("status", "reconnect" if response.status_code in {400, 401, 403} else "retrying")
                            self.db.commit()
                            return 0
                        token = response.json()
                        config.update(token, expires_at=time.time() + token["expires_in"])
                        self.set("connection", config)
                        self.db.commit()
                    for row in self.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY created LIMIT ?",
                                               (limit,)).fetchall():
                        response = client.post("/api/memories", json=self.vault.open_operational(row["payload"]),
                                               headers={"Authorization": "Bearer " + config["access_token"]})
                        if response.is_success:
                            with self.db:
                                self.db.execute("INSERT OR IGNORE INTO receipts VALUES(?)", (row["id"],))
                                self.db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
                            sent += 1
                        elif response.status_code in {400, 409, 413, 422}:
                            # Retain a visible dead letter; don't block later valid work.
                            with self.db:
                                self.db.execute("UPDATE outbox SET status='needs_attention' WHERE id=?", (row["id"],))
                        else:
                            self.set("status", "reconnect" if response.status_code == 401 else
                                     "paused_or_revoked" if response.status_code == 403 else "retrying")
                            self.db.commit()
                            return sent
                    self.set("last_sync", time.time())
                    self.set("status", "connected")
                    self.db.commit()
            except (httpx.HTTPError, ValueError, KeyError):
                self.set("status", "retrying")
                self.db.commit()
            return sent

    def status(self):
        return {"status": self.get("status", "not_connected"), "paused": self.get("paused", False),
                "last_capture": self.get("last_capture"), "last_sync": self.get("last_sync"),
                "queued": self.db.execute("SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0],
                "needs_attention": self.db.execute("SELECT count(*) FROM outbox WHERE status='needs_attention'").fetchone()[0]}


def visible_message(item, host):
    if not isinstance(item, dict):
        return None
    if host == "codex":
        if item.get("type") != "response_item":
            return None
        message = item.get("payload", {})
        if message.get("type") != "message" or message.get("channel") == "analysis":
            return None
    else:
        if item.get("type") not in {"user", "assistant"} or item.get("isMeta"):
            return None
        message = item.get("message", {})
    role = message.get("role")
    if role not in {"user", "assistant"}:
        return None
    content = message.get("content", [])
    if isinstance(content, list):
        content = "\n".join(x["text"] for x in content if isinstance(x, dict)
                            and x.get("type") in {"text", "input_text", "output_text"}
                            and isinstance(x.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        return None
    timestamp = item.get("timestamp")
    try:
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        timestamp = timestamp.isoformat() if timestamp.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        timestamp = None
    return role, content, timestamp, str(item.get("uuid") or message.get("id") or "")


def login(local, url, *, opener=webbrowser.open):
    url = url.rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})) \
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("Use the HTTPS address of your litemem installation.")
    result = {}
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlsplit(self.path).query)
            valid = urlsplit(self.path).path == "/callback" and secrets.compare_digest(params.get("state", [""])[0], state)
            if valid and params.get("code"):
                result["code"] = params["code"][0]
            self.send_response(200 if result else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Connected. Return to your terminal." if result else b"Invalid connection response.")

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", 0), Callback) as server, httpx.Client(base_url=url, timeout=20) as client:
        callback = f"http://127.0.0.1:{server.server_port}/callback"
        response = client.post("/oauth/register", json={"client_name": "litemem continuous capture",
                               "redirect_uris": [callback], "token_endpoint_auth_method": "none"})
        response.raise_for_status()
        client_id = response.json()["client_id"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth_url = url + "/oauth/authorize?" + urlencode({"client_id": client_id, "redirect_uri": callback,
            "response_type": "code", "code_challenge_method": "S256", "code_challenge": challenge,
            "state": state, "scope": "memory", "resource": url + "/mcp"})
        print("Sign in with Slack in the browser to connect this computer.")
        print(auth_url)
        opener(auth_url)
        server.timeout = 1
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
        if not result:
            raise ValueError("Sign-in timed out. Run connect again.")
        response = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": result["code"],
            "client_id": client_id, "code_verifier": verifier, "redirect_uri": callback, "resource": url + "/mcp"})
        response.raise_for_status()
        token = response.json()
        binding = local.get("account_binding")
        if binding and binding["url"] != url:
            raise ValueError("Use --directory for a different installation; queued work belongs to the original account.")
        # Refuse an account switch while any record is buffered: never upload one
        # person's local history under another person's new authorization.
        status = client.get("/api/capture-identity", headers={"Authorization": "Bearer " + token["access_token"]})
        status.raise_for_status()
        owner = status.json()["owner"]
        if binding and binding.get("owner") != owner:
            raise ValueError("This capture directory belongs to another account. Use --directory to keep accounts separate.")
        local.set("account_binding", {"url": url, "owner": owner})
        local.set("connection", {"url": url, "owner": owner, "client_id": client_id, **token,
                                 "expires_at": time.time() + token["expires_in"]})
        local.set("status", "connected")
        local.set("paused", False)
        local.db.commit()


def configure_hosts(home, directory, url, hosts, *, remove=False):
    """Merge only our named connection/commands. Never replace existing hooks."""
    marker = "litemem-managed-capture"
    changed, prepared = [], []

    def write(path, content):
        if path.is_symlink():
            raise ValueError("Configure litemem manually when the host configuration is a symlink.")
        prepared.append((path, content, path.read_text() if path.exists() else None))

    for host in hosts:
        hook_path = home / (".codex/hooks.json" if host == "codex" else ".claude/settings.json")
        document = json.loads(hook_path.read_text()) if hook_path.exists() else {}
        hooks = document.setdefault("hooks", {})
        for event in HOOK_EVENTS:
            groups = []
            for group in hooks.get(event, []):
                kept = [h for h in group.get("hooks", []) if marker not in h.get("command", "")]
                if kept:
                    groups.append({**group, "hooks": kept})
            if not remove:
                command = shlex.join([sys.executable, "-m", "moegent.connector", "--directory", str(directory),
                                      "hook", "--host", host, "--marker", marker])
                groups.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
            if groups:
                hooks[event] = groups
            else:
                hooks.pop(event, None)
        write(hook_path, json.dumps(document, indent=2) + "\n")
        if host == "codex":
            config_path = home / ".codex/config.toml"
            document = tomlkit.parse(config_path.read_text()) if config_path.exists() else tomlkit.document()
            servers = document.setdefault("mcp_servers", {})
            if not remove:
                if "litemem" in servers and servers["litemem"].get("url") != url + "/mcp":
                    raise ValueError("A different litemem MCP is already configured; rename it before connecting.")
                servers["litemem"] = {"url": url + "/mcp"}
            elif servers.get("litemem", {}).get("url") == url + "/mcp":
                del servers["litemem"]
            write(config_path, tomlkit.dumps(document))
        else:
            config_path = home / ".claude.json"
            document = json.loads(config_path.read_text()) if config_path.exists() else {}
            servers = document.setdefault("mcpServers", {})
            if not remove:
                if "litemem" in servers and servers["litemem"].get("url") != url + "/mcp":
                    raise ValueError("A different litemem MCP is already configured; rename it before connecting.")
                servers["litemem"] = {"type": "http", "url": url + "/mcp"}
            elif servers.get("litemem", {}).get("url") == url + "/mcp":
                del servers["litemem"]
            write(config_path, json.dumps(document, indent=2) + "\n")
    # Validate every document before touching any file. Detect concurrent host
    # changes and retain private backups for an explicit rollback.
    for path, content, original in prepared:
        if (path.read_text() if path.exists() else None) != original:
            raise ValueError("Host settings changed during setup. Close the tool and retry.")
        if original is not None:
            backup = path.with_name(path.name + ".litemem-backup-" + str(time.time_ns()))
            atomic_private(backup, original)
        atomic_private(path, content)
        changed.append(path)
    return changed


def main():
    parser = argparse.ArgumentParser(description="Connect private work capture to litemem.")
    parser.add_argument("--directory", type=Path, default=Path.home() / ".local/share/litemem")
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect")
    connect.add_argument("url")
    connect.add_argument("--host", choices=["codex", "claude", "both"], default="both")
    hook = commands.add_parser("hook")
    hook.add_argument("--host", choices=["codex", "claude"], required=True)
    hook.add_argument("--marker", default="")
    backfill = commands.add_parser("backfill")
    backfill.add_argument("path", type=Path)
    backfill.add_argument("--host", choices=["codex", "claude"], required=True)
    for name in ("flush", "status", "pause", "resume", "disconnect"):
        commands.add_parser(name)
    args = parser.parse_args()
    local = LocalCapture(args.directory)
    try:
        if args.command == "connect":
            login(local, args.url)
            hosts = ["codex", "claude"] if args.host == "both" else [args.host]
            configure_hosts(Path.home(), args.directory.resolve(), args.url.rstrip("/"), hosts)
            local.set("hosts", hosts)
            local.db.commit()
            print("Connected. Restart your coding tool to load its hooks and sign in to the litemem MCP when prompted.")
            print("New visible conversation text will be recorded privately. Use backfill to import earlier sessions.")
        elif args.command == "hook":
            # Hooks must not fail the user's coding turn or leak a transcript in an error.
            try:
                event = json.loads(sys.stdin.read(100_000))
                path = event.get("transcript_path")
                if path:
                    local.capture(path, args.host, str(event.get("session_id", Path(path).stem)))
                else:
                    local.set("status", "transcript_unavailable")
                    local.db.commit()
                subprocess.Popen([sys.executable, "-m", "moegent.connector", "--directory", str(args.directory), "flush"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except Exception:
                pass
        elif args.command == "flush":
            local.flush()
        elif args.command == "status":
            print(json.dumps(local.status(), indent=2))
        elif args.command in {"pause", "resume"}:
            local.set("paused", args.command == "pause")
            local.db.commit()
            print("Capture paused." if args.command == "pause" else "Capture resumed.")
        elif args.command == "backfill":
            paths = sorted(args.path.rglob("*.jsonl")) if args.path.is_dir() else [args.path]
            for path in paths:
                while local.capture(path, args.host, path.stem, historical=True):
                    local.flush()
            local.flush()
            print(json.dumps(local.status(), indent=2))
        elif args.command == "disconnect":
            config = local.get("connection")
            if config:
                with contextlib.suppress(httpx.HTTPError):
                    httpx.post(config["url"] + "/oauth/revoke", data={"token": config["refresh_token"]}, timeout=15)
                configure_hosts(Path.home(), args.directory.resolve(), config["url"], local.get("hosts", []), remove=True)
            local.set("paused", True)
            local.set("connection", None)
            local.set("status", "disconnected")
            local.db.commit()
            print("Disconnected. Local encrypted queued work is retained; revoke in Connections if offline.")
    except (httpx.HTTPError, ValueError, OSError):
        print("Could not complete this action. Check the URL, sign-in, and local configuration, then retry.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        local.close()


if __name__ == "__main__":
    main()
