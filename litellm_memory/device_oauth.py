"""Hosted connector approval: the browser never contacts a local callback server."""

import hashlib
import json
import secrets
import time
from html import escape
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .oauth import ReminderHook

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class DeviceRequest(BaseModel):
    client_name: str = Field(default="Codex and Claude Code", min_length=1, max_length=100)
    reminder_hooks: list[ReminderHook] = Field(default_factory=list, max_length=2)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def page(title, body, *, code=200, polling=False):
    script = '<script src="/static/device-setup.js" defer></script>' if polling else ''
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width"><title>{escape(title)} · LiteLLM Memory</title>
    <link rel="stylesheet" href="/static/memory.css">{script}</head><body>
    <main class="auth-card"><p class="eyebrow">LiteLLM Memory</p><h1 id="setup-title">{escape(title)}</h1>
    {body}</main></body></html>''', status_code=code, headers=NO_STORE)


def expired_page():
    return page("Start a new connection", '<p>This setup request has ended. Run the setup command again '
                'and use the new page it opens.</p><a class="button primary" href="/#connections">Open Connections</a>', code=400)


def install_device_oauth(app, store, archive, account, same_origin, issuer, issue_refresh, token_response):
    async def lookup(value, *, browser=False):
        column = 'browser_digest' if browser else 'digest'
        row = await (await store.db.execute(f'SELECT * FROM device_grants WHERE {column}=?', (digest(value),))).fetchone()
        if not row or row['expires'] <= time.time():
            return None
        return row['digest'], json.loads(store.unseal(row['payload']))

    async def save(key, grant):
        await store.db.execute('UPDATE device_grants SET payload=? WHERE digest=?',
                               (store.seal(json.dumps(grant)), key))
        await store.db.commit()

    def waiting():
        return page("Finishing setup", '<p id="setup-message" role="status">Your terminal is configuring your coding tools. '
                    'Keep it running; this page will update when setup is saved.</p>'
                    '<a class="button quiet" href="/#connections">Open Connections</a>', polling=True)

    @app.post('/oauth/device/code')
    async def begin(body: DeviceRequest):
        # High-entropy browser code: never usable to poll for or redeem credentials.
        device_code, user_code, client_id = (secrets.token_urlsafe(32) for _ in range(3))
        grant = {**body.model_dump(), 'client_id': client_id, 'status': 'pending', 'interval': 3, 'last_poll': 0}
        async with app.state.oauth_lock:
            await store.db.execute('DELETE FROM device_grants WHERE expires<=?', (time.time(),))
            await store.db.execute('INSERT INTO device_grants VALUES(?,?,?,?)',
                                   (digest(device_code), digest(user_code), store.seal(json.dumps(grant)), time.time() + 600))
            await store.db.commit()
            # The existing refresh-token flow uses the same public client identity.
            await store.setting('oauth-client:' + client_id, json.dumps(body.model_dump()))
        return JSONResponse({'device_code': device_code, 'user_code': user_code, 'client_id': client_id,
                             'verification_uri': issuer + '/oauth/device',
                             'verification_uri_complete': issuer + '/oauth/device?' + urlencode({'user_code': user_code}),
                             'expires_in': 600, 'interval': 3, 'reminder_hooks': grant['reminder_hooks']}, headers=NO_STORE)

    @app.get('/oauth/device')
    async def verify(request: Request):
        user_code = request.query_params.get('user_code', '')
        found = await lookup(user_code, browser=True)
        if not found:
            return expired_page()
        key, grant = found
        try:
            owner = await account(request)
        except HTTPException as error:
            if error.status_code != 401:
                raise
            if request.session.get('identity'):
                return RedirectResponse('/?' + urlencode({'device': user_code}), status_code=303)
            return RedirectResponse('/auth/slack?' + urlencode({'next': '/oauth/device?' + urlencode({'user_code': user_code})}), status_code=303)
        if grant.get('owner') and grant['owner'] != owner:
            raise HTTPException(403, 'This setup belongs to another account.')
        if grant['status'] != 'pending':
            return waiting()
        nonce = secrets.token_urlsafe(32)
        await store.create_oauth_request(digest(nonce), {'owner': owner, 'device': key}, time.time() + 600)
        commands = ''.join('<p>' + ('Codex' if hook['host'] == 'codex' else 'Claude Code')
                           + '</p><pre class="code-block">' + escape(hook['command']) + '</pre>'
                           for hook in grant['reminder_hooks'])
        reminders = ('<p>Setup adds reminders to save useful work when a session starts and when you send a message. '
                     'They do not read or upload transcripts.</p><details class="advanced">'
                     '<summary>Review reminder commands</summary>' + commands + '</details>') if commands else ''
        return page('Connect your coding tools', '<p>Allow ' + escape(grant['client_name']) +
                    ' to record your work and search your private and shared memories.</p>'
                    '<p>Approve only a setup you started in your terminal. You can disconnect it at any time.</p>' + reminders +
                    '<form method="post" action="/oauth/device/approve"><input type="hidden" name="nonce" value="' + nonce +
                    '"><input type="hidden" name="user_code" value="' + escape(user_code) + '">'
                    '<button class="button primary" name="decision" value="approve">Connect</button>'
                    '<button class="button quiet" name="decision" value="deny">Cancel</button></form>')

    @app.post('/oauth/device/approve')
    async def approve(request: Request):
        same_origin(request)
        owner = await account(request)
        form = await request.form()
        async with app.state.oauth_lock:
            consent = await store.consume_oauth_request(digest(str(form.get('nonce', ''))), owner)
            found = await lookup(str(form.get('user_code', '')), browser=True)
            if not consent or not found or consent.get('device') != found[0] or found[1]['status'] != 'pending':
                return expired_page()
            key, grant = found
            grant.update(owner=owner, status='approved' if form.get('decision') == 'approve' else 'denied')
            await save(key, grant)
        return RedirectResponse('/oauth/device?' + urlencode({'user_code': form['user_code']}), status_code=303)

    @app.get('/oauth/device/status')
    async def status(request: Request):
        owner = await account(request)
        found = await lookup(request.query_params.get('user_code', ''), browser=True)
        if not found:
            return JSONResponse({'status': 'expired'}, headers=NO_STORE)
        grant = found[1]
        if grant.get('owner') != owner:
            raise HTTPException(403, 'This setup belongs to another account.')
        hosts = [hook['host'] for hook in grant['reminder_hooks']]
        setup = hosts[0] if len(hosts) == 1 else 'both'
        return JSONResponse({'status': grant['status'], 'next': '/?setup=' + setup + '#connections'}, headers=NO_STORE)

    @app.post('/oauth/device/complete')
    async def complete(request: Request):
        form = dict(await request.form())
        async with app.state.oauth_lock:
            found = await lookup(form.get('device_code', ''))
            if not found or found[1]['client_id'] != form.get('client_id'):
                raise HTTPException(400, 'invalid_grant')
            key, grant = found
            desired = 'complete' if form.get('status') == 'complete' else 'failed'
            if grant['status'] not in {'installing', desired}:
                raise HTTPException(400, 'invalid_grant')
            if desired == 'failed':
                await archive.revoke_token(grant['owner'], grant['connection_id'])
            grant['status'] = desired
            await save(key, grant)
        return JSONResponse({'status': desired}, headers=NO_STORE)

    async def redeem(form):
        def error(name):
            return JSONResponse({'error': name}, status_code=400, headers=NO_STORE)
        async with app.state.oauth_lock:
            found = await lookup(form.get('device_code', ''))
            if not found:
                return error('expired_token')
            key, grant = found
            if grant['client_id'] != form.get('client_id') or form.get('resource', issuer + '/mcp') != issuer + '/mcp':
                return error('invalid_grant')
            if grant['status'] == 'denied':
                return error('access_denied')
            if grant['status'] not in {'pending', 'approved'}:
                return error('invalid_grant')
            now = time.time()
            too_fast = now - grant['last_poll'] < grant['interval']
            grant['last_poll'] = now
            if too_fast:
                grant['interval'] += 5
            await save(key, grant)
            if too_fast:
                return error('slow_down')
            if grant['status'] == 'pending':
                return error('authorization_pending')
            if not await archive.member(grant['owner']):
                return error('access_denied')
            # Consume before minting. A repeated token request cannot create another connection.
            grant['status'] = 'installing'
            await save(key, grant)
            created = await archive.create_token(grant['owner'], grant['client_name'], lifetime=3600)
            grant['connection_id'] = created['id']
            await save(key, grant)
            refresh = await issue_refresh(created['id'], grant['client_id'])
        return token_response(created['token'], refresh)

    return redeem
