# Codex, Claude Code, and historical capture

The remote MCP exposes explicit record/search/read/ask/status/pause tools. It cannot automatically observe an entire host conversation. Continuous recording is provided by the optional `litellm-memory-connector` host hooks.

## Connect

Install from the release branch while this project is in verification:

```sh
uv tool install --force 'git+https://github.com/moe-berri/litellm-memory.git@main'
litellm-memory-connector connect https://your-litellm-memory.example --host codex
# Or --host claude; omit --host to configure both.
```

The browser opens Slack sign-in and a LiteLLM Memory consent screen. OAuth credentials are stored encrypted in a private local directory. The setup adds the remote MCP URL and capture hooks without removing other MCPs or hooks. Private backups are made before host configuration changes. Restart the host and complete its own MCP authentication prompt. In Codex, open `/hooks` and review/trust the LiteLLM Memory capture hooks; Codex skips untrusted hooks. Review them again after an update changes their commands.

Supported installation locations are `~/.codex/config.toml`, `~/.codex/hooks.json`, `~/.claude/settings.json` and `~/.claude.json`. Symlink-managed host configurations need manual integration. The initial connector supports macOS/Linux and Python 3.12+. Windows support has not been verified.

Hooks use SessionStart, UserPromptSubmit, PostToolUse and Stop to ingest newly appended complete transcript records. They do not perform model inference, remote search or recall injection. The transcript adapters accept visible user/assistant text and exclude system/developer text, tool outputs and hidden reasoning. Images and attachments are not extracted. Codex explicitly treats its transcript format as unstable, so verify connector status after host updates; unsupported input should be treated as a coverage gap, not proof of complete capture.

The dashboard’s **Connections** page provides the same commands for your selected tool and checks for persisted intake. A connected credential is not proof of a trusted hook, and an imported history is not proof of new recording. **Work received** means a nonhistorical record was saved; use the local `status` command to diagnose buffering.

## Offline and paused behavior

The connector commits encrypted source records and its cursor in one local SQLite transaction. Stable source IDs make interrupted uploads safe to retry. Completed receipts prevent duplicate transmission. Failed uploads remain buffered and retry on a later hook or `flush`; records rejected as malformed remain in a visible needs-attention queue. OAuth refresh rotation is serialized across hook processes.

```sh
litellm-memory-connector status
litellm-memory-connector flush
litellm-memory-connector pause
litellm-memory-connector resume
litellm-memory-connector disconnect
```

A 100 MB queue limit stops new buffering without deleting old queued records. `status` shows queued/needs-attention counts, timestamps and connection state. Local buffering is encrypted, but the same OS account has access to its local wrapping key; this is not protection against a compromised computer. The directory is bound to one installation and Slack identity so reconnecting as another person cannot upload the old person's buffered work.

Live capture starts with text timestamped after connection or the latest resume. Paused hooks advance past visible work without recording it. The timestamp boundary also excludes paused text when no hook ran during the pause or a transcript is discovered later. Undated live messages are skipped with a coverage warning because the connector cannot establish when they were written; an explicit historical backfill can import them. Previously buffered work stays queued and can sync after resume. Disconnect removes only LiteLLM Memory's managed hooks/MCP entry, revokes its capture credential when online and pauses the local queue. If offline, revoke the connection in the dashboard too. Queued encrypted work is retained locally, not uploaded to a replacement identity.

Pausing in the dashboard or MCP stops hosted intake. A computer can still buffer locally while offline or disconnected from that control; use `litellm-memory-connector pause` to stop local buffering too. After a dashboard resume, the server discards delayed automatic captures dated before that resume without storing their content. The connector removes those queued copies and reports `skipped_after_pause`. An explicit historical backfill can still import them later. Saving an already-enabled recording preference does not move the boundary.

## Historical backfill

Choose the folders or files you actually intend to import:

```sh
litellm-memory-connector backfill ~/.codex/sessions --host codex
litellm-memory-connector backfill ~/.claude/projects --host claude
```

Work is attributed to its transcript role and original session/date. Historical imports remain private. Import uses the same source identity and encrypted queue as live capture, so replay is idempotent. Its separate cursor permits explicitly importing text previously skipped by live capture. Resume the connector before starting a backfill. It does not infer that an assistant's statement is verified completion. Unknown dates remain unknown.

For the older product-team knowledge repository, the read-only preview/import adapter is:

```sh
uv run python -m moegent.imports /path/to/private/snapshot --actor YOUR_GITHUB_ACTOR --manifest /private/path/manifest.json
```

Read its `--help` before upload. Preview output describes included/skipped records; exact original files and provenance are retained in encrypted evidence. An uploader is not necessarily the speaker. Do not include mixed/unknown-provenance shared notes without checking their source boundary. Keep the legacy MCP until explicit cutover, after the replacement's live capture, historical search and citations are verified.

References: [Codex hooks](https://developers.openai.com/codex/hooks/), [Claude Code hooks](https://code.claude.com/docs/en/hooks).
