# Review and evidence

## Review to answer questions, not to count passes

A whole-app revamp needs breadth and depth. Repeating the same happy-path screenshot twenty times gives little evidence. Use successive passes with different questions and fix what they reveal. Stop when the scoped flows are coherent, the relevant checks pass, and remaining limits are explicit; do not claim an interface is flawless.

## Pass 1: attention and scope

Look at the rendered screen before reading every sentence. Identify its first three visual priorities. Are those the user's first three needs? Does a placeholder, empty card, destructive action, model selector, or low-frequency admin control steal attention?

Then read in DOM order. Does the structure make sense without visual position? Are the heading levels meaningful? Is supporting text explaining a decision or just taking up space? Remove redundant sentences before shrinking typography.

Compare neighboring pages. Page titles, field labels, help, action buttons, and separators should have equivalent emphasis for equivalent roles. A screen should not introduce a new hierarchy because it was implemented later.

## Pass 2: geometry and content stress

Inspect wide desktop, the user's actual narrow pane, tablet/intermediate widths, phone, and a 320px narrow case. Typical useful samples are 1280, 1024, 687, 390, and 320px; these are test samples, not a device support claim.

Check:

- Main content has no unintended horizontal scrolling. A code block or tab strip can have its own intentional overflow.
- Labels remain visible; placeholders do not become larger than labels in narrow desktop panes.
- Long names/model IDs/titles wrap or truncate at an appropriate boundary.
- Forms retain their label/control/help/action relationship when columns stack.
- Nav destinations remain identifiable and reachable.
- Selected tabs and focused controls are not clipped by overflow containers.
- Source dialogs and composers fit a short viewport; the close/send/stop controls remain reachable.
- Browser zoom and large text do not hide required information or create overlapping fixed elements.

Evaluate content that is empty, short, long, and unusually formatted. Do not certify a form using only two-character fake model names. Do not add production fixtures just to exercise the layout; use disposable data.

## Pass 3: keyboard and accessibility

Use keyboard navigation through a representative complete task, not only the first field. Check the skip link, visible focus, tab order, Settings arrow/Home/End behavior, native selects, Enter/Shift+Enter in Ask, Escape, and focus return from dialogs.

Confirm controls have programmatic names and descriptions. A visible heading does not automatically label a nearby field. Decorative SVGs stay out of the accessible name; private/shared meaning stays in it when relevant.

Check contrast with actual computed colors and backgrounds. Review controls and focus indicators separately from body text. Light decorative rules are different from essential control boundaries. A useful visual review is not a complete screen-reader or WCAG certification; state the scope accurately.

For accessibility references, use primary guidance: [W3C tab interaction pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/) and [target size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html). The project generally aims for 44px touch controls; this is a design preference, not a claim that every WCAG target-size rule is 44px.

## Pass 4: state transitions

Choose the meaningful states for the changed surface:

| Surface | States to exercise |
| --- | --- |
| Feed | Loading, records, no records, no matches, filters, person scope, failed read/retry |
| Ask | Empty, typing, submitted, processing, answer/citation, stop, failure/retry, scope change |
| Settings | Initial values, edited draft, busy, saved, rejected save, reload, admin/non-admin |
| Connections | No connections, connected clients, setup instructions, connection failure, revoke boundary |
| Source | Accessible, unavailable, long content, metadata, sharing preview, close/reopen |
| Setup | Unclaimed, connection pending, gateway/Jev unavailable, verified models, Slack return, completion |
| Transfers | Waiting for identity/approval, receiving, complete, cancelled, failed, recent imports |

Not every small edit needs every row. For a cross-app review, cover the affected families and record which live transitions were simulated. Do not claim Slack OAuth works because a synthetic session bypassed it.

Check both visual and behavioral outcome. A click succeeding in automation is not proof the saved setting changed. An HTTP 200 is not proof a trace reached Logfire. A “Copied” label is not proof clipboard permission was granted.

## Pass 5: privacy and recovery

Verify the same surface with private and company-visible evidence when the fixture allows it. Private labels must survive responsive reflow. A company summary must not reveal the original via preview, count, title, tooltip, source link, or error message.

Test at least one relevant recoverable failure with the draft retained. Check that retry does not double-create an object. Test stop/close/back where applicable. Do not manufacture dangerous live scenarios or expand access merely to exercise UI; use the existing isolated tests/fixtures.

## Nielsen's heuristics, applied to this product

These checks apply [Nielsen's usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) to LiteLLM Memory; they are design judgments, not a formal certification.

| Principle | Concrete LiteLLM Memory question |
| --- | --- |
| Status visibility | Can someone tell saved, queued, processing, failed, and finished apart? |
| Familiar language | Does this say work/topic/person rather than an internal memory schema? |
| Control and exit | Can they stop Ask, close a dialog, clear a filter, or leave a draft safely? |
| Consistency | Do equal roles have equal typography, placement, and behavior across pages? |
| Error prevention | Are audiences and destructive consequences clear before commitment? |
| Recognition | Are labels and model/scope selections visible without relying on a placeholder? |
| Efficiency | Can frequent users search, use keyboard controls, and reach tools without extra pages? |
| Minimalism | Does every visible element help the current task? |
| Recovery | Is there a specific next action, with recoverable work preserved? |
| Contextual help | Is guidance next to the confusing step rather than in a general tutorial wall? |

## Screenshot evidence

Use actual rendered application screenshots, not generated mockups. Capture representative stable states after fonts/data load. Read each screenshot before making it a deliverable. Keep synthetic content labelled in captions and walkthroughs.

A good whole-app set includes Activity, Ask empty and answered, People, Connections, all Settings categories, a source detail, recording setup and receipt states, and first-run/standalone flows affected by shared CSS. Add at least one narrow view to show reflow. Do not fill the repository with every transient review screenshot.

Keep canonical images in `docs/assets/` and document them through `docs/ui-review.md`. Local scratch galleries belong outside the launch-facing source unless a maintained gallery is explicitly requested. Update or retire stale canonical images after redesigning the related screens.

If a local preview is the handoff, provide its actual URL and say it is local and synthetic. Do not label it a deployment. If the preview process is not reachable, fix it or report that instead of sharing a dead link.

## Final critique

Before finishing, ask:

- Did I simplify the decision path, or only change styling?
- Which old inconsistency did I fix at the shared level?
- Did I make a page cleaner by hiding information people need?
- Did I accidentally change product behavior, permissions, or supported models?
- What will happen with realistic names, long answers, and failed requests?
- Which claims are supported by checks, and which still need live evidence?
- Did documentation and skill examples move with the implementation?

Deliver the change, relevant evidence, and meaningful limits. Do not report an arbitrary audit count or use “perfect” as a substitute for verification.
