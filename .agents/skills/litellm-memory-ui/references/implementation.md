# Implementation guide

## Work with the actual application

The dashboard is plain HTML, CSS, and ES modules served by the Python app. There is no frontend build step. Preserve this simplicity unless a separately requested change justifies a different architecture. Do not introduce a component framework, icon package, remote font dependency, or CSS-in-JS layer for a visual refinement.

The canonical implementation is `static/memory.html`, `static/memory.css`, and `static/memory.js`. Setup and transfer logic have their own modules. Shared primitives live in `static/ui.js`. Standalone auth/transfer/connector pages share `memory.css`; include them in a cross-app design change.

Files in ignored `scripts/local/` are local development artifacts and may contain sensitive material. Do not bulk-read that directory, commit it, or assume its preview fixture is available to a fresh contributor. Inspect specifically relevant files only. Use the tracked tests and documented deployment as reproducible sources.

## Edit shared rules at their source

Use the semantic type tokens and existing classes. Avoid appending a second block of overrides for the same component after every visual iteration. Consolidate obsolete rules and responsive exceptions once the change is proven.

Keep sections of CSS organized by shared foundations, shell, screens/components, standalone flows, and responsive rules. A breakpoint changes layout geometry first. Name exceptions by component or role, not by the screenshot that motivated them.

Before changing a selector, find all callers. `.form-footer` and `.model-grid` are used in setup as well as Settings. `.auth-card` also serves authentication and transfers. A global fix must be reviewed on those surfaces; a specific variation should get a narrow class such as `.guide-page`.

Do not style IDs for ordinary reusable visual roles when a class would express the meaning. IDs remain appropriate for JS hooks and unique behavior. Do not rename them casually during a markup rewrite; handlers depend on them.

## Form pattern

```html
<form id="example-form" class="section-controls">
  <label class="field-label" for="example-value">Field name</label>
  <input id="example-value" aria-describedby="example-help example-error" />
  <p class="field-help" id="example-help">A consequence or format hint.</p>
  <p class="inline-error" id="example-error" role="alert"></p>
  <div class="form-footer">
    <span id="example-status" role="status"></span>
    <button class="button primary">Save changes</button>
  </div>
</form>
```

Adapt to a real request and existing API. Do not copy example IDs into multiple forms or add generic help where none is useful. Native labels, inputs, buttons, forms, details, and dialogs provide behavior that should not be reimplemented without need.

For a pending operation, use the shared `busy` primitive. Preserve drafts after errors. Prefer disabling the action being submitted rather than making the entire page inert. If fields can change while a request is in flight, retain a clear request snapshot and avoid reporting the edited draft as saved when only the earlier value was persisted.

## Dynamic content and icons

Use `element()` and `textContent` for user/model/API strings. Rich Markdown goes through the existing parser and sanitizer; never insert a model's HTML directly. Preserve citation handling and source authorization.

Static SVG paths are maintained in one map in `memory.js`. `icon(name)` produces one SVG with a consistent viewBox and hidden decorative semantics. In HTML use one `data-icon` placeholder. In dynamic rendering append the SVG once.

Temporary copy feedback uses `copyText`, which restores the original child nodes, including icons. Do not replace the button's markup permanently with text after copying. Visible feedback should match the actual clipboard result, not just a click.

Avoid generated CSS text for meaningful labels or commands. Decorative generated symbols can leak into accessibility output inconsistently. Prefer a real decorative SVG with `aria-hidden` when indicating an action.

## State and race handling

Use the existing request-version/session-version checks. A late response from a previous person, scope, route, or session must not repaint the current view. Do not discard these guards while simplifying rendering.

Maintain distinctions between visible draft state, server-saved state, queued work, completed processing, and published summaries. Style cannot collapse those states into one “ready” badge.

Initialization must not show a full-screen loading block above an already displayed workspace. Keep the loading state localized and avoid large layout jumps. A route should settle on the intended page without leaving stale content visible after an auth failure.

Use returned data, not inferred availability, for counts, model catalogs, Jev readiness, sharing permissions, and admin controls. Client hiding is not an authorization boundary; preserve server checks.

## Privacy and credentials

The frontend's private memory lives in the current tab's memory; do not persist it in localStorage/sessionStorage for convenience. Do not log prompts, answers, source text, tool arguments, account identifiers, or credentials to console/telemetry as part of visual debugging.

Synthetic preview records must remain synthetic. Do not populate screenshots from another person's live private history or copy production data into fixtures. Keep keys and deployment state outside Git.

Never weaken CSP, authentication, cross-origin protection, or data access to make a screenshot easier. A local fixture can deliberately simulate external services, but that must stay isolated and be described honestly.

## New component decision

Before adding an abstraction, answer:

1. Is there a repeated interaction or visual role, or just a one-off layout need?
2. Which existing primitive is closest, and why does it fail this case?
3. What is the smallest reusable API that preserves semantics and states?
4. Which existing callers should use it now, and which should remain untouched?

A CSS class or small shared DOM helper is often enough. Avoid a generic configuration-driven screen engine, a large polymorphic component, or duplicated near-identical components for each page.

## Verification and maintenance

Run relevant commands from `CONTRIBUTING.md`:

```sh
uv run pytest -q
uv run ruff check .
node --check static/memory.js
node --check static/setup.js
node --check static/migration.js
node --check static/ui.js
```

Choose checks proportional to the actual change. Browser verification is essential for layout; syntax checks cannot prove it. Add behavior tests for new state transitions or authorization/data handling, not snapshot tests that merely repeat the exact copy or CSS implementation.

Use the repository formatter when available. Keep markup, selectors, and event handlers straightforward to review. Check for duplicate IDs, missing JS-bound elements, broken internal links, and unused rules after a substantial restructuring.

Update the relevant skill reference when introducing a maintained pattern. Update `docs/ui-review.md` and current assets when a cross-app change makes them stale. Do not retain multiple contradictory “final” screenshot sets in the public project.
