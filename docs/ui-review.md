# Interface guide

The UI uses a shared hierarchy: 24px page titles, 16px content titles, 14px body, 13px controls, and 12px supporting detail. Settings uses one reading column: section purpose, controls, then the related action. Personal tabs precede the administrator-only Workspace tab. Placeholders remain secondary; narrow desktop panes do not trigger touch-sized input text.

The UI uses one sidebar, a chronological feed, explicit private/company labels, and a single-column Ask conversation. Search and advanced filters stay out of the way until needed. Icons use one Lucide grid and local font assets; there is no frontend build step or external font request.

The LiteLLM Memory wordmark uses 17px text so the full name stays readable at desktop and mobile widths.

The maintained [LiteLLM Memory UI skill](../.agents/skills/litellm-memory-ui/SKILL.md) explains the design language and how to extend it.

## Workspace setup

Setup uses one spacing pattern: section title and explanation, related fields, then action and feedback. Helpers stay close to their field; Slack permission guidance has a separate action group. Expanded model and Slack forms follow the same rhythm. Empty status messages do not reserve space.

![Workspace setup](assets/setup.jpg)

## Search and the compact header

Search opens a panel near the top of the window, keeping scope and results together. It leaves the page underneath in place. Submit explicitly, open sources from results, and use Escape or Close to return. In the compact header the wordmark, Search and account control share a center line; the five destinations are evenly spaced below.

![Search](assets/search.jpg)

[View the compact header](assets/header.jpg).

## Activity

Company is the first tab and opens by default; My work is second. Ask about the selected feed, or open an entry to inspect its source. Private entries carry both a lock and a label.

![Activity](assets/dashboard.jpg)

## People and recording

Select a person from the feed or directory to browse their shared work. The person’s name and query scope stay visible, and the route survives reload and Back.

![Person activity](assets/person.jpg)

Connections puts saved-work status and connected tools first. Setup, recording controls and historical imports are separate disclosures. Pause/resume and processing retries live here; manual MCP setup and troubleshooting are in the connection guide. A connected credential and a saved-work receipt remain separate facts.

![Recording status](assets/connections.jpg)

## Ask

Choose the memory scope and a gateway model. Responses include clickable source citations. Composers omit repeated privacy boilerplate; memory entries still show their audience. Stop cancels work; changing scope confirms before clearing a conversation.

![Ask](assets/ask.jpg)

## Sharing

Memories start private. Future sharing is opt-in. Write topics that Jev should keep private; no predefined category grid is required. Existing restrictive policies remain enforced.

![Privacy](assets/settings.jpg)

## Sources and metadata

A summary links back to its evidence. The original and metadata remain available without filling the feed with raw JSON.

![Source detail](assets/memory.jpg)

## Review scope

These are screenshots of the actual local application with synthetic records and simulated model replies. They do not establish live gateway or Slack readiness.

The core-loop review covers joining → Company → tool instructions → authorized/waiting → first received work → My work, person browsing/reload/Back, Ask scope, sharing preview, saved-filter editing, and recording pause. Browser checks include 1280px, 390px and 320px layouts, keyboard Settings navigation, source-dialog dismissal and recording-status failure/retry. Long connection commands scroll inside their own block without widening the page. Connections checks cover initial setup, automatic client-list refresh, a saved first-record receipt, pause/resume and failed-save rollback, independent import-tool selection, and direct recovery from the paused Activity notice. Settings checks include personal/admin tab visibility, keyboard navigation and saving a time zone. The search review also covers Company/My work/person scopes, empty results, a simulated failure and retry, source detail returning to results, Escape with focus restoration, and a 400px-high window with scrolling results. Search and navigation fit at 320px without horizontal overflow.

The backend suite covers selective hook guidance without transcript uploads, optional agent instructions, owner-only intake receipts, expired/refreshed connections, import-versus-live status, private/shared access, on-demand overviews and independent Luna defaults. The September 21 spacing review also covers connected setup, model-save feedback, expanded models, gateway editing, Slack access recovery, Settings disclosures, Connections instructions, feed, Ask and search at narrow widths. Ruff and frontend syntax/format checks pass. UI capture was simulated through a disposable fixture; these checks do not prove a real host connection, external model quality or live Slack OAuth. The release checklist tracks those separately.

Use the [release checklist](release-checklist.md) for deployment status and `CONTRIBUTING.md` for repeatable checks. Previous development screenshots and chronological review logs were removed; Git history retains them. This is not a claim of a flawless interface or a complete screen-reader/browser certification.
