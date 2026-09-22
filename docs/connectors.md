# Connect Codex or Claude Code

The remote MCP exposes record/search/read/ask/status/pause tools. The connector adds reminder hooks so your coding agent saves useful work memories as you work. It does not automatically upload your conversations.

## Connect

Install from the release branch while this project is in verification:

```sh
uv tool install --force 'git+https://github.com/BerriAI/litellm-memory.git@main'
litellm-memory-connector connect https://your-litellm-memory.example
# Optional: --host codex or --host claude to configure only one.
```

The command configures **Codex and Claude Code together** by default. The browser opens Slack sign-in and one consent screen. Review the MCP access and reminder commands, then choose **Connect**. Setup registers trust for only those exact Codex hooks; it does not disable hook verification or approve unrelated hooks. Setup checks Codex before opening the browser. On macOS, it can use the Codex executable bundled with the desktop app even when `codex` is not on your terminal’s PATH. A separate CLI installation is not required in that case. Other platforms need the Codex CLI available on PATH.

Approval stays on the hosted website. The terminal polls for approval over HTTPS; the browser never visits a localhost callback. Keep the terminal running while the website says **Finishing setup**. After credentials and local setup are saved, the browser returns to **Connections → Try your connection**. Quit and reopen your coding tools once, then start a new task. Copy the first message from Connections and send it to your coding agent. It explicitly uses `record_work`; editing `AGENTS.md` or `CLAUDE.md` does not count. Connections checks for a persisted memory and offers **View my work** when it arrives. It does not infer a successful save from approval alone.

Both coding tools use the same encrypted local OAuth connection through a stdio MCP bridge. No separate MCP sign-in or copied API key is required. Refresh is serialized across processes; revoking the connector stops access from both tools. The remote `/mcp` endpoint remains available for other OAuth-capable clients. Setup verifies and caches the server’s tool catalog so starting a coding task does not wait on a network call. Rerun setup after updating the connector to refresh that catalog. Calls go to the same remote server; it does not read transcripts or implement another memory store.

Private backups are made before host configuration changes. If hook verification fails, setup restores the configuration it wrote; concurrent user or host edits are preserved. Existing unrelated MCPs, hooks and instructions are preserved. An update that changes reminder definitions needs fresh approval. If setup cannot verify trust, it reports failure; use `/hooks` to review Codex's current state. Keep the terminal running until connection completes. A cancelled or expired request needs a fresh `connect` command. If local setup fails, the attempted credential is revoked and the browser directs you to the terminal error. If setup is saved but the completion notice cannot reach the website, the terminal prints the Connections link so you can verify the first memory.

Supported installation locations are `~/.codex/config.toml`, `~/.codex/hooks.json`, `~/.claude/settings.json` and `~/.claude.json`. Symlink-managed host configurations need manual integration. The initial connector supports macOS/Linux and Python 3.12+. Windows support has not been verified.

### Optional agent instructions

Hooks provide memory guidance without changing your global agent instructions. To put the same guidance in your global instructions too, add `--agent-instructions` to `connect`, or enable it later:

```sh
litellm-memory-connector instructions enable
# Disable the guidance without stopping recording:
litellm-memory-connector instructions disable
```

Both commands default to Codex and Claude Code; use `--host codex` or `--host claude` to choose one. The installer manages a marked block in `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md`, keeps unrelated instructions, and makes private backups. Start a new session after changing instructions. `disconnect` removes the block together with this connector’s MCP entry and hooks.

Other memory products, including `product-team-knowledge`, remain separate. Their MCP entries, hooks and instruction blocks are not removed by this installer.

## If a connection request expires

Return to the coding tool or terminal and start connecting again, then use the new browser tab. The connector waits five minutes for sign-in; the consent page expires after ten minutes. Each consent is separate, so connecting another tool does not replace a pending request. Approval is bound to the Slack account that opened it and can be used only once.

## What gets saved

SessionStart and UserPromptSubmit provide lightweight guidance to the coding agent. The importance threshold is low: current work, progress, attempted approaches and results, findings, decisions and reasons, blockers, next steps and corrections all count. The agent saves concise, self-contained memories through `record_work`, preserving attribution and uncertainty. These records are marked as agent-authored memories, not verbatim transcript evidence. LiteLLM Memory keeps that text as written: it does not call another model to summarize it again. Processing adds the search index and any required Jev privacy checks. When you share one, the preview shows the memory text; source details and metadata remain private. Older transcript imports can still use summarization.

Acknowledgments, repetitions and turns with no useful new context can be skipped. There is no mandatory empty checkpoint or forced extra turn. Hooks never read a transcript, upload text, search memories or run a separate model. Retrieval happens only when requested. Actual saving depends on the host agent following the guidance and completing an authorized MCP call; the connector cannot guarantee complete coverage or model judgment.

The dashboard’s **Connections** page provides setup commands and checks persisted intake. Ask the agent to remember what you are working on to verify the first save. A connected credential is not proof of a trusted hook or successful MCP save. **Work received** confirms a saved memory from new work.

## Status, pause and disconnect

Live memories are saved through MCP; if a save fails, the agent should report it rather than claim success. There is no automatic full-transcript fallback.

```sh
litellm-memory-connector status
litellm-memory-connector pause
litellm-memory-connector resume
litellm-memory-connector disconnect
```

Local `pause` changes hook guidance to tell the agent not to save automatically. It is advisory for an already-running agent; pause in the dashboard or MCP to block incoming saves at the API as well. Resume starts reminders again without recording the paused period. Saved memories remain available.

Disconnect checks both Codex and Claude Code, removes LiteLLM Memory's managed hooks, matching MCP entry and optional global instructions, and revokes the connector credential when online. It also removes partial setup left by a failed installation, even if the saved host list is incomplete. If offline, revoke the connection in the dashboard too.

## Existing import utilities (outside the MVP)

Historical conversation imports are not part of onboarding or the core product loop. The existing explicit CLI utilities are retained for operators; setup never runs them. Existing records remain searchable. This section documents those utilities, not a setup or release requirement.

Choose the folders or files you actually intend to import:

```sh
litellm-memory-connector backfill ~/.codex/sessions --host codex
litellm-memory-connector backfill ~/.claude/projects --host claude
```

An explicit backfill imports visible transcript messages, rather than asking an agent to select useful memories. Work is attributed to its transcript role and original session/date. Imports remain private and replay is idempotent. Resume the connector before starting a backfill. Unknown dates remain unknown. System/developer text, tool results and hidden reasoning are excluded; images and attachments are not extracted. An assistant's statement is not proof of verified completion. Transcript formats can change, so unsupported formats are a coverage gap.

Imports use an encrypted local outbox. Records and cursors commit in one transaction; stable source IDs make retries safe. Run `litellm-memory-connector flush` to retry failed uploads, and `status` to see queued work and items needing attention. A 100 MB limit stops new buffering without deleting queued records. Local `pause` also stops import uploads. Disconnect retains encrypted queued imports locally; they cannot be uploaded under a replacement Slack identity. The local wrapping key is accessible to the same OS account, so queue encryption does not protect a compromised computer.

For the older product-team knowledge repository, the read-only preview/import adapter is:

```sh
uv run python -m litellm_memory.imports /path/to/private/snapshot --actor YOUR_GITHUB_ACTOR --manifest /private/path/manifest.json
```

Read its `--help` before upload. Preview output describes included/skipped records; exact original files and provenance are retained in encrypted evidence. An uploader is not necessarily the speaker. Do not include mixed/unknown-provenance shared notes without checking their source boundary. The legacy MCP remains separate; removing it requires an explicit cutover request.

References: [Codex hooks](https://developers.openai.com/codex/hooks/), [Claude Code hooks](https://code.claude.com/docs/en/hooks).
