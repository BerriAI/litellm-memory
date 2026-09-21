"""Synthetic encrypted-search benchmark; no network, real histories or model calls.

Run with: uv run python scripts/benchmark_search.py --records 10000 --people 4 --cache-mb 128
"""

import argparse
import asyncio
import hashlib
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

from moegent.archive import Archive, Reader
from moegent.crypto import KeyVault, encrypt_blob


async def benchmark(count, people, cache_mb, parallel):
    with tempfile.TemporaryDirectory(prefix='litellm-memory-benchmark-') as directory:
        root = Path(directory)
        archive = Archive(root / 'memory.sqlite3', KeyVault(root), None, sharing_enabled=True)
        await archive.open()
        archive.search_index.budget = cache_mb * 1024 * 1024
        try:
            keys = {}
            for person in range(people):
                owner = f'SYNTHETIC:USER{person}'
                await archive.enroll(owner, {'name': f'Synthetic person {person}'})
                keys[owner] = await archive.key(owner)
            vector = [(i % 13) / 13 for i in range(768)]
            rows = []
            for i in range(count):
                owner = f'SYNTHETIC:USER{i % people}'
                record_id = hashlib.sha256(str(i).encode()).hexdigest()
                shared = (i // people) % 2 == 0
                payload = {'id': record_id, 'owner': owner, 'title': f'Scheduler investigation {i}',
                           'content': 'The scheduler queues work and retries transient failures. ' * 8,
                           'summary': 'Investigated retry behavior; a fix is planned, not yet shipped.',
                           'kind': 'finding', 'source': 'synthetic',
                           'created_at': f'2026-09-{i % 20 + 1:02}T12:00:00+00:00',
                           'captured_at': '2026-09-20T12:00:00+00:00',
                           'metadata': {'project': 'scheduler'}, 'labels': {'finding': .95},
                           'embedding': vector, 'embedding_model': 'synthetic-768'}
                public = {**payload, 'content': payload['summary'], 'metadata': {}}
                rows.append((record_id, owner, record_id, record_id, json.dumps(encrypt_blob(payload, keys[owner])),
                             json.dumps(encrypt_blob(public, keys[owner])) if shared else None,
                             'public' if shared else 'private', 'ready', time.time(), time.time()))
                if len(rows) == 400:
                    await insert(archive, rows)
                    rows.clear()
            if rows:
                await insert(archive, rows)
            await archive.db.commit()

            async def query(case, person=0):
                owner = f'SYNTHETIC:USER{person}'
                started = time.monotonic()
                result = await archive.search(Reader(owner), 'scheduler retries', vector=vector,
                                              embedding_model='synthetic-768')
                assert all(r['owner'] == owner or r['visibility'] == 'public' for r in result['results'])
                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                print(json.dumps({'case': case, 'records': count, 'people': people,
                    'cache_budget_mb': cache_mb, 'seconds': round(time.monotonic() - started, 3),
                    'results': len(result['results']), 'partitions_built': archive.search_index.builds,
                    'cache_estimate_mb': round(archive.search_index.used / 1024**2, 1),
                    'process_peak_mb': round(peak / (1024**2 if sys.platform == 'darwin' else 1024), 1)}), flush=True)

            await query('cold')
            await query('warm')
            await query('warm-repeat')
            await asyncio.gather(*(query(f'parallel-{i + 1}', i % people) for i in range(parallel)))
        finally:
            await archive.close()


async def insert(archive, rows):
    await archive.db.executemany('''INSERT INTO records
        (id,owner,source_hash,content_hash,payload,publication,visibility,status,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?)''', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=int, default=10000)
    parser.add_argument('--people', type=int, default=4)
    parser.add_argument('--cache-mb', type=int, default=128)
    parser.add_argument('--parallel', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.records <= 1000000 or not 1 <= args.people <= 100 or not 1 <= args.parallel <= 8:
        parser.error('Use 1–1,000,000 records, 1–100 people and 1–8 parallel queries.')
    if not 16 <= args.cache_mb <= 2048:
        parser.error('Use a cache of 16–2048 MB.')
    asyncio.run(benchmark(args.records, args.people, args.cache_mb, args.parallel))
