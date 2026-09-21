import json

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_model_settings import application


@pytest.mark.asyncio
async def test_real_mcp_sdk_initializes_captures_searches_and_cannot_cross_accounts(tmp_path):
    calls = []
    app = application(tmp_path, calls)
    async with app.router.lifespan_context(app):
        archive = app.state.archive
        await archive.enroll('T:OWNER', {'name': 'Owner'})
        await archive.enroll('T:OTHER', {'name': 'Other'})
        token = await archive.create_token('T:OWNER', 'SDK test')
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                headers={'Authorization': 'Bearer ' + token['token']}) as http:
            async with streamable_http_client('http://localhost:8000/mcp/', http_client=http) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == 'litemem'
                    listing = await session.list_tools()
                    assert {t.name for t in listing.tools} == {
                        'record_work', 'search_memories', 'read_memory', 'ask_memories', 'capture_status', 'pause_capture'}
                    captured = await session.call_tool('record_work', {'source_id': 'mcp-once', 'content': 'UNIQUE_PRIVATE_EVIDENCE'})
                    assert not captured.is_error, captured
                    receipt = json.loads(captured.content[0].text)
                    assert not calls  # Capture and initialization do not do model recall.
                    found = await session.call_tool('search_memories', {'query': 'UNIQUE_PRIVATE_EVIDENCE'})
                    assert not found.is_error, found
                    results = json.loads(found.content[0].text)
                    assert results['results'][0]['id'] == receipt['id']
                    read = await session.call_tool('read_memory', {'record_id': receipt['id']})
                    assert not read.is_error
                    assert 'UNIQUE_PRIVATE_EVIDENCE' in read.content[0].text
        other = await archive.create_token('T:OTHER', 'Other SDK client')
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                headers={'Authorization': 'Bearer ' + other['token']}) as http:
            async with streamable_http_client('http://localhost:8000/mcp/', http_client=http) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    read = await session.call_tool('read_memory', {'record_id': receipt['id']})
                    assert read.is_error and 'UNIQUE_PRIVATE_EVIDENCE' not in str(read)
        await archive.revoke_token('T:OWNER', token['id'])
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app)) as http:
            response = await http.post('http://localhost:8000/mcp/', headers={
                'Authorization': 'Bearer ' + token['token'], 'Accept': 'application/json, text/event-stream'},
                json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
            assert response.status_code == 401
