# Release status

The implementation is on `main`. The existing hosted assistant has not been switched to the new app. This repository is still private; publishing and production cutover are separate release actions.

## Verification

CI runs unit/API tests, Ruff, frontend syntax checks, real Codex/Claude hook dispatch against disposable fixtures, and Docker/PostgreSQL/Ente build, restart and clean-volume restore. [Current checks](https://github.com/moe-berri/litellm-memory/pull/1/checks).

The core-loop revision passed [CI run 35648509804](https://github.com/moe-berri/litellm-memory/actions/runs/35648509804), source `0da818f`: **153 tests**, Ruff, four frontend syntax checks, both real host-hook checks, and Docker/PostgreSQL/Ente build, restart and clean-volume restore. Browser review uses synthetic records and simulated inference; [screenshots and review scope](ui-review.md). No synthetic check is a real-history quality score.

Frontend format checks also pass. Browser checks cover first-use intake status, person browsing and routes, query scope, privacy preview, saved-filter recovery and 390px/320px layouts. Automated daily overview sweeps are removed; source processing and explicit Ask remain.

## Before the owner-only pilot

- [ ] Enable TypeSafe Jev on the actual LiteLLM gateway and pass the typed probe. Last checked: gateway 1.102.0 returned 404; its key could not administer the missing route.
- [ ] Verify content logging suppression for chat, embeddings and TypeSafe calls on that gateway.
- [ ] Update and verify required Slack identity scopes, then complete personal Slack OAuth and hosted capture.
- [ ] Verify a personal DM and shared-channel audience checks using explicitly authorized test participants.
- [ ] Import approved owner history, reconcile original hashes/counts, and verify interrupted replay.
- [ ] Complete the reviewed [real-history answer-quality evaluation](answer-quality.md).
- [ ] Deploy `litellm_memory.application:app` through the portable launcher, initially owner-only and sharing-off. Verify capture → Ente → processing → explicit Ask → valid citation after restart.
- [ ] If telemetry is enabled, confirm a test trace arrives and inspect its attributes. Logfire cloud has not been connected by the repository's tests.

## Before publishing or team rollout

- [ ] Reconcile deployment/README branch references with the released revision.
- [ ] Size the instance/cache using representative history and concurrency.
- [ ] Review operator key custody and provider retention with the team; do not describe hosted processing as zero knowledge.
- [ ] Verify updated screenshots, setup, recovery and member documentation against the released build.
- [ ] Remove the isolated legacy assistant/runtime only after its replacement and historical import are proven. It remains for migration compatibility and is not the new app's memory backend.

The legacy product-team repository/MCP remains in place until cutover. No real teammate has been enrolled or real memory published by the synthetic checks.

## Known limits

Capture preserves individual visible transcript messages. Current/daily views synthesize them, but the feed is not yet organized into canonical session episodes. Tool execution and hidden reasoning are excluded, so recorded claims are not proof of completed work.

The user-defined Jev UI currently supports private-topic rules. Metadata browsing is separate; arbitrary saved semantic browsing criteria are not implemented.

The starter deployment is single-replica. Search uses a bounded plaintext RAM index over encrypted storage. Local synthetic measurements with 100,000 records / ten people and a 512 MB cache produced roughly **31 seconds cold, 0.4–0.6 seconds warm, and 40 seconds for four concurrent direct retrievals**, excluding inference and Ente network calls. Warm speed is not a production latency promise; cold loads and cache contention need further work for large teams.

Reproduce with `uv run python scripts/benchmark_search.py --records 100000 --people 10 --cache-mb 512 --parallel 4`. The benchmark uses temporary synthetic encrypted records and no external model calls.
