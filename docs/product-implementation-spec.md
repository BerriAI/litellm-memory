# litemem product contract

litemem is a queryable record of what people do with AI. It captures work continuously, lets people browse it, and answers questions when asked. It is not an automatic context-injection system for coding agents.

This document describes the implementation on the feature branch. It replaces the earlier development plan. Deployment and live validation are tracked separately in the [release checklist](release-checklist.md).

## The product

- **Activity:** chronological personal or company feed, direct Ask input, search and metadata filters. Private records show a lock and text.
- **Ask:** one conversation, a visible audience selector, and models discovered through the configured LiteLLM key. Answers cite accessible evidence. People can cancel a running answer.
- **People:** enrolled Slack profiles, shared activity, and an action to ask about that person's work.
- **Connections:** instructions for Codex/Claude Code capture and remote MCP, plus connected-client revocation.
- **Settings:** privacy, recording, dates, administrator workspace configuration, and personal data controls. Rare operations stay behind disclosures or explicit actions.

One sidebar provides navigation. No recommendation algorithm, fixed memory-category checklist, social engagement mechanics, or automatic retrieval on every coding turn.

## Joining and deployment

The Render Blueprint or Docker Compose starts the web app, persistent storage and Ente Museum. The initial administrator claims setup with a private deployment code, connects a LiteLLM URL/key, selects models, and installs Slack from the supplied manifest. Setup progressively reveals these controls on one page and preserves recoverable input after errors.

Slack is the only member identity provider. Both the Slack workspace and exact verified email domain must match; `berri.ai` is the default for this installation. Self-hosters select their own domain. Members do not manage vault keys or a separate application password. An optional encrypted file export has its own passphrase; browser-to-browser installation transfer does not.

Jev must work through LiteLLM before setup completes. The gateway owns the TypeSafe credential. Model discovery is key-scoped; a missing Jev entry in `/v1/models` alone is not a failed capability check. Agent, embeddings, Slack and background model roles can be configured. Background work initially uses Luna.

## Capture and storage

Host hooks observe new visible user/assistant transcript messages. The remote MCP cannot observe an entire conversation on its own. Hooks perform no inference, retrieval or prompt injection. They exclude hidden reasoning, system/developer context and tool-result dumps.

Each captured message retains its original text and available source, session, role, time and metadata. Capture currently processes individual messages, splitting exceptionally long messages into parts. The connector encrypts its local outbox and resumes interrupted uploads with stable source identities. Recording is opt-in through connector setup; pausing does not delete history or silently backfill the paused period.

The API stores immutable encrypted source objects and separate encrypted derived records in Ente. SQLite holds encrypted operational state. Git holds code and documentation, never live personal memories. Historical imports preserve originals, dates and attribution, deduplicate replays, and never publish automatically. Deletion markers stop old backfills from restoring deleted records.

Background processing creates a short readable description and search embeddings. It does not force new work into “progress / decision / finding / blocker / plan.” Existing metadata and previous classifications remain available for search. Current/daily summaries combine source records with citations and coverage; they are derived views, never replacements for the evidence.

## Filters and sharing

Everything starts private. Sharing publishes a separate summary to eligible company members; the original conversation remains private. Members can preview and share a summary, withdraw it, or opt in to automatic sharing of future summaries. Existing history stays private unless explicitly shared.

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

The capture format is a transport envelope around evidence, not a demand that a coding model author a structured “learning.” Message-level capture protects originals and makes retries simple, but message-level processing can create fragmented summaries and unnecessary model calls. Current/daily views improve readability; they do not yet make session episodes the canonical feed unit. Evaluate that on real history before adding another processing layer.

Visible conversation captures what was said, not proof that a tool ran or a claim is true. The answer contract preserves that distinction. Future changes should follow observed retrieval failures and user needs, not add more categories, settings or synthetic success metrics.

The remaining delivery gates and scale limits belong in the [release checklist](release-checklist.md), not in this product contract.
