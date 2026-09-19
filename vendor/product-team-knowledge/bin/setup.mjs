#!/usr/bin/env node
import { homedir } from 'node:os';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { access } from 'node:fs/promises';
import { run } from '../src/github.mjs';
import { options, createStore } from '../src/runtime.mjs';
import { planSetup, applySetup } from '../src/setup.mjs';

const args = process.argv.slice(2), opts = options(args);
const homeIndex = args.indexOf('--home'), home = homeIndex < 0 ? homedir() : resolve(args[homeIndex + 1]);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
try {
  if (Number(process.versions.node.split('.')[0]) < 20) throw new Error('Node.js 20 or newer is required');
  const gh = opts.gh.includes('/') ? opts.gh : (await run('/usr/bin/which', [opts.gh])).trim();
  const explicit = args.indexOf('--agents');
  let agents = explicit >= 0 ? args[explicit + 1].split(',') : [];
  if (explicit < 0) for (const [agent, dir] of [['codex', '.codex'], ['claude', '.claude']]) { try { await access(join(home, dir)); agents.push(agent); } catch {} }
  if (!agents.length) throw new Error('No supported agent detected. Install Codex or Claude Code first, or pass --agents codex,claude');
  if (agents.some(a => !['codex', 'claude'].includes(a))) throw new Error('Supported setup targets: codex,claude');
  const config = { ...opts, gh, home, root, node: process.execPath, agents, codexHome: homeIndex >= 0 ? join(home, '.codex') : process.env.CODEX_HOME || join(home, '.codex') };
  const changes = await planSetup(config);
  if (args.includes('--dry-run')) { console.log(JSON.stringify({ agents, wouldChange: changes.map(c => c.path) }, null, 2)); }
  else {
    const store = createStore({ ...opts, gh });
    await store.github.actor();
    const snapshot = await store.github.snapshot();
    if (!snapshot.files.has('knowledge/team.md')) throw new Error('Repository seed is missing; finish publishing the repository first');
    const result = await applySetup(changes);
    console.log(`Shared knowledge configured for ${agents.join(' and ')}. ${result.changed.length} files updated.`);
    console.log('Start a new agent session. Codex: open /hooks and trust the product-team-knowledge hooks. Accept the MCP connection prompt if your client shows one.');
    console.log('Test with: What do you know about our product team from shared knowledge?');
    console.log(`Repository: https://github.com/${opts.repo}`);
  }
} catch (error) { console.error(error.message); process.exitCode = 1; }
