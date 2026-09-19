import asyncio
import json
import time
import uuid
from pathlib import Path

import aiosqlite


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.path.chmod(0o600)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
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
        await self.db.commit()

    async def close(self):
        await self.db.close()

    async def enqueue(self, owner, kind, payload, dedupe=None):
        job_id = str(uuid.uuid4())
        async with self.lock:
            await self.db.execute(
                "INSERT OR IGNORE INTO jobs(id,dedupe,owner,kind,payload,created) VALUES(?,?,?,?,?,?)",
                (job_id, dedupe, owner, kind, json.dumps(payload), time.time()),
            )
            await self.db.commit()
            if dedupe:
                row = await (await self.db.execute("SELECT id FROM jobs WHERE dedupe=?", (dedupe,))).fetchone()
                return row["id"]
        return job_id

    async def get_job(self, job_id):
        row = await (await self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))).fetchone()
        return dict(row) if row else None

    async def next_job(self):
        async with self.lock:
            row = await (await self.db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
            )).fetchone()
            if not row:
                return None
            await self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
            await self.db.commit()
            return {**dict(row), "payload": json.loads(row["payload"]), "attempts": row["attempts"] + 1}

    async def result(self, job_id, *, answer=None, error=None, status="done"):
        async with self.lock:
            await self.db.execute(
                "UPDATE jobs SET status=?,answer=COALESCE(?,answer),error=? WHERE id=?",
                (status, answer, error, job_id),
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
        row = await (await self.db.execute(
            "SELECT approved FROM channels WHERE team=? AND channel=?", (team, channel)
        )).fetchone()
        return bool(row and row["approved"])

    async def setting(self, key, value=None):
        if value is not None:
            async with self.lock:
                await self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))
                await self.db.commit()
        row = await (await self.db.execute("SELECT value FROM settings WHERE key=?", (key,))).fetchone()
        return row["value"] if row else None

    async def history(self, session):
        rows = await (await self.db.execute(
            "SELECT role,content FROM history WHERE session=? ORDER BY id DESC LIMIT 16", (session,)
        )).fetchall()
        return [dict(row) for row in reversed(rows)]

    async def remember(self, session, question, answer):
        async with self.lock:
            await self.db.executemany("INSERT INTO history(session,role,content) VALUES(?,?,?)", [
                (session, "user", question), (session, "assistant", answer),
            ])
            await self.db.commit()
