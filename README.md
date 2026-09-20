# litemem

A LiteLLM Labs product. Query your work memories on the web or through **@litemem** in Slack.

This repository is evolving from the original personal assistant into an encrypted work-memory service built with Ente and LiteLLM. The instructions below currently describe the existing running assistant; the new service is still being integrated.

OpenAI Agents SDK for Python runs the tool loop. All chat and embedding inference goes through LiteLLM;
the default routes are `gpt-5.6-luna-priority` and `gemini/gemini-embedding-001`. FastAPI serves the chat,
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
event retries, job recovery, browser session isolation, streaming, cancellation, thread steering, and source links. `uv run ruff check moegent tests scripts` checks Python.
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
GitHub actor is the uploader, not necessarily the speaker. Citations open readable full memories; an answer’s Sources button shows all cited memories together. Combined source links also work from Slack. The web interface streams answer text, supports Stop/Retry, and saves conversations locally in the browser.

## Slack and access

The Render blueprint uses `ACCESS_MODE=allowlist`. Set `ALLOWED_SLACK_USERS=T123:U123,T123:U456`
to exact workspace/member pairs. The same gate protects web chat, sources, search, and Slack requests.
The deployed allowlist is Moe, Tin, Krrish, and Ishaan. Only Moe can activate channels.

Web pages redirect to Slack sign-in automatically and return to the requested source or page afterward.
The workspace hint reuses Slack’s existing session and consent where available. A first visit may need consent.
Sign out from the History dialog to clear the local session and conversation history.
`ACCESS_MODE=public` is still available as an explicit configuration choice.
Slack events provide verified workspace/user IDs; web users sign in through Slack OIDC. The same server policy
guards chat, job access, search, and memory reads. A deployment-level provenance boundary restricts every caller to captures by `MEMORY_ALLOWED_ACTORS=moe-berri`. Consolidated notes are included only when all original sources belong to Moe. Mixed or unknown provenance is excluded. Search filters and direct reads cannot bypass this boundary. Per-caller memory scopes are not implemented yet.

Only `SLACK_OWNER_USER_ID` in `SLACK_TEAM_ID` can activate channel access. Slack does not prevent someone else
from clicking Invite. litemem verifies the bot's `member_joined_channel.inviter`, persists approval, and leaves
when the inviter is anyone else or missing. Until approved, it does not answer there. Channels it cannot leave
(such as a workspace's general channel) remain disabled. Removing the bot revokes approval. Old membership events
cannot override newer ones. After a lost database, remove and re-invite the bot as the owner to enroll it again.
When an invitation record is missing for a group DM, litemem checks Slack’s conversation metadata and
enrolls it only if Moe created it and the bot is a member. Explicit denials or removals take precedence.
Group-DM metadata/history require `mpim:read` and `mpim:history`; thread follow-ups use `message.mpim`.
This check is independent of the public/allowlist caller policy.

Generate an app manifest with `uv run python scripts/slack_manifest.py https://YOUR-APP.onrender.com`.
Configure the generated scopes, event subscriptions, and both redirect URLs. Install as the owner using an OAuth
URL whose state is signed with `URLSafeTimedSerializer(SESSION_SECRET, salt='slack-install')` and contains the
configured Slack team ID. The callback exchanges the code and persists the bot credential on the private disk.
App configuration access/refresh tokens are provisioning credentials only; they are never deployed to the server.
Slack signatures and timestamp validation are required even in public mode. Events are durably queued before
acknowledgment; duplicate Slack message timestamps are ignored even when Slack sends both an app mention and a
channel-message event. Replies appear in the original thread. The manifest enables Slack’s native Agent view;
changing scopes requires reinstalling the app. This is separate from the OpenAI Agents SDK tool loop.

A separate feedback loop adds 👀 while a request is queued/running, updates one progress reply, and removes the
reaction when finished. DMs also use native agent status. Follow-up messages in a litemem thread work without
another mention. A newer message from the same asker cancels their unfinished run and starts a new one with the
updated context; another asker does not cancel their run. Send `stop` in that thread to stop your active request.
Thread text is fetched through conversations.replies (bounded to 1,000 fetched messages, with the root and up to
59 recent messages supplied to the model). If Slack history is unavailable, the model is explicitly told it has
only saved litemem exchanges. Files and attachments are not downloaded. Unrelated channel conversations are ignored.
Slack status/reaction failures are best-effort and cannot prevent answer delivery.

## Render

`render.yaml` describes one paid Starter Docker web service and a 1 GB persistent disk. Keep `STATE_DIR=/data`.
The disk holds queued jobs, approved channel IDs, the installed bot token, and embedding/blob caches. Run one app
worker/instance; SQLite and the in-process job runner are intentionally sized for this small deployment.
The source memory repository and LiteLLM remain available when the laptop is off.

Set the environment variables in `.env.example` as Render secrets (or a secret file named `litemem.env`).
Never include `.env` in the image or source control. Set `PUBLIC_BASE_URL` to the Render URL before Slack setup.
`/health` is the health check. Render restarts may briefly interrupt access; queued jobs resume from disk.
Slack delivery retries reuse stable message IDs, although network failures cannot guarantee exactly-once delivery.
Per-memory access control, multi-instance workers, and general task execution are future work.

Eyes reactions are added before model work starts. Failed reaction calls retry with backoff and respect
Slack’s rate-limit delay; cleanup is marked complete only after removal succeeds or the reaction is already absent.

The agent posts a short, request-specific plan using its progress tool before substantive research.
Credit/quota exhaustion is reported as a problem with the configured route and is not retried.
A failing route does not imply that every route or the gateway key is out of credits.
