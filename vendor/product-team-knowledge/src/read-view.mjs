import { decodeNote } from './schema.mjs';

// A deployment-level boundary, separate from user-supplied search filters.
// The service may inspect provenance, but only eligible files reach retrieval tools.
export class ActorReadView {
  constructor(github, actors) {
    if (!actors.length || actors.some(a => !a.trim())) throw new Error('Configure at least one allowed capture actor');
    this.github = github;
    this.actors = new Set(actors);
  }
  async snapshot() {
    const snapshot = await this.github.snapshot();
    const paths = [...snapshot.files.keys()].filter(p => /^(journal|archive)\/.*\.json$/.test(p));
    const records = [];
    for (let i = 0; i < paths.length; i += 8) {
      records.push(...await Promise.all(paths.slice(i, i + 8).map(async path => ({
        path, data: JSON.parse(await this.github.text(snapshot, path)),
      }))));
    }
    const byId = new Map();
    for (const record of records) {
      const list = byId.get(record.data.id) || [];
      list.push(record);
      byId.set(record.data.id, list);
    }
    const allowed = new Set([...byId].filter(([id, group]) => id && group.every(({ path, data }) =>
      path.endsWith(`/${id}.json`) && this.actors.has(data.actor)
    )).map(([id]) => id));
    const files = new Map(records.filter(r => allowed.has(r.data.id)).map(r => [r.path, snapshot.files.get(r.path)]));
    for (const [path, sha] of snapshot.files) {
      if (!path.startsWith('knowledge/') || !path.endsWith('.md')) continue;
      const note = decodeNote(await this.github.text(snapshot, path));
      // Unknown, absent, or mixed provenance is excluded. Original sources remain readable.
      if (note.sources.length && note.sources.every(id => allowed.has(id))) files.set(path, sha);
    }
    return { ...snapshot, files };
  }
  async text(snapshot, path) {
    if (!snapshot.files.has(path)) throw new Error('Memory not found');
    return this.github.text(snapshot, path);
  }
  async commit() { throw new Error('This memory view is read-only'); }
  async actor() { throw new Error('This memory view is read-only'); }
}
