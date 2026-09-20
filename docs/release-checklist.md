# Release evidence and remaining gates

This file distinguishes implementation from verified deployment. It is not a claim that the full pilot has been released.

## Verified on September 20, 2026

- The implementation branch's initial suite passed **74 tests**, Ruff and JavaScript syntax checks locally and on Linux CI.
- [CI run 35530917378](https://github.com/moe-berri/litemem/actions/runs/35530917378) built the actual Docker/Compose stack, provisioned PostgreSQL + pinned Ente Museum, verified ciphertext write/read/decrypt/delete, checked closed registration and setup access, restarted the app, and passed again with persistent credentials.
- Actual separate Ente storage in the private Render pilot also passed encrypted round-trip/deletion checks after bootstrap registration was disabled. The old hosted app remains the running entry point.
- The real MCP Python SDK connected to the new ASGI service, recorded/searched/read owned evidence and denied cross-account reads and revoked tokens.
- Desktop and 390px mobile browser checks showed the activity feed, private locks, catch-up panel and all model settings without console errors. These used isolated synthetic data.
- Connector tests cover partial transcript rows, role filtering, encrypted local queueing, interruption/replay, token rotation, offline retries, pause, configuration preservation and account-safe connection behavior.
- Privacy tests cover owner/public/workspace boundaries, distinct public summaries/vectors, live policy changes, private topic rules, cached Jev work, automatic type sharing and nonpublication of historical imports.
- New optional Logfire instrumentation passed a canary test that exception messages/private input do not appear in exported spans. Full-suite validation after subsequent changes remains required.

## Performance measurements

A local synthetic 10,000-record fixture with 768-dimensional vectors measured 3.64 seconds cold and 0.40 seconds for each of two warm hybrid searches. Estimated index cache: 91.4 MB; whole-process peak: about 183 MB on the developer's Apple Silicon machine. This is a small local sample, not a production p95/SLA. The 128 MB cache avoids the measured repeated rebuilds that occurred with a 64 MB budget. Inference time is excluded. Larger multi-person and concurrent-query measurements remain open.

## Required before declaring the pilot ready

- [ ] Enable/authorize TypeSafe Jev on the actual LiteLLM gateway and pass its typed probe. Current gateway returns 404; the configured key cannot administer pass-through routes (403). No direct-provider fallback is allowed.
- [ ] Verify the pilot gateway's no-log behavior for chat, embedding and TypeSafe pass-through requests.
- [ ] Complete encrypted portable export/import and a deletion-aware installation backup/restore proof.
- [ ] Complete corrections/revision relationships, account rejoin/recovery and remaining lifecycle controls.
- [ ] Prove continuous capture with actual supported Codex/Claude hosts and personal OAuth, not only synthetic adapter fixtures.
- [ ] Apply/verify needed Slack scopes and test owned-DM and least-audience shared-channel behavior without enrolling teammates or sending unsolicited messages.
- [ ] Import approved Moe history, reconcile exact original hashes/counts, and prove rerun/restart behavior. The legacy preview alone is not a completed import.
- [ ] Finish citation/history/job-limit hardening and remaining spec acceptance checks.
- [ ] Finish larger archive/parallel-query measurements and document operating limits.
- [ ] Deploy the integrated entry point to the private owner-only/sharing-off pilot, then prove actual capture → Ente → processing → explicit query → valid citation after restart.
- [ ] Reconcile README/setup instructions and release branch references with the actual released revision; keep the repository private until the owner chooses to publish it.

The legacy product-team repository/MCP stays in place until explicit cutover. No real teammate has been enrolled and no real memory has been published as part of these checks.
