"""Bounded, audience-partitioned RAM indexes over encrypted persistent records.

Only an authorized partition is decrypted. Partition fingerprints avoid rebuilding
unchanged FTS indexes; an LRU limits resident plaintext. Cold/evicted partitions
are reconstructed from the encrypted record/embedding store, never a disk FTS.
"""

import asyncio
import hashlib
import heapq
import json
import math
import re
import sqlite3
from array import array
from collections import OrderedDict

from .crypto import decrypt_blob, encode


class Partition:
    def __init__(self, rows, key, audience, fingerprint):
        self.fingerprint, self.documents, self.vectors, self.size = fingerprint, {}, {}, 0
        self.index = sqlite3.connect(':memory:')
        self.index.execute('PRAGMA temp_store=MEMORY')
        self.index.execute('CREATE VIRTUAL TABLE work USING fts5(id UNINDEXED,title,content,metadata)')
        for row in rows:
            blob = row['payload'] if audience == 'owner' else row['publication']
            data = decrypt_blob(json.loads(blob), key)
            if data.get('id') != row['id'] or data.get('owner') != row['owner']:
                raise ValueError('Encrypted index identity mismatch')
            text = data['content'] + '\n' + data.get('summary', '')
            metadata = json.dumps(data.get('metadata', {}))
            self.index.execute('INSERT INTO work VALUES(?,?,?,?)', (row['id'], data['title'], text, metadata))
            if data.get('embedding'):
                vector = array('f', data['embedding'])
                self.vectors[row['id']] = (data.get('embedding_model'), vector,
                                           math.sqrt(math.sumprod(vector, vector)) or 1)
            result = {k: v for k, v in data.items() if k not in {
                'original', 'embedding', 'publication_embedding', 'content', 'privacy_blocked', 'publication_digest'}}
            result.update(visibility=row['visibility'], status=row['status'], historical=bool(row['historical']),
                          excerpt=(data.get('summary') or data['content'])[:500])
            result['metadata'] = {k: v for k, v in result.get('metadata', {}).items() if k != 'original_file'}
            self.documents[row['id']] = result
            # Conservative payload + FTS estimate; account for Python objects and
            # SQLite pages separately, without permitting plaintext disk spilling.
            self.size += len(encode(result)) * 3 + len(text.encode()) * 3 + len(metadata.encode()) * 3 + 1500
            self.size += len(data.get('embedding') or []) * 4
        self.index.commit()
        self.size += self.index.execute('PRAGMA page_count').fetchone()[0] * self.index.execute('PRAGMA page_size').fetchone()[0]

    def close(self):
        self.index.close()


class SearchIndex:
    def __init__(self, archive, budget=128 * 1024 * 1024):
        self.archive, self.budget, self.used = archive, budget, 0
        self.cache = OrderedDict()
        self.builds = 0
        self.load_lock = asyncio.Lock()

    def evict_owner(self, owner):
        for key in list(self.cache):
            if key[0] == owner:
                old = self.cache.pop(key)
                self.used -= old.size
                old.close()

    def close(self):
        for partition in self.cache.values():
            partition.close()
        self.cache.clear()
        self.used = 0

    def keep(self, key, partition):
        while self.cache and self.used + partition.size > self.budget:
            _, old = self.cache.popitem(last=False)
            self.used -= old.size
            old.close()
        if partition.size <= self.budget:
            self.cache[key] = partition
            self.used += partition.size
            return True
        return False

    async def partitions(self, reader, owner):
        # Fingerprints contain only IDs/operational revisions, not source text.
        # Even on a cold query only one bounded partition is decrypted at a time.
        principal = await self.archive.member(reader.owner)
        if not principal:
            raise PermissionError('Account is unavailable')
        cursor = await self.archive.db.execute('''SELECT r.id,r.owner,r.updated,r.status,r.visibility,
            m.policy_version,substr(r.id,1,2) AS shard FROM records r JOIN members m ON m.id=r.owner
            WHERE r.deleted=0 AND m.active=1 AND m.team=? AND (? IS NULL OR r.owner=?)
            AND ((r.owner=? AND ?='owner') OR (?=1 AND r.visibility='public' AND r.publication IS NOT NULL))
            ORDER BY r.owner,shard,r.id''', (reader.team, owner, owner, reader.owner, reader.audience,
                                            int(self.archive.sharing_enabled)))
        group, fingerprint_rows, batch = None, [], 0
        try:
            async for row in cursor:
                audience = 'owner' if row['owner'] == reader.owner and reader.audience == 'owner' else 'public'
                key = (row['owner'], audience, row['shard'])
                if group and key != group:
                    if fingerprint_rows:
                        yield (*group, batch), fingerprint_rows
                    fingerprint_rows = []
                    batch = 0
                group = key
                fingerprint_rows.append(tuple(row))
                if len(fingerprint_rows) >= 64:
                    yield (*group, batch), fingerprint_rows
                    fingerprint_rows = []
                    batch += 1
            if group and fingerprint_rows:
                yield (*group, batch), fingerprint_rows
        finally:
            await cursor.close()

    async def load(self, key, fingerprints):
        async with self.load_lock:
            return await self._load(key, fingerprints)

    async def _load(self, key, fingerprints):
        generation = hashlib.sha256(encode(fingerprints)).hexdigest()
        old = self.cache.pop(key, None)
        if old:
            if old.fingerprint == generation:
                self.cache[key] = old
                return old, True
            self.used -= old.size
            old.close()
        ids = [row[0] for row in fingerprints]
        placeholders = ','.join('?' for _ in ids)
        rows = await (await self.archive.db.execute(f'''SELECT id,owner,payload,publication,visibility,status,
            historical,deleted FROM records WHERE id IN ({placeholders})''', ids)).fetchall()
        # Policy can change between listing and load. Recheck before decrypting;
        # response delivery also checks the full policy stamp.
        member = await self.archive.member(key[0])
        if not member or any(str(r[5]) != str(member['policy_version']) for r in fingerprints):
            raise PermissionError('Access changed during search. Please retry.')
        rows = [r for r in rows if not r['deleted'] and (key[1] == 'owner' or (
            self.archive.sharing_enabled and r['visibility'] == 'public' and r['publication']))]
        partition = Partition(rows, await self.archive.key(key[0]), key[1], generation)
        self.builds += 1
        return partition, self.keep(key, partition)

    async def search(self, reader, query='', *, owner=None, visibility=None, kind=None, source=None,
                     created_from=None, created_before=None, offset=0, limit=30, vector=None, embedding_model=None):
        if not 0 <= offset <= 10000:
            raise ValueError('Narrow the date range to browse beyond 10,000 results.')
        limit = max(1, min(limit, 100))
        stamp = await self.archive.policy_stamp(reader)
        terms = re.findall(r'[\w-]+', query, flags=re.UNICODE)[:30]
        expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms)
        lexical, semantic, recent = [], [], []
        total, used_vectors = 0, False
        norm = math.sqrt(math.sumprod(vector, vector)) if vector else 0

        def keep(heap, score, record_id, document, maximum):
            item = (score, record_id, document)
            if len(heap) < maximum:
                heapq.heappush(heap, item)
            elif item[:2] > heap[0][:2]:
                heapq.heapreplace(heap, item)

        async for key, fingerprints in self.partitions(reader, owner):
            partition, cached = await self.load(key, fingerprints)
            try:
                allowed = {}
                for record_id, d in partition.documents.items():
                    if ((visibility and d['visibility'] != visibility)
                            or (kind and d['kind'] != kind and d.get('labels', {}).get(kind, 0) < 0.8)
                            or (source and d['source'] != source)
                            or (created_from and (not d.get('created_at') or d['created_at'] < created_from))
                            or (created_before and (not d.get('created_at') or d['created_at'] >= created_before))):
                        continue
                    allowed[record_id] = d
                    if not query.strip():
                        total += 1
                        keep(recent, d.get('created_at') or d.get('captured_at') or '', record_id, d, offset + limit)
                if query.strip():
                    if expression:
                        for record_id, score in partition.index.execute(
                            'SELECT id,bm25(work,0,4,1,1) FROM work WHERE work MATCH ?', (expression,)):
                            if record_id in allowed:
                                keep(lexical, -score, record_id, allowed[record_id], 200)
                    if vector and norm:
                        for record_id, (model, embedding, magnitude) in partition.vectors.items():
                            if record_id not in allowed or model != embedding_model or len(embedding) != len(vector):
                                continue
                            used_vectors = True
                            cosine = math.sumprod(vector, embedding) / (norm * magnitude)
                            keep(semantic, cosine, record_id, allowed[record_id], 100)
            finally:
                if not cached:
                    partition.close()
        if await self.archive.policy_stamp(reader) != stamp or not await self.archive.member(reader.owner):
            raise PermissionError('Access changed during search. Please retry.')
        if query.strip():
            scores, documents = {}, {}
            for ranked in [lexical, semantic]:
                for rank, (_, record_id, document) in enumerate(sorted(ranked, reverse=True)):
                    scores[record_id] = scores.get(record_id, 0) + 1 / (60 + rank + 1)
                    documents[record_id] = document
            ranked_ids = sorted(scores, key=scores.get, reverse=True)
            total = len(ranked_ids)
            results = [documents[r] for r in ranked_ids[offset:offset + limit]]
        else:
            results = [d for _, _, d in sorted(recent, reverse=True)[offset:offset + limit]]
        return {'results': results, 'total': total, 'next_offset': offset + len(results)
                if offset + len(results) < min(total, 10000) else None,
                'mode': 'hybrid' if used_vectors else 'keywords', 'coverage': await self.archive.stats(reader.owner)}
