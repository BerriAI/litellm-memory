#!/usr/bin/env node
import { appendFile } from 'node:fs/promises';
import { createStore } from '../src/runtime.mjs';
import { modelClient, runNightly } from '../src/nightly.mjs';

try {
  const store = createStore();
  const pending = await store.pending(1);
  let result = { batches: 0, archived: 0, updated: 0 };
  if (pending.total) {
    const complete = modelClient({ baseUrl: process.env.MEMORY_BASE_URL, apiKey: process.env.MEMORY_API_KEY, model: process.env.MEMORY_MODEL });
    result = await runNightly(store, complete, { log: item => console.log(JSON.stringify(item)) });
  }
  console.log(JSON.stringify(result));
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY,
    `Shared knowledge: ${result.archived} observations archived, ${result.updated} note updates, ${result.batches} batches. Originals retained.\n`);
} catch (error) { console.error(error.message); process.exitCode = 1; }
