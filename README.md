# Moegent

A Slack and web research assistant for Moe’s own captured memories.

OpenAI Agents SDK for Python runs the tool loop. All chat and embedding inference goes through LiteLLM;
the default routes are `openai/gpt-5.6-luna` and `openai/text-embedding-3-small`. FastAPI serves the chat,
memory explorer, Slack events, and optional Slack sign-in. The model receives only read-only memory tools
plus a public-page reader; an optional LiteLLM search tool can be configured with `LITELLM_SEARCH_TOOL`.

## Run locally

Install Python 3.12+, Node 20+, GitHub CLI, and uv. Copy `.env.example` to `.env` and fill in credentials.
For local memory reads, signing in with `gh auth login` also works when `GH_TOKEN` is absent.

```sh
uv sync
npm ci --prefix vendor/product-team-knowledge
uv run uvicorn moegent.app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. `uv run pytest -q` checks Slack signatures, caller gates, owner-only invitations,
event retries, job recovery, and browser session isolation. `uv run ruff check moegent tests scripts` checks Python.
The vendored memory runtime is from the shared knowledge repository with metadata and semantic search extensions;
see `vendor/UPSTREAM.md`. It contains implementation code only, not copies of the memories.

## Search

The memory source remains `BerriAI/product-team-knowledge` on GitHub. Every search checks its latest revision.
Keyword search uses Fuse.js. Semantic search embeds chunks and compares cosine similarity locally; hybrid search
combines both rankings. Vectors are cached by text hash and model/version on disk, so changed records are embedded
incrementally. There is no vector database. Startup warms the cache in the background while health checks remain
available. The first full semantic search can take longer if the index is cold.

The explorer supports query, mode, scope, kind, creation dates, archive inclusion, and sorting. The underlying
memory tool also filters actors, source, certainty, status, layers, update dates, and supports pagination.
Date bounds use UTC, inclusive `from` and exclusive `before`; legacy unknown dates remain unknown.
GitHub actor is the uploader, not necessarily the speaker. Citations open the full source memory.

## Slack and access

The initial `ACCESS_MODE=public` permits anyone with the web URL, and anyone in approved Slack channels or DMs,
to ask questions. To enable an allowlist, set `ACCESS_MODE=allowlist` and `ALLOWED_SLACK_USERS=T123:U123,T123:U456`.
Slack events provide verified workspace/user IDs; web users sign in through Slack OIDC. The same server policy
guards chat, job access, search, and memory reads. A deployment-level provenance boundary restricts every caller to captures by `MEMORY_ALLOWED_ACTORS=moe-berri`. Consolidated notes are included only when all original sources belong to Moe. Mixed or unknown provenance is excluded. Search filters and direct reads cannot bypass this boundary. Per-caller memory scopes are not implemented yet.

Only `SLACK_OWNER_USER_ID` in `SLACK_TEAM_ID` can activate channel access. Slack does not prevent someone else
from clicking Invite. Moegent verifies the bot's `member_joined_channel.inviter`, persists approval, and leaves
when the inviter is anyone else or missing. Until approved, it does not answer there. Channels it cannot leave
(such as a workspace's general channel) remain disabled. Removing the bot revokes approval. Old membership events
cannot override newer ones. After a lost database, remove and re-invite the bot as the owner to enroll it again.
This check is independent of the public/allowlist caller policy.

Generate an app manifest with `uv run python scripts/slack_manifest.py https://YOUR-APP.onrender.com`.
Configure the generated scopes, event subscriptions, and both redirect URLs. Install as the owner using an OAuth
URL whose state is signed with `URLSafeTimedSerializer(SESSION_SECRET, salt='slack-install')` and contains the
configured Slack team ID. The callback exchanges the code and persists the bot credential on the private disk.
App configuration access/refresh tokens are provisioning credentials only; they are never deployed to the server.
Slack signatures and timestamp validation are required even in public mode. Events are durably queued before
acknowledgment; duplicate event IDs are ignored. Replies appear in the original thread.

## Render

`render.yaml` describes one paid Starter Docker web service and a 1 GB persistent disk. Keep `STATE_DIR=/data`.
The disk holds queued jobs, approved channel IDs, the installed bot token, and embedding/blob caches. Run one app
worker/instance; SQLite and the in-process job runner are intentionally sized for this small deployment.
The source memory repository and LiteLLM remain available when the laptop is off.

Set the environment variables in `.env.example` as Render secrets (or a secret file named `moegent.env`).
Never include `.env` in the image or source control. Set `PUBLIC_BASE_URL` to the Render URL before Slack setup.
`/health` is the health check. Render restarts may briefly interrupt access; queued jobs resume from disk.
Slack delivery retries reuse stable message IDs, although network failures cannot guarantee exactly-once delivery.
Per-memory access control, multi-instance workers, and general task execution are future work.
