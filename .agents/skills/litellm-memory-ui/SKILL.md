---
name: litellm-memory-ui
description: Design, implement, and review LiteLLM Memory screens using the project's visual hierarchy, interaction patterns, product language, and privacy semantics. Use for LiteLLM Memory UI changes and extensions, including setup, activity, chat, settings, and responsive states.
---

# LiteLLM Memory UI

Build an interface people can understand before they read instructions. LiteLLM Memory is a private, queryable history of work, with company sharing and an explicit Ask interface. Its character comes from clear organization and careful details: quiet, precise, useful, lightly social. It is an internal work tool, not a marketing page or engagement feed.

These are LiteLLM Memory's project conventions, not universal design rules. Current user instructions and verified product behavior override this document. Preserve existing functionality, originals, privacy boundaries, and unsaved input. A UI task does not authorize changing who can access data, enrolling people, or deploying to production.

## Start with the task, then the eye path

Before writing markup, identify:

- What brought the person here, and the single outcome this surface helps them reach.
- The first three things they should notice, in order.
- The minimum information needed to act correctly; what can wait behind a disclosure.
- The scope of each action: this field, this form, this memory, this person, or the workspace.
- The initial, loading, populated, empty, error, and restricted states that actually apply.

Use the existing page and component patterns before inventing another one. A visual refresh is not permission to add product features, taxonomies, dashboard metrics, badges, or new navigation levels.

## Read the relevant references

For any meaningful design change, read [Foundations](references/foundations.md). Then choose the smallest relevant set:

| Work | Reference |
| --- | --- |
| Forms, tabs, lists, buttons, dialogs, status, focus | [Components and interaction](references/components.md) |
| Activity, Ask, People, Connections, Settings, onboarding, transfer | [Screen recipes](references/screens.md) |
| Names, labels, private/company meaning, explanations, errors | [Product language](references/language.md) |
| HTML/CSS/JS structure, primitives, new patterns, repository integration | [Implementation](references/implementation.md) |
| Browser review, responsive and keyboard checks, evidence, Nielsen criteria | [Review](references/review.md) |

For a whole-app redesign, read all six. For a text change, use language plus the affected component; do not run an unrelated deployment audit. The depth is available when it changes a decision.

## Essential design contract

1. **Content has priority.** Page titles orient; section titles group; labels identify; helper text supports; placeholders only demonstrate. Do not promote supporting text by making it larger.
2. **Structure creates hierarchy.** Group related controls, align edges, and separate tasks with spacing or a rule before adding another card, color, or heading size.
3. **Controls have a stable scale.** Desktop defaults are 24px page titles, 16px section/content titles, 14px body, 13px controls/labels, and 12px metadata. Settings sections use the same compact 16px section heading. Narrow desktop panes do not inflate input text.
4. **One dominant action per task group.** Scope actions to the relevant form. Routine controls stay visually quiet; risky actions are separated and named by consequence.
5. **Private is explicit.** Preserve lock plus text; distinguish shared summaries from private original conversations. Never imply that a selected query scope publishes the answer.
6. **Simple is not vague.** Keep visible labels, recovery actions, real status, and discoverable navigation. Remove duplication and decoration first.
7. **Do not make the user decode implementation.** Gateway and model configuration belong in setup/admin contexts. Ordinary work should read as work, memories, people, and questions.
8. **Review actual states.** Inspect the rendered interface at real desktop and narrow widths. A screenshot of the happy path alone is insufficient.

## Implementation anchors

Paths below are relative to the repository root, three levels above this skill directory:

- `static/memory.css`: tokens, shared visual rules, components, responsive behavior.
- `static/memory.html`: workspace shell, pages, forms, and dialogs.
- `static/memory.js`: rendering, permissions, state transitions, source dialogs, query interactions.
- `static/ui.js`: shared element, busy, copy-feedback, and confirmation primitives.
- `static/setup.js`, `static/migration.js`: first-run and transfer flows.
- `static/auth.html`, `static/transfer*.html`, connector setup in `litellm_memory/application.py`: standalone surfaces using the same language.
- `docs/ui-review.md`: current screenshots and evidence scope.

CSS is the source of truth for implemented values. References describe intended use and rationale. If they disagree, inspect the current user request and code; fix the discrepancy deliberately and update both. Do not copy an obsolete screenshot's mistakes just because it is documented.

## Finish with evidence

Inspect affected screens, including adjacent states and narrow layouts. Test interaction changes, preserve keyboard access, and check the console. Report what changed and what was verified; state any material untested boundary. Update current screenshots and these references when the design contract changes. Do not claim perfection, accessibility certification, or live integrations from a synthetic preview.
