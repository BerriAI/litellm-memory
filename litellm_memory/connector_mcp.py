"""Stdio bridge for coding tools, using the connector's single encrypted sign-in."""

import asyncio

import httpx2
from mcp import ClientSession
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


def bridge(remote, instructions):
    async def list_tools(context, params):
        return await remote.list_tools(params=params)

    async def call_tool(context, params):
        return await remote.call_tool(params.name, params.arguments)

    return Server('litellm-memory', title='LiteLLM Memory', version='1.0.0', instructions=instructions,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(directory, url):
    async with httpx2.AsyncClient(auth=ConnectorAuth(directory, url), timeout=180,
                                 follow_redirects=False) as http:
        async with streamable_http_client(url + '/mcp/', http_client=http) as streams:
            async with ClientSession(*streams) as remote:
                initialized = await remote.initialize()
                server = bridge(remote, initialized.instructions)
                async with stdio_server() as (reader, writer):
                    await server.run(reader, writer, server.create_initialization_options())
