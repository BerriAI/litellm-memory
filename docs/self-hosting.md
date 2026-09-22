# Self-hosting LiteLLM Memory

## Deployment shape

The supported starter topology is one LiteLLM Memory web container with a durable `/data` volume and one PostgreSQL 17 service. The web container supervises the app and a private Ente Museum process on loopback port 9080. Only the web port is published. Museum stores encrypted entities in PostgreSQL; app state, wrapped keys, access policies, encrypted jobs and the encrypted local search source live on `/data`.

One app replica is intentional: SQLite and process-local job coordination require a single writer deployment. Do not scale this image to multiple replicas or attach its volume to competing app processes. Larger teams should size the instance from measured workloads; see the release checklist for current performance evidence.

The Render Blueprint sets `SEARCH_CACHE_MB=512`; Docker's portable default is 128 MB. This is a maximum index budget, not a startup allocation. Reserve memory for Python, Ente, SQLite and concurrent requests as well. A 30,000-record/10-person synthetic fixture used about 502 MB for the Python process with two queries; 100,000 owned records needed a 1,024 MB cache to avoid repeated rebuilds. At that size, warm retrieval measured about five seconds and cold retrieval about 54 seconds. These are current operating limits, not an instant-search promise for arbitrary archive size. Use [the benchmark and results](release-checklist.md#performance-measurements) before a large rollout.

With 100,000 records across ten people and half shared, the current 512 MB cache and indexed metadata scans measured 0.41–0.58 seconds warm and 31.18 seconds cold. Four direct concurrent retrievals still took 40.47–40.51 seconds while the cache filled and partitions rebuilt. The Python process peaked at 767 MB, excluding Museum and PostgreSQL. Warm retrieval is fast when the needed partitions fit; the starter topology is not validated for fast cold/concurrent search at that scale. Size and benchmark your actual sharing pattern before expanding beyond a small pilot.

## Render

The root `render.yaml` and `Dockerfile` provision the complete starter topology. Click the README deploy button, review the services and pricing, then deploy. Keep the generated `SESSION_SECRET` and `SETUP_CODE` in Render's secret environment. The app discovers its public URL from `RENDER_EXTERNAL_URL`.

Open the deployed HTTPS URL and paste the setup code to claim it. The code is for installation administration, not for member sign-in. It rotates when claimed. If setup is interrupted, the browser retains a time-limited setup session; otherwise retrieve the current code from the private `/data/setup-code` file in Render's shell.

The page keeps the gateway connection, model choices and Slack progress together. Provide only a LiteLLM URL/key for inference. Setup discovers the models available to that key, requires agent and embedding candidates, and makes real requests to verify tool calling, structured background generation, embeddings and TypeSafe Jev. A model appearing in the list alone is not enough to pass setup. Failed checks leave the workspace inactive and show what needs attention.

The Slack app needs its normal Slack application credentials; LiteLLM Memory supplies a manifest/create link, validates the installation and returns to the same page. Complete sign-in using an eligible Slack account. Activation requires model, Jev, Slack identity and encrypted-storage checks to pass.

Render auto-deploy is disabled in the reusable Blueprint so an upstream commit cannot update other people's installations without their decision. The current private deployment is separate from this reusable Blueprint. It permits verified `berri.ai` members of the configured Slack workspace to join; workspace sharing remains disabled.

## Docker / other hosts

Requirements: Docker Engine with Compose, OpenSSL and a POSIX shell. Run `./scripts/self-host.sh`; it creates `.env.selfhost` with a random database password and starts `compose.yaml`. It never overwrites an existing environment file. State is stored in named Docker volumes, not the checkout.

For Slack, configure a stable **HTTPS** public URL and put your normal TLS reverse proxy in front of `127.0.0.1:8000`. Edit `PUBLIC_BASE_URL` in `.env.selfhost` before Slack setup, then recreate the app with:

```sh
docker compose --env-file .env.selfhost up -d
```

Use the public URL consistently in the browser and Slack manifest. Cross-origin requests are rejected. Localhost HTTP is for local development; Slack cannot deliver production callbacks there.

The database port is not published. Museum is loopback-only. Do not expose Museum or PostgreSQL publicly, and do not add a public passwordless shortcut to setup.

## Connect TypeSafe Jev through LiteLLM

Jev's built-in pass-through integration was introduced in **LiteLLM v1.103.0-rc**. A gateway on an older release can serve chat and embeddings normally while returning HTTP 404 for Jev. See the [launch announcement](https://docs.litellm.ai/blog/typesafe_jev) and [integration instructions](https://docs.litellm.ai/docs/pass_through/typesafe).

The gateway operator must deploy a release containing the integration and configure `TYPESAFE_API_KEY` on the **gateway**. This can be a secret deployment environment variable followed by a restart. On gateways supporting the admin configuration API, an administrator can instead send `POST /config/update` with only `{"environment_variables":{"TYPESAFE_API_KEY":"<provider key>"}}`; LiteLLM stores the value encrypted and reloads its configuration. Preserve all other settings. A successful typed probe must confirm that the live workers picked up the credential. The TypeSafe provider key is not a LiteLLM virtual key and does not belong in LiteLLM Memory's connection form or repository. The built-in TypeSafe route does not require another model or custom endpoint.

LiteLLM Memory uses its existing LiteLLM key to call `POST /typesafe/v1/systemone` with a harmless typed question. A successful typed response is the readiness check; absence from `/v1/models` does not mean Jev is unsupported. On HTTP 404, setup also tries to show the gateway's reported version. Version discovery is optional and never replaces the real probe, so custom builds and backports can work.

After enabling the integration, select **Check again** on the same setup page. If the route still returns 404, check gateway reverse-proxy routing and the configured Jev alias. HTTP 401/403 requires checking the LiteLLM key's route permissions and the gateway's upstream credentials. When TypeSafe reports its own authentication error, setup identifies the gateway's provider credential rather than labelling the LiteLLM key invalid. Verify the deployed pass-through's no-log behavior before processing real histories; a successful classification alone does not prove logging is disabled.

An installed Slack app can still be missing required permissions. Setup shows **Update Slack access** when the recorded installation lacks any required scope, and prevents activation until the updated installation completes. Approve it with the workspace's eligible installer account, then complete **Confirm with Slack**. The app checks current membership and the verified company email domain before enrollment.

## Configuration

Most configuration is completed in the app and stored encrypted. Deployment environment options include:

| Variable | Purpose |
| --- | --- |
| `PUBLIC_BASE_URL` | Stable external HTTPS origin; Render supplies its URL automatically. |
| `STATE_DIR` | Durable directory; `/data` in the image. |
| `DATABASE_URL` | PostgreSQL for bundled Ente. Render supplies it; Compose constructs it. |
| `SESSION_SECRET` | Stable random signing secret. Generated on disk if absent. |
| `SETUP_CODE` | Initial deployment claim code. Generated privately if absent. |
| `ALLOWED_EMAIL_DOMAIN` | Initial eligible domain; defaults to `berri.ai`. Setup can set another. |
| `REGISTRATION_MODE` | `domain` for eligible teammates, `owner_only` for an isolated pilot. |
| `SHARING_ENABLED` | Initial company sharing switch; defaults false. Admins can enable it in Settings. |
| `ENTE_BASE_URL`, `ENTE_AUTH_TOKEN` | Use an existing compatible Museum instead of the bundled process. |
| `VAULT_WRAPPING_KEY` | Optional base64 32-byte root key; otherwise generated in `/data/vault-wrapping-key`. |
| `OBSERVABILITY_EXPORT` | `none` (default), `otlp`, or `logfire`. |
| `OTLP_TRACES_ENDPOINT` | Your OTLP HTTP trace collector, only with `otlp`. |
| `LOGFIRE_TOKEN` | Optional Logfire cloud credential, only with `logfire`. |
| `SEARCH_CACHE_MB` | Bounded in-memory search index budget, 128 MB by default; size from the measurements below. |
| `MAX_PENDING_QUERIES_PER_MEMBER` | Maximum queued/running questions per person, 4 by default. |
| `MAX_PENDING_QUERIES` | Maximum pending questions installation-wide, 100 by default. Two agent runs execute at once across interfaces. |
| `RECOVERY_MODE` | Set `true` before first boot of an installation restore: disables workers and all HTTP interfaces except liveness. |

Once saved in the web app, encrypted deployment settings take precedence over their initial environment defaults. Model changes are validated through LiteLLM and versioned. Slack/background inherit the default agent when not overridden. Embedding changes requeue indexing; reads continue using keywords until compatible vectors are available.

In **Settings → Models → LiteLLM connection**, an administrator can rotate the existing gateway's key. All configured roles and Jev must pass with the new key before it replaces the old one. The key is never returned to the browser. Changing to a different gateway is an installation migration; it is not a silent change to the meaning of existing model routes.

Domain-mode workspaces can transfer administration to another enrolled, currently eligible Slack member from Settings. This transfers workspace configuration authority, without granting access to private memories. The owner-only pilot disables this control.

## Dates and on-demand overviews

New records are processed continuously. Current or daily overviews are built only when requested through Ask or the overview API; no periodic history sweep or external cron is required. Existing source records and saved views remain available.

Personal display timezones are controlled in **Settings → Preferences**, persist in encrypted member profiles and affect presentation/date filters only. Requested daily overviews use the workspace timezone chosen during setup. Legacy schedule settings remain readable through the compatibility API, with automatic refresh disabled. Timezone data is included in the image.

## External Ente

Set both `ENTE_BASE_URL` and `ENTE_AUTH_TOKEN` for a dedicated service account on a compatible Museum deployment. The app initializes the `memory` entity-key namespace through Ente's API, then stores per-member encrypted objects. Museum registration should be disabled after provisioning. You do not need Ente Photos, its web UI, or an object-storage bucket for LiteLLM Memory's entity API.

The app's root key and member-key envelopes must be backed up independently of Ente. Possession of Ente ciphertext alone is insufficient to recover memories.

## Backups, updates and recovery

Back up **both** `/data` and the Museum PostgreSQL database. The app's root key, encrypted deployment configuration, encrypted SQLite databases and member-key envelopes are required to recover. A database-only backup is incomplete. Keep backups encrypted with your infrastructure's backup encryption and restrict operator access.

For a consistent starter backup, stop the LiteLLM Memory service, back up its volume and PostgreSQL, then restart it. A restore must use matching application and Ente snapshots, preserve the root key and deployment secrets, and run on an isolated hostname with `RECOVERY_MODE=true` until verified. This prevents queued Slack/model work from restarting. Preserve deletion tombstones; never restore an old snapshot over a live instance and assume revoked records remain revoked. The clean-volume restore is tested in [CI](https://github.com/BerriAI/litellm-memory/actions/runs/35532804507).

For Compose, with a protected backup directory prepared:

```sh
umask 077
docker compose --env-file .env.selfhost stop litellm-memory
docker compose --env-file .env.selfhost exec -T postgres pg_dump -U litellm_memory -d litellm_memory -Fc > /private/backup/ente.dump
docker compose --env-file .env.selfhost run --rm --no-deps -T litellm-memory sh -c 'tar -cf - -C /data .' > /private/backup/state.tar
docker compose --env-file .env.selfhost up -d --wait
```

Encrypt the resulting backup files using your backup system. The tar includes root keys and is not protected merely because records inside SQLite are encrypted. Restore into a **new project/volumes**, with the original release checked out and a private environment file configured for the new database, isolated URL and `RECOVERY_MODE=true`:

```sh
docker compose --env-file .env.restore -p litellm-memory-restore build litellm-memory
docker compose --env-file .env.restore -p litellm-memory-restore up -d --wait postgres
docker compose --env-file .env.restore -p litellm-memory-restore exec -T postgres pg_restore -U litellm_memory -d litellm_memory < /private/backup/ente.dump
docker compose --env-file .env.restore -p litellm-memory-restore run --rm --no-deps -T litellm-memory sh -c 'tar -xf - -C /data' < /private/backup/state.tar
docker compose --env-file .env.restore -p litellm-memory-restore up -d --wait
```

Use a separate host or a different host port so the restored service does not compete with the live service. `/health` must report `recovery`. Verify both databases, key availability, known evidence and deletion tombstones through a private operator shell; reconcile changes since the backup before cutover. Only then set `RECOVERY_MODE=false`, recreate the service and restore callbacks/capture. The disposable synthetic proof in `tests/smoke_bundle.py` is for CI, not a script to run against real users' databases.

Pin the code/image revision you deploy. Back up before updating, read migration notes, build the new image and recreate only the app. Never run `docker compose down -v` against an installation you want to keep: `-v` deletes its volumes. Changing the gateway key or rotating the root encryption key are different operations; do not replace a root key to rotate an API credential.

## Health and diagnostics

`GET /health` is a liveness check. Setup runs a real encrypted Ente round trip before activation. Connections shows revoked/active clients, and the connector's `status` reports offline backlog. Settings shows pending/failed processing and allows retries. A working health endpoint alone does not prove Jev, Slack or model access.

Operational tracing is off by default. [Set up Pydantic Logfire or an OTLP collector](observability.md) to see request channels, models, tool calls, timing, outcomes and reported token counts. Private text, credentials and identities are excluded. No cloud telemetry backend is required.

## Personal portability

The normal path is **Settings → Your data → Transfer history**. Members enter the destination's HTTPS origin, sign in with the same Slack account there, and approve both ends in one browser tab. No file password or manually copied key is needed. The destination verifies the encrypted transfer before importing private historical records. It preserves source identities, original text, dates and metadata; derived summaries, classification and embeddings are rebuilt with the destination's configured models. It never copies sharing grants or auto-publishes history. [Member walkthrough](member-guide.md#move-or-download-your-history).

Uploads must arrive within 30 minutes. Processing then runs durably for up to 24 hours and resumes after restart; paused recording or unavailable membership/storage checks defer it. Settings links to recent imports. Keep the entire persistent app directory in backups, including `transfers.sqlite3`; recovery mode keeps its worker stopped. Completion/cancellation removes temporary ciphertext and receiving key material. Existing deletions/conflicts are excluded, and interrupted replay deduplicates by original source identity.

Limits are two active transfers per member, 100,000 records and 512 MB of staged ciphertext per transfer, and 1 GB of staging per installation. Budget free disk space for SQLite WAL and both staging and the resulting archive. Larger archives, changed Slack identities or offline destinations use the file/CLI path below. Source copies are retained; moving history is not an installation shutdown or deletion procedure.

Members can also download an encrypted export or readable JSONL. The optional encrypted file has its own passphrase, independent of Slack sign-in. It contains original evidence and metadata. With the connector signed into the destination account, use:

```sh
litellm-memory-transfer export my-memory-export.litellm-memory
litellm-memory-transfer restore my-memory-export.litellm-memory
```

Passwords are entered interactively, never command-line arguments. Restore verifies the complete authenticated file before uploading, resumes safely by original source identity, keeps imports private and respects destination deletion tombstones. A different Slack identity requires an explicit `--allow-owner-remap`; review attribution before using it. If capture is paused, restore also remains paused until the member resumes it.
