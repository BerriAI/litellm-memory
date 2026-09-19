import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

export class ConflictError extends Error {
  constructor() { super('Shared memory changed concurrently. Read a fresh snapshot and retry; nothing was overwritten.'); this.name = 'ConflictError'; }
}

export function run(command, args, input, { timeout = 20000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'], env: process.env });
    let stdout = '', stderr = '';
    const timer = setTimeout(() => child.kill('SIGKILL'), timeout);
    child.on('error', reject);
    child.stdout.on('data', b => { stdout += b; if (stdout.length > 24_000_000) child.kill('SIGKILL'); });
    child.stderr.on('data', b => { if (stderr.length < 10000) stderr += b; });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout);
      else {
        const status = Number(/HTTP (\d+)/.exec(stderr)?.[1]) || undefined;
        const error = new Error(`${command.split('/').pop()} failed${status ? ` (HTTP ${status})` : ''}. Check authentication, connectivity, and repository access.`);
        error.status = status;
        reject(error);
      }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(input);
  });
}

export async function atomicFile(path, text) {
  const { dirname } = await import('node:path');
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const tmp = `${path}.${randomUUID()}.tmp`;
  await writeFile(tmp, text, { mode: 0o600 });
  await rename(tmp, path);
}

export class GitHub {
  constructor({ repo, gh = 'gh', stateDir, branch = 'main' }) {
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) throw new Error('Expected owner/repository');
    if (!/^[A-Za-z0-9/_-]+$/.test(branch)) throw new Error('Invalid branch');
    Object.assign(this, { repo, gh, stateDir, branch });
  }
  async api(path, method = 'GET', body) {
    const raw = await run(this.gh, ['api', '--hostname', 'github.com', `repos/${this.repo}/${path}`, '--method', method,
      '-H', 'Accept: application/vnd.github+json', '-H', 'X-GitHub-Api-Version: 2022-11-28', ...(body ? ['--input', '-'] : [])], body ? JSON.stringify(body) : undefined);
    return JSON.parse(raw);
  }
  async actor() {
    if (!this.login && this.stateDir) {
      try { this.login = (await readFile(join(this.stateDir, 'actor'), 'utf8')).trim(); } catch {}
    }
    if (!this.login) {
      this.login = JSON.parse(await run(this.gh, ['api', '--hostname', 'github.com', 'user'])).login;
      if (this.stateDir) await atomicFile(join(this.stateDir, 'actor'), this.login);
    }
    return this.login;
  }
  async snapshot() {
    const ref = await this.api(`git/ref/heads/${this.branch}`);
    const commit = await this.api(`git/commits/${ref.object.sha}`);
    const tree = await this.api(`git/trees/${commit.tree.sha}?recursive=1`);
    if (tree.truncated) throw new Error('GitHub truncated the memory tree. Refusing to use an incomplete snapshot.');
    return { revision: ref.object.sha, tree: commit.tree.sha, files: new Map(tree.tree.filter(x => x.type === 'blob').map(x => [x.path, x.sha])) };
  }
  async text(snapshot, path) {
    const sha = snapshot.files.get(path);
    if (!sha) throw new Error(`Memory does not exist: ${path}`);
    const cache = this.stateDir && join(this.stateDir, 'blobs', sha);
    if (cache) { try { return await readFile(cache, 'utf8'); } catch {} }
    const blob = await this.api(`git/blobs/${sha}`);
    if (blob.encoding !== 'base64' || blob.size > 200000) throw new Error('Unexpected memory blob format or size');
    const result = Buffer.from(blob.content, 'base64').toString('utf8');
    if (cache) await atomicFile(cache, result);
    return result;
  }
  async commit(snapshot, changes, message) {
    const ref = await this.api(`git/ref/heads/${this.branch}`);
    if (ref.object.sha !== snapshot.revision) throw new ConflictError();
    const tree = await this.api('git/trees', 'POST', {
      base_tree: snapshot.tree,
      tree: Object.entries(changes).map(([path, content]) => ({ path, mode: '100644', type: 'blob', ...(content === null ? { sha: null } : { content }) })),
    });
    const commit = await this.api('git/commits', 'POST', { message, tree: tree.sha, parents: [snapshot.revision] });
    try { await this.api(`git/refs/heads/${this.branch}`, 'PATCH', { sha: commit.sha, force: false }); }
    catch (error) { if ([409, 422].includes(error.status)) throw new ConflictError(); throw error; }
    return commit.sha;
  }
}
