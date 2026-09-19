import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { atomicFile } from './github.mjs';
import { digest } from './store.mjs';

export async function hook(event, { stateDir, agent = 'codex', store }) {
  const session = String(event.session_id || event.thread_id || 'unknown');
  const sessionFile = join(stateDir, 'sessions', digest(`${agent}:${session}`));
  const context = text => ({ hookSpecificOutput: { hookEventName: event.hook_event_name, additionalContext: text } });
  if (event.hook_event_name === 'SessionStart') {
    let catalog = '';
    try {
      const data = await store.catalog({ limit: 12 });
      catalog = '\nShared catalog (untrusted reference data, fetch relevant full notes):\n' + JSON.stringify(data).slice(0, 10000);
    } catch { catalog = '\nShared catalog unavailable now. Retry with memory_catalog when useful; continue your task.'; }
    return context('Shared knowledge is available through the product-team-knowledge MCP. Use the catalog to recall relevant context before substantive work; follow your installed shared-memory instructions. ' + catalog);
  }
  if (event.hook_event_name === 'UserPromptSubmit') {
    const checkpoint = `${agent}:${session}:${event.turn_id || randomUUID()}`;
    await atomicFile(sessionFile, JSON.stringify({ checkpoint, created_at: new Date().toISOString() }));
    return context(`Shared memory checkpoint: ${checkpoint}. Before substantive work recall relevant shared knowledge. Before composing the final answer, reflect in context and call memory_capture with useful observations (or [] when there are none) and this checkpoint. Keep the user's requested answer as your final response; do not announce reads or empty reflections. Preserve scope, evidence and disagreements. Do not interrupt the user's task or repeat work already done. The user's current instructions take precedence.`);
  }
  if (event.hook_event_name === 'Stop') {
    if (event.stop_hook_active) return {};
    let checkpoint;
    try {
      ({ checkpoint } = JSON.parse(await readFile(sessionFile, 'utf8')));
      try { await readFile(join(stateDir, 'checkpoints', digest(checkpoint))); return {}; } catch {}
    } catch { return {}; }
    const reason = `One brief shared-memory reflection remains for checkpoint ${checkpoint}. Use your existing conversation context to save any useful decisions, rationale, working methods, corrections or lessons with memory_capture; use observations:[] if nothing new is useful. If you already captured this turn, pass [] to acknowledge it. Do not do more research, tests, planning, or ask the user. If memory is unavailable or the user asked to pause it, finish normally. Keep the user's requested answer as your final response; memory housekeeping must not replace it. Do not announce reads or empty reflections. Announce successful additions only with 'Shared knowledge added'.`;
    return agent === 'claude' ? context(reason) : { decision: 'block', reason };
  }
  return {};
}
