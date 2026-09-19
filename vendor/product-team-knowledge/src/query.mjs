import { z } from 'zod';

const date = z.union([z.iso.date(), z.iso.datetime({ offset: true })]);
export const searchSchema = z.object({
  query: z.string().max(2000).default(''),
  mode: z.enum(['fuzzy', 'semantic', 'hybrid']).default('fuzzy'),
  scope: z.string().max(200).optional(),
  source: z.string().max(1000).optional(),
  layers: z.array(z.enum(['long_term', 'short_term', 'archive'])).min(1).optional(),
  kinds: z.array(z.enum(['workflow', 'decision', 'correction', 'learning', 'context', 'disagreement'])).min(1).optional(),
  certainties: z.array(z.enum(['user_stated', 'observed', 'inferred'])).min(1).optional(),
  actors: z.array(z.string().min(1).max(100)).min(1).optional(),
  statuses: z.array(z.enum(['active', 'disputed', 'superseded'])).min(1).optional(),
  created_from: date.optional(), created_before: date.optional(),
  updated_from: date.optional(), updated_before: date.optional(),
  include_archive: z.boolean().default(false),
  include_superseded: z.boolean().default(false),
  sort_by: z.enum(['relevance', 'created_at', 'updated_at']).default('relevance'),
  sort_order: z.enum(['asc', 'desc']).default('desc'),
  min_similarity: z.number().min(-1).max(1).optional(),
  offset: z.number().int().min(0).default(0),
  limit: z.number().int().min(1).max(100).default(8),
}).strict();

export function parseSearch(input) {
  const options = searchSchema.parse(input);
  for (const field of ['created', 'updated']) {
    const from = options[`${field}_from`], before = options[`${field}_before`];
    if (from && before && Date.parse(from) >= Date.parse(before)) throw new Error(`${field}_from must be earlier than ${field}_before`);
  }
  if (options.layers?.includes('archive') && !options.include_archive) throw new Error('Set include_archive: true to search the archive layer');
  if (options.statuses?.includes('superseded') && !options.include_superseded) throw new Error('Set include_superseded: true to search superseded notes');
  return options;
}

export function filterDocuments(documents, options) {
  return documents.filter(doc => {
    for (const key of ['scope', 'source']) {
      if (options[key] && !String(doc[key] || '').toLowerCase().includes(options[key].toLowerCase())) return false;
    }
    for (const [filter, field] of [['layers', 'layer'], ['kinds', 'kind'], ['certainties', 'certainty'], ['actors', 'actor'], ['statuses', 'status']]) {
      if (options[filter] && !options[filter].includes(doc[field])) return false;
    }
    for (const field of ['created', 'updated']) {
      const from = options[`${field}_from`], before = options[`${field}_before`];
      if (!from && !before) continue;
      const value = Date.parse(doc[`${field}_at`]);
      if (!Number.isFinite(value) || (from && value < Date.parse(from)) || (before && value >= Date.parse(before))) return false;
    }
    return true;
  });
}

export function sortResults(results, { sort_by, sort_order }, query) {
  const direction = sort_order === 'asc' ? 1 : -1;
  const value = doc => sort_by === 'relevance'
    ? (query.trim() ? doc.score : Date.parse(doc.created_at || doc.updated_at))
    : Date.parse(doc[sort_by]);
  return [...results].sort((a, b) => {
    const av = value(a), bv = value(b), aKnown = Number.isFinite(av), bKnown = Number.isFinite(bv);
    // Missing dates always sort last. Never invent a creation date from an update.
    if (aKnown !== bKnown) return aKnown ? -1 : 1;
    return (aKnown ? direction * (av - bv) : 0) || a.id.localeCompare(b.id);
  });
}

export function hybridResults(fuzzy, semantic) {
  const results = new Map();
  for (const [method, ranked] of [['fuzzy', fuzzy], ['semantic', semantic]]) {
    ranked.forEach((doc, rank) => {
      const item = results.get(doc.id) || { ...doc, score: 0 };
      if (method === 'fuzzy') { item.fuzzy_score = doc.score; item.matched_terms = doc.matched_terms; }
      else item.semantic_score = doc.score;
      // Reciprocal rank fusion combines unlike score scales without calibration.
      item.score += 1 / (60 + rank + 1);
      results.set(doc.id, item);
    });
  }
  return [...results.values()];
}
