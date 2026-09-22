# Codex, Claude Code, and historical capture

The remote MCP exposes record/search/read/ask/status/pause tools. The connector adds reminder hooks so your coding agent saves useful work memories as you work. It does not automatically upload your conversations.

## Connect

Install from the release branch while this project is in verification:

```sh
uv tool install --force 'git+https://github.com/moe-berri/litellm-memory.git@main'
litellm-memory-connector connect https://your-litellm-memory.example --host codex
# Or --host claude; omit --host to configure both.
```

The browser opens Slack sign-in and a LiteLLM Memory consent screen. OAuth credentials are stored encrypted in a private local directory. The setup adds the remote MCP URL and reminder hooks without removing other MCPs or hooks. Private backups are made before host configuration changes. Restart the host and complete its own MCP authentication prompt. In Codex, open `/hooks` and review/trust the LiteLLM Memory hooks; Codex skips untrusted hooks. Review them again after an update changes their commands.

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

## What gets saved

SessionStart and UserPromptSubmit provide lightweight guidance to the coding agent. The importance threshold is low: current work, progress, attempted approaches and results, findings, decisions and reasons, blockers, next steps and corrections all count. The agent saves concise, self-contained memories through `record_work`, preserving attribution and uncertainty. These records are marked as agent-authored memories, not verbatim transcript evidence.

Acknowledgments, repetitions and turns with no useful new context can be skipped. There is no mandatory empty checkpoint or forced extra turn. Hooks never read a transcript, upload text, search memories or run a separate model. Retrieval happens only when requested. Actual saving depends on the host agent following the guidance and completing an authorized MCP call; the connector cannot guarantee complete coverage or model judgment.

The dashboard’s **Connections** page provides setup commands and checks persisted intake. Ask the agent to remember what you are working on to verify the first save. A connected credential is not proof of a trusted hook or successful MCP save, and an imported history is not proof of new recording. **Work received** means a nonhistorical memory was saved.

## Offline and paused behavior

Live memories are saved through MCP; if a save fails, the agent should report it rather than claim success. There is no automatic full-transcript fallback. Explicit history imports use an encrypted local outbox: records and cursors commit in one transaction, stable source IDs make retries safe, and `flush` retries failed uploads. Malformed records remain in a visible needs-attention queue.

```sh
litellm-memory-connector status
litellm-memory-connector flush
litellm-memory-connector pause
litellm-memory-connector resume
litellm-memory-connector disconnect
```

A 100 MB queue limit stops new buffering without deleting old queued records. `status` shows queued/needs-attention counts, timestamps and connection state. Local buffering is encrypted, but the same OS account has access to its local wrapping key; this is not protection against a compromised computer. The directory is bound to one installation and Slack identity so reconnecting as another person cannot upload the old person's buffered work.

Local `pause` changes hook guidance to tell the agent not to save automatically and stops import uploads. It is advisory for an already-running agent; pause in the dashboard or MCP to enforce the hosted intake boundary as well. Resume does not scan or automatically import skipped conversations. Disconnect removes LiteLLM Memory's managed hooks, MCP entry and optional global instructions, revokes the connector credential when online and pauses the local queue. If offline, revoke the connection in the dashboard too. Queued encrypted imports are retained locally, not uploaded to a replacement identity.

The dashboard/MCP pause is enforced by the API. It does not delete saved memories. Import buffering is local; use the local pause command to stop it too.

## Historical backfill

Choose the folders or files you actually intend to import:

```sh
litellm-memory-connector backfill ~/.codex/sessions --host codex
litellm-memory-connector backfill ~/.claude/projects --host claude
```

An explicit backfill imports visible transcript messages, rather than asking an agent to select useful memories. Work is attributed to its transcript role and original session/date. Imports remain private and replay is idempotent. Resume the connector before starting a backfill. Unknown dates remain unknown. System/developer text, tool results and hidden reasoning are excluded; images and attachments are not extracted. An assistant's statement is not proof of verified completion. Transcript formats can change, so unsupported formats are a coverage gap.

For the older product-team knowledge repository, the read-only preview/import adapter is:

```sh
uv run python -m litellm_memory.imports /path/to/private/snapshot --actor YOUR_GITHUB_ACTOR --manifest /private/path/manifest.json
```

Read its `--help` before upload. Preview output describes included/skipped records; exact original files and provenance are retained in encrypted evidence. An uploader is not necessarily the speaker. Do not include mixed/unknown-provenance shared notes without checking their source boundary. Keep the legacy MCP until explicit cutover, after the replacement's live capture, historical search and citations are verified.

References: [Codex hooks](https://developers.openai.com/codex/hooks/), [Claude Code hooks](https://code.claude.com/docs/en/hooks).
