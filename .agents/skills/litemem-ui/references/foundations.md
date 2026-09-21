# Foundations

## Contents

Product character · attention and hierarchy · typography · color · layout and spacing · surfaces · icons · responsive behavior · extending the system.

## Product character

litemem should feel like a well-made workspace: familiar, composed, and easy to read. The content is the interesting part. A person should recognize a colleague, scan a useful update, and ask a question without interpreting decorative UI.

The product can feel social through real Slack identities, authorship, chronology, and shared work. Do not introduce popularity signals, reactions, recommendation ranking, achievements, streaks, or synthetic activity as visual filler. These require a separate product request.

A clean interface can contain detail. The requirement is that detail arrives in the right place and at the right time. A model identifier is useful in the model picker; a storage implementation paragraph beside a feed entry is not. A private label matters; a badge for every internal processing stage does not.

## Attention is a limited resource

For each screen, write an eye path in ordinary words. Examples:

- Activity: where I am → ask or choose Company/My work → entry title → useful context → source and date.
- Settings: page → selected category → section purpose → named control → its consequence → save action.
- Ask with an answer: question → answer → supporting citations → follow-up composer. Model selection is secondary.
- First run: what this connection enables → required credentials → check result → next available action.

A static layout only expresses intended attention; it is not evidence from eye tracking. Verify through a quick scan, actual interaction, and user feedback. Do not present guessed gaze paths as measured facts.

Use emphasis in this order:

1. Position and grouping: place the relevant information together and in reading order.
2. Space: create a larger separation between tasks than between a label and its input.
3. Weight and contrast: distinguish headings from explanation and enabled actions from supporting controls.
4. Size: use the limited type scale.
5. Color or a surface: reserve these for meaningful differentiation or interactive boundaries.

If everything is bold, outlined, or in a card, none of it has priority. If every paragraph is very light and very small, the screen is merely difficult to read. Both are hierarchy failures.

## Typography

Use the locally bundled Inter variable font, with the existing system-font fallback. Do not load a new font from a third party or mix display faces into the product.

| Role | Default | Weight | Line height / use |
| --- | --- | --- | --- |
| Page title | 24px / `--text-page` | 650 | 1.3; one per page, tight tracking around -0.7px |
| Normal section or feed title | 16px / `--text-section` | 600 | 1.4–1.5; scanable title, not a hero |
| Settings section heading | 14px | 600 | 1.4; grouping is reinforced by layout |
| Reading body | 14px / `--text-body` | 400 | 1.65–1.75; answers, source content, feed excerpts |
| Labels, control text, concise explanations | 13px / `--text-control` | 400–550 | 1.5–1.65; labels have more weight than input values |
| Metadata and field help | 12px / `--text-meta` | 400–500 | 1.5–1.6; dates, origin, explanatory detail |
| Tiny utility exceptions | 10–11px | 400–550 | Existing footer, keyboard hint, narrow navigation labels; never primary instructions |
| Source-dialog title | 20px | 600 | 1.4; allow wrapping |
| Empty Ask prompt | 24px | 600 | 1.35; the prompt is content, not marketing |

The scale is semantic, not a global rule that every `h2` must look identical. An `h2` inside a settings section is a compact group label; a feed title is the content people are scanning. Keep these exceptions few, named, and documented.

### Inputs and placeholders

- A visible label remains visible after typing. Placeholder text cannot carry the only description of a field.
- Desktop inputs use 13px regular text. An Ask writing area uses 14px to match reading text.
- Placeholders use the same size as the control, regular weight, and a secondary text color. They should look like examples, never headlines.
- Explain required format in help text if it must remain available after typing. Use an example as the placeholder, not an entire instruction paragraph.
- Use a short example that still makes sense if clipped in a narrow input. Do not widen the entire page to fit a placeholder.
- On small touch devices, the current input rule uses 16px to avoid unwanted input zoom; placeholders remain 14px. Apply this through pointer/device context, not width alone. A 687px desktop pane is not automatically a phone.
- Native selects must remain legible with long gateway model identifiers. Do not create a custom dropdown just to hide their native appearance.

### Text length and wrapping

Use natural sentence case. Avoid all-caps page or section labels. The small existing first-run eyebrow can identify product context, but do not add one above every heading.

Body copy should generally fit within roughly 50–80 characters per line when reading sustained prose. Short setup or settings explanations can be narrower. Treat this as a reading guide, not a hard width that breaks forms or localization.

Long names, source titles, model IDs, project names, and error messages are normal states. Wrap prose and headings. Truncate only secondary summaries or space-constrained identity labels, with a full view available. Never silently truncate the destination or consequence of a destructive action.

## Color and contrast

Current shared tokens:

| Token | Value | Intent |
| --- | --- | --- |
| `--bg`, `--paper` | `#ffffff` | Main surface |
| `--sidebar` | `#f8f9fa` | Quiet navigation background |
| `--ink` | `#24272d` | Titles, important labels, primary button |
| `--body` | `#4e5560` | Reading/supporting text that needs comfortable contrast |
| `--muted` | `#69717d` | Secondary explanation and metadata |
| `--line` | `#e9ecf0` | Decorative separators, not a semantic signal by itself |
| `--strong-line` | `#d9dde3` | Existing border treatment; assess contrast when it is the only control boundary |
| `--soft` | `#f5f6f7` | Quiet fill, code, user messages |
| `--accent` | `#5357c7` | Links and focus; not a blanket purple UI theme |
| `--danger` | `#b42318` | Error/destructive meaning |

Do not communicate private/public, saved/failed, or on/off through color alone. Add text or an unambiguous state indicator. A light divider is decorative; a required control boundary or state indicator needs its own contrast review.

Do not assert contrast compliance from a palette table. Check the actual foreground/background, font size and weight, disabled state, focus state, and device rendering. Use stronger text instead of repeated pale-gray nested descriptions. Never lower opacity on an entire content group just to create hierarchy.

## Layout, rhythm, and alignment

Use a small recurring spacing vocabulary: 4, 8, 12, 16, 20, 24, 28, 32, 40, and 48px. Values are a working vocabulary, not a requirement to retrofit every icon offset to a multiple of four.

- 4–8px connects tightly related content: icon/label, label/control, name/metadata.
- 12–16px groups controls or text within a task.
- 20–28px separates list items or task blocks.
- 32–48px separates major columns or page regions.
- A label is closer to its own input than to the field above it.
- An action sits closer to its own form than to the next section.
- Align headings, explanatory paragraphs, fields, and separators to a few shared edges.

Desktop shell: 208px sidebar, reducing to 190px at the intermediate breakpoint. Main page uses 40px top / 48px side padding and a bounded width. A wide monitor should add outside breathing room, not stretch text indefinitely.

Settings: section purpose in a 210px column, 40px gap, controls in the remaining width. The section begins and ends as one group. Collapse this to a single reading column at 960px viewport width before the controls become cramped. Do not preserve two columns merely for visual symmetry.

Activity: a primary reading column and a 200px contextual rail with generous separation. At 960px and below, the secondary rail yields to content; the team directory remains available through People.

## Surfaces and borders

Prefer plain sections with spacing and a quiet separator for settings and lists. Use a bounded surface when it clarifies an interactive object: composer, connection instructions, code, dialog, or contextual note.

- Buttons and inputs: about 6px radius.
- Navigation items and ordinary containers: 6–8px.
- Composers and user-message surface: 10px.
- Dialogs: 12px.

Use no shadow for ordinary settings groups. A restrained shadow can separate an overlay or sticky composer from content beneath it. Avoid frosted glass, gradients, ornamental glows, colored outlines, nested cards, and oversized empty containers. These are not part of the current language.

## Icons

Use the existing Lucide paths on a 24px viewBox, stroke width 1.7, round caps/joins. Default visible size is 18px; navigation and dense controls can use 14–18px, private metadata 12px. Keep icon weight optically consistent with its label.

An icon provides recognition, not another heading. Do not enlarge icons to fill empty space. Avoid emoji as interface chrome and Unicode symbols where the shared SVG system provides the same action.

Use one SVG per icon. Do not wrap an already-rendered SVG in another icon component. Mark decorative SVGs `aria-hidden="true"`; put the action's accessible name on the button. Preserve the icon when temporary feedback such as “Copied” ends.

## Responsive intent

Responsive design changes layout before text hierarchy:

- Wide desktop: persistent sidebar, bounded content, side-by-side Settings descriptions/controls.
- Intermediate: reduced page padding; drop the activity rail; stack Settings sections as needed.
- At 700px: navigation becomes a compact top shell; use full available content width.
- At 560px: compact icon-plus-label navigation, single-column model forms, wrapped inline forms.
- At 320px: every destination remains reachable and named; no page-wide horizontal overflow.

Do not hide Settings or Connections behind unfamiliar unlabeled icons just to keep the nav on one line. Do not compress a model selector until its value is unreadable. Do not shrink text to make a broken layout fit. Test portrait, short windows, zoom, and long content as well as nominal widths.

## Extending the language

When asked for a new surface, map it to an existing task pattern first. A notification preference is a Settings section; a source import status is a list/status task; a follow-up question belongs to Ask. Reuse the same components and size roles even if the content is new.

Introduce a new pattern only when the old one materially fails the task. Explain the failure, implement the smallest shared addition, and document where it applies. Do not create an app-wide abstraction for a one-off margin or invent a new product feature to showcase the design system.
