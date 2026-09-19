import Fuse from 'fuse.js';

const stopWords = new Set('the and for how what why with this that does have from about our are was when should can you work team'.split(' '));

export function fuzzySearch(query, documents) {
  if (!query.trim()) return documents.map(item => ({ ...item, score: 0 })).sort((a, b) => (b.created_at || b.updated_at || '').localeCompare(a.created_at || a.updated_at || ''));
  const index = new Fuse(documents, {
    includeScore: true, ignoreLocation: true, ignoreFieldNorm: true,
    threshold: 0.34, minMatchCharLength: 2,
    keys: [{ name: 'title', weight: 0.35 }, { name: 'when_to_use', weight: 0.3 }, { name: 'scope', weight: 0.2 }, { name: 'content', weight: 0.15 }],
  });
  const tokens = [...new Set(query.toLowerCase().match(/[\p{L}\p{N}_-]{2,}/gu) || [])];
  const terms = tokens.filter(t => !stopWords.has(t));
  const needles = terms.length ? terms : [query];
  const matches = new Map();
  for (const term of needles) for (const { item, score = 1, refIndex } of index.search(term)) {
    const current = matches.get(refIndex) || { ...item, score: 0, matched_terms: [] };
    current.score += 1 - score;
    current.matched_terms.push(term);
    matches.set(refIndex, current);
  }
  return [...matches.values()].sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
}
