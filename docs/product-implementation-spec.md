# LiteLLM Memory product contract

LiteLLM Memory is a queryable record of what people do with AI. It captures work continuously, lets people browse it, and answers questions when asked. It is not an automatic context-injection system for coding agents.

This document describes the current implementation. It replaces the earlier development plan. Deployment and live validation are tracked separately in the [release checklist](release-checklist.md).

## The product

The MVP centers on one loop: **set up, start recording, browse shared work, and ask about it.** The Company feed and browsing a teammate's shared activity are primary experiences alongside Ask. Company opens by default, with My work second for personal history. People can see only work shared with them; recording alone never publishes private originals.

Prioritize making that loop reliable and easy to understand: successful setup, a visible first capture, readable entries, clear sharing, useful browsing, and cited answers. The dashboard has no separate catch-up page, daily-summary settings, or manual-note composer. Importing old conversations is outside the MVP scope and is not part of onboarding. Auxiliary workflows stay outside the main loop. The implementation below includes existing supporting features; their presence does not make each one essential to the core experience.

- **Activity:** chronological Company feed by default, My work second, direct Ask input, search and metadata filters. Private records show a lock and text.
- **Ask:** one conversation, a visible audience selector, and models discovered through the configured LiteLLM key. Answers cite accessible evidence. People can cancel a running answer.
- **People:** enrolled Slack profiles, shared activity, and an action to ask about that person's work.
- **Connections:** in-app tool setup, real intake receipts, recording controls and connected-client revocation. Authorized clients and received work are separate states.
- **Settings:** Sharing, Preferences (dates and viewing exclusions), Your data, and administrator-only Workspace. Rare operations stay behind disclosures or explicit actions.

One sidebar provides navigation. No recommendation algorithm, fixed memory-category checklist, social engagement mechanics, or automatic retrieval on every coding turn.

## Joining and deployment

The Render Blueprint or Docker Compose starts the web app, persistent storage and Ente Museum. The initial administrator claims setup with a private deployment code, connects a LiteLLM URL/key, selects models, and installs Slack from the supplied manifest. Setup progressively reveals these controls on one page and preserves recoverable input after errors.

Slack is the only member identity provider. Both the Slack workspace and exact verified email domain must match; `berri.ai` is the default for this installation. Self-hosters select their own domain. Members do not manage vault keys or a separate application password. An optional encrypted file export has its own passphrase; browser-to-browser installation transfer does not.

Jev must work through LiteLLM before setup completes. The gateway owns the TypeSafe credential. Model discovery is key-scoped; a missing Jev entry in `/v1/models` alone is not a failed capability check. Agent, embeddings, Slack and background model roles can be configured. New setup suggests Luna for background work when the gateway exposes it, independently of the agent model. Explicit operator choices are preserved; deployments without Luna choose from their available generation models.

After joining, members land on Company. A compact setup prompt leads to inline Codex/Claude instructions. Connection status checks persisted receipts without searching memory or invoking a model. Imports alone do not mark live intake as received. Person feeds and My work have stable routes; browser Back restores the selected person.

## Capture and storage

SessionStart and UserPromptSubmit hooks remind the coding agent to save useful new work with a low threshold: what it is doing, progress, attempts and results, findings, decisions, blockers and next steps. The agent writes concise memories through MCP or skips a turn with no useful change. Hooks do not read/upload transcripts, run another model, search history, or force a continuation. Optional global instructions provide the same guidance.

Agent-authored memories are identified as such and preserve available source/session context and uncertainty. Recording is opt-in through connector setup; pausing preserves saved memories; resuming does not record the paused period.

The API stores immutable encrypted source objects and separate encrypted derived records in Ente. SQLite holds encrypted operational state. Git holds code and documentation, never live personal memories. Existing imported records remain searchable with their originals, dates, attribution and metadata preserved; removing import from onboarding does not remove data. Deletion markers stop old backfills from restoring deleted records.

For agent-authored MCP memories, background processing preserves the submitted text and adds search embeddings and required Jev checks. It does not summarize the memory again. Older transcript imports still generate a readable description. It does not force new work into “progress / decision / finding / blocker / plan.” Existing metadata and previous classifications remain available for search. Current/daily summaries are built on explicit request, with citations and coverage. There is no automatic daily or five-minute overview sweep. Existing summaries and original evidence are preserved.

## Filters and sharing

Everything starts private. Sharing publishes a separate copy of the memory text to eligible company members; source details and metadata remain private. Members can preview and share a memory, withdraw it, or opt in to automatic sharing of future memories. Older imported conversations publish their derived description while the original conversation remains private. Existing history stays private unless explicitly shared.

Members describe **topics that must stay private** in plain language. Jev evaluates complete source chunks for those rules. Results are cached by source, criterion and version; adding a rule computes only its missing results and applies it to future records. New privacy rules withdraw existing publications while checks run. Uncertain matches remain private. Older restrictive type policies are preserved for compatibility and are not silently widened.

Browsing uses text, person, source, project, saved type metadata and dates. Personal viewing exclusions narrow feed, Ask and derived views without changing sharing permissions. The MVP's user-defined Jev controls are privacy-topic rules; arbitrary saved semantic browsing categories are not implemented and must not be advertised as available.

## Ask and Slack

Web, Slack, MCP and API use the same read-only query agent. It can search, inspect originals it is permitted to read, list people and consult current/daily views. It has no tool that publishes work or changes permissions.

Permissions apply before retrieval/decryption and again before delivering an answer. Shared Slack conversations use company-public evidence only and fail closed for unknown or ineligible participants. Private questions require a verified personal audience. Changing audience/person invalidates previous conversation context; access changes can also invalidate in-flight answers.

Source text is untrusted data. Answers preserve attribution, uncertainty and dates, distinguish plans from outcomes, and admit missing evidence. Citation IDs must correspond to sources actually read. Synthetic tests cannot establish real answer quality; the release gate requires a reviewed real-history evaluation.

## Privacy boundary and visibility

Encrypted storage prevents the Git repo and ordinary administrator screens from exposing personal records. The hosted application is an authorized processor and can decrypt records for retrieval/inference. Someone controlling its runtime and keys could do the same. This is not zero knowledge against a malicious deployment operator.

LiteLLM calls request content logging suppression. Operators must verify their actual gateway/provider retention settings. Optional Pydantic Logfire or OTLP traces show usage, model/tool activity and latency without private text or identities. [Observability setup](observability.md).

## Design review: what changed and what remains

The earlier implementation imposed a predefined taxonomy and repeated it in sharing, filters and the feed. That was an unnecessary product decision. New processing no longer requires it, and the UI exposes simple sharing choices and user-written privacy topics.

The coding agent writes a concise work memory through MCP. The service preserves it as written; a second model rewriting it was redundant and could block indexing and sharing on an unrelated generation failure. The stored envelope carries provenance and stable IDs for retries, without imposing a “learning” format. Historical transcript imports remain a separate operator workflow. Evaluate real retrieval failures before adding another processing layer.

Visible conversation captures what was said, not proof that a tool ran or a claim is true. The answer contract preserves that distinction. Future changes should follow observed retrieval failures and user needs, not add more categories, settings or synthetic success metrics.

The remaining delivery gates and scale limits belong in the [release checklist](release-checklist.md), not in this product contract.
