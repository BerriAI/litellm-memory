# litemem

**Your team's work, with a memory.** A [LiteLLM Labs](https://github.com/BerriAI) product, built with [Ente](https://github.com/ente-io/ente) and [LiteLLM](https://github.com/BerriAI/litellm).

litemem records the work people do with AI and makes it easy to ask about later. Connect Codex or Claude Code, browse your private activity, share selected summaries with your company, and ask questions in the web app or through **@litemem** in Slack.

This is a work archive you query deliberately. It does not search old memories or inject context into every coding turn.

> **Release status:** the implementation is on `codex/litemem-implementation`. Deployment verification is in progress. The existing hosted assistant has not been switched to this application. See the [release checklist](docs/release-checklist.md) for verified behavior and remaining gates.

## What it does

- A chronological activity feed, Slack profiles, private lock indicators, and an **Ask** interface with citations.
- Continuous capture with an encrypted local outbox; optional historical backfills preserve source text, original dates and metadata.
- Plain-language work updates, current-work views, and daily catch-ups with source links and coverage information.
- Private by default. Members can preview a shared summary, choose shareable types, and keep topics private. Company-public never means internet-public.
- The same authorized query agent through the web, Slack, API and remote MCP. Shared Slack conversations use only company-public evidence safe for the conversation's participants.
- All inference through **your LiteLLM gateway**. Model pickers use its `/v1/models`; agent, Slack, background, embedding and classification models are configurable.
- TypeSafe **Jev** classifies source chunks in parallel. Results are cached per source/criterion/version. Changing a filter does not rerun completed classification.

## Deploy

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/moe-berri/litemem/tree/codex/litemem-implementation)

The Blueprint provisions the web app, a persistent volume and PostgreSQL. Ente Museum runs privately alongside the app and is provisioned automatically. Review Render's charges before creating the deployment; durable hosting is not a free-tier promise. This repository currently requires GitHub access. Once published, others can fork and deploy it with their own infrastructure and gateway.

**On any Docker host:**

```sh
git clone --branch codex/litemem-implementation https://github.com/moe-berri/litemem.git
cd litemem
./scripts/self-host.sh
```

Open the address shown by your deployment. Setup stays on one page:

1. Claim the installation using its deployment setup code.
2. Connect your **LiteLLM URL and API key**. litemem discovers models, suggests defaults, and checks Jev through the gateway.
3. Connect Slack and finish with your verified company identity. Slack returns you to the same setup page.

Members then use **Sign in with Slack**. They do not handle encryption keys, recovery phrases or a separate litemem password. Eligibility is restricted to the configured Slack workspace and exact verified email domain; `berri.ai` is the initial default and self-hosters choose their own domain.

**Jev is required.** If your gateway does not support it, setup explains the missing capability and retains your inputs. Enable the [LiteLLM TypeSafe integration](https://docs.litellm.ai/docs/pass_through/typesafe) on the gateway. litemem never asks members for a TypeSafe key or bypasses the gateway.

Read [self-hosting](docs/self-hosting.md) for HTTPS, backups, updates, external Ente and configuration.

## Connect your tools

Open **Connections → Set up continuous capture** in litemem. On the computer running your coding tools:

```sh
uv tool install 'git+https://github.com/moe-berri/litemem.git@codex/litemem-implementation'
litemem-connector connect https://your-litemem.example
```

Sign in with Slack in the browser. The connector preserves existing host settings and memory connections. Restart your coding tool, then authenticate its litemem MCP when prompted. Use `--host codex` or `--host claude` to configure only one.

The connector records visible user and assistant text. It excludes system messages, tool-result dumps and hidden reasoning. Local capture requires a compatible host with transcript hooks; remote MCP alone cannot observe conversations. The website and Slack work without installing the connector.

[Connector guide](docs/connectors.md) · [API and data formats](docs/api.md)

## How privacy works

litemem encrypts persisted records with Ente-compatible libsodium encryption and stores ciphertext in Ente Museum. Each member has a separate wrapped key. Original evidence, derived records, public summaries, profiles, jobs and configuration are encrypted at rest; search indexes live in bounded process memory.

**The hosted application is a trusted processor.** It can decrypt authorized work to search it and call the LiteLLM gateway. A person controlling the runtime and its keys could also do that. This is not Ente Photos' zero-knowledge client model, and we do not claim protection against a malicious deployment operator.

Normal administrator interfaces cannot inspect someone else's private records. API authorization is checked before decryption and again before delivering agent output. Publishing shares a separate summary, while the original evidence stays private. Requests ask LiteLLM to suppress content logging, and agent tracing is disabled; the gateway and model provider still need appropriate retention settings.

Read the [security and privacy model](docs/security.md) before operating an installation.

## Develop

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
```

The new app is `moegent.application:app`; the portable launcher is `python -m moegent.boot`. The Python package retains its original internal name to make migration easier. `moegent.app:app`, the original `Dockerfile`, and the vendored knowledge runtime are legacy migration paths, not dependencies of the new memory service.

[Architecture](docs/architecture.md) · [Product specification](docs/product-implementation-spec.md) · [Release checklist](docs/release-checklist.md)

## Open source

litemem is licensed under **AGPL-3.0**. The bundled Ente Museum executable is unmodified and also AGPL-3.0; see [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). The self-hosted core needs no proprietary litemem control plane. Hosting and model inference may have costs.

Code belongs here; personal histories, credentials, deployment state and database exports do not. Keep those out of Git, even in a private fork.
