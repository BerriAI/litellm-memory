"""Short-lived recipient-key transfers. Only ciphertext is staged on disk.

Sealed boxes provide recipient confidentiality/integrity, not sender identity.
Browser sessions, same-origin checks, scoped receiving grants, and explicit
approval supply authorization. The server never fetches a user-supplied URL.
"""

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import aiosqlite
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .archive import Reader, WorkInput
from .crypto import b64, encode, unb64

GRANT_SECONDS = 1800
MAX_STAGED_BYTES = 512 * 1024 * 1024
MAX_TOTAL_STAGED_BYTES = 1024 * 1024 * 1024
MAX_RECORDS = 100_000
PACKET_BYTES = 600_000


def origin(value):
    # This value also enters the sender page's CSP. Reconstruct a strict origin;
    # never interpolate a user-provided netloc into a response header.
    message = 'Use the HTTPS origin of the other LiteLLM Memory installation.'
    try:
        if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 or char in "\\;\"'<>`" for char in value):
            raise ValueError(message)
        parts = urlsplit(value)
        if (not parts.hostname or '@' in parts.netloc or parts.query or parts.fragment
                or parts.path not in {'', '/'} or parts.scheme not in {'http', 'https'}):
            raise ValueError(message)
        host, port = parts.hostname.encode('idna').decode('ascii').lower(), parts.port
        if ':' in host:
            host = ipaddress.IPv6Address(host).compressed
        elif len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                        for label in host.split('.')):
            raise ValueError(message)
        if parts.scheme == 'http' and host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError(message)
        if port is not None and not 1 <= port <= 65535:
            raise ValueError(message)
        authority = f'[{host}]' if ':' in host else host
        if port is not None and port != (443 if parts.scheme == 'https' else 80):
            authority += f':{port}'
        return f'{parts.scheme}://{authority}'
    except (ValueError, UnicodeError):
        raise ValueError(message) from None


class Invitation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = 1
    id: UUID
    source: str = Field(max_length=2000)
    destination: str = Field(max_length=2000)
    owner: str = Field(min_length=3, max_length=200)
    public_key: str = Field(max_length=100)
    upload_token: str = Field(min_length=32, max_length=120, repr=False)
    expires: float

    @field_validator('source', 'destination')
    @classmethod
    def valid_origin(cls, value):
        return origin(value)


class ReceiveRequest(BaseModel):
    source: str = Field(max_length=2000)


class Packet(BaseModel):
    index: int = Field(ge=0, le=MAX_RECORDS)
    ciphertext: str = Field(min_length=64, max_length=900_000, repr=False)


class Transfers:
    def __init__(self, settings, archive):
        self.settings, self.archive, self.vault = settings, archive, archive.vault
        self.lock = asyncio.Lock()
        self.export_slot = asyncio.Semaphore(1)
        self.work_lock = asyncio.Lock()

    async def open(self):
        path = self.settings.state_dir / 'transfers.sqlite3'
        self.db = await aiosqlite.connect(path)
        path.chmod(0o600)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript('''
            PRAGMA secure_delete=ON;
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS transfers (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, direction TEXT NOT NULL, state TEXT NOT NULL,
                expires REAL NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                frames INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, records INTEGER DEFAULT 0,
                next_frame INTEGER DEFAULT 0, next_record INTEGER DEFAULT 0,
                next_attempt REAL DEFAULT 0, wait_reason TEXT DEFAULT '',
                restored INTEGER DEFAULT 0, duplicates INTEGER DEFAULT 0, excluded INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS transfer_chunks (
                transfer_id TEXT NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
                idx INTEGER NOT NULL, ciphertext TEXT NOT NULL,
                PRIMARY KEY(transfer_id,idx)
            );
        ''')
        columns = {row[1] for row in await (await self.db.execute('PRAGMA table_info(transfers)')).fetchall()}
        for name, definition in [('next_attempt', 'REAL DEFAULT 0'), ('wait_reason', "TEXT DEFAULT ''")]:
            if name not in columns:
                await self.db.execute(f'ALTER TABLE transfers ADD COLUMN {name} {definition}')
        await self.cleanup()

    async def close(self):
        await self.db.close()

    async def cleanup(self):
        async with self.lock:
            await self.db.execute('DELETE FROM transfer_chunks WHERE transfer_id IN (SELECT id FROM transfers WHERE expires<=?)',
                                  (time.time(),))
            await self.db.execute('DELETE FROM transfers WHERE expires<=?', (time.time(),))
            await self.db.commit()

    async def get(self, identity, owner, *, direction=None):
        row = await (await self.db.execute('SELECT * FROM transfers WHERE id=? AND owner=? AND expires>?',
                                          (str(identity), owner, time.time()))).fetchone()
        if not row or (direction and row['direction'] != direction):
            raise LookupError('Transfer expired or is unavailable.')
        return {**dict(row), 'payload': self.vault.open_operational(row['payload'])}

    async def insert(self, identity, owner, direction, payload, expires):
        await self.cleanup()
        async with self.lock:
            active = await (await self.db.execute("SELECT count(*) FROM transfers WHERE owner=? AND state NOT IN ('complete','cancelled')",
                                                 (owner,))).fetchone()
            if active[0] >= 2:
                raise ValueError('Finish or cancel an existing transfer first.')
            await self.db.execute('''INSERT INTO transfers(id,owner,direction,state,expires,payload,created)
                VALUES(?,?,?,'open',?,?,?)''', (identity, owner, direction, expires,
                                               self.vault.seal_operational(payload), time.time()))
            await self.db.commit()

    async def check_capacity(self, extra):
        row = await (await self.db.execute("SELECT coalesce(sum(bytes),0) FROM transfers WHERE state NOT IN ('complete','cancelled')")).fetchone()
        if row[0] + extra > MAX_TOTAL_STAGED_BYTES:
            raise ValueError('Transfer storage is busy. Finish or cancel another transfer, then retry.')

    async def receive(self, owner, source):
        source, destination = origin(source), origin(self.settings.public_base_url)
        if source == destination:
            raise ValueError('Choose a different installation.')
        key = PrivateKey.generate()
        invitation = Invitation(id=uuid4(), source=source, destination=destination, owner=owner,
                                public_key=b64(bytes(key.public_key)), upload_token=secrets.token_urlsafe(48),
                                expires=time.time() + GRANT_SECONDS)
        await self.insert(str(invitation.id), owner, 'receive', {
            'invitation': invitation.model_dump(mode='json', exclude={'upload_token'}), 'private_key': b64(bytes(key)),
            'upload_digest': hashlib.sha256(invitation.upload_token.encode()).hexdigest(),
        }, invitation.expires)
        return invitation.model_dump(mode='json')

    async def inbox(self, identity, request_origin, token=None):
        raw = await (await self.db.execute("SELECT owner FROM transfers WHERE id=? AND direction='receive' AND expires>?",
                                          (str(identity), time.time()))).fetchone()
        if not raw:
            raise LookupError('Transfer expired or is unavailable.')
        row = await self.get(identity, raw['owner'], direction='receive')
        expected_origin = row['payload'].get('invitation', {}).get('source')
        if request_origin != expected_origin or not await self.archive.member(row['owner']):
            raise PermissionError('This receiving grant is unavailable.')
        if token is not None and not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(),
                                                           row['payload'].get('upload_digest', '')):
            raise PermissionError('Invalid receiving grant.')
        return row

    async def outgoing_context(self, identity, owner):
        row = await self.get(identity, owner, direction='send')
        await self.send_packet(identity, owner, 0)  # Recheck policy before returning the receiving capability.
        return {**self.public(row), 'frames': row['frames'], 'invitation': row['payload']['invitation']}

    async def outgoing(self, owner, invitation):
        if (invitation.version != 1 or invitation.owner != owner
                or invitation.source != origin(self.settings.public_base_url)
                or invitation.destination == invitation.source):
            raise ValueError('Sign in with the same Slack account on both installations and approve the destination.')
        if not time.time() < invitation.expires <= time.time() + GRANT_SECONDS + 60:
            raise ValueError('The receiving invitation expired. Connect again.')
        try:
            box = SealedBox(PublicKey(unb64(invitation.public_key)))
        except (ValueError, TypeError):
            raise ValueError('Invalid receiving key.') from None
        identity = str(uuid4())
        binding = invitation.model_dump(mode='json')
        await self.insert(identity, owner, 'send', {'invitation': binding,
            'policy': await self.archive.policy_stamp(Reader(owner))}, invitation.expires)
        frames, count, total, batch, batch_bytes = 0, 0, 0, [], 0
        checksum = hashlib.sha256()

        async def stage(records=None, *, final=False):
            nonlocal frames, total
            value = {'version': 1, 'grant': str(invitation.id), 'source': invitation.source,
                     'destination': invitation.destination, 'owner': owner, 'index': frames,
                     'kind': 'manifest' if final else 'records'}
            value.update({'records': count, 'digest': checksum.hexdigest()} if final else {'records': records})
            ciphertext = b64(box.encrypt(encode(value)))
            if len(ciphertext) > 900_000 or total + len(ciphertext) > MAX_STAGED_BYTES:
                raise ValueError('This transfer is too large. Use an encrypted offline export for this archive.')
            async with self.lock:
                row = await self.get(identity, owner)
                if row['state'] != 'open':
                    raise ValueError('Transfer was cancelled.')
                await self.check_capacity(len(ciphertext))
                await self.db.execute('INSERT INTO transfer_chunks VALUES(?,?,?)', (identity, frames, ciphertext))
                await self.db.execute('UPDATE transfers SET bytes=bytes+? WHERE id=?', (len(ciphertext), identity))
                await self.db.commit()
            total += len(ciphertext)
            frames += 1
            if not final:
                checksum.update(ciphertext.encode())

        try:
            async with self.export_slot:
                async for record in self.archive.iter_records(Reader(owner), owner=owner):
                    value = {k: v for k, v in record.items() if k not in {'embedding', 'publication_embedding'}}
                    size = len(encode(value))
                    if batch and batch_bytes + size > PACKET_BYTES:
                        await stage(batch)
                        batch, batch_bytes = [], 0
                    batch.append(value)
                    batch_bytes += size
                    count += 1
                    if count > MAX_RECORDS:
                        raise ValueError('Use an encrypted offline export for archives over 100,000 records.')
                if batch:
                    await stage(batch)
                await stage(final=True)
                async with self.lock:
                    if (await self.get(identity, owner))['state'] != 'open':
                        raise ValueError('Transfer was cancelled.')
                    await self.db.execute("UPDATE transfers SET frames=?,records=?,bytes=?,state='ready' WHERE id=?",
                                          (frames, count, total, identity))
                    await self.db.commit()
            return {'id': identity, 'frames': frames, 'records': count, 'expires': invitation.expires}
        except BaseException:
            with contextlib.suppress(LookupError):
                await self.cancel(identity, owner)
            raise

    async def send_packet(self, identity, owner, index):
        row = await self.get(identity, owner, direction='send')
        if row['state'] != 'ready' or row['payload']['policy'] != await self.archive.policy_stamp(Reader(owner)):
            raise PermissionError('Memory access changed. Start a new transfer.')
        packet = await (await self.db.execute('SELECT ciphertext FROM transfer_chunks WHERE transfer_id=? AND idx=?',
                                            (str(identity), index))).fetchone()
        if not packet:
            raise LookupError('Transfer packet unavailable.')
        return {'index': index, 'ciphertext': packet[0]}

    def decode(self, row, ciphertext, index):
        try:
            key = PrivateKey(unb64(row['payload']['private_key']))
            value = json.loads(SealedBox(key).decrypt(unb64(ciphertext)))
            invitation = row['payload']['invitation']
            expected = {'version': 1, 'grant': row['id'], 'source': invitation['source'],
                        'destination': invitation['destination'], 'owner': row['owner'], 'index': index}
            if not isinstance(value, dict) or any(value.get(k) != v for k, v in expected.items()):
                raise ValueError()
            if value.get('kind') == 'records':
                if not isinstance(value['records'], list) or not value['records'] or len(value['records']) > MAX_RECORDS:
                    raise ValueError()
                for record in value['records']:
                    if record.get('owner') != row['owner']:
                        raise ValueError()
                    WorkInput.model_validate(record['original'])
            elif value.get('kind') != 'manifest':
                raise ValueError()
            return value
        except (CryptoError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Transfer integrity or recipient verification failed.') from None

    async def accept(self, identity, owner, packet):
        async with self.lock:
            row = await self.get(identity, owner, direction='receive')
            if row['state'] not in {'open', 'uploaded'}:
                raise ValueError('This receiving grant no longer accepts uploads.')
            previous = await (await self.db.execute('SELECT ciphertext FROM transfer_chunks WHERE transfer_id=? AND idx=?',
                                                   (str(identity), packet.index))).fetchone()
            if previous:
                if previous[0] != packet.ciphertext:
                    raise ValueError('A transfer packet cannot be replaced.')
                return {'received': packet.index, 'duplicate': True}
            if row['state'] == 'uploaded' or packet.index != row['frames']:
                raise ValueError('Send transfer packets in order.')
            value = self.decode(row, packet.ciphertext, packet.index)
            count = len(value['records']) if value['kind'] == 'records' else 0
            if row['bytes'] + len(packet.ciphertext) > MAX_STAGED_BYTES or row['records'] + count > MAX_RECORDS:
                raise ValueError('Transfer exceeds this installation’s import limit.')
            await self.check_capacity(len(packet.ciphertext))
            await self.db.execute('INSERT INTO transfer_chunks VALUES(?,?,?)', (str(identity), packet.index, packet.ciphertext))
            await self.db.execute('UPDATE transfers SET frames=frames+1,bytes=bytes+?,records=records+?,state=? WHERE id=?',
                                  (len(packet.ciphertext), count, 'uploaded' if value['kind'] == 'manifest' else 'open', str(identity)))
            await self.db.commit()
            return {'received': packet.index, 'duplicate': False}

    async def start_import(self, identity, owner):
        async with self.lock:
            row = await self.get(identity, owner, direction='receive')
            if row['state'] in {'importing', 'waiting', 'complete'}:
                return self.public(row)
            if row['state'] != 'uploaded':
                raise ValueError('The complete transfer must arrive before import starts.')
            # Authenticate every record and the final ordered manifest before
            # writing a single source into the destination's memory archive.
            checksum, count = hashlib.sha256(), 0
            for index in range(row['frames']):
                packet = await (await self.db.execute('SELECT ciphertext FROM transfer_chunks WHERE transfer_id=? AND idx=?',
                                                    (str(identity), index))).fetchone()
                if not packet:
                    raise ValueError('Transfer is incomplete.')
                value = self.decode(row, packet[0], index)
                if index == row['frames'] - 1:
                    if (value['kind'] != 'manifest' or value['records'] != count
                            or value.get('digest') != checksum.hexdigest() or count != row['records']):
                        raise ValueError('Transfer manifest verification failed.')
                else:
                    if value['kind'] != 'records':
                        raise ValueError('Unexpected final packet.')
                    checksum.update(packet[0].encode())
                    count += len(value['records'])
            await self.db.execute("UPDATE transfers SET state='importing',expires=? WHERE id=?", (time.time() + 86400, str(identity)))
            await self.db.commit()
            return self.public(await self.get(identity, owner))

    async def step(self, limit=32):
        async with self.work_lock:
            row = await (await self.db.execute("SELECT id,owner FROM transfers WHERE direction='receive' AND state IN ('importing','waiting') AND expires>? AND next_attempt<=? ORDER BY next_attempt,created LIMIT 1",
                                              (time.time(), time.time()))).fetchone()
            if not row:
                return False
            identity, owner = row['id'], row['owner']
            member = await self.archive.member(owner)
            if not member:
                await self.cancel(identity, owner)
                return True
            if not member['capture_enabled']:
                await self.defer(identity, 'recording')
                return False
            try:
                if hasattr(self.archive, 'check_eligibility') and not await self.archive.check_eligibility(owner):
                    await self.defer(identity, 'slack')
                    return False
            except Exception:
                await self.defer(identity, 'slack')
                return False
            for _ in range(limit):
                # Cancellation waits for at most the in-flight record; it cannot
                # race completion or resurrect a deleted receiving key.
                async with self.lock:
                    row = await self.get(identity, owner, direction='receive')
                    if row['state'] not in {'importing', 'waiting'}:
                        return False
                    if row['next_frame'] == row['frames'] - 1:
                        await self.db.execute('DELETE FROM transfer_chunks WHERE transfer_id=?', (identity,))
                        await self.db.execute("UPDATE transfers SET state='complete',payload=? WHERE id=?", (
                            self.vault.seal_operational({'invitation': row['payload']['invitation'],
                                                       'upload_digest': row['payload']['upload_digest']}), identity))
                        await self.db.commit()
                        return True
                    packet = await (await self.db.execute('SELECT ciphertext FROM transfer_chunks WHERE transfer_id=? AND idx=?',
                                                         (identity, row['next_frame']))).fetchone()
                    records = self.decode(row, packet[0], row['next_frame'])['records']
                    original = WorkInput.model_validate(records[row['next_record']]['original']).model_copy(update={'historical': True})
                    try:
                        result = await self.archive.capture(owner, original)
                        counter = 'duplicates' if result.get('duplicate') else 'restored'
                    except ValueError:
                        counter = 'excluded'  # Tombstones/conflicts never overwrite existing evidence.
                    except Exception:
                        await self.defer(identity, 'storage')
                        return False
                    next_record, next_frame = row['next_record'] + 1, row['next_frame']
                    if next_record == len(records):
                        next_frame, next_record = next_frame + 1, 0
                    await self.db.execute(f"UPDATE transfers SET state='importing',wait_reason='',next_attempt=?,next_frame=?,next_record=?,{counter}={counter}+1 WHERE id=? AND state IN ('importing','waiting')",
                                          (time.time(), next_frame, next_record, identity))
                    await self.db.commit()
            return True

    async def defer(self, identity, reason):
        await self.db.execute("UPDATE transfers SET state='waiting',wait_reason=?,next_attempt=? WHERE id=? AND state IN ('importing','waiting')",
                              (reason, time.time() + 30, identity))
        await self.db.commit()

    async def cancel(self, identity, owner):
        async with self.lock:
            await self.get(identity, owner)
            await self.db.execute('DELETE FROM transfer_chunks WHERE transfer_id=?', (str(identity),))
            await self.db.execute("UPDATE transfers SET state='cancelled',payload=? WHERE id=?", (self.vault.seal_operational({}), str(identity)))
            await self.db.commit()
        return {'status': 'cancelled'}

    def public(self, row):
        return {k: row[k] for k in ('id', 'direction', 'state', 'created', 'expires', 'frames', 'records', 'restored', 'duplicates', 'excluded', 'wait_reason')}

    async def status(self, identity, owner):
        return self.public(await self.get(identity, owner))

    async def recent(self, owner):
        rows = await (await self.db.execute('SELECT * FROM transfers WHERE owner=? AND expires>? ORDER BY created DESC LIMIT 10',
                                          (owner, time.time()))).fetchall()
        return [self.public(dict(row)) for row in rows]

    async def worker(self):
        while True:
            try:
                await self.cleanup()
                active = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                active = False  # Keep encrypted staging; retry without logging content or provider exceptions.
            await asyncio.sleep(0.1 if active else 10)
