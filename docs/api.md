# API and data contracts

The web app, Slack adapter and remote MCP share one API/backend. The current API is documented by the installation's FastAPI OpenAPI schema. Authenticated requests accept a same-origin Slack browser session or a personal bearer credential where noted. Mutating account requests are checked for cross-origin access; browser-only account/admin operations reject bearer tokens. The dedicated transfer inbox permits its explicitly approved source origin with a narrowly scoped receiving capability, never an account session.

## Main endpoints

| Method / path | Purpose |
| --- | --- |
| `GET /api/me` | Browser identity, enrollment and settings summary. |
| `GET /api/models` | Authorized chat model choices from the LiteLLM gateway. |
| `POST /api/memories` | Idempotent private source capture. Delayed automatic captures outside the resumed recording window return `status: skipped` without storing content. |
| `POST /api/memories/search` | Authorized feed, keyword or hybrid retrieval. |
| `GET /api/memories/{id}` | Allowed source evidence; originals are owner-only. |
| `GET /api/memories/{id}/sharing-preview` | Browser-only preview of the separate summary. |
| `PUT /api/memories/{id}/visibility` | Browser-only sharing intent; processing checks still apply. |
| `DELETE /api/memories/{id}` | Browser-only denial/tombstone followed by retryable purge. |
| `POST /api/memories/{id}/corrections` | Browser-only, private dated correction; preserves original evidence and withdraws the old publication. |
| `GET /api/people` | Enrolled Slack profiles, without others' private activity counts. |
| `POST /api/people/{owner}/overview` | Current/daily/project overview and coverage. |
| `POST /api/chat` | Queue an explicit memory query; returns a job ID. |
| `GET /api/jobs/{id}` | Authorized query progress/result. |
| `POST /api/jobs/{id}/cancel` | Cancel an owned query. |
| `/api/privacy-rules` | Owner-managed private-topic criteria. |
| `/api/sharing-preferences` | Allowed types and opt-in future auto-sharing. |
| `/api/connections` | View/revoke owned clients; browser-only creation. |
| `POST /api/export` | Owned readable JSONL or passphrase-encrypted download; browser or personal bearer. |
| `GET /api/transfers` | Browser-owned recent transfer status, without history contents. |
| `POST /api/transfers/receive` | Browser owner approves a source origin and obtains a temporary receiving invitation. |
| `POST /api/transfers/send` | Browser owner approves the invitation/destination; stages recipient-encrypted packets. |
| `GET /api/transfers/{id}` | Browser-owned transfer counts and state. |
| `GET /api/transfers/{id}/outgoing` | Browser-owned resumable send context, including the scoped receiving invitation. |
| `GET /api/transfers/{id}/packets/{index}` | Owned ciphertext packet after current policy revalidation. |
| `POST /api/transfers/{id}/packets` | Browser owner accepts a recipient-encrypted packet. |
| `POST /api/transfers/{id}/finish` | Verifies the complete manifest and queues private import. |
| `DELETE /api/transfers/{id}` | Browser owner cancels and removes temporary key/staging. |
| `/api/transfer-inbox/{id}` | Origin-bound capability endpoint: upload, finish, cancel or content-free status for this receiving grant only. |
| `POST /api/leave` | Browser-only departure and revocation. |
| `POST /api/rejoin` | Slack browser identity restores its departed account, with capture/sharing paused and old tokens still revoked. |
| `/api/admin/models` | Admin model configuration, catalogs and capability checks. |
| `PUT /api/admin/sharing` | Admin workspace sharing switch. |
| `GET /api/admin/connection` | Admin gateway address/status, never its secret. |
| `PUT /api/admin/connection/key` | Validate and rotate the existing gateway's key. |
| `POST /api/admin/transfer` | Domain-mode admin transfers settings authority to an eligible enrolled teammate. |

Request bodies are limited to 1 MB, captures to 200,000 characters / 300 KB encoded, and queries to 16,000 characters. Burst limits return `429` with `Retry-After`. Validation errors do not echo submitted content or upstream payloads.

Pending question quotas are atomic and survive restarts: defaults are four per person and 100 installation-wide. An idempotent retry returns the original job even when the quota is full. All agent interfaces share a two-run execution limit; cancellation releases its slot. Historical classification has its own worker pool and Jev concurrency limit.

## Capture

```json
{
  "source_id": "my-tool:session-1:message-4",
  "source": "my-tool",
  "session_id": "session-1",
  "content": "We decided to keep setup on one page. The Slack callback returns to it.",
  "title": "Keep setup together",
  "occurred_at": "2026-09-20T12:00:00Z",
  "metadata": {"role": "user", "project": "litemem"},
  "historical": false
}
```

The authenticated owner is assigned server-side. Repeating the same source/content is safe; changing the content requires a new revision ID. A receipt can be `storing` (encrypted durable intake awaiting Ente) or `pending` (stored, awaiting processing). `ready` means derived processing completed. Pause/revocation returns an access error. Deleted source IDs remain tombstoned and cannot be restored by replay.

After recording resumes, a delayed nonhistorical `codex` or `claude` capture dated before that resume (or missing a timezone-aware date) returns `{"status":"skipped","reason":"outside_capture_window"}`. No source is stored and no record ID is issued. Connectors should remove that automatic queued copy without marking it permanently imported, so a later explicit historical backfill remains possible. Already-stored sources retain their normal duplicate receipt.

Dates are original work dates, not import times. Unknown dates remain null. The importer records original bytes/digests and provenance separately. Capturing an assistant message does not certify its claims.

## Search and queries

Search accepts `query`, `owner`, `visibility`, `kind`, `source`, `created_from`, `created_before`, `mode`, `offset`, and `limit`. Date bounds are inclusive `from`, exclusive `before`; undated sources are omitted from date-filtered results. Hybrid search reports keyword fallback when embeddings are unavailable/incompatible. Metadata participates in keyword retrieval. Scope filters constrain permitted material; they never grant access.

Chat accepts a question, optional gateway model, optional person scope, audience (`owner`/`public`), and a previous owned job ID. The server loads history; clients cannot supply an arbitrary privileged transcript. Changing scope/audience requires a fresh conversation. Policy changes invalidate stale results.

Final memory citation links must reference sources read in the same authorized run (or checked source manifests from an overview). Invented links fail the answer with a retry message. This validates source identity and access; it does not automatically prove that every generated sentence faithfully summarizes its source.

## Export and corrections

Use **Transfer history** for passwordless browser migration. The receiving invitation binds version, grant ID, source/destination origins, Slack owner, temporary public key, expiry and an upload-only token. Sender approval stages ordered sealed packets and an authenticated final count/digest. The browser forwards those packets to `/api/transfer-inbox/{id}` using `Authorization: Bearer <upload_token>`, its source `Origin`, and no cookies. `POST` accepts `{"action":"packet","packet":{"index":0,"ciphertext":"…"}}` or `{"action":"finish"}`; `GET` reports progress and `DELETE` cancels. This token cannot query memories or authorize any normal account API.

Packets are immutable and duplicate uploads are idempotent. `frames` in status gives the next upload index, allowing retries to skip acknowledged packets. After finish, state is `importing`, `waiting`, or `complete`; further uploads are rejected. `wait_reason` distinguishes paused recording, unavailable Slack verification and unavailable storage. Receipt counts report restored, duplicate and excluded records. Cancellation stops after any in-flight archive write and preserves already imported records. Grant limits and retention are documented under [personal portability](self-hosting.md#personal-portability).

`POST /api/export` without a body returns readable JSONL. For a protected download, send `{"encrypted":true,"passphrase":"at least twelve characters"}`. The passphrase applies only to the file and is not saved or used for sign-in. Restore with `litemem-transfer restore my-memory-export.litemem`; the CLI verifies the complete authenticated stream and original identity before any upload. Original source IDs, content, dates and metadata are preserved, imports remain private, and existing deletion tombstones prevent replay.

Corrections accept `content`, optional `title` and `reason`. They create new dated evidence with a `supersedes` reference; original bytes remain unchanged. The original's publication is withdrawn immediately. Owner reads include available revision links. Export/restore rebuilds links when retaining the same Slack identity; cross-identity migration preserves the original provenance IDs but does not translate revision links.

## Remote MCP

Connect to `https://YOUR_INSTALLATION/mcp`. Discovery is available at `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-authorization-server`. OAuth supports public-client registration, authorization code + S256 PKCE, one-hour access tokens, 30-day rotating refresh tokens and revocation. Redirects must be HTTPS or loopback; callback/resource/client binding is checked.

Tools: `record_work`, `search_memories`, `read_memory`, `ask_memories`, `capture_status`, `pause_capture`. Tools cannot publish records or change privacy policies. Recording and status checks do not automatically invoke memory search.

## Retry rules

Retry network errors, `429` after its indicated delay, and temporary `5xx` using the same source ID. Do not retry a validation failure with changed content under the same ID. Reauthenticate a revoked/expired connection; refreshing a consumed OAuth refresh token revokes that connection. Never treat a failed capture as proof that work was stored remotely—inspect the returned receipt and processing/storage status.
