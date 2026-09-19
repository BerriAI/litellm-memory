import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { observationSchema, consolidationSchema, slug } from './schema.mjs';
import { WORKFLOW } from './runtime.mjs';
import { searchSchema } from './query.mjs';

export function makeServer(store) {
  const server = new McpServer({ name: 'product-team-knowledge', version: '0.1.0' }, {
    instructions: store.readOnly ? 'Read-only memories scoped by capture provenance. Search before reading full notes; preserve evidence and uncertainty.' : WORKFLOW,
  });
  const register = (name, description, shape, readOnly, handler) => server.registerTool(name, {
    description, inputSchema: shape, annotations: { readOnlyHint: readOnly, destructiveHint: false, openWorldHint: true },
  }, async args => {
    try { return { content: [{ type: 'text', text: JSON.stringify(await handler(args)) }] }; }
    catch (error) { return { isError: true, content: [{ type: 'text', text: error.message }] }; }
  });
  register('memory_catalog', 'Start here: compact catalog of shared knowledge and unprocessed observations. Use relevance descriptions to decide which notes to read. Continue from next_offset when present.', {
    offset: z.number().int().min(0).default(0), limit: z.number().int().min(1).max(100).default(50),
  }, true, args => store.catalog(args));
  register('memory_search', 'Search memories with fuzzy text, semantic similarity, or hybrid ranking. Semantic/hybrid modes require configured embeddings. Filter metadata before ranking; an empty query browses matching records. Date ranges are inclusive from / exclusive before; date-only values are UTC midnight. Missing dates do not match date filters. Actor is the GitHub uploader, not necessarily the speaker. Archive and superseded notes require explicit opt-in. Use next_offset to paginate; compare revision across pages.', searchSchema.shape,
    true, ({ query, ...opts }) => store.search(query, opts));
  register('memory_read', 'Read a full knowledge note or original observation by ID, including archived evidence.', { id: slug }, true, ({ id }) => store.read(id));
  if (store.readOnly) return server;
  register('memory_capture', 'Automatically save useful learnings from the current conversation to shared short-term memory. Include rationale, scope and evidence. Use observations:[] with the checkpoint when reflection found nothing useful. Offline writes queue locally for retry.', {
    observations: z.array(observationSchema).max(8), checkpoint: z.string().max(200).optional(),
  }, false, ({ observations, checkpoint }) => store.capture(observations, checkpoint));
  register('memory_sync', 'Retry pending local writes after a connectivity failure. No model API is required.', {}, false, () => store.flush());
  register('memory_pending', 'For the consolidating agent: get the oldest pending records and their snapshot revision.', { limit: z.number().int().min(1).max(30).default(20) }, true, ({ limit }) => store.pending(limit));
  register('memory_consolidate', 'For consolidation only: atomically update long-term knowledge, archive exact original observations forever, and record each disposition. Requires the pending snapshot revision; concurrent changes fail without overwriting.', consolidationSchema.shape, false, args => store.consolidate(args));
  return server;
}
