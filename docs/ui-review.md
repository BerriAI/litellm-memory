# Interface guide

The UI uses one sidebar, a chronological feed, explicit private/company labels, and a single-column Ask conversation. Search and advanced filters stay out of the way until needed. Icons use one Lucide grid and local font assets; there is no frontend build step or external font request.

## Activity

Ask directly, browse your work or company summaries, and open an entry to inspect its source. Private entries carry both a lock and a label.

![Activity](assets/dashboard.jpg)

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

Review covers Activity, Ask, People, Connections, settings and setup, plus narrow layouts, visible privacy labels, keyboard focus, citations, filtering and recoverable errors. Native dialogs provide Escape and focus trapping; operations retain drafts when saving fails. Markdown is sanitized and private content is not persisted in browser local storage.

Use the [release checklist](release-checklist.md) for deployment status and `CONTRIBUTING.md` for repeatable checks. Previous development screenshots and chronological review logs were removed; Git history retains them. This is not a claim of a flawless interface or a complete screen-reader/browser certification.
