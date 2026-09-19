#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { createStore, options } from '../src/runtime.mjs';
import { makeServer } from '../src/server.mjs';
import { searchArguments } from '../src/cli-search.mjs';

const args = process.argv.slice(2), command = args[0], store = createStore(options(args));
try {
  if (command === 'serve') {
    const server = makeServer(store);
    await server.connect(new StdioServerTransport());
    // Each client retries its own durable outbox. Never spawn another agent.
    let flushing = false;
    const retry = setInterval(async () => { if (flushing) return; flushing = true; try { await store.flush(); } catch {} finally { flushing = false; } }, 60000);
    retry.unref();
  } else {
    let result;
    if (command === 'catalog') result = await store.catalog();
    else if (command === 'search') { const { query, ...opts } = searchArguments(args.slice(1)); result = await store.search(query, opts); }
    else if (command === 'read') result = await store.read(args[1]);
    else if (command === 'pending') result = await store.pending(20);
    else if (command === 'sync') result = await store.flush();
    else if (command === 'capture' || command === 'consolidate') {
      const data = JSON.parse(await readFile(args[1], 'utf8'));
      result = command === 'capture' ? await store.capture(data.observations, data.checkpoint) : await store.consolidate(data);
    } else throw new Error('Usage: memory.mjs serve|catalog|search QUERY [--archive]|read ID|pending|sync|capture FILE|consolidate FILE');
    console.log(JSON.stringify(result, null, 2));
  }
} catch (error) { console.error(error.message); process.exitCode = 1; }
