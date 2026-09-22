# Components and interaction

## Contents

Page headers · navigation and tabs · settings sections · fields · actions · lists · chat · dialogs · status and errors · keyboard and focus.

## Page header

Use `.page-heading` with one `h1`, optionally a `.page-description`, and a single related page action. Descriptions explain the task; they do not repeat the heading in a sentence. Activity uses the date instead of an unnecessary slogan. Ask does not need both a subtitle and a banner explaining what Ask means.

Keep the title aligned with the content below it. Right-side actions align with the title row, not the bottom of a title-plus-description block. Keep them on the same row when they fit; wrap naturally when they do not. Do not add breadcrumbs to these shallow top-level pages or a second workspace navigation bar above the sidebar.

## Navigation and tabs

The main destinations are Activity, Ask, People, Connections, and Settings. In the narrow top shell, center the wordmark, labelled Search button and account control on one row; place the five destinations in an evenly spaced row below. Do not leave utility links stranded at the far edge. The selected item is clear through weight and shape or underline. Icons complement labels. The account menu contains sign-out; do not repeat it after unrelated Settings forms.

Use tabs only for sibling panels in one context. Settings uses Sharing, Preferences, Your data, then Workspace for admins. Recording controls belong in Connections. Do not add a category to accommodate one minor field that already belongs in a section.

Settings tabs are actual `role="tab"` controls in a `tablist`, with `aria-selected`, `aria-controls`, and a labelled `tabpanel`. One selected tab participates in the Tab sequence. Left/Right moves among visible tabs; Home/End reaches the first/last. Hidden admin tabs are excluded. Keep focus visible inside the tab strip even if it scrolls horizontally. Tabs switch already available panels without moving keyboard focus into them unexpectedly.

Do not give all tabs `aria-pressed`: these select panels, rather than independently toggling settings. Feed visibility can remain a labelled group of mutually exclusive pressed buttons because it filters the current view rather than switching a set of tab panels.

## Settings section anatomy

```html
<section class="settings-section">
  <div class="section-copy">
    <h2>Private topics</h2>
    <p>Keep specific topics out of shared summaries.</p>
  </div>
  <div class="section-controls">
    <!-- Labelled field or setting row, help, related action/status. -->
  </div>
</section>
```

Use one reading sequence at every width: section purpose, controls, then the related action. Bound the content width so explanations and fields stay close. Do not put an action between the explanation and its unrelated next section.

Use `.setting-row` for a compact name/explanation with a switch or action. A switch has a visible label and a programmatic relationship to that label. Its current value must remain discernible beyond color. Keep longer consequence text below the name, not as part of a large heading.

Separate adjacent independent tasks with a quiet horizontal rule and vertical padding. Do not border every sentence, wrap a setting row in another card, or place a decorative icon beside every section name.

### Save behavior must be unambiguous

Respect the current API behavior:

- Sharing preferences use a Save changes form.
- Recording and personal-agent switches currently save on change, with completion/failure feedback.
- Models use Check & save models because validation happens before persistence.
- A new private topic submits independently with Add topic.

Do not make a control look immediately applied if it only changes a draft. Do not silently convert a save-based form to autosave during a styling task. If the task specifically changes save behavior, handle pending state, failure rollback or retained draft, and simultaneous edits.

Place a page form's save action at the left edge below its controls, followed by status in the same footer. Keep that same order in the HTML so reading and keyboard order match. On small screens allow status to occupy its own line; an empty status must not reserve space. Dialog confirmation actions retain their right alignment.

### Spacing ownership

Use `.form-stack` for sequential fields and actions: the parent owns a 16px gap, and direct children have no outer vertical margin. Use `.form-field` to keep one label/control group and its helper together with a 6px gap. Labels contain their control with a 7px gap. Do not add a second field margin inside a grid or stack.

Setup panels separate task groups by 20px and use 24px padding around section rules. The section title and description stay together with a 6px gap. Action explanations sit 12px above their button. Helper text remains 12px; a broad paragraph selector must not enlarge it. Disclosures use 16px between their summary and expanded content. Review both collapsed and expanded states, populated status, errors, and hidden sections; hidden children must not leave empty layout rows.

## Fields

Use native inputs/selects/textareas when they meet the task. Prefer visible labels above controls; reserve visually hidden labels for a well-understood search/composer context that has an accessible name.

A field's order is label → control → help/error. Use `aria-describedby` for associated descriptions and validation messages. Keep labels concise enough to scan as a form, not a paragraph. Supporting text explains why or what happens, rather than repeating the input's noun.

Use two columns only for short related fields with comparable importance, such as default agent and embeddings. Stack them before they become cramped. A long description, credential, URL, or topic does not need a matching neighboring input just for symmetry.

Use appropriate types and autocomplete semantics. Password fields never echo secrets into a status line or console. A placeholder is not a saved value; keep that distinction visible.

### Field states

| State | Expected behavior |
| --- | --- |
| Empty | Visible label, useful short example where needed, honest required/optional state |
| Focused | Clear focus indicator, no layout shift, label still visible |
| Entered | Same type scale; value survives recoverable failure |
| Invalid | State what needs fixing next to the field; preserve the entered value |
| Submitting | Disable the submitting action, mark busy, prevent duplicate submissions |
| Failed | Explain the failure and offer retry or correction in context |
| Saved | Confirm the specific outcome; do not imply a queued process has finished |
| Disabled | Explain the prerequisite when it is not obvious; do not erase relevant values |

For asynchronous processing, saving a rule and completing its historical classification are different events. Report “Topic added” and “Checking existing work” only when the server supplies that status. Do not invent percentages or remaining time.

## Buttons and links

- `.primary`: one dominant action in a task group; dark neutral fill and white text.
- `.secondary`: ordinary explicit action; quiet outline.
- `.quiet` / `.text-button`: lower-emphasis action like refresh, cancel, or edit.
- `.danger`: describes an action with destructive consequences; use the consequence in the label.
- `.icon-button`: familiar space-constrained operation with an explicit accessible name.

A navigation destination uses a link where possible; a state change or submission uses a button. Set `type="button"` for non-submit actions inside forms. Do not add chevrons to every action by default. An arrow means direction; a lock means privacy; neither is decoration.

Desktop controls are compact, generally 36–40px high. Touch contexts use larger targets, generally at least 44px. These are project defaults; assess actual target spacing and accessibility requirements rather than counting icon pixels.

Avoid a full-width primary button on a large screen unless the task is intentionally a small single-action form. On phones, a full-width action can be appropriate if it reduces ambiguity rather than creating a giant footer.

## Lists and rows

A row should let the person answer “what is this?” before “what can I do to it?”. Use a title/name first, context next, and secondary metadata last. Keep actions aligned so their position is predictable from row to row.

Feed entries have author, private/company meaning, time, a meaningful title, a readable excerpt, and source metadata. Put source details and raw metadata in the detail view. Do not present every internal label as a chip. Keep the main title clickable and independently keyboard accessible.

People rows contain identity, a brief scope explanation, and Ask. Do not make the whole row an ambiguous button containing nested buttons. Long names wrap or truncate without moving the action offscreen.

Connection rows state the client and relevant connection metadata, with revoke separate. A disconnected state must not look like an empty/loading list. Keep raw connection IDs secondary unless necessary for diagnosis.

## Chat

One reading column. The answer should be the largest content region, not the model picker, assistant avatar, or tool trace. Use an understated user-message surface; responses can be plain text flow with rendered Markdown and citations.

The composer contains writing area first, scope/model controls second, and the send/stop action. Omit repeated privacy explanations. Scope and model selections must remain readable and operable when long. On narrow screens wrap controls instead of clipping the send button.

Suggested questions are optional examples of useful work, not large feature tiles. They should not outrank the input or the empty-state prompt. Generated icons are decorative and must not become part of the submitted question text.

Maintain clear states for waiting, responding, stopping, failed, cancelled, and finished. Do not call a query “complete” merely because a request was accepted. Keep a partial response distinguishable from a finished answer.

Citations are real links to accessible evidence. The number of citations is secondary; evidence content is available on demand. Never fabricate source links to make a mock feel complete.

## Dialogs

Use native `<dialog>` for source details, export protection, transfer setup, and meaningful confirmations. Provide a named heading, close/cancel action, sensible initial focus, Escape handling, and focus restoration. A source dialog can be wider than a short confirmation.

The visual sequence is title/audience → content → secondary detail → actions. Keep close at a predictable top corner. Make long content scroll within the dialog without losing access to its exit. Check short viewports and zoom.

In a source detail, sharing preview is prominent; correction and deletion are under More actions. These controls must retain the original/source distinction. Do not reintroduce a mandatory taxonomy or manual-note authoring into the main feed.

Confirmations name the affected object and consequence. Focus Cancel initially for destructive actions. Do not add a confirmation to ordinary navigation, every save, or read-only disclosure. Do not use confirmation wording that implies access a person does not have.

## Status, empty states, and errors

Status belongs beside the affected operation. Use toast feedback for a brief completed action; use persistent inline feedback for a failure or unfinished task the person may need to address. A toast is not the only place to report a blocked save.

Empty states answer what is empty, why if known, and a useful next action. Distinguish no records, no matches, filtered records, no shared work, lost access, and loading. “No memories” is wrong when the request failed.

Use `role="status"` or a polite live region for completion/loading feedback, and an appropriate error announcement for failures. Avoid reading the whole chat stream or feed aloud on every update.

Error text explains the result and next step: “Couldn’t save this topic. Your text is still here; try again.” Show details useful to recovery; do not surface stack traces, secrets, or internal payloads.

## Keyboard and focus

All primary flows must be usable without a pointer. Preserve the skip link, visible focus, logical source order, tabs' arrow behavior, and native dialog handling. Hover cannot be the only way to reveal necessary actions.

When an action removes the focused element, restore focus to a meaningful survivor. Do not send focus to the page start after every background update. If a route focuses `main`, suppress only that container's unnecessary outline, not all outlines globally.

Enter sends in Ask, Shift+Enter makes a new line, and IME composition must not submit prematurely. A native select keeps native keyboard behavior. Global shortcuts do not intercept text editing without care.
