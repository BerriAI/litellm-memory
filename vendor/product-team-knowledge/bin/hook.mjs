#!/usr/bin/env node
import { createStore, options } from '../src/runtime.mjs';
import { hook } from '../src/hooks.mjs';
const opts = options(), args = process.argv.slice(2);
let raw = '';
try {
  for await (const chunk of process.stdin) { raw += chunk; if (raw.length > 1000000) throw new Error('Hook input too large'); }
  const event = JSON.parse(raw);
  const agent = args[args.indexOf('--agent') + 1] || 'codex';
  console.log(JSON.stringify(await hook(event, { ...opts, agent, store: createStore(opts) })));
} catch { console.log('{}'); }
