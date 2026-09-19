import { parseSearch } from './query.mjs';

export function searchArguments(args) {
  const result = { query: '' };
  const lists = new Set(['layers', 'kinds', 'certainties', 'actors', 'statuses']);
  const numbers = new Set(['limit', 'offset', 'min_similarity']);
  const values = new Set(['mode', 'scope', 'source', 'sort_by', 'sort_order', 'created_from', 'created_before', 'updated_from', 'updated_before', ...lists, ...numbers]);
  let hasQuery = false;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--archive') result.include_archive = true;
    else if (arg === '--include-superseded') result.include_superseded = true;
    else if (arg.startsWith('--')) {
      const key = arg.slice(2).replaceAll('-', '_');
      const value = args[++i];
      if (value === undefined || value.startsWith('--')) throw new Error(`Missing value for ${arg}`);
      if (['repo', 'gh', 'state_dir'].includes(key)) continue;
      if (!values.has(key)) throw new Error(`Unknown search option: ${arg}`);
      result[key] = lists.has(key) ? value.split(',').map(v => v.trim()) : numbers.has(key) ? Number(value) : value;
    } else if (!hasQuery) { result.query = arg; hasQuery = true; }
    else throw new Error('Quote the search query as one argument');
  }
  return parseSearch(result);
}
