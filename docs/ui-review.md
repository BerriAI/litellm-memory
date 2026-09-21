# Interface guide

The UI uses a shared hierarchy: 24px page titles, 16px content titles, 14px body, 13px controls, and 12px supporting detail. Settings groups pair a concise purpose with their controls and related action, stacking into one column in narrow windows. Placeholders remain secondary; narrow desktop panes do not trigger touch-sized input text.

The UI uses one sidebar, a chronological feed, explicit private/company labels, and a single-column Ask conversation. Search and advanced filters stay out of the way until needed. Icons use one Lucide grid and local font assets; there is no frontend build step or external font request.

The LiteLLM Memory wordmark uses 17px text so the full name stays readable at desktop and mobile widths.

The maintained [LiteLLM Memory UI skill](../.agents/skills/litellm-memory-ui/SKILL.md) explains the design language and how to extend it.

## Activity

Company is the first tab and opens by default; My work is second. Ask about the selected feed, or open an entry to inspect its source. Private entries carry both a lock and a label.

![Activity](assets/dashboard.jpg)

## People and recording

Select a person from the feed or directory to browse their shared work. The person’s name and query scope stay visible, and the route survives reload and Back.

![Person activity](assets/person.jpg)

Connections provides inline tool setup, optional history import, and a server-confirmed receipt. It distinguishes authorization from saved work; returning members can collapse setup instructions.

![Recording status](assets/connections.jpg)

## Ask

Choose the memory scope and a gateway model. Responses include clickable source citations. Stop cancels work; changing scope confirms before clearing a conversation.

![Ask](assets/ask.jpg)

## Privacy

Memories start private. Future sharing is opt-in. Write topics that Jev should keep private; no predefined category grid is required. Existing restrictive policies remain enforced.

![Privacy](assets/settings.jpg)

## Sources and metadata

A summary links back to its evidence. The original and metadata remain available without filling the feed with raw JSON.

![Source detail](assets/memory.jpg)

## Review scope

These are screenshots of the actual local application with synthetic records and simulated model replies. They do not establish live gateway or Slack readiness.

The core-loop review covers joining → Company → tool instructions → authorized/waiting → first received work → My work, person browsing/reload/Back, Ask scope, sharing preview, saved-filter editing, and recording pause. Browser checks include 1280px, 390px and 320px layouts, keyboard Settings navigation, source-dialog dismissal and recording-status failure/retry. Long connection commands scroll inside their own block without widening the page. No JavaScript console errors were observed.

The backend suite passes **157 tests**, including owner-only intake receipts, expired/refreshed connections, import-versus-live status, private/shared access, on-demand overviews and independent Luna defaults. Ruff and frontend syntax/format checks pass. UI capture was simulated through a disposable fixture; these checks do not prove a real host connection, external model quality or live Slack OAuth. The release checklist tracks those separately.

Use the [release checklist](release-checklist.md) for deployment status and `CONTRIBUTING.md` for repeatable checks. Previous development screenshots and chronological review logs were removed; Git history retains them. This is not a claim of a flawless interface or a complete screen-reader/browser certification.
