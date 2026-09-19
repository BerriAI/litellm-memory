import { readFile } from 'node:fs/promises';
import { z } from 'zod';
import { consolidationSchema, slug } from './schema.mjs';
import { ConflictError } from './github.mjs';

const prompt = await readFile(new URL('../prompts/consolidate.md', import.meta.url), 'utf8');
const commitSchema = consolidationSchema.omit({ revision: true });
function parameters(schema) { const { $schema, ...result } = z.toJSONSchema(schema); return result; }
export const NIGHTLY_TOOLS = [
  { type: 'function', function: { name: 'read_knowledge', description: 'Read the complete existing knowledge note before changing it.', parameters: parameters(z.object({ id: slug }).strict()) } },
  { type: 'function', function: { name: 'read_evidence', description: 'Inspect an original observation cited by an existing note, including archived evidence.', parameters: parameters(z.object({ id: slug }).strict()) } },
  { type: 'function', function: { name: 'commit_memory', description: 'Commit the completed consolidation, preserving originals in the archive. Account for every supplied observation.', parameters: parameters(commitSchema) } },
];

export function validateBatchPlan(value, pending, notes, readIds) {
  const plan = commitSchema.parse(value);
  const expected = new Set(pending.items.map(n => n.id));
  if (plan.processed.length !== expected.size || new Set(plan.processed.map(n => n.id)).size !== expected.size || plan.processed.some(n => !expected.has(n.id))) {
    throw new Error('Account for each observation in the supplied batch exactly once, and no other observations.');
  }
  for (const update of plan.updates) if (notes.has(update.id) && !readIds.has(update.id)) throw new Error(`Read existing note ${update.id} before replacing it.`);
  return { ...plan, revision: pending.revision };
}

export async function runBatch(store, complete, { log = () => {} } = {}) {
  const snapshot = await store.github.snapshot();
  const pending = await store.pending(12, snapshot);
  if (!pending.items.length) return null;
  const notes = new Map((await store.knowledge(snapshot)).map(n => [n.id, n]));
  const readIds = new Set();
  let emptyTurns = 0;
  const messages = [{ role: 'system', content: prompt }, { role: 'user', content: JSON.stringify({
    instruction: 'Consolidate these observations. Read related notes, then call commit_memory.',
    pending: pending.items,
    knowledge_catalog: [...notes.values()].map(({ id, title, when_to_use, scope, status }) => ({ id, title, when_to_use, scope, status })),
  }) }];
  for (let step = 0; step < 25; step++) {
    const message = await complete(messages, NIGHTLY_TOOLS);
    if (!message || message.role !== 'assistant') throw new Error('Model returned an invalid assistant response');
    messages.push({ role: 'assistant', content: message.content || null, ...(message.tool_calls?.length ? { tool_calls: message.tool_calls } : {}) });
    if (!message.tool_calls?.length) {
      if (++emptyTurns >= 3) throw new Error('The configured model returned no tool calls in three consecutive responses. Check MEMORY_MODEL: consolidation requires a working tool-capable route. All observations are still pending.');
      messages.push({ role: 'user', content: 'Use the tools to finish: read relevant existing notes, then call commit_memory. No prose-only answer is needed.' });
      continue;
    }
    emptyTurns = 0;
    for (const call of message.tool_calls) {
      let result;
      try {
        const args = JSON.parse(call.function.arguments);
        if (call.function.name === 'read_knowledge') {
          slug.parse(args.id);
          result = notes.get(args.id);
          if (!result) throw new Error('Unknown knowledge ID; use the supplied catalog.');
          readIds.add(args.id);
        } else if (call.function.name === 'read_evidence') {
          slug.parse(args.id);
          const path = [...snapshot.files.keys()].find(p => /^(journal|archive)\//.test(p) && p.endsWith(`/${args.id}.json`));
          if (!path) throw new Error('Original observation not found at this snapshot.');
          result = JSON.parse(await store.github.text(snapshot, path));
        } else if (call.function.name === 'commit_memory') {
          const plan = validateBatchPlan(args, pending, notes, readIds);
          result = await store.consolidate(plan);
          log({ archived: result.archived, updated: result.updated, revision: result.revision });
          return result;
        } else throw new Error('Only read_knowledge, read_evidence and commit_memory are available.');
      } catch (error) {
        if (error instanceof ConflictError) throw error;
        result = { error: error.message };
      }
      log({ step: step + 1, tool: ['read_knowledge', 'read_evidence', 'commit_memory'].includes(call.function.name) ? call.function.name : 'unknown', status: result?.error ? 'rejected' : 'completed' });
      messages.push({ role: 'tool', tool_call_id: call.id, content: JSON.stringify(result) });
    }
  }
  throw new Error('The consolidating model did not produce a valid commit. All remaining observations are still pending.');
}

export async function runNightly(store, complete, { log = () => {} } = {}) {
  const totals = { batches: 0, archived: 0, updated: 0 };
  let conflicts = 0;
  for (;;) {
    try {
      const result = await runBatch(store, complete, { log });
      if (!result) return totals;
      totals.batches++; totals.archived += result.archived; totals.updated += result.updated;
      conflicts = 0;
    } catch (error) {
      if (!(error instanceof ConflictError) || ++conflicts >= 4) throw error;
      log({ message: 'Memory changed during processing; retrying from the latest revision.' });
    }
  }
}

export function modelClient({ baseUrl, apiKey, model, fetchImpl = fetch }) {
  if (!baseUrl || !apiKey || !model) throw new Error('Set MEMORY_BASE_URL, MEMORY_API_KEY and MEMORY_MODEL for hosted consolidation.');
  const endpoint = new URL(`${baseUrl.replace(/\/$/, '')}/chat/completions`);
  if (endpoint.protocol !== 'https:' && !['localhost', '127.0.0.1'].includes(endpoint.hostname)) throw new Error('The model endpoint must use HTTPS.');
  return async (messages, tools) => {
    for (let attempt = 0; attempt < 3; attempt++) {
      const response = await fetchImpl(endpoint, {
        method: 'POST', redirect: 'error', headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, messages, tools, tool_choice: 'required', max_tokens: 8192 }),
        signal: AbortSignal.timeout(180000),
      });
      if (!response.ok) {
        if ([429, 502, 503, 504].includes(response.status) && attempt < 2) { await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 2000)); continue; }
        throw new Error(`Nightly model request failed (HTTP ${response.status}). Pending memories are preserved.`);
      }
      const data = await response.json();
      const choice = data.choices?.[0];
      if (choice?.finish_reason === 'length') throw new Error('Model output was truncated. Pending memories are preserved; use a model with sufficient output capacity.');
      if (!choice?.message) throw new Error('Model returned no assistant message.');
      return choice.message;
    }
  };
}
