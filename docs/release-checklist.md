# Release checklist

LiteLLM Memory is prerelease software. Validate a deployment with synthetic records before allowing real team history. Keep deployment-specific evidence, credentials and real-history evaluations outside this repository.

## Automated checks

[CI](https://github.com/BerriAI/litellm-memory/actions/workflows/verify.yml) runs unit/API tests, Ruff, frontend syntax checks, Codex/Claude hook checks with disposable fixtures, and Docker/PostgreSQL/Ente build, restart, backup and restore checks. [Secret scanning](https://github.com/BerriAI/litellm-memory/actions/workflows/secrets.yml) checks Git history and the current source tree.

The tests cover account and audience boundaries, encryption, token revocation, connector recovery, processing, query delivery and source citations. Synthetic fixtures and mocked services do not establish live provider behavior or real-history answer quality. See [the interface screenshots](ui-review.md) for their evidence scope.

## Before a deployment rollout

- [ ] Pin the source revision and pass its CI checks.
- [ ] Complete setup with your gateway, required Slack scopes, verified company domain and encrypted-storage check.
- [ ] Verify capture → processing → Ask → valid citation after a restart.
- [ ] Verify a personal Slack DM and an approved shared conversation with authorized test participants.
- [ ] Verify logging suppression and retention for chat, embeddings and TypeSafe requests on your gateway and providers.
- [ ] Review [operator key custody](security.md) and test a backup/restore on an isolated installation.
- [ ] Review [answer quality](answer-quality.md) using authorized records and recorded judgments kept outside Git.
- [ ] Size memory and concurrency for the intended archive; run the benchmark below.
- [ ] Verify setup, recovery and member guidance against the revision being deployed.
- [ ] If telemetry is enabled, confirm a test trace arrives with only the documented content-free attributes.

## Before publishing a repository

- [ ] Scan every branch, tag and pull-request reference, including historical content and screenshots.
- [ ] Review GitHub discussions, pull-request text, Actions logs and downloadable artifacts for private information.
- [ ] Keep personal profiles, private source copies, credentials, histories and deployment state out of the published source and history.
- [ ] Enable GitHub secret scanning and push protection.

Removing a file from the latest commit does not remove earlier copies from Git history or pull requests. Rewriting branch history alone also does not erase GitHub's retained pull-request references.

## Known limits

Recording depends on the coding agent choosing useful work and successfully calling MCP. A hook reminder is not proof of a save; skipped turns are valid. Agent-authored memories are accounts, not raw evidence of tool execution. Historical conversation import is an explicit operator utility rather than a required onboarding step.

Private-topic rules apply through Jev. Arbitrary saved semantic browsing criteria are not implemented. Hosted processing can decrypt authorized records and is not zero knowledge against the deployment operator.

The starter deployment supports one application replica. Daily summaries send the complete day to the selected background model; operators must choose an appropriate context capacity. Million-token inference has not been established by synthetic tests.

## Performance measurements

A local synthetic run with 100,000 records across ten people and a 512 MB search cache measured roughly 31 seconds cold, 0.4–0.6 seconds warm, and 40 seconds for four concurrent direct retrievals. These measurements exclude inference and Ente network calls and are not production latency guarantees.

Reproduce with:

```sh
uv run python scripts/benchmark_search.py --records 100000 --people 10 --cache-mb 512 --parallel 4
```

The benchmark uses temporary synthetic encrypted records and makes no external model calls. Measure your actual sharing pattern and concurrency before expanding a deployment.
