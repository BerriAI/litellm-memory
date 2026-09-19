import { z } from 'zod';
import YAML from 'yaml';

export const slug = z.string().regex(/^[a-z0-9][a-z0-9-]{0,79}$/);
export const observationSchema = z.object({
  title: z.string().min(3).max(180),
  when_to_use: z.string().min(5).max(700),
  content: z.string().min(10).max(6000),
  kind: z.enum(['workflow', 'decision', 'correction', 'learning', 'context', 'disagreement']),
  scope: z.string().min(1).max(200),
  certainty: z.enum(['user_stated', 'observed', 'inferred']),
  evidence: z.string().min(5).max(2000),
  source: z.string().min(3).max(1000),
}).strict();

export const noteSchema = z.object({
  id: slug,
  title: z.string().min(3).max(180),
  when_to_use: z.string().min(5).max(700),
  scope: z.string().min(1).max(200),
  status: z.enum(['active', 'disputed', 'superseded']),
  content: z.string().min(10).max(12000),
  sources: z.array(slug).min(1).max(500),
}).strict();

export const consolidationSchema = z.object({
  revision: z.string().regex(/^[a-f0-9]{40}$/),
  updates: z.array(noteSchema).max(30),
  processed: z.array(z.object({
    id: slug,
    outcome: z.enum(['incorporated', 'duplicate', 'episodic', 'disagreement']),
    reason: z.string().min(5).max(1000),
    note_ids: z.array(slug).max(20),
  }).strict()).min(1).max(30),
}).strict();

export function encodeNote(note, now, { created_at = undefined } = { created_at: now }) {
  const { content, ...meta } = noteSchema.parse(note);
  return `---\n${YAML.stringify({ ...meta, ...(created_at ? { created_at } : {}), updated_at: now })}---\n\n${content.trim()}\n`;
}

export function decodeNote(text) {
  const match = /^---\n([\s\S]*?)\n---\n([\s\S]*)$/.exec(text);
  if (!match) throw new Error('Invalid knowledge Markdown frontmatter');
  const { created_at, updated_at, ...meta } = YAML.parse(match[1]);
  return { ...noteSchema.parse({ ...meta, content: match[2].trim() }), ...(created_at ? { created_at } : {}), updated_at };
}

// Credentials are never useful reasoning evidence. Preserve the observation,
// replacing recognizable credential literals before they enter Git history.
export function redact(value) {
  return value
    .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[REDACTED PRIVATE KEY]')
    .replace(/\b(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{12,}/g, '[REDACTED TOKEN]')
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/-]{12,}/gi, '$1[REDACTED]');
}
