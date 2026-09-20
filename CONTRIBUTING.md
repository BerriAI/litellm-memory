# Contributing to litemem

litemem is a LiteLLM Labs work-history product built with Ente and LiteLLM. Contributions should preserve simple setup, source evidence and clear privacy boundaries.

## Local development

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
node --check static/memory.js
node --check static/setup.js
node --check static/migration.js
```

The unit/API suite uses temporary synthetic data and mocked external services. It does not need Slack, Ente or model credentials. Docker deployment tests use disposable PostgreSQL/Ente containers; see `.github/workflows/verify.yml`. Use the documented Compose deployment for a complete local installation, with a separate test Slack workspace and gateway when exercising external integrations.

## Where to work

- `moegent/application.py`: web API, identity and authorization boundaries.
- `moegent/archive.py`, `crypto.py`, `ente.py`: encrypted originals, per-person keys, publications and lifecycle.
- `gateway.py`, `model_settings.py`, `processing.py`: LiteLLM discovery, model settings and cached Jev classification.
- `query.py`, `memory_slack.py`, `mcp_api.py`: query agent and interfaces.
- `connector.py`, `imports.py`, `transfer.py`, `migration.py`: capture, historical imports, encrypted files and temporary recipient-key browser transfers.
- `static/`: the dashboard and progressive setup flow, without a frontend build step.

The internal package name `moegent` is retained for migration compatibility. The original `moegent/app.py` is the legacy assistant, not the new application entry point.

## Review expectations

Explain the user-visible problem, the resulting behavior, and relevant checks. Add focused tests for authorization, data integrity, retry behavior or state transitions that your change affects. Check meaningful interface changes in a browser, including narrow layouts and keyboard navigation. Do not substitute synthetic tests for claims of live integration or measured answer quality.

Keep originals immutable and preserve dates, roles and provenance. Retrieval is explicit, not injected on every coding turn. Never send private originals into a public-summary query path. A successful model response does not prove its claims, and cached classifications must be keyed to the source, criterion and version.

Do not commit histories, credentials, local state, exports or real-user fixtures. Run live imports, sharing changes and Slack sends only against explicitly authorized accounts. Do not add unrestricted HTTP/LLM instrumentation: operational observability must remain content-free. Report vulnerabilities privately to the repository owner.

Update relevant documentation when behavior changes. Changes are contributed under the repository's AGPL-3.0 license. See [the architecture](docs/architecture.md), [security model](docs/security.md), [product specification](docs/product-implementation-spec.md), and [release evidence](docs/release-checklist.md).
