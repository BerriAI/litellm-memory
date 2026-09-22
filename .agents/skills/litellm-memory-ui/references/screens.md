# Screen recipes

These recipes describe the existing product and how to extend it consistently. They are not permission to add features or a promise that every live integration is configured.

## Activity

**Task:** understand recent work, find an entry, or ask about it.

**Order:** page title/date and Refresh → compact recording prompt when needed → selected person, if any → Ask surface → Company/My work selection and filters → chronology → evidence. Company comes first and is selected on a fresh visit. People support discovery; there is no Catch up panel or manual-note composer.

The home Ask form follows the selected feed: company-shared work, the user's own work, or a selected person's shared work. Its placeholder and query scope must agree. Switching feed does not publish or change anyone's access.

Keep the feed chronological. The useful title and work excerpt are more prominent than source, timestamp, and processing count. Author identity adds social context; it should not consume more space than the work. The private lock and text remain visible even when metadata wraps.

The home Ask form has a visible label, concise placeholder and send control. Do not repeat a privacy reassurance underneath every composer. Avoid a welcome hero, metric cards, progress rings, or recommendation panel above the first entry. The feed should appear within a normal viewport, not beneath an onboarding essay.

Search opens a native dialog near the top of the viewport, without navigating or rearranging the underlying page. Put query, explicit Company/My work/person scope, and results together. Search only on submission; typing, opening the panel and changing scope do not fetch work. Clear stale results when the input changes, retain the query on failure, and preserve the underlying page, scroll and keyboard focus on close. Source details can open above search and return to the selected result. Metadata filters remain scoped to the Activity feed; saved viewing exclusions in Settings → Preferences apply to both and must be disclosed when active. Personal viewing exclusions do not change sharing.

For someone else's feed, show a compact identity header and Back to Company action; scope the question to their shared work. Person routes survive reload and browser Back. Changing the feed/person clears temporary filters; saved exclusions remain enforced. Do not expose private counts, hidden titles, or inaccessible source previews merely to fill the layout.

## Daily summaries

**Task:** understand what a person or the company worked on during a particular day.

**Order:** page/person identity → Company/My work and date → author/audience → readable summary → sources. Reuse Activity's page width and primary reading-column width so switching views does not shift their edges. Use quiet separators between people. Previous/Next skip to dates with generated summaries; a native date input opens any date. Show the workspace timezone. Summaries cover completed days only; today’s work is available in Activity. Do not infer inactivity from an empty day.

Person pages provide Activity and Daily summaries navigation using the same summary renderer. Person, scope and date routes survive reload and Back. This is an archive of generated summaries: show “None available yet” before one is ready, and never show generation failures or pending placeholders. Network-read errors retain their recovery action. Generation runs nightly at midnight by default; browsing never triggers it. Owner summaries show a lock and Private; company summaries contain only approved publications. Reuse the source dialog for citations. The same summaries are available to Ask and Slack.

## Ask: empty, active, answered

**Task:** get a grounded answer about accessible work.

**Empty:** page identity → modest prompt → one explanatory sentence → a few useful suggested questions → composer. Use ordinary questions people would ask, not example prompts stuffed with product jargon.

**Active:** user's question → truthful working state → emerging response → Stop. Avoid an intrusive full-page loading overlay. The person can still understand the selected scope and model.

**Answered:** response and citations take priority. Keep scope and model controls compact below the writing area. Show an active-filter notice only when saved exclusions affect the answer. Longer answers need readable headings/lists/code without escaping the content width.

Switching scope must respect the existing conversation-reset confirmation. A person's shared memory scope is not their private memory. Choosing All accessible memories is a query choice, not a share/publication action. Ask remains private to the person asking in the web UI.

**Failure:** keep the question/draft and a recovery action. A simulated preview answer is visibly identified as simulated; never disguise it as a live model response in evidence.

## People

**Task:** recognize a teammate, browse their shared work, or ask about it.

A simple directory is enough: page title and short explanation, search/count, person rows. Names and Slack-derived avatars lead; scope helper text follows; Ask is aligned at the end. Do not invent roles, active-now states, ranking, contribution scores, or “last seen” metadata.

The empty directory explains that people appear after joining with Slack. Search with no matches offers a clear explanation and retains the query. A missing avatar uses the existing initials fallback. Self is identifiable without a large badge.

## Connections

**Task:** connect the coding tool once, then see/manage connections.

The page starts with actual recording status, Refresh status, and View my work when there is saved history. A connected credential is not proof of running hooks; imported history is not new intake. Report saved receipts precisely.

Connected tools follow the status. Label valid credentials Connected; do not confuse MCP last-used timestamps with recording receipts. Inline setup offers a tool selector (both tools by default), copyable install/connect commands, one browser approval for access and exact reminder hooks, and a copyable first-save message. Collapse setup for returning members with a connection and received work. Keep recording controls in a separate disclosure. Old-conversation imports are outside the MVP scope; do not add them to setup or Connections. Troubleshooting and manual MCP remain in the linked connection guide. Keep command text selectable on narrow screens.

The connector guide groups the real steps: install and approve once; reopen coding tools; send the first message and confirm its saved receipt. The callback returns to Connections with installation commands hidden and the first-save steps open. Both hosts use the encrypted connector sign-in through the local MCP bridge; do not add a second OAuth step. Optional instructions and connection controls follow those steps. Code is copyable/selectable, monospaced, and horizontally scrollable rather than wrapping commands into invalid lines. Keep requirements near the relevant setup, not in the home feed.

Explain that hooks remind the agent to save useful new work through MCP; they do not upload transcripts. The agent can skip turns without useful changes. Memory retrieval is explicit. Do not imply that a hook or a connected credential guarantees a successful save.

## Settings

**Task:** make a deliberate preference or workspace change with a predictable effect.

The page title is Settings. A short subtitle establishes scope, followed by Sharing, Preferences, Your data and administrator-only Workspace tabs. Every panel uses the same settings-section pattern, not a different card layout for each category.

### Sharing

Two task groups: sharing and private topics.

- Sharing explains private-by-default and exposes the opt-in for new memories when company sharing is available.
- Private topics uses a visible label, example, Add topic, concise Jev explanation, and existing rules.
- Do not add Progress/Decisions/Findings/Blockers/Plans checklists. Existing restrictive legacy policies must continue to work even though new users do not receive a preset taxonomy.
- Company sharing being unavailable is a real state with an explanation; do not substitute a disabled switch that looks like a personal preference.
- Existing history and source details remain distinct from newly shared memory text. Copy must preserve that boundary.

### Recording belongs in Connections

Group Record new work and Let my agent use my history under Recording controls in Connections. A paused-state action opens those controls directly. Show processing status only while records are pending or failed, with retry only when failures exist. Do not show a routine all-organized panel or disabled retry button.

### Preferences

Personal date/time display and a disclosed saved-viewing-preferences editor. There are no daily-summary scheduling controls. Existing exclusions must remain visible through an active-filter notice on the feed and editable here.

### Workspace

Available only to the administrator. Group company sharing, models, and administration. Within Models, common roles are visible; classification details are disclosed; credential rotation and ownership transfer are lower-frequency administration tasks.

All model options come from the configured LiteLLM gateway. Do not add direct-provider credential fields or invented model options. Jev readiness must reflect the actual check. A placeholder catalog used in a test is not proof that the real gateway is ready.

### Your data

Transfer, export, and leave are separate tasks with plain consequences. Encrypted export and readable export are distinct choices, not an ambiguous single download action. Leave is separated from routine export; its label is specific. Do not call it Delete account unless the implemented action actually does that.

## Source detail

**Source:** compact audience/close bar → title and provenance → the memory text → expandable originals/metadata → one sharing action and a compact More actions menu. Agent-authored memories are kept as written; do not add another summary heading or rewrite. Processing and privacy restrictions have specific, quiet inline explanations rather than a red list of possible causes. A source title may wrap. Original text remains legible, selectable, and properly escaped/sanitized. Metadata remains filterable and inspectable without rendering a raw JSON wall in the feed.

Sharing preview is the primary owner action. Corrections and deletion stay under More actions. The API and MCP can record work manually, but the dashboard focuses on connected capture rather than note authoring.

## First run and identity

**Task:** reach a working installation with the fewest decisions needed.

Use one progressive setup surface. Claim protects a fresh installation; LiteLLM connection discovers models/checks Jev; Slack connection returns to the same setup context. Expand the next step as it becomes actionable; do not translate backend dependencies into a separate page for each API call.

Credentials have visible labels; connection status has precise wording. Advanced model choices stay available without competing with the default agent and embeddings. Preserve entered non-secret setup state on recoverable errors while following the existing secret-handling behavior.

Slack is the user's sign-in method. Do not invent a second product password or a user-managed encryption-key screen. Eligibility follows the deployment's verified Slack/email rules. Do not claim arbitrary accounts can join because the UI looks open.

Member onboarding confirms joining, then opens Company immediately. A compact prompt leads to recording setup without blocking shared browsing. It progresses from not connected to waiting for work to a saved receipt; never label a hook “running” from credentials alone. Auth expiry is recoverable and clearly distinguished from no data. Never show private stale content after the application has invalidated access.

## Transfer and export

Keep the source, destination, account identity, and next step clear. The receiving page and sending page use the same type scale and standalone form language. No-memory, waiting, processing, complete, cancelled, and failed states have different meanings.

Do not let success styling appear before the server confirms the appropriate stage. Receiving privately is separate from sharing. A transfer doesn't erase the source. Export passphrases are specific to downloaded encrypted files; do not imply they are required for normal Slack sign-in.

## New screens

Start from the nearest recipe and state the user task. Reuse components at the same semantic size and spacing. If you cannot explain which primary task the new page serves, revisit scope before adding navigation.

For example, a requested import review would likely use a page header, status, a source list, and scoped actions. It would not automatically need a new dashboard, metric cards, badges for every status, or a new accent color. A future semantic-filter builder must follow the real model/API behavior; the existing private-topic editor does not establish that arbitrary saved semantic browsing already works.
