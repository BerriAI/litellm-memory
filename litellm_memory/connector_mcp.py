"""Stdio bridge for coding tools, using the connector's single encrypted sign-in."""

import asyncio

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .connector import LocalCapture


def credentials(directory, url):
    local = LocalCapture(directory)
    try:
        binding = local.get('account_binding')
        connection = local.get('connection')
        if not binding or binding['url'] != url or not connection or connection['url'] != url:
            raise ValueError('Connect this computer to LiteLLM Memory first.')
        local.flush(limit=0, blocking=True)  # Refresh only; never upload history from the bridge.
        connection = local.get('connection')
        if not connection or connection['owner'] != binding['owner'] or local.get('status') != 'connected':
            raise ValueError('Reconnect this computer to LiteLLM Memory.')
        return connection['access_token']
    finally:
        local.close()


class ConnectorAuth(httpx2.Auth):
    def __init__(self, directory, url):
        self.directory, self.url = directory, url

    async def async_auth_flow(self, request):
        token = await asyncio.to_thread(credentials, self.directory, self.url)
        request.headers['Authorization'] = 'Bearer ' + token
        yield request


async def describe(url, token):
    """Verify the remote MCP once during setup and cache its public tool catalog."""
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + token}, timeout=20) as http:
        async with streamable_http_client(url + '/mcp/', http_client=http) as streams:
            async with ClientSession(*streams) as remote:
                initialized = await remote.initialize()
                listing = await remote.list_tools()
                if not {'record_work', 'read_memory', 'capture_status'} <= {tool.name for tool in listing.tools}:
                    raise ValueError('The installation is missing required memory tools.')
                return {'instructions': initialized.instructions,
                        'tools': [tool.model_dump(mode='json', by_alias=True) for tool in listing.tools]}


async def serve(directory, url):
    local = LocalCapture(directory)
    try:
        binding = local.get('account_binding')
        if not binding or binding['url'] != url or not local.get('connection'):
            raise ValueError('Connect this computer first.')
        catalog = local.get('mcp_catalog')
        if not catalog:
            raise ValueError('Run setup again to verify the memory tools.')
    finally:
        local.close()

    async def list_tools(context, params):
        # Codex gives optional servers a short startup window. Publish the verified
        # catalog locally; a slow/offline gateway must not hide the tool itself.
        return types.ListToolsResult(tools=[types.Tool.model_validate(tool) for tool in catalog['tools']])

    async def call_tool(context, params):
        try:
            async with httpx2.AsyncClient(auth=ConnectorAuth(directory, url), timeout=180,
                                         follow_redirects=False) as http:
                async with streamable_http_client(url + '/mcp/', http_client=http) as streams:
                    async with ClientSession(*streams) as remote:
                        await remote.initialize()
                        return await remote.call_tool(params.name, params.arguments)
        except Exception:
            return types.CallToolResult(is_error=True, content=[types.TextContent(type='text', text=
                'LiteLLM Memory could not complete this request. Check Connections and reconnect if needed. '
                'Do not claim this memory was saved.')])

    server = Server('litellm-memory', title='LiteLLM Memory', version='1.0.0', instructions=catalog['instructions'],
                    on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
