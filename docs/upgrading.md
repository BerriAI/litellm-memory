# Upgrading from litemem

The product is **LiteLLM Memory**, the repository and package are `litellm-memory`, and the Slack app is **LiteLLM Memory** with the bot handle **@memory**. It is a LiteLLM Labs product. This is a name change, not a new workspace: keep the existing database, keys, Slack application and access policies.

## Coding tools

Upgrade the connector from the new repository:

```sh
uv tool install --force 'git+https://github.com/moe-berri/litellm-memory.git@main'
litellm-memory-connector status
```

The new `litellm-memory-connector`, `litellm-memory-transfer` and `litellm-memory-evaluate` commands are canonical. The three `litemem-*` commands remain compatibility aliases. Re-run `litellm-memory-connector connect https://your-existing-host` to refresh host hooks, restart the coding tool, and review/trust the changed hooks in Codex.

If `~/.local/share/litemem` exists, all three tools continue using it, including its encrypted queue, credentials, evaluation runs, account binding and keys. Fresh installs use `~/.local/share/litellm-memory`. If both directories exist, the legacy directory wins; use `--directory` to explicitly select another. Do not merge directories or move them while hooks are running.

Existing MCP entries named `litemem` retain that name and their host OAuth configuration. New entries use `litellm-memory`. Reconnecting replaces old managed hooks without adding a second recorder. Disconnect recognizes both names and only removes MCP entries for the connected URL.

The Python package is now `litellm_memory`. New hooks and launch commands use it. A small `moegent` compatibility namespace resolves older module commands from the same source, so installed `python -m moegent.connector` hooks and existing application entry points keep working. Use the canonical package for new code; do not mix both import namespaces in one process.

## Hosted application

Keep the same `STATE_DIR`, database and secrets. Existing `/etc/secrets/litemem.env` and `/etc/secrets/moegent.env` files still load; `/etc/secrets/litellm-memory.env` is the new preferred name and takes precedence. Saved model configuration keeps its original storage key. Existing encrypted exports ending in `.litemem` still restore; new exports use `.litellm-memory`.

Fresh installs store query jobs and operational settings in `litellm-memory.sqlite3`. If `moegent.sqlite3` exists, it takes precedence, including when both filenames exist. The app uses it in place without moving a live SQLite database or losing its journal. Memory originals remain in the same Ente storage and `memory.sqlite3` index. Legacy browser storage and session-cookie names remain compatible; cached pages can still load the old icon URL.

### Docker Compose

New deployments use the `litellm-memory` project/service and `litellm_memory` database/user. For an existing deployment, first take a consistent backup using the old configuration. Stop the old application before starting the renamed service. Keep your existing `.env.selfhost`, including its password, and add these **non-secret** bindings, substituting your actual project and volume names:

```dotenv
COMPOSE_PROJECT_NAME=litemem
MEMORY_STATE_VOLUME=litemem_litemem-state
MEMORY_DB_NAME=litemem
MEMORY_DB_USER=litemem
```

For a standard previous installation, the PostgreSQL volume remains `litemem_postgres-data`. Verify the actual mounted volumes with `docker inspect` before upgrading; a custom project requires its own values. Then, from the new checkout:

```sh
docker compose --env-file .env.selfhost up --build --remove-orphans --wait -d
```

Do not use `down -v`. Changing a directory, project name or volume binding can select an empty volume. The self-host script detects the standard old state volume and stops until explicit bindings are provided. Use the same bindings for backups and restores; the new-install examples in the hosting guide use the new database/user names.

### Render

The renamed Blueprint is for new deployments. An existing service must retain its disk, database, environment and callback hostname. Update its connected GitHub repository to `moe-berri/litellm-memory` and deploy the code to the **existing service**. Do not apply the new resource names as a replacement Blueprint or create a second empty database. Existing hostnames can remain unchanged; a hostname migration requires updating Slack callbacks separately.

## Slack

Keep the current Slack app ID, installation, scopes and callback URLs. In Basic Information set the app name to **LiteLLM Memory**; in App Home set the bot display name/default username to **memory**. New manifests use these names automatically. Refresh any app-specific suggested prompts containing the old product name. Renaming does not enroll users, publish memories or change permissions.

## Observability

New traces use service `litellm-memory` and spans such as `litellm-memory.query`. Older traces remain under `litemem`; include both service names when comparing across the upgrade. Content logging remains disabled by design.

Old names in compatibility code, the isolated legacy deployment manifest, and original imported history are intentional. Historical records and Git history are not rewritten.
