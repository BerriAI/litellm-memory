import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { atomicFile } from './github.mjs';

const hash = text => createHash('sha256').update(text).digest('hex');
function normalized(vector) {
  if (!Array.isArray(vector) || !vector.length || !vector.every(Number.isFinite)) throw new Error('Embedding response contains an invalid vector');
  const norm = Math.hypot(...vector);
  if (!Number.isFinite(norm) || norm === 0) throw new Error('Embedding response contains an invalid vector');
  return vector.map(x => x / norm);
}

export class EmbeddingIndex {
  constructor({ baseUrl, apiKey, model, stateDir, version = '1', fetchImpl = fetch, batchSize = 16, chunkChars = 1800 } = {}) {
    if (!baseUrl || !apiKey || !model) throw new Error('Semantic search requires MEMORY_EMBEDDING_BASE_URL, MEMORY_EMBEDDING_API_KEY and MEMORY_EMBEDDING_MODEL');
    const url = new URL(baseUrl);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Invalid embedding base URL');
    if (!Number.isInteger(batchSize) || batchSize < 1 || !Number.isInteger(chunkChars) || chunkChars < 1) throw new Error('Invalid embedding batch or chunk size');
    Object.assign(this, { baseUrl: baseUrl.replace(/\/$/, ''), apiKey, model, fetchImpl, batchSize, chunkChars });
    const namespace = hash(JSON.stringify([this.baseUrl, model, version, chunkChars, 'memory-chunks-v1']));
    this.cacheDir = stateDir && join(stateDir, 'embeddings', namespace);
    this.queue = Promise.resolve();
  }
  async embed(inputs) {
    let response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/embeddings`, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(60000),
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.apiKey}` },
        body: JSON.stringify({ model: this.model, input: inputs, encoding_format: 'float' }),
      });
    } catch { throw new Error('Embedding request failed; check gateway connectivity'); }
    if (!response.ok) throw new Error(`Embedding gateway returned HTTP ${response.status}`);
    const body = await response.json();
    if (!Array.isArray(body.data) || body.data.length !== inputs.length) throw new Error('Embedding response has the wrong number of vectors');
    const vectors = new Array(inputs.length);
    for (const row of body.data) {
      if (!Number.isInteger(row.index) || row.index < 0 || row.index >= inputs.length || vectors[row.index]) throw new Error('Embedding response has invalid indices');
      vectors[row.index] = normalized(row.embedding);
    }
    if (vectors.some(v => v.length !== vectors[0].length)) throw new Error('Embedding response has inconsistent dimensions');
    return vectors;
  }
  chunks(doc) {
    const prefix = `${doc.title}\n${doc.scope}\n${doc.when_to_use}\n`;
    // Unicode code points avoid slicing a surrogate pair at a chunk boundary.
    const chars = Array.from(doc.content || '');
    const chunks = [];
    for (let start = 0; start < Math.max(chars.length, 1); start += this.chunkChars) chunks.push(prefix + chars.slice(start, start + this.chunkChars).join(''));
    return chunks;
  }
  async cached(text, dimensions) {
    if (!this.cacheDir) return undefined;
    try {
      const value = JSON.parse(await readFile(join(this.cacheDir, `${hash(text)}.json`), 'utf8'));
      const vector = normalized(value);
      return vector.length === dimensions ? vector : undefined;
    } catch (error) {
      if (error.code && error.code !== 'ENOENT') throw error;
      return undefined; // A missing or damaged disposable cache can be rebuilt.
    }
  }
  rank(query, documents) {
    // Avoid duplicate embedding calls when concurrent requests see a cold cache.
    const run = this.queue.then(() => this.rankOnce(query, documents));
    this.queue = run.catch(() => {});
    return run;
  }
  async rankOnce(query, documents) {
    if (!documents.length) return [];
    const [queryVector] = await this.embed([query]);
    const chunks = new Map();
    const byDocument = documents.map(doc => ({ doc, texts: this.chunks(doc) }));
    for (const { texts } of byDocument) for (const text of texts) chunks.set(text, undefined);
    await Promise.all([...chunks.keys()].map(async text => chunks.set(text, await this.cached(text, queryVector.length))));
    const missing = [...chunks].filter(([, vector]) => !vector).map(([text]) => text);
    for (let offset = 0; offset < missing.length; offset += this.batchSize) {
      const batch = missing.slice(offset, offset + this.batchSize), vectors = await this.embed(batch);
      if (vectors.some(v => v.length !== queryVector.length)) throw new Error('Query and document embedding dimensions differ; use one embedding model');
      await Promise.all(batch.map(async (text, index) => {
        chunks.set(text, vectors[index]);
        if (this.cacheDir) await atomicFile(join(this.cacheDir, `${hash(text)}.json`), JSON.stringify(vectors[index]));
      }));
    }
    return byDocument.map(({ doc, texts }) => ({ ...doc,
      score: Math.max(...texts.map(text => chunks.get(text).reduce((sum, value, i) => sum + value * queryVector[i], 0))),
    })).sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  }
}
