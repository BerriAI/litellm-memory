import asyncio
import json
import time
import uuid
from pathlib import Path

import aiosqlite


class Storage:
    def __init__(self, path: Path, vault=None):
        self.path = path
        self.vault = vault
        self.lock = asyncio.Lock()

    def seal(self, value):
        return self.vault.seal_operational(value) if self.vault and value is not None else value

    def unseal(self, value):
        if self.vault and isinstance(value, str) and value.startswith("enc:v1:"):
            return self.vault.open_operational(value)
        return value

    def job_row(self, row):
        result = dict(row)
        for field in ("payload", "answer", "partial", "phase", "error"):
            if field in result:
                result[field] = self.unseal(result[field])
        return result

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.path.chmod(0o600)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, dedupe TEXT UNIQUE, owner TEXT NOT NULL,
              kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT DEFAULT 'queued',
              answer TEXT, error TEXT, attempts INTEGER DEFAULT 0, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS channels (
              team TEXT, channel TEXT, approved INTEGER, event_ts REAL,
              PRIMARY KEY(team,channel)
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS history (
              id INTEGER PRIMARY KEY, session TEXT, role TEXT, content TEXT
            );
            UPDATE jobs SET status='queued' WHERE status='running';
        """)
        columns = {row[1] for row in await (await self.db.execute("PRAGMA table_info(jobs)")).fetchall()}
        for column, default in [("phase", "Queued"), ("partial", "")]:
            if column not in columns:
                await self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT DEFAULT '{default}'")
        for column, definition in [("response_ts", "TEXT"), ("indicator_active", "INTEGER DEFAULT 0"),
                                   ("indicator_done", "INTEGER DEFAULT 0"), ("slack_team", "TEXT"),
                                   ("slack_channel", "TEXT"), ("slack_thread", "TEXT")]:
            if column not in columns:
                await self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
        migrated_plaintext = False
        rows = await (await self.db.execute("SELECT * FROM jobs")).fetchall()
        for raw in rows:
            row = self.job_row(raw)
            payload = json.loads(row["payload"])
            if self.vault:
                migrated_plaintext |= any(raw[k] is not None and not str(raw[k]).startswith("enc:v1:")
                                          for k in ("payload", "answer", "partial", "phase", "error"))
            await self.db.execute("""UPDATE jobs SET slack_team=?,slack_channel=?,slack_thread=?,
                payload=?,answer=?,partial=?,phase=?,error=? WHERE id=?""", (
                payload.get("team"), payload.get("channel"), payload.get("thread_ts"),
                *(self.seal(row.get(k)) for k in ("payload", "answer", "partial", "phase", "error")), row["id"]))
        if self.vault:
            for table, field, identity in [("history", "content", "id"), ("settings", "value", "key")]:
                rows = await (await self.db.execute(f"SELECT {identity},{field} FROM {table}")).fetchall()
                for row in rows:
                    if row[field] is not None and not str(row[field]).startswith("enc:v1:"):
                        migrated_plaintext = True
                        await self.db.execute(f"UPDATE {table} SET {field}=? WHERE {identity}=?",
                                              (self.seal(row[field]), row[identity]))
        await self.db.commit()
        if migrated_plaintext:
            # Consume and close the checkpoint cursor before VACUUM; its pending
            # result otherwise holds a SQLite statement open and prevents startup.
            async with self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
                await cursor.fetchall()
            async with self.db.execute("VACUUM") as cursor:
                await cursor.fetchall()
            async with self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
                await cursor.fetchall()

    async def close(self):
        await self.db.close()

    async def enqueue(self, owner, kind, payload, dedupe=None):
        job_id = str(uuid.uuid4())
        async with self.lock:
            await self.db.execute(
                """INSERT OR IGNORE INTO jobs(id,dedupe,owner,kind,payload,created,slack_team,slack_channel,slack_thread)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (job_id, dedupe, owner, kind, self.seal(json.dumps(payload)), time.time(),
                 payload.get("team"), payload.get("channel"), payload.get("thread_ts")),
            )
            await self.db.commit()
            if dedupe:
                row = await (await self.db.execute("SELECT id FROM jobs WHERE dedupe=?", (dedupe,))).fetchone()
                return row["id"]
        return job_id

    async def get_job(self, job_id):
        row = await (await self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))).fetchone()
        return self.job_row(row) if row else None

    async def next_job(self):
        async with self.lock:
            row = await (await self.db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
            )).fetchone()
            if not row:
                return None
            await self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
            await self.db.commit()
            return {**self.job_row(row), "payload": json.loads(self.unseal(row["payload"])),
                    "attempts": row["attempts"] + 1}

    async def by_dedupe(self, dedupe):
        row = await (await self.db.execute("SELECT id FROM jobs WHERE dedupe=?", (dedupe,))).fetchone()
        return row["id"] if row else None

    async def slack_thread_jobs(self, team, channel, thread):
        rows = await (await self.db.execute("""
            SELECT * FROM jobs WHERE kind='slack' AND slack_team=?
            AND slack_channel=? AND slack_thread=?
            ORDER BY created
        """, (team, channel, thread))).fetchall()
        return [{**self.job_row(row), "payload": json.loads(self.unseal(row["payload"]))} for row in rows]

    async def slack_indicators(self):
        rows = await (await self.db.execute(
            "SELECT * FROM jobs WHERE kind='slack' AND indicator_done=0 ORDER BY created LIMIT 100"
        )).fetchall()
        return [{**self.job_row(row), "payload": json.loads(self.unseal(row["payload"]))} for row in rows]

    async def slack_delivery(self, job_id, *, response_ts=None, active=None, done=None):
        async with self.lock:
            await self.db.execute("""UPDATE jobs SET response_ts=COALESCE(?,response_ts),
                indicator_active=COALESCE(?,indicator_active),indicator_done=COALESCE(?,indicator_done)
                WHERE id=?""", (response_ts, active, done, job_id))
            await self.db.commit()

    async def result(self, job_id, *, answer=None, error=None, status="done"):
        async with self.lock:
            await self.db.execute(
                "UPDATE jobs SET status=?,answer=COALESCE(?,answer),error=? WHERE id=? AND status!='cancelled'",
                (status, self.seal(answer), self.seal(error), job_id),
            )
            if status == 'failed':
                await self.db.execute("UPDATE jobs SET partial=? WHERE id=?", (self.seal(''), job_id))
            await self.db.commit()

    async def progress(self, job_id, phase, partial=None):
        async with self.lock:
            await self.db.execute(
                "UPDATE jobs SET phase=?,partial=COALESCE(?,partial) WHERE id=? AND status='running'",
                (self.seal(phase), self.seal(partial), job_id),
            )
            await self.db.commit()

    async def cancel(self, job_id):
        async with self.lock:
            await self.db.execute(
                "UPDATE jobs SET status='cancelled',phase='Stopped' WHERE id=? AND status IN ('queued','running')",
                (job_id,),
            )
            await self.db.commit()

    async def channel_event(self, team, channel, approved, event_ts):
        async with self.lock:
            await self.db.execute("""
                INSERT INTO channels VALUES(?,?,?,?) ON CONFLICT(team,channel) DO UPDATE SET
                approved=excluded.approved,event_ts=excluded.event_ts
                WHERE excluded.event_ts >= channels.event_ts
            """, (team, channel, int(approved), float(event_ts)))
            await self.db.commit()

    async def channel_allowed(self, team, channel):
        return bool(await self.channel_approval(team, channel))

    async def channel_approval(self, team, channel):
        row = await (await self.db.execute(
            "SELECT approved FROM channels WHERE team=? AND channel=?", (team, channel)
        )).fetchone()
        return bool(row["approved"]) if row else None

    async def setting(self, key, value=None):
        if value is not None:
            async with self.lock:
                await self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, self.seal(value)))
                await self.db.commit()
        row = await (await self.db.execute("SELECT value FROM settings WHERE key=?", (key,))).fetchone()
        return self.unseal(row["value"]) if row else None

    async def delete_setting(self, key):
        async with self.lock:
            await self.db.execute("DELETE FROM settings WHERE key=?", (key,))
            await self.db.commit()

    async def history(self, session):
        rows = await (await self.db.execute(
            "SELECT role,content FROM history WHERE session=? ORDER BY id DESC LIMIT 16", (session,)
        )).fetchall()
        return [{**dict(row), "content": self.unseal(row["content"])} for row in reversed(rows)]

    async def remember(self, session, question, answer):
        async with self.lock:
            await self.db.executemany("INSERT INTO history(session,role,content) VALUES(?,?,?)", [
                (session, "user", self.seal(question)), (session, "assistant", self.seal(answer)),
            ])
            await self.db.commit()
