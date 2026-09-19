import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { parse as parseToml } from 'smol-toml';
import { atomicFile } from './github.mjs';
import { HOST_INSTRUCTIONS } from './runtime.mjs';

export const SERVER_NAME = 'product-team-knowledge';
export const DAYTIME_TOOLS = ['memory_catalog', 'memory_search', 'memory_read', 'memory_capture', 'memory_sync', 'memory_pending'];
const MARKER = '--managed-product-team-knowledge';
const begin = '# BEGIN product-team-knowledge', end = '# END product-team-knowledge';
export const shellQuote = s => `'${s.replaceAll("'", "'\\''")}'`;
const get = async path => { try { return await readFile(path, 'utf8'); } catch (e) { if (e.code === 'ENOENT') return ''; throw e; } };

function block(text, start, finish, content) {
  const first = text.indexOf(start), last = text.indexOf(finish);
  if ((first < 0) !== (last < 0) || last >= 0 && last < first) throw new Error('Damaged managed block; refusing to overwrite configuration');
  if (first >= 0) return text.slice(0, first) + `${start}\n${content}\n${finish}` + text.slice(last + finish.length);
  return text + (text.endsWith('\n') || !text ? '' : '\n') + `\n${start}\n${content}\n${finish}\n`;
}

export async function planSetup({ home, codexHome = join(home, '.codex'), root, node, gh, repo, stateDir, agents = ['codex', 'claude'] }) {
  const changes = [];
  const put = async (path, edit) => { const before = await get(path), after = edit(before); if (before !== after) changes.push({ path, before, after }); };
  const editJSON = (path, edit) => put(path, text => { const data = text.trim() ? JSON.parse(text) : {}; edit(data); return JSON.stringify(data, null, 2) + '\n'; });
  const args = [join(root, 'bin/memory.mjs'), 'serve', '--repo', repo, '--gh', gh, '--state-dir', stateDir];
  const mcp = { command: node, args };
  const hooks = agent => [node, join(root, 'bin/hook.mjs'), '--agent', agent, '--repo', repo, '--gh', gh, '--state-dir', stateDir, MARKER].map(shellQuote).join(' ');
  const addHooks = (data, agent) => {
    data.hooks ||= {};
    for (const event of ['SessionStart', 'UserPromptSubmit', 'Stop']) {
      const groups = data.hooks[event] || [];
      if (!Array.isArray(groups)) throw new Error(`Unexpected ${event} hook format`);
      // Preserve every unrelated handler, including handlers sharing a group.
      data.hooks[event] = groups.map(g => ({ ...g, hooks: g.hooks.filter(h => !h.command?.includes(MARKER)) })).filter(g => g.hooks.length);
      data.hooks[event].push({ hooks: [{ type: 'command', command: hooks(agent), timeout: 15 }] });
    }
  };
  if (agents.includes('codex')) {
    await put(join(codexHome, 'config.toml'), text => {
      const config = parseToml(text);
      if (config.mcp_servers?.[SERVER_NAME] && !text.includes(begin)) throw new Error('Existing product-team-knowledge MCP config is not managed by this installer');
      const approvals = DAYTIME_TOOLS.map(tool => `[mcp_servers.${SERVER_NAME}.tools.${tool}]\napproval_mode = "approve"`).join('\n\n');
      const content = `[mcp_servers.${SERVER_NAME}]\ncommand = ${JSON.stringify(node)}\nargs = ${JSON.stringify(args)}\nstartup_timeout_sec = 20\ntool_timeout_sec = 120\n\n${approvals}`;
      const result = block(text, begin, end, content); parseToml(result); return result;
    });
    const override = join(codexHome, 'AGENTS.override.md');
    const instructionPath = await get(override) ? override : join(codexHome, 'AGENTS.md');
    await put(instructionPath, text => block(text, '<!-- BEGIN product-team-knowledge -->', '<!-- END product-team-knowledge -->', HOST_INSTRUCTIONS));
    await editJSON(join(codexHome, 'hooks.json'), data => addHooks(data, 'codex'));
  }
  if (agents.includes('claude')) {
    await editJSON(join(home, '.claude.json'), data => {
      data.mcpServers ||= {};
      const old = data.mcpServers[SERVER_NAME];
      if (old && !old.args?.some(a => String(a).endsWith('/bin/memory.mjs'))) throw new Error('Existing product-team-knowledge MCP config is not managed by this installer');
      data.mcpServers[SERVER_NAME] = { type: 'stdio', ...mcp };
    });
    await put(join(home, '.claude/CLAUDE.md'), text => block(text, '<!-- BEGIN product-team-knowledge -->', '<!-- END product-team-knowledge -->', HOST_INSTRUCTIONS));
    await editJSON(join(home, '.claude/settings.json'), data => {
      addHooks(data, 'claude');
      data.permissions ||= {};
      data.permissions.allow = [...new Set([...(data.permissions.allow || []), ...DAYTIME_TOOLS.map(tool => `mcp__${SERVER_NAME}__${tool}`)])];
    });
  }
  return changes;
}

export async function applySetup(changes) {
  // Validate the complete plan before touching any host config.
  for (const change of changes) if (await get(change.path) !== change.before) throw new Error('Agent settings changed during setup. Rerun setup.');
  const backupId = new Date().toISOString().replaceAll(':', '-');
  for (const { path, before } of changes) {
    await mkdir(dirname(path), { recursive: true, mode: 0o700 });
    if (before) await writeFile(`${path}.product-knowledge-backup-${backupId}`, before, { mode: 0o600, flag: 'wx' });
  }
  for (const { path, before, after } of changes) {
    if (await get(path) !== before) throw new Error(`Settings changed during setup at ${path}; backups retained. Rerun setup.`);
    await atomicFile(path, after);
  }
  return { changed: changes.map(c => c.path), backupId };
}
