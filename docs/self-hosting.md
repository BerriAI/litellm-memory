# Self-hosting litemem

## Deployment shape

The supported starter topology is one litemem web container with a durable `/data` volume and one PostgreSQL 17 service. The web container supervises the app and a private Ente Museum process on loopback port 9080. Only the web port is published. Museum stores encrypted entities in PostgreSQL; app state, wrapped keys, access policies, encrypted jobs and the encrypted local search source live on `/data`.

One app replica is intentional: SQLite and process-local job coordination require a single writer deployment. Do not scale this image to multiple replicas or attach its volume to competing app processes. Larger teams should size the instance from measured workloads; see the release checklist for current performance evidence.

## Render

The root `render.yaml` and `Dockerfile.litemem` provision the complete starter topology. Click the README deploy button, review the services and pricing, then deploy. Keep the generated `SESSION_SECRET` and `SETUP_CODE` in Render's secret environment. The app discovers its public URL from `RENDER_EXTERNAL_URL`.

Open the deployed HTTPS URL and paste the setup code to claim it. The code is for installation administration, not for member sign-in. It rotates when claimed. If setup is interrupted, the browser retains a time-limited setup session; otherwise retrieve the current code from the private `/data/setup-code` file in Render's shell.

The page keeps the gateway connection, model choices and Slack progress together. Provide only a LiteLLM URL/key for inference. The Slack app needs its normal Slack application credentials; litemem supplies a manifest/create link, validates the installation and returns to the same page. Complete sign-in using an eligible Slack account. Finish is enabled only after model, Jev, Slack identity and encrypted-storage checks pass.

Render auto-deploy is disabled in the reusable Blueprint so an upstream commit cannot update other people's installations without their decision. The current private pilot is separate from this reusable deployment and remains owner-only with sharing disabled.

## Docker / other hosts

Requirements: Docker Engine with Compose, OpenSSL and a POSIX shell. Run `./scripts/self-host.sh`; it creates `.env.selfhost` with a random database password and starts `compose.yaml`. It never overwrites an existing environment file. State is stored in named Docker volumes, not the checkout.

For Slack, configure a stable **HTTPS** public URL and put your normal TLS reverse proxy in front of `127.0.0.1:8000`. Edit `PUBLIC_BASE_URL` in `.env.selfhost` before Slack setup, then recreate the app with:

```sh
docker compose --env-file .env.selfhost up -d
```

Use the public URL consistently in the browser and Slack manifest. Cross-origin requests are rejected. Localhost HTTP is for local development; Slack cannot deliver production callbacks there.

The database port is not published. Museum is loopback-only. Do not expose Museum or PostgreSQL publicly, and do not add a public passwordless shortcut to setup.

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

Once saved in the web app, encrypted deployment settings take precedence over their initial environment defaults. Model changes are validated through LiteLLM and versioned. Slack/background inherit the default agent when not overridden. Embedding changes requeue indexing; reads continue using keywords until compatible vectors are available.

## External Ente

Set both `ENTE_BASE_URL` and `ENTE_AUTH_TOKEN` for a dedicated service account on a compatible Museum deployment. The app initializes the `memory` entity-key namespace through Ente's API, then stores per-member encrypted objects. Museum registration should be disabled after provisioning. You do not need Ente Photos, its web UI, or an object-storage bucket for litemem's entity API.

The app's root key and member-key envelopes must be backed up independently of Ente. Possession of Ente ciphertext alone is insufficient to recover memories.

## Backups, updates and recovery

Back up **both** `/data` and the Museum PostgreSQL database. The app's root key, encrypted deployment configuration, encrypted SQLite databases and member-key envelopes are required to recover. A database-only backup is incomplete. Keep backups encrypted with your infrastructure's backup encryption and restrict operator access.

For a consistent starter backup, stop the litemem service, back up its volume and PostgreSQL, then restart it. A restore must use matching application and Ente snapshots, preserve the root key and deployment secrets, and run on an isolated hostname with Slack callbacks/capture disconnected until verified. Preserve deletion tombstones; never restore an old snapshot over a live instance and assume revoked records remain revoked. The tested restore procedure is a release gate, not an assumed property of having backups.

Pin the code/image revision you deploy. Back up before updating, read migration notes, build the new image and recreate only the app. Never run `docker compose down -v` against an installation you want to keep: `-v` deletes its volumes. Changing the gateway key or rotating the root encryption key are different operations; do not replace a root key to rotate an API credential.

## Health and diagnostics

`GET /health` is a liveness check. Setup runs a real encrypted Ente round trip before activation. Connections shows revoked/active clients, and the connector's `status` reports offline backlog. Settings shows pending/failed processing and allows retries. A working health endpoint alone does not prove Jev, Slack or model access.

Operational tracing is off by default. Optional Logfire or OTLP spans record operation names, timing and success/failure, never source text, prompts, completions, tokens, exception text or stack variables. No cloud telemetry backend is required. Do not add unrestricted HTTP/model auto-instrumentation around the app.
