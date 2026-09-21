# Screen recipes

These recipes describe the existing product and how to extend it consistently. They are not permission to add features or a promise that every live integration is configured.

## Activity

**Task:** understand recent work, find an entry, or ask about it.

**Order:** page title/date and optional Add memory → compact Ask surface → My work/Company selection and filters → chronology → evidence. People and catch-up context are secondary.

Keep the feed chronological. The useful title and work excerpt are more prominent than source, timestamp, and processing count. Author identity adds social context; it should not consume more space than the work. The private lock and text remain visible even when metadata wraps.

The home Ask form has a visible label, concise placeholder, send control, and one short privacy line. Avoid a welcome hero, metric cards, progress rings, or recommendation panel above the first entry. The feed should appear within a normal viewport, not beneath an onboarding essay.

Search and Filter are discoverable controls, with advanced exclusions disclosed only when relevant. Clear search and clear filters must return to a understandable broader state. Personal viewing exclusions do not change sharing; keep that distinction in the explanation.

For someone else's feed, name the person and scope the question to their shared work. Do not expose private counts, hidden titles, or inaccessible source previews merely to fill the layout.

## Ask: empty, active, answered

**Task:** get a grounded answer about accessible work.

**Empty:** page identity → modest prompt → one explanatory sentence → a few useful suggested questions → composer. Use ordinary questions people would ask, not example prompts stuffed with product jargon.

**Active:** user's question → truthful working state → emerging response → Stop. Avoid an intrusive full-page loading overlay. The person can still understand the selected scope and model.

**Answered:** response and citations take priority. Keep model settings and privacy feedback compact below the writing area. Longer answers need readable headings/lists/code without escaping the content width.

Switching scope must respect the existing conversation-reset confirmation. A person's shared memory scope is not their private memory. Choosing All accessible memories is a query choice, not a share/publication action. Ask remains private to the person asking in the web UI.

**Failure:** keep the question/draft and a recovery action. A simulated preview answer is visibly identified as simulated; never disguise it as a live model response in evidence.

## People

**Task:** recognize a teammate, browse their shared work, or ask about it.

A simple directory is enough: page title and short explanation, search/count, person rows. Names and Slack-derived avatars lead; scope helper text follows; Ask is aligned at the end. Do not invent roles, active-now states, ranking, contribution scores, or “last seen” metadata.

The empty directory explains that people appear after joining with Slack. Search with no matches offers a clear explanation and retains the query. A missing avatar uses the existing initials fallback. Self is identifiable without a large badge.

## Connections

**Task:** connect the coding tool once, then see/manage connections.

The setup entry point gets a short, useful surface: what connecting does, Connect a tool, optional manual MCP disclosure. Connected clients are a straightforward list below it. Do not put a wall of commands ahead of the ordinary path.

The connector guide groups the real steps: install/connect; restart and complete MCP/hook setup; optional history import/status commands. Code is copyable/selectable, monospaced, and horizontally scrollable rather than wrapping commands into invalid lines. Keep requirements near the relevant setup, not in the home feed.

Explain that recording and search are different: visible work is saved continuously; memory retrieval is explicit. Do not imply manual MCP calls alone provide the same continuous capture as host hooks. Do not claim tool output or hidden reasoning is captured when it is excluded.

## Settings

**Task:** make a deliberate preference or workspace change with a predictable effect.

The page title is Settings. A short subtitle establishes scope, followed by sibling tabs. Every panel uses the same settings-section pattern, not a different card layout for each category.

### Privacy

Two task groups: sharing and private topics.

- Sharing explains private-by-default and exposes the existing opt-in for future summaries when company sharing is available.
- Private topics uses a visible label, example, Add topic, concise Jev explanation, and existing rules.
- Do not add Progress/Decisions/Findings/Blockers/Plans checklists. Existing restrictive legacy policies must continue to work even though new users do not receive a preset taxonomy.
- Company sharing being unavailable is a real state with an explanation; do not substitute a disabled switch that looks like a personal preference.
- Existing history and original conversations remain distinct from newly shared summaries. Copy must preserve that boundary.

### Recording

Group Record new work and Personal agent access under Recording. Processing is a separate status group with actual counts and retry if applicable. Do not style “all organized” as a warning or give a disabled retry button primary emphasis.

### General

Personal date/time display first; workspace daily catch-up scheduling second. The scope of the two time zones is visible. Non-admin users can see the appropriate schedule information without being shown fake editable controls.

### Workspace

Available only to the administrator. Group company sharing, models, and administration. Within Models, common roles are visible; classification details are disclosed; credential rotation and ownership transfer are lower-frequency administration tasks.

All model options come from the configured LiteLLM gateway. Do not add direct-provider credential fields or invented model options. Jev readiness must reflect the actual check. A placeholder catalog used in a test is not proof that the real gateway is ready.

### Your data

Transfer, export, and leave are separate tasks with plain consequences. Encrypted export and readable export are distinct choices, not an ambiguous single download action. Leave is separated from routine export; its label is specific. Do not call it Delete account unless the implemented action actually does that.

## Source detail and manual capture

**Source:** audience and title → useful content → provenance → expandable originals/metadata → actions. A source title may wrap. Original text remains legible, selectable, and properly escaped/sanitized. Metadata remains filterable and inspectable without rendering a raw JSON wall in the feed.

**Capture:** a modest dialog, visible labels, concise example, Private lock, Save memory. No required schema fields beyond the existing API contract. On error, retain the draft. Successful save is not equivalent to completed classification or permission to share.

## First run and identity

**Task:** reach a working installation with the fewest decisions needed.

Use one progressive setup surface. Claim protects a fresh installation; LiteLLM connection discovers models/checks Jev; Slack connection returns to the same setup context. Expand the next step as it becomes actionable; do not translate backend dependencies into a separate page for each API call.

Credentials have visible labels; connection status has precise wording. Advanced model choices stay available without competing with the default agent and embeddings. Preserve entered non-secret setup state on recoverable errors while following the existing secret-handling behavior.

Slack is the user's sign-in method. Do not invent a second product password or a user-managed encryption-key screen. Eligibility follows the deployment's verified Slack/email rules. Do not claim arbitrary accounts can join because the UI looks open.

Member onboarding is small and direct. Auth expiry is recoverable and clearly distinguished from no data. Never show private stale content after the application has invalidated access.

## Transfer and export

Keep the source, destination, account identity, and next step clear. The receiving page and sending page use the same type scale and standalone form language. No-memory, waiting, processing, complete, cancelled, and failed states have different meanings.

Do not let success styling appear before the server confirms the appropriate stage. Receiving privately is separate from sharing. A transfer doesn't erase the source. Export passphrases are specific to downloaded encrypted files; do not imply they are required for normal Slack sign-in.

## New screens

Start from the nearest recipe and state the user task. Reuse components at the same semantic size and spacing. If you cannot explain which primary task the new page serves, revisit scope before adding navigation.

For example, a requested import review would likely use a page header, status, a source list, and scoped actions. It would not automatically need a new dashboard, metric cards, badges for every status, or a new accent color. A future semantic-filter builder must follow the real model/API behavior; the existing private-topic editor does not establish that arbitrary saved semantic browsing already works.
