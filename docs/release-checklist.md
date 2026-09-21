# Release status

The implementation is on `main`, and the private Render service runs the new application through the portable launcher. Admin onboarding is not yet complete. This repository is still private; owner onboarding, team rollout and open-source publication are separate milestones.

## Verification

CI runs unit/API tests, Ruff, frontend syntax checks, real Codex/Claude hook dispatch against disposable fixtures, and Docker/PostgreSQL/Ente build, restart and clean-volume restore. [Current checks](https://github.com/moe-berri/litellm-memory/actions/workflows/verify.yml).

The core-loop revision passed [CI run 35648509804](https://github.com/moe-berri/litellm-memory/actions/runs/35648509804), source `0da818f`: **153 tests**, Ruff, four frontend syntax checks, both real host-hook checks, and Docker/PostgreSQL/Ente build, restart and clean-volume restore. Browser review uses synthetic records and simulated inference; [screenshots and review scope](ui-review.md). No synthetic check is a real-history quality score.

Frontend format checks also pass. Browser checks cover first-use intake status, person browsing and routes, query scope, privacy preview, saved-filter recovery and 390px/320px layouts. Automated daily overview sweeps are removed; source processing and explicit Ask remain.

The September 21 readiness revision passes **171 local tests** and Ruff; frontend syntax checks pass. Setup checks Jev, tool calling, generation of the actual work-record schema and embeddings through the configured gateway; regression cases cover absent agent/embedding routes, upstream TypeSafe authentication errors, retryable service failures, invalid structured output and a JSON-mode failure found with the live Luna route. Slack permission recovery was reviewed at desktop and 390px widths, including keyboard access and browser errors. [PR #7](https://github.com/moe-berri/litellm-memory/pull/7) passed all CI jobs and deployed as `7b17fa7`.

A synthetic integration run on deployed `7b17fa7` inside Render passed capture, parallel processing, cached Jev classification, encrypted Ente storage, a private-topic exclusion, denial of peer access to originals, a cited Luna answer and preserved originals after reopening its temporary archive. Two malformed summaries recovered through normal processing retries. Jev also briefly failed during verification, then recovered without a credential change; its cause was not established. The final run used actual gateway/Ente services and the deployed code; all five temporary encrypted objects were deleted. It does not establish hosted Slack authentication, real-history answer quality or complete gateway logging suppression.

## Before the owner-only pilot

- [x] Enable TypeSafe Jev on the actual LiteLLM gateway and pass the typed probe. On September 21, gateway 1.103.0 returned `jev-1.13.0` through the deployment's existing LiteLLM key after its TypeSafe credential was saved using the admin configuration API.
- [ ] Verify content logging suppression for chat, embeddings and TypeSafe calls on that gateway.
- [ ] Update and verify required Slack identity scopes, then complete personal Slack OAuth and hosted capture. The installed token authenticates, but its live `users.info` check reports `missing_scope`; `users:read` and `users:read.email` must be approved through the installation flow.
- [ ] Verify a personal DM and shared-channel audience checks using explicitly authorized test participants.
- [ ] Import approved owner history, reconcile original hashes/counts, and verify interrupted replay.
- [ ] Complete the reviewed [real-history answer-quality evaluation](answer-quality.md).
- [x] Deploy `litellm_memory.application:app` through the portable launcher, initially owner-only and sharing-off. The live service reports Ente storage; an encrypted write/read/delete round trip from the Render instance passed on September 21.
- [ ] Verify authenticated hosted capture → Ente → processing → explicit Ask → valid citation after restart.
- [ ] If telemetry is enabled, confirm a test trace arrives and inspect its attributes. Logfire cloud has not been connected by the repository's tests.

## Before publishing or team rollout

- [ ] Reconcile deployment/README branch references with the released revision.
- [ ] Size the instance/cache using representative history and concurrency.
- [ ] Review operator key custody and provider retention with the team; do not describe hosted processing as zero knowledge.
- [ ] Verify updated screenshots, setup, recovery and member documentation against the released build.
- [ ] Remove the isolated legacy assistant/runtime only after its replacement and historical import are proven. It is not the new app's memory backend.

The legacy product-team repository/MCP remains in place until cutover. No real teammate has been enrolled or real memory published by the synthetic checks.

## Known limits

Capture preserves individual visible transcript messages. Current/daily views synthesize them, but the feed is not yet organized into canonical session episodes. Tool execution and hidden reasoning are excluded, so recorded claims are not proof of completed work.

The user-defined Jev UI currently supports private-topic rules. Metadata browsing is separate; arbitrary saved semantic browsing criteria are not implemented.

The starter deployment is single-replica. Search uses a bounded plaintext RAM index over encrypted storage. Local synthetic measurements with 100,000 records / ten people and a 512 MB cache produced roughly **31 seconds cold, 0.4–0.6 seconds warm, and 40 seconds for four concurrent direct retrievals**, excluding inference and Ente network calls. Warm speed is not a production latency promise; cold loads and cache contention need further work for large teams.

Reproduce with `uv run python scripts/benchmark_search.py --records 100000 --people 10 --cache-mb 512 --parallel 4`. The benchmark uses temporary synthetic encrypted records and no external model calls.
