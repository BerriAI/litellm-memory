# API and data contracts

The web app, Slack adapter and remote MCP share one API/backend. The public API uses `/api/v1` and is documented by the installation's FastAPI OpenAPI schema. Existing `/api` routes are compatibility aliases to the same handlers, authorization and rate-limit buckets; they are omitted from OpenAPI. OAuth, Slack and MCP retain their protocol paths. Installed capture/transfer clients may continue using those aliases during upgrades. Authenticated requests accept a same-origin Slack browser session or a personal bearer credential where noted. Mutating account requests are checked for cross-origin access; browser-only account/admin operations reject bearer tokens. The dedicated transfer inbox permits its explicitly approved source origin with a narrowly scoped receiving capability, never an account session.

## Main endpoints

| Method / path | Purpose |
| --- | --- |
| `GET /api/v1/me` | Browser identity, enrollment and settings summary. |
| `GET /api/v1/recording-status` | Own connection count, persisted intake receipt and processing counts; never teammate activity. |
| `GET /api/v1/schedule` | Workspace date configuration, automatic daily refresh and the next scheduled time. |
| `PUT /api/v1/admin/schedule` | Browser admin saves timezone/daily time with the current revision. |
| `GET /api/v1/display-preferences` | Authenticated member reads their private display timezone; null follows the device. |
| `PUT /api/v1/display-preferences` | Browser member changes their own display timezone, without changing access or sharing. |
| `GET /api/v1/models` | Authorized chat model choices from the LiteLLM gateway. |
| `POST /api/v1/memories` | Idempotent private source capture. Delayed automatic captures outside the resumed recording window return `status: skipped` without storing content. |
| `POST /api/v1/memories/search` | Authorized feed, keyword or hybrid retrieval. |
| `GET /api/v1/memories/{id}` | Allowed source evidence; originals are owner-only. |
| `GET /api/v1/memories/{id}/sharing-preview` | Browser-only preview of the separate summary. |
| `PUT /api/v1/memories/{id}/visibility` | Browser-only sharing intent; processing checks still apply. |
| `DELETE /api/v1/memories/{id}` | Browser-only denial/tombstone followed by retryable purge. |
| `POST /api/v1/memories/{id}/corrections` | Browser-only, private dated correction; preserves original evidence and withdraws the old publication. |
| `GET /api/v1/memories/{id}/privacy-matches` | Owner browser only; current matching private topics from cached Jev results. |
| `GET /api/v1/sharing/preview-existing` | Owner browser only; counts of eligible and skipped private records, plus a confirmation revision. |
| `POST /api/v1/sharing/existing` | Owner browser only; `{"revision":"…"}` queues only eligible records from the unchanged preview. Stale previews return 400 and require review again. |
| `GET /api/v1/people` | Enrolled Slack profiles, without others' private activity counts. |
| `GET /api/v1/daily-summaries` | Browse available dates and stored summaries; optional completed `day=YYYY-MM-DD`, `owner`, and `audience=public` (default) or `owner`. Identity still constrains access. Returns timezone, today, latest completed day, available days, summaries and processing state. Missing/stale views are queued. |
| `POST /api/v1/people/{owner}/overview` | Current/daily/project overview and coverage. |
| `POST /api/v1/chat` | Queue an explicit memory query; returns a job ID. |
| `GET /api/v1/jobs/{id}` | Authorized query progress/result. |
| `POST /api/v1/jobs/{id}/cancel` | Cancel an owned query. |
| `/api/v1/privacy-rules` | Owner-managed private-topic criteria. |
| `/api/v1/sharing-preferences` | Opt-in future auto-sharing; nullable `allowed_types` retains legacy restrictions. Omitting it preserves the saved policy. |
| `GET /api/v1/view-preferences` | The authenticated member's private retrieval exclusions. |
| `PUT /api/v1/view-preferences` | Browser-only update to personal people/project/source/type exclusions. |
| `/api/v1/connections` | View/revoke owned clients; browser-only creation. |
| `POST /api/v1/export` | Owned readable JSONL or passphrase-encrypted download; browser or personal bearer. |
| `GET /api/v1/transfers` | Browser-owned recent transfer status, without history contents. |
| `POST /api/v1/transfers/receive` | Browser owner approves a source origin and obtains a temporary receiving invitation. |
| `POST /api/v1/transfers/send` | Browser owner approves the invitation/destination; stages recipient-encrypted packets. |
| `GET /api/v1/transfers/{id}` | Browser-owned transfer counts and state. |
| `GET /api/v1/transfers/{id}/outgoing` | Browser-owned resumable send context, including the scoped receiving invitation. |
| `GET /api/v1/transfers/{id}/packets/{index}` | Owned ciphertext packet after current policy revalidation. |
| `POST /api/v1/transfers/{id}/packets` | Browser owner accepts a recipient-encrypted packet. |
| `POST /api/v1/transfers/{id}/finish` | Verifies the complete manifest and queues private import. |
| `DELETE /api/v1/transfers/{id}` | Browser owner cancels and removes temporary key/staging. |
| `/api/v1/transfer-inbox/{id}` | Origin-bound capability endpoint: upload, finish, cancel or content-free status for this receiving grant only. |
| `POST /api/v1/leave` | Browser-only departure and revocation. |
| `POST /api/v1/rejoin` | Slack browser identity restores its departed account, with capture/sharing paused and old tokens still revoked. |
| `/api/v1/admin/models` | Admin model configuration, catalogs and capability checks. |
| `PUT /api/v1/admin/sharing` | Admin workspace sharing switch. |
| `GET /api/v1/admin/connection` | Admin gateway address/status, never its secret. |
| `PUT /api/v1/admin/connection/key` | Validate and rotate the existing gateway's key. |
| `POST /api/v1/admin/transfer` | Domain-mode admin transfers settings authority to an eligible enrolled teammate. |

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
  "metadata": {"role": "user", "project": "litellm-memory"},
  "historical": false
}
```

The authenticated owner is assigned server-side. Repeating the same source/content is safe; changing the content requires a new revision ID. A receipt can be `storing` (encrypted durable intake awaiting Ente) or `pending` (stored, awaiting processing). `ready` means derived processing completed. Pause/revocation returns an access error. Deleted source IDs remain tombstoned and cannot be restored by replay.

After recording resumes, a delayed nonhistorical `codex` or `claude` capture dated before that resume (or missing a timezone-aware date) returns `{"status":"skipped","reason":"outside_capture_window"}`. No source is stored and no record ID is issued. Connectors should remove that automatic queued copy without marking it permanently imported, so a later explicit historical backfill remains possible. Already-stored sources retain their normal duplicate receipt.

Dates are original work dates, not import times. Unknown dates remain null. The importer records original bytes/digests and provenance separately. Capturing an assistant message does not certify its claims.

## Search and queries

Search accepts `query`, `owner`, `visibility`, `kind`, `source`, `project`, `created_from`, `created_before`, `date_timezone`, `mode`, `offset`, and `limit`. Date bounds are inclusive `from`, exclusive `before`. Calendar-only bounds use `date_timezone` (IANA name, default UTC); full timestamps require an offset. Comparisons and chronology use actual instants across offsets. Like daily summaries, date search uses event time when supplied and recording time for live agent memories without an event time. Results expose `activity_at` and `date_basis` (`event`, `recorded`, or null); `created_at` and original source timestamps remain unchanged. Undated historical imports, other undated sources and explicitly timezone-unknown events remain outside exact date ranges. An empty query browses all accessible records within the filters, with `next_offset` for pagination. The query agent's calendar dates use the workspace timezone. Hybrid search reports keyword fallback when embeddings are unavailable/incompatible. Metadata participates in keyword retrieval. Project matching uses permitted project metadata and derived project names, never another person's private original metadata. Source names accept the standard client labels as well as source IDs. Scope filters constrain permitted material; they never grant access.

Personal viewing preferences have four arrays: `hidden_people`, `hidden_projects`, `hidden_sources`, and `hidden_types`. Empty arrays include all otherwise permitted work. They apply to web/API/MCP search and the shared query agent, including source reads inside an answer. Source links opened deliberately and exports still expose only their normally authorized originals, independent of viewing exclusions. A changed preference invalidates earlier query results/history; it does not modify publications, stored evidence or classification caches. Preferences persist encrypted with the member profile and are not part of the team directory. Filtered catch-ups have separate encrypted derived views and cannot replace another person's unfiltered view. Shared-audience search excludes private archive counts and personal-filter status.

Once Jev labels are available, type matching and feed chips use those cached classifications at the 0.8 threshold. An unrelated summarizer's suggested `kind` cannot override a negative Jev result. Original type metadata remains the fallback for records not yet classified.

Chat accepts a question, optional gateway model, optional person scope, audience (`owner`/`public`), and a previous owned job ID. The server loads history; clients cannot supply an arbitrary privileged transcript. Changing scope/audience requires a fresh conversation. Policy changes invalidate stale results.

Final memory citation links must reference sources read in the same authorized run (or checked source manifests from an overview). If the model drafts an answer from search results without reading sources, the application re-reads up to 30 retrieved candidates under the current access, person scope and viewing preferences, then corrects the draft against their full permitted summaries. The correction pass uses short source references, which the application resolves to exact source URLs; unknown references are rejected. It never converts an empty citation-repair evidence set into a successful “no evidence” answer. Invented links that cannot be repaired fail the answer with a retry message. These checks do not automatically prove that every generated sentence faithfully summarizes its source; answer-quality review remains necessary.

## Export and corrections

Use **Transfer history** for passwordless browser migration. The receiving invitation binds version, grant ID, source/destination origins, Slack owner, temporary public key, expiry and an upload-only token. Sender approval stages ordered sealed packets and an authenticated final count/digest. The browser forwards those packets to `/api/v1/transfer-inbox/{id}` using `Authorization: Bearer <upload_token>`, its source `Origin`, and no cookies. `POST` accepts `{"action":"packet","packet":{"index":0,"ciphertext":"…"}}` or `{"action":"finish"}`; `GET` reports progress and `DELETE` cancels. This token cannot query memories or authorize any normal account API.

Packets are immutable and duplicate uploads are idempotent. `frames` in status gives the next upload index, allowing retries to skip acknowledged packets. After finish, state is `importing`, `waiting`, or `complete`; further uploads are rejected. `wait_reason` distinguishes paused recording, unavailable Slack verification and unavailable storage. Receipt counts report restored, duplicate and excluded records. Cancellation stops after any in-flight archive write and preserves already imported records. Grant limits and retention are documented under [personal portability](self-hosting.md#personal-portability).

`POST /api/v1/export` without a body returns readable JSONL. For a protected download, send `{"encrypted":true,"passphrase":"at least twelve characters"}`. The passphrase applies only to the file and is not saved or used for sign-in. Restore with `litellm-memory-transfer restore my-memory-export.litellm-memory`; the CLI verifies the complete authenticated stream and original identity before any upload. Original source IDs, content, dates and metadata are preserved, imports remain private, and existing deletion tombstones prevent replay.

Corrections accept `content`, optional `title` and `reason`. For safe retries, also send a stable UUID `request_id` and the original `occurred_at` timestamp; reuse both with the unchanged content. The ID is scoped to the original record. They create new dated evidence with a `supersedes` reference; original bytes remain unchanged. The original's publication is withdrawn immediately. Owner reads include available revision links. Export/restore rebuilds links when retaining the same Slack identity; cross-identity migration preserves the original provenance IDs but does not translate revision links.

## Remote MCP

Connect to `https://YOUR_INSTALLATION/mcp`. Discovery is available at `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-authorization-server`. The bundled connector starts at `POST /oauth/device/code` with its client name and reminder commands, opens `verification_uri_complete`, then polls `/oauth/token` with the returned client ID, device code and `urn:ietf:params:oauth:grant-type:device_code` grant. It respects pending/slow-down/expiry responses and posts completion only after saving local setup. Browser consent/status require the signed-in account; browser codes cannot redeem tokens. This setup endpoint combines client registration with a device authorization request and is specific to the connector.

For other MCP clients, OAuth supports public-client registration, authorization code + S256 PKCE, one-hour access tokens, 30-day rotating refresh tokens and revocation. Redirects must be HTTPS or loopback; callback/resource/client binding is checked.

Tools: `record_work`, `search_memories`, `read_memory`, `ask_memories`, `capture_status`, `pause_capture`. Tools cannot publish records or change privacy policies. Recording and status checks do not automatically invoke memory search.

## Retry rules

Retry network errors, `429` after its indicated delay, and temporary `5xx` using the same source ID. Do not retry a validation failure with changed content under the same ID. Reauthenticate a revoked/expired connection; refreshing a consumed OAuth refresh token revokes that connection. Never treat a failed capture as proof that work was stored remotely—inspect the returned receipt and processing/storage status.

## Scheduling and display

Daily summaries generate automatically for completed days in one nightly run. The worker checks whether a run is due once a minute, catches up missed days, and incorporates late captures at the next nightly run. Daily reads only return generated summaries and their available dates; they never queue missing summaries or expose pending/failed jobs. Current overviews remain on demand. Generated views are checked against current permissions and viewing exclusions before delivery.

Schedule endpoints return `timezone`, `daily_time`, `revision`, `automatic_refresh: true` and `next_run_at`. The workspace timezone defines daily boundaries, and a queued view retains its original timezone. The default daily time is midnight (00:00). Today and future dates are rejected by daily-summary reads; agents use source records to answer questions about today.

`GET /api/v1/recording-status` returns only the authenticated member’s `capture_enabled`, `agent_enabled`, `connected_clients`, `received`, `last_received_at` (Unix seconds or null), and processing `stats`. Receipts exclude historical imports, deleted records and intake awaiting storage. They are not hook heartbeats. A client counts as connected only while not revoked and its access credential or unconsumed refresh credential remains valid. The endpoint does not run inference or retrieval.

Display preferences are `{ "timezone": "Asia/Tokyo" }` or `{ "timezone": null }`. They are private, persist with the member profile and do not invalidate conversations, reclassify records or change anyone's access. `/api/v1/me` also returns the member's `display_timezone`.
