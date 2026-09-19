import { randomUUID, createHash } from 'node:crypto';
import { mkdir, readdir, readFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { observationSchema, consolidationSchema, decodeNote, encodeNote, redact, slug } from './schema.mjs';
import { fuzzySearch } from './search.mjs';
import { parseSearch, filterDocuments, sortResults, hybridResults } from './query.mjs';
import { atomicFile, ConflictError } from './github.mjs';

export const digest = s => createHash('sha256').update(s).digest('hex');
export const json = value => JSON.stringify(value, null, 2) + '\n';

export class MemoryStore {
  constructor(github, { stateDir, now = () => new Date().toISOString(), embeddings, readOnly = false } = {}) {
    Object.assign(this, { github, stateDir, now, embeddings, readOnly });
  }
  async documents(snapshot, prefix) {
    const paths = [...snapshot.files.keys()].filter(p => p.startsWith(prefix) && p.endsWith(prefix === 'knowledge/' ? '.md' : '.json')).sort();
    const result = [];
    // Small parallel batches avoid exhausting GitHub's secondary rate limits.
    for (let i = 0; i < paths.length; i += 8) {
      result.push(...await Promise.all(paths.slice(i, i + 8).map(async path => {
        const text = await this.github.text(snapshot, path);
        const value = prefix === 'knowledge/' ? decodeNote(text) : JSON.parse(text);
        if (prefix === 'knowledge/' && path !== `knowledge/${value.id}.md`) throw new Error(`Knowledge ID does not match its filename: ${path}`);
        return { ...value, path };
      })));
    }
    return result;
  }
  async knowledge(snapshot) { return this.documents(snapshot, 'knowledge/'); }
  async pending(limit = 20, snapshot) {
    snapshot ||= await this.github.snapshot();
    const paths = [...snapshot.files.keys()].filter(p => p.startsWith('journal/') && p.endsWith('.json')).sort();
    const items = await Promise.all(paths.slice(0, limit).map(async path => ({ ...JSON.parse(await this.github.text(snapshot, path)), path })));
    return { revision: snapshot.revision, total: paths.length, items };
  }
  async search(query = '', input = {}) {
    const opts = parseSearch({ ...input, query });
    const snapshot = await this.github.snapshot();
    const notes = (await this.knowledge(snapshot)).filter(n => opts.include_superseded || n.status !== 'superseded').map(n => ({ ...n, layer: 'long_term' }));
    const pending = (await this.documents(snapshot, 'journal/')).map(n => ({ ...n, layer: 'short_term' }));
    const archived = opts.include_archive ? (await this.documents(snapshot, 'archive/')).map(n => ({ ...n, layer: 'archive' })) : [];
    const candidates = filterDocuments([...notes, ...pending, ...archived], opts);
    let matches = fuzzySearch(query, candidates);
    if (opts.mode !== 'fuzzy' && query.trim()) {
      if (!this.embeddings) throw new Error('Semantic search is not configured; configure an embedding model or use mode: fuzzy');
      const semantic = (await this.embeddings.rank(query, candidates)).filter(n => opts.min_similarity === undefined || n.score >= opts.min_similarity);
      matches = opts.mode === 'hybrid' ? hybridResults(matches, semantic) : semantic;
    }
    const ranked = sortResults(matches, opts, query);
    return { revision: snapshot.revision, mode: opts.mode, total_matches: ranked.length,
      next_offset: opts.offset + opts.limit < ranked.length ? opts.offset + opts.limit : null,
      results: ranked.slice(opts.offset, opts.offset + opts.limit).map(({ id, title, when_to_use, scope, status, kind, certainty, actor, source, layer, score, fuzzy_score, semantic_score, matched_terms, content, created_at, updated_at }) =>
      ({ id, title, when_to_use, scope, status, kind, certainty, actor, source, layer, score, fuzzy_score, semantic_score, matched_terms, excerpt: content.slice(0, 700), created_at, updated_at })),
      hint: ranked.length ? undefined : 'No matching memories. Try related terms, broader filters, or memory_catalog.' };
  }
  async catalog({ offset = 0, limit = 50 } = {}) {
    const snapshot = await this.github.snapshot();
    const notes = await this.knowledge(snapshot);
    const pending = await this.pending(10, snapshot);
    return { revision: snapshot.revision, team_brief: notes.find(n => n.id === 'team')?.content, total_notes: notes.length,
      next_offset: offset + limit < notes.length ? offset + limit : null,
      knowledge: notes.slice(offset, offset + limit).map(({ id, title, scope, status, when_to_use }) => ({ id, title, scope, status, when_to_use })), pending_count: pending.total,
      pending_preview: pending.items.map(({ id, title, scope, when_to_use }) => ({ id, title, scope, when_to_use })) };
  }
  async read(id) {
    slug.parse(id);
    const snapshot = await this.github.snapshot();
    const path = [...snapshot.files.keys()].find(p => p === `knowledge/${id}.md` || /^(journal|archive)\//.test(p) && p.endsWith(`/${id}.json`));
    if (!path) throw new Error('Memory not found');
    const text = await this.github.text(snapshot, path);
    return { revision: snapshot.revision, layer: path.split('/')[0], ...(path.endsWith('.md') ? decodeNote(text) : JSON.parse(text)) };
  }
  async capture(observations, checkpoint) {
    if (this.readOnly) throw new Error('This memory view is read-only');
    if (!Array.isArray(observations) || observations.length > 8) throw new Error('Capture up to eight focused observations at a time');
    if (checkpoint !== undefined && (typeof checkpoint !== 'string' || checkpoint.length > 200)) throw new Error('Invalid checkpoint');
    const parsed = observations.map(o => observationSchema.parse(JSON.parse(redact(JSON.stringify(o)))));
    const actor = parsed.length ? await this.github.actor() : undefined;
    const now = this.now();
    const batch = checkpoint || randomUUID();
    const items = parsed.map(o => ({ ...o, id: digest(`${actor}:${batch}:${JSON.stringify(o)}`), actor, created_at: now }));
    if (items.length) {
      if (!this.stateDir) throw new Error('A local outbox directory is required for capture');
      await atomicFile(join(this.stateDir, 'outbox', `${digest(`${actor}:${batch}:${JSON.stringify(parsed)}`)}.json`), json(items));
    }
    // The local receipt records either a durable outbox write or a considered
    // no-learning result, preventing unnecessary Stop-hook continuations.
    if (checkpoint && this.stateDir) await atomicFile(join(this.stateDir, 'checkpoints', digest(checkpoint)), now);
    const sync = await this.flush();
    return { message: items.length ? (sync.queued ? 'Shared knowledge queued; automatic retry is pending.' : 'Shared knowledge added.') : 'No new shared knowledge.', ids: items.map(x => x.id), ...sync };
  }
  async flush() {
    if (this.readOnly) return { queued: 0 };
    if (!this.stateDir) return { queued: 0 };
    const dir = join(this.stateDir, 'outbox');
    await mkdir(dir, { recursive: true, mode: 0o700 });
    const files = (await readdir(dir)).filter(p => p.endsWith('.json')).sort();
    for (const file of files.slice(0, 10)) {
      try {
        const items = JSON.parse(await readFile(join(dir, file), 'utf8'));
        for (let attempt = 0; attempt < 3; attempt++) {
          const snapshot = await this.github.snapshot();
          const changes = {};
          for (const item of items) {
            observationSchema.parse(Object.fromEntries(Object.keys(observationSchema.shape).map(k => [k, item[k]])));
            slug.parse(item.id);
            if (!/^\d{4}-\d{2}-\d{2}T/.test(item.created_at)) throw new Error('Invalid outbox date');
            const exists = [...snapshot.files.keys()].some(p => /^(journal|archive)\//.test(p) && p.endsWith(`/${item.id}.json`));
            if (!exists) changes[`journal/${item.created_at.slice(0, 10)}/${item.id}.json`] = json(item);
          }
          try {
            if (Object.keys(changes).length) await this.github.commit(snapshot, changes, `memory: capture ${Object.keys(changes).length} observation(s)`);
            break;
          } catch (error) { if (!(error instanceof ConflictError) || attempt === 2) throw error; }
        }
        await unlink(join(dir, file)).catch(e => { if (e.code !== 'ENOENT') throw e; });
      } catch (error) {
        return { queued: (await readdir(dir)).filter(p => p.endsWith('.json')).length, warning: error.message };
      }
    }
    return { queued: (await readdir(dir)).filter(p => p.endsWith('.json')).length };
  }
  async consolidate(input) {
    const plan = consolidationSchema.parse(input);
    const snapshot = await this.github.snapshot();
    if (snapshot.revision !== plan.revision) throw new ConflictError();
    const processedIds = new Set(plan.processed.map(p => p.id));
    if (processedIds.size !== plan.processed.length || new Set(plan.updates.map(n => n.id)).size !== plan.updates.length) throw new Error('Duplicate IDs in consolidation');
    const now = this.now(), changes = {}, oldNotes = new Map((await this.knowledge(snapshot)).map(n => [n.id, n]));
    const updates = new Map(plan.updates.map(n => [n.id, n]));
    const knownSources = new Set([...snapshot.files.keys()].filter(p => p.startsWith('archive/') && p.endsWith('.json')).map(p => p.split('/').at(-1).slice(0, -5)));
    for (const id of processedIds) knownSources.add(id);
    for (const note of plan.updates) {
      if (note.sources.some(id => !knownSources.has(id))) throw new Error('A knowledge source must be archived evidence or an observation processed in this commit');
      // Keep historical provenance even if a model omits an old source.
      const sources = [...new Set([...(oldNotes.get(note.id)?.sources || []), ...note.sources])];
      const previous = oldNotes.get(note.id);
      changes[`knowledge/${note.id}.md`] = encodeNote({ ...note, sources }, now, { created_at: previous ? previous.created_at : now });
    }
    for (const disposition of plan.processed) {
      const path = [...snapshot.files.keys()].find(p => p.startsWith('journal/') && p.endsWith(`/${disposition.id}.json`));
      if (!path) throw new Error(`Observation is no longer pending: ${disposition.id}`);
      const raw = await this.github.text(snapshot, path), observation = JSON.parse(raw);
      if (disposition.outcome !== 'episodic' && !disposition.note_ids.length) throw new Error('Durable and duplicate observations need a destination note');
      for (const id of disposition.note_ids) {
        const note = updates.get(id) || oldNotes.get(id);
        if (!note) throw new Error(`Destination note does not exist: ${id}`);
        if (!note.sources.includes(disposition.id)) throw new Error('Destination note must cite the observation being archived');
      }
      if (observation.kind === 'disagreement' || disposition.outcome === 'disagreement') {
        if (disposition.outcome !== 'disagreement' || !disposition.note_ids.some(id => (updates.get(id) || oldNotes.get(id))?.status === 'disputed')) throw new Error('Disagreements must be retained in a disputed knowledge note');
      }
      const archivePath = `archive/${observation.created_at.slice(0, 7)}/${observation.id}.json`;
      if (snapshot.files.has(archivePath)) throw new Error('Archive collision; refusing to replace original evidence');
      changes[archivePath] = raw;
      changes[path] = null;
    }
    const receipt = { id: randomUUID(), created_at: now, based_on: snapshot.revision, processed: plan.processed, updated_notes: plan.updates.map(n => n.id) };
    changes[`consolidations/${now.slice(0, 7)}/${receipt.id}.json`] = json(receipt);
    const revision = await this.github.commit(snapshot, changes, `memory: consolidate ${plan.processed.length} observations into ${plan.updates.length} notes`);
    return { message: 'Shared knowledge updated.', revision, archived: plan.processed.length, updated: plan.updates.length, receipt: receipt.id };
  }
}
