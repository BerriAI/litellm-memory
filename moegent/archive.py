"""Per-person work archive. Ciphertext persists; authorization precedes decryption."""

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite
from pydantic import BaseModel, Field

from .crypto import KeyVault, decrypt_blob, encode, encrypt_blob, new_key
from .search_index import SearchIndex


def now() -> str:
    return datetime.now(UTC).isoformat()


class WorkInput(BaseModel):
    source_id: str = Field(min_length=1, max_length=250)
    title: str = Field(default="", max_length=300)
    content: str = Field(min_length=1, max_length=200000)
    source: str = Field(default="manual", max_length=100)
    session_id: str = Field(default="", max_length=250)
    occurred_at: datetime | None = None
    kind: str = Field(default="work", max_length=50)
    metadata: dict = Field(default_factory=dict)
    historical: bool = False


@dataclass(frozen=True)
class Reader:
    owner: str
    audience: Literal["owner", "public"] = "owner"

    @property
    def team(self):
        return self.owner.split(":", 1)[0]


class Archive:
    def __init__(self, path: Path, vault: KeyVault, museum, *, sharing_enabled=False):
        self.path, self.vault, self.museum = path, vault, museum
        self.sharing_enabled = sharing_enabled
        self.search_index = SearchIndex(self)
        self.lock = asyncio.Lock()
        self.owner_locks: dict[str, asyncio.Lock] = {}

    def owner_lock(self, owner):
        return self.owner_locks.setdefault(owner, asyncio.Lock())

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.path.chmod(0o600)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY, team TEXT NOT NULL, profile TEXT NOT NULL, wrapped_key TEXT NOT NULL,
                active INTEGER DEFAULT 1, agent_enabled INTEGER DEFAULT 1, capture_enabled INTEGER DEFAULT 1,
                policy_version INTEGER DEFAULT 1, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_tokens (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL, created REAL NOT NULL, last_used REAL, revoked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, source_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL, payload TEXT NOT NULL, visibility TEXT DEFAULT 'private',
                intent TEXT DEFAULT 'private', status TEXT DEFAULT 'pending', historical INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0, next_attempt REAL DEFAULT 0, error TEXT,
                created REAL NOT NULL, updated REAL NOT NULL, deleted INTEGER DEFAULT 0,
                UNIQUE(owner, source_hash)
            );
            CREATE INDEX IF NOT EXISTS records_owner ON records(owner,deleted,created);
            CREATE INDEX IF NOT EXISTS records_pending ON records(status,next_attempt,historical,created);
            CREATE TABLE IF NOT EXISTS privacy_rules (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, version TEXT NOT NULL,
                payload TEXT NOT NULL, active INTEGER DEFAULT 1, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS classification (
                record_id TEXT, digest TEXT, criterion TEXT, version TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(record_id,digest,criterion,version)
            );
            CREATE TABLE IF NOT EXISTS archive_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS record_relations (
                record_id TEXT PRIMARY KEY, previous_id TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL
            );
            UPDATE records SET status='pending' WHERE status='processing';
        """)
        columns = {r[1] for r in await (await self.db.execute("PRAGMA table_info(records)")).fetchall()}
        if "work_lease" not in columns:
            await self.db.execute("ALTER TABLE records ADD COLUMN work_lease TEXT NOT NULL DEFAULT ''")
        if "publication" not in columns:
            await self.db.execute("ALTER TABLE records ADD COLUMN publication TEXT")
            # Old rows were never approved as separate public excerpts.
            await self.db.execute("UPDATE records SET visibility='private' WHERE visibility='public'")
        if "source_payload" not in columns:
            await self.db.execute("ALTER TABLE records ADD COLUMN source_payload TEXT")
        await self.db.execute("UPDATE records SET work_lease='' WHERE status='pending'")
        token_columns = {r[1] for r in await (await self.db.execute("PRAGMA table_info(api_tokens)")).fetchall()}
        if "expires" not in token_columns:
            await self.db.execute("ALTER TABLE api_tokens ADD COLUMN expires REAL")
        await self.db.execute("""CREATE TABLE IF NOT EXISTS refresh_tokens (
            digest TEXT PRIMARY KEY, connection_id TEXT NOT NULL, client_id TEXT NOT NULL,
            expires REAL NOT NULL, consumed INTEGER DEFAULT 0)""")
        await self.db.commit()

    async def close(self):
        self.search_index.close()
        await self.db.close()

    async def member(self, owner):
        row = await (await self.db.execute("SELECT * FROM members WHERE id=? AND active=1", (owner,))).fetchone()
        if not row:
            return None
        return {**dict(row), "profile": self.vault.open_operational(row["profile"])}

    async def enroll(self, owner, profile):
        async with self.owner_lock(owner):
            existing = await self.member(owner)
            if existing:
                await self.db.execute("UPDATE members SET profile=? WHERE id=?", (
                    self.vault.seal_operational({**existing["profile"], **profile}), owner))
            else:
                previous = await (await self.db.execute("SELECT id FROM members WHERE id=?", (owner,))).fetchone()
                if previous:
                    raise ValueError("This account has left. Restore it explicitly before rejoining.")
                await self.db.execute("INSERT INTO members(id,team,profile,wrapped_key,created) VALUES(?,?,?,?,?)",
                                      (owner, owner.split(":", 1)[0], self.vault.seal_operational(profile),
                                       self.vault.wrap(owner, new_key()), time.time()))
            await self.db.commit()
        return await self.member(owner)

    async def key(self, owner, *, require_agent=False):
        member = await self.member(owner)
        if not member or (require_agent and not member["agent_enabled"]):
            raise PermissionError("The personal agent does not have access to this account.")
        return self.vault.unwrap(owner, member["wrapped_key"])

    async def create_token(self, owner, label="Codex / Claude Code", lifetime=86400 * 30):
        if not await self.member(owner):
            raise PermissionError("Sign up before creating a connection.")
        token = "llmem_" + secrets.token_urlsafe(40)
        token_id = str(uuid.uuid4())
        expires = time.time() + lifetime
        await self.db.execute("INSERT INTO api_tokens(id,owner,digest,label,created,expires) VALUES(?,?,?,?,?,?)", (
            token_id, owner, hashlib.sha256(token.encode()).hexdigest(),
            self.vault.seal_operational(label[:120]), time.time(), expires))
        await self.db.commit()
        return {"id": token_id, "token": token, "label": label[:120], "expires_at": expires}

    async def token_owner(self, token):
        row = await (await self.db.execute("""
            SELECT api_tokens.id, owner FROM api_tokens JOIN members ON members.id=api_tokens.owner
            WHERE digest=? AND revoked=0 AND members.active=1 AND (expires IS NULL OR expires>?)
        """, (hashlib.sha256(token.encode()).hexdigest(), time.time()))).fetchone()
        if not row:
            return None
        if hasattr(self, 'check_eligibility') and not await self.check_eligibility(row['owner']):
            return None
        await self.db.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (time.time(), row["id"]))
        await self.db.commit()
        return row["owner"]

    async def tokens(self, owner):
        rows = await (await self.db.execute(
            "SELECT id,label,created,last_used,revoked FROM api_tokens WHERE owner=? ORDER BY created DESC", (owner,)
        )).fetchall()
        return [{**dict(r), "label": self.vault.open_operational(r["label"])} for r in rows]

    async def revoke_token(self, owner, token_id):
        await self.db.execute("UPDATE api_tokens SET revoked=1 WHERE id=? AND owner=?", (token_id, owner))
        await self.db.commit()

    async def capture(self, owner: str, body: WorkInput):
        if self.museum is None:
            raise RuntimeError("Ente encrypted storage has not been configured.")
        member = await self.member(owner)
        if not member or not member["capture_enabled"]:
            raise PermissionError("Capture is paused for this account.")
        value = body.model_dump(mode="json")
        if len(encode(value)) > 300000:
            raise ValueError("Split this capture into smaller records.")
        digest = hashlib.sha256(encode({k: v for k, v in value.items() if k != "historical"})).hexdigest()
        source_hash = hashlib.sha256((body.source + ":" + body.source_id).encode()).hexdigest()
        record_id = hashlib.sha256((owner + ":" + source_hash).encode()).hexdigest()
        async with self.owner_lock(owner):
            current_member = await self.member(owner)
            if not current_member or not current_member["capture_enabled"]:
                raise PermissionError("Capture is paused for this account.")
            old = await (await self.db.execute("SELECT * FROM records WHERE id=?", (record_id,))).fetchone()
            if old:
                if old["deleted"]:
                    raise ValueError("This source was deleted and will not be reimported.")
                if old["content_hash"] != digest:
                    previous = (await self.decode(old))["original"]
                    if {k: v for k, v in previous.items() if k != "historical"} != {
                            k: v for k, v in value.items() if k != "historical"}:
                        raise ValueError("A source ID cannot replace its original content. Use a new revision ID.")
                return {"id": record_id, "status": old["status"], "duplicate": True}
            payload = {"schema": 1, "id": record_id, "owner": owner, "original": value,
                       "title": body.title or "Recorded work", "content": body.content,
                       "created_at": value["occurred_at"], "captured_at": now(),
                       "kind": body.kind, "source": body.source, "metadata": body.metadata,
                       "summary": "", "labels": {}, "embedding": None}
            blob = encrypt_blob(payload, await self.key(owner))
            stamp = time.time()
            await self.db.execute("""INSERT INTO records
                (id,owner,source_hash,content_hash,payload,source_payload,status,historical,created,updated)
                VALUES(?,?,?,?,?,?,'storing',?,?,?)""", (record_id, owner, source_hash, digest, json.dumps(blob), json.dumps(blob),
                                             int(body.historical), stamp, stamp))
            # Corrections stay private even when future automatic sharing is on.
            # Record the link with the intake so crash recovery and portable
            # imports rebuild it without changing any original source bytes.
            previous_id = body.metadata.get('supersedes') if body.source == 'correction' else None
            if isinstance(previous_id, str) and len(previous_id) == 64 and all(c in '0123456789abcdef' for c in previous_id):
                await self.db.execute('INSERT INTO record_relations VALUES(?,?,?,?)',
                                      (record_id, previous_id, owner, 'correction'))
                await self.db.execute("UPDATE records SET visibility='private',intent='private' WHERE id=? AND owner=?",
                                      (previous_id, owner))
                await self.db.execute('UPDATE members SET policy_version=policy_version+1 WHERE id=?', (owner,))
                self.search_index.evict_owner(owner)
            elif body.source != 'correction':
                await self.db.execute("UPDATE records SET intent='auto' WHERE id=?", (record_id,))
            await self.db.commit()
        # Commit the encrypted intake before network I/O. Both object IDs and
        # ciphertext survive a crash; background recovery retries the same bytes.
        await self.persist_intake(record_id)
        current = await self.row(record_id)
        return {"id": record_id, "status": current["status"], "duplicate": False}

    async def persist_intake(self, record_id):
        row = await self.row(record_id)
        if not row:
            return
        async with self.owner_lock(row["owner"]):
            row = await self.row(record_id)
            if row["status"] != "storing" or row["deleted"]:
                return
            try:
                await self.museum.put(record_id + ":source", json.loads(row["source_payload"]))
                await self.museum.put(record_id, json.loads(row["payload"]))
            except Exception:
                await self.db.execute("UPDATE records SET next_attempt=?,error='storage' WHERE id=?",
                                      (time.time() + 30, record_id))
            else:
                await self.db.execute("UPDATE records SET status='pending',next_attempt=0,error=NULL WHERE id=?", (record_id,))
            await self.db.commit()

    async def allowed(self, reader: Reader, row) -> bool:
        if row["deleted"] or not await self.member(reader.owner):
            return False
        owner_member = await self.member(row["owner"])
        if not owner_member or owner_member["team"] != reader.team:
            return False
        if reader.audience == "owner" and row["owner"] == reader.owner:
            return True
        return bool(self.sharing_enabled and row["visibility"] == "public" and row["publication"])

    async def row(self, record_id):
        row = await (await self.db.execute("SELECT * FROM records WHERE id=?", (record_id,))).fetchone()
        return dict(row) if row else None

    async def decode(self, row):
        payload = decrypt_blob(json.loads(row["payload"]), await self.key(row["owner"]))
        if payload.get("id") != row["id"] or payload.get("owner") != row["owner"]:
            raise ValueError("Encrypted record identity mismatch")
        return payload

    async def read(self, reader: Reader, record_id):
        row = await self.row(record_id)
        if not row or not await self.allowed(reader, row):
            raise LookupError("Memory not found")
        payload = await self.visible_payload(reader, row)
        if reader.owner == row['owner'] and reader.audience == 'owner':
            related = await (await self.db.execute('''SELECT l.record_id,l.previous_id,l.kind FROM record_relations l
                JOIN records a ON a.id=l.record_id AND a.owner=l.owner AND a.deleted=0
                JOIN records b ON b.id=l.previous_id AND b.owner=l.owner AND b.deleted=0
                WHERE l.owner=? AND (l.record_id=? OR l.previous_id=?)''', (reader.owner, record_id, record_id))).fetchall()
            payload = {**payload, 'revisions': [dict(r) for r in related]}
        return {**payload, "visibility": row["visibility"], "status": row["status"],
                "historical": bool(row["historical"]), "error": row["error"]}

    async def visible_payload(self, reader, row):
        if reader.owner == row["owner"] and reader.audience == "owner":
            return await self.decode(row)
        if not row["publication"]:
            raise LookupError("Memory not found")
        payload = decrypt_blob(json.loads(row["publication"]), await self.key(row["owner"]))
        if payload.get("id") != row["id"] or payload.get("owner") != row["owner"]:
            raise ValueError("Encrypted publication identity mismatch")
        return payload

    async def records(self, reader, *, owner=None):
        return [item async for item in self.iter_records(reader, owner=owner)]

    async def iter_records(self, reader, *, owner=None):
        if not await self.member(reader.owner):
            raise PermissionError("Account is unavailable")
        # This query narrows by workspace and ACL before any private content is decrypted.
        cursor = await self.db.execute("""SELECT r.* FROM records r JOIN members m ON m.id=r.owner
            WHERE r.deleted=0 AND m.active=1 AND m.team=?
            AND ((r.owner=? AND ?='owner') OR (r.visibility='public' AND r.publication IS NOT NULL AND ?=1))
            AND (? IS NULL OR r.owner=?) ORDER BY r.created DESC""", (reader.team, reader.owner, reader.audience,
                                        int(self.sharing_enabled), owner, owner))
        try:
            while rows := await cursor.fetchmany(32):
                for row in rows:
                    if await self.allowed(reader, row):
                        yield {**await self.visible_payload(reader, row), "visibility": row["visibility"],
                               "status": row["status"], "historical": bool(row["historical"])}
        finally:
            await cursor.close()

    async def search(self, reader, query="", *, owner=None, visibility=None, kind=None, source=None,
                     created_from=None, created_before=None, offset=0, limit=30, vector=None, embedding_model=None):
        return await self.search_index.search(reader, query, owner=owner, visibility=visibility, kind=kind,
            source=source, created_from=created_from, created_before=created_before, offset=offset, limit=limit,
            vector=vector, embedding_model=embedding_model)

    async def rules(self, owner):
        rows = await (await self.db.execute(
            "SELECT * FROM privacy_rules WHERE owner=? AND active=1 ORDER BY created", (owner,))).fetchall()
        key = await self.key(owner)
        return [{"id": r["id"], "version": r["version"],
                 **decrypt_blob(json.loads(r["payload"]), key)} for r in rows]

    async def sharing_preferences(self, owner):
        member = await self.member(owner)
        if not member:
            raise PermissionError('Account is unavailable')
        return member['profile'].get('sharing', {
            'allowed_types': ['progress', 'decision', 'finding', 'blocker', 'plan'],
            'auto_share': False, 'future_since': None})

    async def set_sharing_preferences(self, owner, allowed_types, auto_share):
        valid = {'progress', 'decision', 'finding', 'blocker', 'plan'}
        if not isinstance(allowed_types, list) or any(not isinstance(t, str) or t not in valid for t in allowed_types):
            raise ValueError('Choose supported memory types.')
        if not isinstance(auto_share, bool):
            raise ValueError('Choose whether to share future work automatically.')
        if auto_share and (not self.sharing_enabled or not allowed_types):
            raise PermissionError('Enable workspace sharing and choose at least one type first.')
        async with self.owner_lock(owner):
            member = await self.member(owner)
            if not member:
                raise PermissionError('Account is unavailable')
            previous = await self.sharing_preferences(owner)
            profile = member['profile']
            profile['sharing'] = {'allowed_types': sorted(set(allowed_types)), 'auto_share': auto_share,
                                  'future_since': previous['future_since'] if previous['auto_share'] and auto_share
                                  else time.time() if auto_share else None}
            await self.db.execute('UPDATE members SET profile=?,policy_version=policy_version+1 WHERE id=?', (
                self.vault.seal_operational(profile), owner))
            # Tightening a type filter withdraws old publications before cached
            # classifications are reused. This never marks old private work public.
            await self.db.execute("""UPDATE records SET visibility='private',
                status=CASE WHEN status='storing' THEN 'storing' ELSE 'pending' END,
                attempts=0,next_attempt=0 WHERE owner=? AND deleted=0""", (owner,))
            await self.db.commit()
        return profile['sharing']

    async def publication_allowed(self, row, payload):
        if not self.sharing_enabled or row['intent'] == 'private':
            return False
        prefs = await self.sharing_preferences(row['owner'])
        matched = {key for key, confidence in payload.get('labels', {}).items() if confidence >= 0.8}
        if not matched or not matched.issubset(set(prefs['allowed_types'])):
            return False
        if row['intent'] == 'public':
            return True
        return bool(prefs['auto_share'] and prefs['future_since'] is not None and not row['historical']
                    and row['created'] >= prefs['future_since'])

    async def add_rule(self, owner, instruction):
        instruction = instruction.strip()
        if not instruction or len(instruction) > 1000:
            raise ValueError("Describe a privacy rule in 1–1,000 characters.")
        version = hashlib.sha256(instruction.encode()).hexdigest()
        rule_id = str(uuid.uuid4())
        async with self.owner_lock(owner):
            if any(r["version"] == version for r in await self.rules(owner)):
                raise ValueError("That privacy rule already exists.")
            blob = encrypt_blob({"instruction": instruction}, await self.key(owner))
            await self.db.execute("INSERT INTO privacy_rules(id,owner,version,payload,created) VALUES(?,?,?,?,?)",
                                  (rule_id, owner, version, json.dumps(blob), time.time()))
            await self.db.execute("UPDATE members SET policy_version=policy_version+1 WHERE id=?", (owner,))
            # Hide immediately. Reclassification can only restore previously requested public access.
            await self.db.execute("""UPDATE records SET visibility='private',
                status=CASE WHEN status='storing' THEN 'storing' ELSE 'pending' END,attempts=0,
                next_attempt=0 WHERE owner=? AND deleted=0""", (owner,))
            await self.db.commit()
        return {"id": rule_id, "version": version, "instruction": instruction}

    async def remove_rule(self, owner, rule_id):
        async with self.owner_lock(owner):
            await self.db.execute("UPDATE privacy_rules SET active=0 WHERE owner=? AND id=?", (owner, rule_id))
            await self.db.execute("UPDATE members SET policy_version=policy_version+1 WHERE id=?", (owner,))
            await self.db.execute("""UPDATE records SET status=CASE WHEN status='storing' THEN 'storing' ELSE 'pending' END,
                                  attempts=0,next_attempt=0 WHERE owner=? AND deleted=0""",
                                  (owner,))
            await self.db.commit()

    async def set_visibility(self, owner, record_id, visibility):
        if visibility not in {"private", "public"}:
            raise ValueError("Choose private or public.")
        if visibility == "public" and not self.sharing_enabled:
            raise PermissionError("Sharing is off for this rollout. All records remain private.")
        async with self.owner_lock(owner):
            row = await self.row(record_id)
            if not row or row["owner"] != owner or row["deleted"]:
                raise LookupError("Memory not found")
            await self.db.execute("""UPDATE records SET intent=?,visibility='private',
                status=CASE WHEN status='storing' THEN 'storing' ELSE 'pending' END,
                attempts=0,next_attempt=0 WHERE id=?""", (visibility, record_id))
            await self.db.execute("UPDATE members SET policy_version=policy_version+1 WHERE id=?", (owner,))
            await self.db.commit()

    async def delete(self, owner, record_id):
        async with self.owner_lock(owner):
            row = await self.row(record_id)
            if not row or row["owner"] != owner:
                raise LookupError("Memory not found")
            await self.db.execute("UPDATE records SET deleted=1,visibility='private',status='deleting' WHERE id=?",
                                  (record_id,))
            await self.db.execute("UPDATE members SET policy_version=policy_version+1 WHERE id=?", (owner,))
            await self.db.commit()
            self.search_index.evict_owner(owner)
        # Denial is committed before remote deletion. Failures remain durably
        # queued for cleanup, including a publication created before a crash.
        await self.purge_record(record_id)

    async def correct(self, owner, record_id, content, title='', reason=''):
        original = await self.read(Reader(owner), record_id)
        if original['owner'] != owner:
            raise PermissionError('Only the author can add a correction to this memory.')
        if not content.strip():
            raise ValueError('Describe the correction.')
        # This is new evidence with a new timestamp. It never rewrites the old
        # source bytes or silently makes the replacement public.
        saved = await self.capture(owner, WorkInput(source_id='correction:' + str(uuid.uuid4()),
            content=content, title=title or ('Correction: ' + original['title'])[:300], source='correction',
            occurred_at=now(), metadata={'supersedes': record_id, 'reason': reason, 'role': 'user'}))
        return saved

    async def purge_record(self, record_id):
        row = await self.row(record_id)
        if not row or not row["deleted"] or row["status"] != "deleting":
            return
        async with self.owner_lock(row["owner"]):
            try:
                await self.museum.delete(record_id)
                await self.museum.delete(record_id + ":public")
                await self.museum.delete(record_id + ":source")
                if await (await self.db.execute("SELECT 1 FROM sqlite_master WHERE name='work_views'")).fetchone():
                    views = await (await self.db.execute('SELECT id FROM work_views WHERE owner=?', (row['owner'],))).fetchall()
                    for view in views:
                        await self.museum.delete('view:' + view['id'])
                    await self.db.execute("""UPDATE work_views SET payload=NULL,generation=NULL,policy=NULL,
                        status='pending',next_attempt=0 WHERE owner=?""", (row['owner'],))
            except Exception:
                await self.db.execute("UPDATE records SET next_attempt=?,error='cleanup' WHERE id=?",
                                      (time.time() + 60, record_id))
            else:
                await self.db.execute("""UPDATE records SET payload='{}',source_payload=NULL,publication=NULL,
                    status='deleted',error=NULL WHERE id=?""",
                                      (record_id,))
                await self.db.execute("DELETE FROM classification WHERE record_id=?", (record_id,))
            await self.db.commit()

    async def cleanup(self):
        pending = await (await self.db.execute("""SELECT id FROM records WHERE deleted=0 AND status='storing'
            AND next_attempt<=? ORDER BY created LIMIT 25""", (time.time(),))).fetchall()
        for row in pending:
            await self.persist_intake(row["id"])
        rows = await (await self.db.execute("""SELECT id FROM records WHERE deleted=1
            AND status='deleting' AND next_attempt<=? LIMIT 25""", (time.time(),))).fetchall()
        for row in rows:
            await self.purge_record(row["id"])

    async def stats(self, owner):
        rows = await (await self.db.execute("""SELECT status,COUNT(*) AS n FROM records
            WHERE owner=? AND deleted=0 GROUP BY status""", (owner,))).fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        return {"total": sum(counts.values()), "ready": counts.get("ready", 0),
                "pending": counts.get("pending", 0) + counts.get("processing", 0) + counts.get("storing", 0),
                "awaiting_storage": counts.get("storing", 0),
                "failed": counts.get("failed", 0)}

    async def people(self, reader):
        rows = await (await self.db.execute("SELECT id FROM members WHERE team=? AND active=1 ORDER BY created",
                                          (reader.team,))).fetchall()
        people = []
        for row in rows:
            m = await self.member(row["id"])
            # A directory lists enrolled members' Slack profiles, never their private activity counts.
            people.append({"id": row["id"], "name": m["profile"].get("name", "Member"),
                           "avatar": m["profile"].get("avatar", ""), "is_self": row["id"] == reader.owner})
        return people

    async def next_work(self, historical=False):
        async with self.lock:
            row = await (await self.db.execute("""SELECT r.*,m.policy_version FROM records r
                JOIN members m ON m.id=r.owner WHERE r.status='pending' AND r.deleted=0
                AND r.historical=? AND r.next_attempt<=? AND m.active=1 AND m.agent_enabled=1
                ORDER BY r.created LIMIT 1""", (int(historical), time.time()))).fetchone()
            if not row:
                return None
            lease = str(uuid.uuid4())
            await self.db.execute("UPDATE records SET status='processing',attempts=attempts+1,work_lease=? WHERE id=?",
                                  (lease, row["id"]))
            await self.db.commit()
            return {**dict(row), "attempts": row["attempts"] + 1, "work_lease": lease}

    async def cached_labels(self, row, criteria):
        rows = await (await self.db.execute("SELECT * FROM classification WHERE record_id=? AND digest=?",
                                          (row["id"], row["content_hash"]))).fetchall()
        key = await self.key(row["owner"], require_agent=True)
        return {r["criterion"]: decrypt_blob(json.loads(r["payload"]), key) for r in rows
                if r["criterion"] in criteria and r["version"] == criteria[r["criterion"]]["version"]}

    async def cache_label(self, row, criterion, version, result):
        blob = encrypt_blob(result, await self.key(row["owner"], require_agent=True))
        await self.db.execute("INSERT OR REPLACE INTO classification VALUES(?,?,?,?,?)",
                              (row["id"], row["content_hash"], criterion, version, json.dumps(blob)))
        await self.db.commit()

    async def complete_work(self, row, payload, *, private_match):
        async with self.lock, self.owner_lock(row["owner"]):
            member = await self.member(row["owner"])
            current = await self.row(row["id"])
            if not member or not current or current["deleted"]:
                return
            if current["status"] != "processing" or current["work_lease"] != row["work_lease"]:
                return
            if member["policy_version"] != row["policy_version"] or not member["agent_enabled"]:
                await self.db.execute("UPDATE records SET status='pending' WHERE id=?", (row["id"],))
                await self.db.commit()
                return
            blob = encrypt_blob(payload, await self.key(row["owner"], require_agent=True))
            if not current["source_payload"]:
                # Upgrade pre-journal rows once, preserving the original evidence
                # before replacing their rebuildable derived object.
                source_blob = encrypt_blob({**await self.decode(current), "summary": "", "embedding": None},
                                           await self.key(row["owner"]))
                await self.museum.put(row["id"] + ":source", source_blob)
                await self.db.execute("UPDATE records SET source_payload=? WHERE id=?", (json.dumps(source_blob), row["id"]))
            await self.museum.put(row["id"], blob, existing=True)
            visible = ("public" if await self.publication_allowed(current, payload)
                       and not private_match and payload.get("summary") else "private")
            publication = current["publication"]
            if visible == "public":
                # Publication has its own encrypted object and approved text. It
                # never carries the original transcript, metadata, or private index.
                public = {k: payload.get(k) for k in ["schema", "id", "owner", "title", "summary", "kind",
                          "source", "created_at", "captured_at", "projects", "labels"]}
                public.update(content=payload["summary"], metadata={},
                              embedding=payload.get("publication_embedding"),
                              embedding_model=payload.get("embedding_model"), published_at=now())
                public_blob = encrypt_blob(public, await self.key(row["owner"], require_agent=True))
                await self.museum.put(row["id"] + ":public", public_blob, existing=bool(publication))
                publication = json.dumps(public_blob)
            await self.db.execute("""UPDATE records SET payload=?,publication=?,visibility=?,status='ready',error=NULL,
                updated=? WHERE id=? AND deleted=0""", (json.dumps(blob), publication, visible, time.time(), row["id"]))
            await self.db.commit()

    async def fail_work(self, row, stage):
        # Store only a fixed stage name, never an upstream response or captured text.
        status = "failed" if row["attempts"] >= 4 else "pending"
        await self.db.execute("""UPDATE records SET status=?,error=?,next_attempt=?
            WHERE id=? AND deleted=0 AND status='processing' AND work_lease=?""",
                              (status, stage, time.time() + min(300, 10 * 2 ** row["attempts"]),
                               row["id"], row["work_lease"]))
        await self.db.commit()

    async def retry_failed(self, owner):
        await self.db.execute("""UPDATE records SET status='pending',attempts=0,error=NULL,next_attempt=0
            WHERE owner=? AND status='failed' AND deleted=0""", (owner,))
        await self.db.commit()

    async def preferences(self, owner, *, capture_enabled=None, agent_enabled=None):
        async with self.owner_lock(owner):
            if capture_enabled is not None:
                await self.db.execute("UPDATE members SET capture_enabled=? WHERE id=?", (int(capture_enabled), owner))
            if agent_enabled is not None:
                await self.db.execute("UPDATE members SET agent_enabled=?,policy_version=policy_version+1 WHERE id=?",
                                      (int(agent_enabled), owner))
            await self.db.commit()

    async def leave(self, owner):
        async with self.owner_lock(owner):
            await self.db.execute("""UPDATE members SET active=0,agent_enabled=0,capture_enabled=0,
                policy_version=policy_version+1 WHERE id=?""", (owner,))
            await self.db.execute("UPDATE api_tokens SET revoked=1 WHERE owner=?", (owner,))
            await self.db.execute("UPDATE records SET visibility='private' WHERE owner=?", (owner,))
            await self.db.commit()
            self.search_index.evict_owner(owner)

    async def rejoin(self, owner, profile):
        async with self.owner_lock(owner):
            row = await (await self.db.execute('SELECT * FROM members WHERE id=? AND active=0', (owner,))).fetchone()
            if not row:
                raise ValueError('There is no departed account to restore.')
            previous = self.vault.open_operational(row['profile'])
            sharing = previous.get('sharing', {})
            sharing.update(auto_share=False, future_since=None)
            sharing.setdefault('allowed_types', ['progress', 'decision', 'finding', 'blocker', 'plan'])
            previous.update(profile, sharing=sharing)
            await self.db.execute('''UPDATE members SET active=1,agent_enabled=1,capture_enabled=0,
                policy_version=policy_version+1,profile=? WHERE id=?''', (self.vault.seal_operational(previous), owner))
            await self.db.execute("UPDATE records SET visibility='private',intent='private',work_lease='' WHERE owner=?", (owner,))
            await self.db.execute("UPDATE records SET status='pending' WHERE owner=? AND status='processing'", (owner,))
            await self.db.commit()

    async def policy_stamp(self, reader):
        # Scope includes owners of every possibly visible record, so revoking a shared
        # source also prevents an in-flight answer from being delivered to another user.
        rows = await (await self.db.execute("SELECT id,policy_version,active,agent_enabled FROM members WHERE team=?",
                                          (reader.team,))).fetchall()
        return hashlib.sha256(encode([tuple(r) for r in rows])).hexdigest()
