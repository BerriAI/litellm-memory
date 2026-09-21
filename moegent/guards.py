"""Small request limits for the single-instance self-hosted application."""

import asyncio
import hashlib
import time
from collections import deque

from starlette.responses import JSONResponse


class RequestGuard:
    def __init__(self, app, max_body=1_000_000):
        self.app, self.max_body = app, max_body
        self.buckets = {}
        self.last_sweep = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope['path']
        # Both API spellings consume the same limit; alternating cannot double it.
        if path.startswith('/api/v1/'):
            path = '/api/' + path.removeprefix('/api/v1/')
        rule = {
            '/oauth/register': (12, 3600), '/oauth/token': (120, 60), '/api/setup/claim': (20, 900),
            '/api/chat': (30, 60), '/api/memories': (360, 60), '/api/memories/search': (120, 60),
            '/api/export': (6, 300), '/api/rejoin': (6, 300),
            '/api/transfers/receive': (6, 300), '/api/transfers/send': (6, 300),
        }.get(path)
        if path.startswith('/api/transfer-inbox/'):
            rule = (360, 60)
        if rule and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
            current = time.monotonic()
            if current - self.last_sweep > 60:
                self.buckets = {key: value for key, value in self.buckets.items() if value[-1] > current - 3600}
                self.last_sweep = current
            client = scope.get('client') or ('unknown', 0)
            headers = dict(scope.get('headers', []))
            credential = headers.get(b'authorization') or headers.get(b'cookie', b'')
            # Aggregate IP limits remain even if a caller rotates fabricated cookies.
            identities = [client[0], hashlib.sha256(credential).hexdigest()] if credential else [client[0]]
            for identity in identities:
                key = (path, identity)
                if len(self.buckets) > 20000 and key not in self.buckets:
                    return await self.reject(scope, receive, send, 429, 'Please try again shortly.')
                bucket = self.buckets.setdefault(key, deque())
                while bucket and bucket[0] <= current - rule[1]:
                    bucket.popleft()
                if len(bucket) >= rule[0]:
                    return await self.reject(scope, receive, send, 429, 'Too many requests. Please try again shortly.')
                bucket.append(current)
        messages, size = [], 0

        async def read_body():
            nonlocal size
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return False
                size += len(message.get('body', b''))
                if size > self.max_body:
                    return False
                messages.append(message)
                if not message.get('more_body'):
                    return True

        try:
            accepted = await asyncio.wait_for(read_body(), 30)
        except TimeoutError:
            return await self.reject(scope, receive, send, 408, 'The request took too long. Try again.')
        if not accepted:
            return await self.reject(scope, receive, send, 413, 'Split this request into smaller captures.')
        pending = deque(messages)

        async def replay():
            return pending.popleft() if pending else await receive()

        await self.app(scope, replay, send)

    async def reject(self, scope, receive, send, code, message):
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
        if code == 429:
            headers['Retry-After'] = '60'
        await JSONResponse({'detail': message}, status_code=code, headers=headers)(scope, receive, send)
