# Release evidence and remaining gates

This file distinguishes implementation from verified deployment. It is not a claim that the full pilot has been released.

## Verified on September 20, 2026

- The implementation branch's initial suite passed **74 tests**, Ruff and JavaScript syntax checks locally and on Linux CI.
- [CI run 35532804507](https://github.com/moe-berri/litemem/actions/runs/35532804507) subsequently passed **83 tests** and a complete Docker backup/restore into fresh PostgreSQL and app volumes. The restored installation preserved member keys, exact evidence and deletion tombstones.
- [CI run 35533964981](https://github.com/moe-berri/litemem/actions/runs/35533964981) passed **88 tests** and the full Docker restore again, including recovery-mode denial of Slack/MCP before explicitly resuming the restored synthetic instance. Admin key rotation/transfer, shared query capacity and the Slack partial-profile correction passed. Current branch checks are visible on [draft PR #1](https://github.com/moe-berri/litemem/pull/1).
- [CI run 35530917378](https://github.com/moe-berri/litemem/actions/runs/35530917378) built the actual Docker/Compose stack, provisioned PostgreSQL + pinned Ente Museum, verified ciphertext write/read/decrypt/delete, checked closed registration and setup access, restarted the app, and passed again with persistent credentials.
- Actual separate Ente storage in the private Render pilot also passed encrypted round-trip/deletion checks after bootstrap registration was disabled. The old hosted app remains the running entry point.
- The real MCP Python SDK connected to the new ASGI service, recorded/searched/read owned evidence and denied cross-account reads and revoked tokens.
- Desktop and 390px mobile browser checks showed the activity feed, private locks, catch-up panel and all model settings without console errors. These used isolated synthetic data.
- Connector tests cover partial transcript rows, role filtering, encrypted local queueing, interruption/replay, token rotation, offline retries, pause, configuration preservation and account-safe connection behavior.
- [CI run 35534905557](https://github.com/moe-berri/litemem/actions/runs/35534905557) passed **89 tests**, full Docker restore, and actual Codex **0.155.1** / Claude Code **2.1.278** hook dispatch. Both CLIs produced and uploaded visible user and assistant messages through the installed connector in disposable accounts. Model responses and the upload endpoint were loopback fixtures; this does not verify personal Slack OAuth or live Jev. Codex's one-run hook trust bypass was limited to that empty CI account, not installed on a user's computer.
- [CI run 35535349622](https://github.com/moe-berri/litemem/actions/runs/35535349622) passed **92 tests**, both real-host checks and the full Docker restore on source commit `ef66b07`. The stricter host checks now require exactly the visible prompt and reply, excluding Codex's injected user-role context. Regression tests cover local and dashboard pause/resume, offline delayed intake, and subsequent explicit historical import. The setup page and CLI share one Slack manifest with the agent interface and membership scopes.
- Read-only inspection of the existing Slack app confirmed that `users:read` and `users:read.email` are absent from its current OAuth scope list. It must be updated/reinstalled before the new membership checks can work; no Slack permissions were changed during this inspection.
- The local suite now passes **101 tests**, Ruff and all three frontend module checks. Nine transfer tests cover recipient-bound packets, exact evidence/date preservation, restart resume, ownership/CORS/capability isolation, manifest verification, tombstones, pause, departure, expiry, storage bounds, cancellation races and CSP origin validation. A two-installation browser test copied one synthetic record through the single-tab flow without a password, then showed its original September 18 date and private status at the destination. This is synthetic evidence, not a transfer of real member history.
- [CI run 35537465423](https://github.com/moe-berri/litemem/actions/runs/35537465423) passed **101 tests**, both real CLI hook checks, and the complete Docker build/restart/recovery/restore on source commit `8d5653e`. The clean-volume restore now also verifies that a staged transfer's ciphertext and encrypted recipient key remain usable after recovery.
- Privacy tests cover owner/public/workspace boundaries, distinct public summaries/vectors, live policy changes, private topic rules, cached Jev work, automatic type sharing and nonpublication of historical imports.
- New optional Logfire instrumentation passed a canary test that exception messages/private input do not appear in exported spans. Full-suite validation after subsequent changes remains required.

## Performance measurements

These are local synthetic measurements on the developer's Apple Silicon machine with 768-dimensional vectors, excluding inference and Ente network calls. They are not production p95/SLA claims.

| Fixture | Cache budget | Cold | Warm | Process peak |
| --- | --- | --- | --- | --- |
| 10,000 owned records, initial implementation | 128 MB | 3.64 s | 0.40 s | 183 MB |
| 100,000 owned records, undersized cache | 128 MB | 58.34 s | 50.74–52.68 s | 194 MB |
| 100,000 owned records, covering metadata index | 1,024 MB | 54.34 s | 4.74–5.06 s | 932 MB |
| 30,000 records / 10 people, half shared | 256 MB | 11.69 s | 0.25–0.83 s | 410 MB |
| 100,000 records / 10 people, half shared | 512 MB | 36.34 s | 7.76–9.18 s | 629 MB |

Two simultaneous queries by different people in the 30,000-record fixture completed in 2.51 and 2.52 seconds, including loading the second person's private partitions; peak memory was 502 MB. Access assertions rejected other people's private results. A visible working set larger than the cache repeatedly rebuilds its encrypted partitions and is substantially slower. The Render Blueprint uses a 512 MB cache; the smaller Docker default is 128 MB. Larger archives need sizing from actual records and acceptable cold-start latency.

Four direct retrieval calls by different people in the 100,000-record/10-person fixture completed in **46.50–46.54 seconds**. The 512 MB cache filled and cumulative partition builds rose from 2,560 to 5,709; process peak was 629 MB. This stress test bypasses the normal two-agent execution cap and excludes model calls. It shows poor concurrent retrieval at this size, not a production latency guarantee. A larger cache or stronger instance needs its own measurement before a team-scale rollout.

Reproduce multi-person measurements with `uv run python scripts/benchmark_search.py --records 30000 --people 10 --cache-mb 256 --parallel 2`. The fixture uses temporary encrypted data and makes no external calls. It measures retrieval, not answer quality or full model latency.

Reproduce the larger stress case with `uv run python scripts/benchmark_search.py --records 100000 --people 10 --cache-mb 512 --parallel 4`.

## Required before declaring the pilot ready

- [ ] Enable/authorize TypeSafe Jev on the actual LiteLLM gateway and pass its typed probe. Current gateway returns 404; the configured key cannot administer pass-through routes (403). No direct-provider fallback is allowed.
- [ ] Verify the pilot gateway's no-log behavior for chat, embedding and TypeSafe pass-through requests.
- [x] Complete encrypted portable export/import and a deletion-aware installation backup/restore proof.
- [x] Provide the normal passwordless installation transfer, plus a member guide explaining sharing, capture, imports and lifecycle controls.
- [x] Implement/test private corrections with original evidence preservation, revision links and explicit account rejoin without restoring old grants.
- [x] Verify administration transfer, gateway-key rotation and recovery-mode controls.
- [x] Prove real Codex/Claude lifecycle hook dispatch and transcript compatibility, beyond adapter fixtures.
- [ ] Complete personal Slack OAuth and actual capture through the hosted pilot; the isolated host tests above do not replace this.
- [ ] Apply/verify needed Slack scopes and test owned-DM and least-audience shared-channel behavior without enrolling teammates or sending unsolicited messages.
- [ ] Import approved Moe history, reconcile exact original hashes/counts, and prove rerun/restart behavior. The legacy preview alone is not a completed import.
- [x] Verify bounded shared query capacity, server-owned history and citation-ID validation.
- [ ] Finish the real-history answer-quality evaluation and remaining full-pilot acceptance checks.
- [x] Measure larger archives and multiple people/concurrent queries; document the cold-start and cache limits.
- [ ] Deploy the integrated entry point to the private owner-only/sharing-off pilot, then prove actual capture → Ente → processing → explicit query → valid citation after restart.
- [ ] Reconcile README/setup instructions and release branch references with the actual released revision; keep the repository private until the owner chooses to publish it.

The legacy product-team repository/MCP stays in place until explicit cutover. No real teammate has been enrolled and no real memory has been published as part of these checks.
