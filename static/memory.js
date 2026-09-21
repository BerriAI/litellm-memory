import { initializeSetup } from "./setup.js?v=2";
import { initializeTransfer } from "./migration.js?v=1";
import { marked } from "./vendor/marked.esm.js";
import DOMPurify from "./vendor/purify.es.mjs";
import { element as el, busy, confirmAction, copyText } from "./ui.js";

// Private content lives in this tab's memory only, never local/session storage.
const $ = (id) => document.getElementById(id);
initializeTransfer();
const state = {
  me: null,
  people: [],
  view: "home",
  feed: "shared",
  person: null,
  next: null,
  feedVersion: 0,
  models: [],
  modelSettings: null,
  previousJob: null,
  job: null,
  chatVersion: 0,
  toastTimer: null,
  overviewVersion: 0,
  memoryVersion: 0,
  settingsLoaded: false,
  cancelRequested: false,
  settingsSection: "privacy",
  chatScope: "mine",
  chatRequest: null,
  captureRequest: null,
  sessionVersion: 0,
  viewPreferences: null,
  preferencesSaving: false,
  schedule: null,
};
// Lucide paths: one 24px grid and one stroke system, no nested icon wrappers.
const icons = {
  activity:
    '<rect width="18" height="18" x="3" y="3" rx="2" />\n  <path d="M21 9H3" />\n  <path d="M21 15H3" />',
  chat: '<path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719" />',
  connection:
    '<path d="M12 22v-5" />\n  <path d="M15 8V2" />\n  <path d="M17 8a1 1 0 0 1 1 1v4a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1z" />\n  <path d="M9 8V2" />',
  settings:
    '<path d="M14 17H5" />\n  <path d="M19 7h-9" />\n  <circle cx="17" cy="17" r="3" />\n  <circle cx="7" cy="7" r="3" />',
  chevron: '<path d="m6 9 6 6 6-6" />',
  close: '<path d="M18 6 6 18" />\n  <path d="m6 6 12 12" />',
  lock: '<circle cx="12" cy="16" r="1" />\n  <rect x="3" y="10" width="18" height="12" rx="2" />\n  <path d="M7 10V7a5 5 0 0 1 10 0v3" />',
  search: '<path d="m21 21-4.34-4.34" />\n  <circle cx="11" cy="11" r="8" />',
  arrow: '<path d="m5 12 7-7 7 7" />\n  <path d="M12 19V5" />',
  next: '<path d="M5 12h14" /><path d="m12 5 7 7-7 7" />',
  plus: '<path d="M5 12h14" />\n  <path d="M12 5v14" />',
  filter:
    '<path d="M2 5h20" />\n  <path d="M6 12h12" />\n  <path d="M9 19h6" />',
  shared:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />\n  <path d="M16 3.128a4 4 0 0 1 0 7.744" />\n  <path d="M22 21v-2a4 4 0 0 0-3-3.87" />\n  <circle cx="9" cy="7" r="4" />',
};
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("icon");
  svg.innerHTML = icons[name] || icons.lock;
  return svg;
}
function show(id, value = true) {
  $(id).hidden = !value;
}
function toast(message) {
  $("toast").textContent = message;
  show("toast");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => show("toast", false), 5000);
}
function explain(error) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
async function api(path, data, method) {
  const sessionVersion = state.sessionVersion;
  const options = {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    cache: "no-store",
  };
  options.method = method || (data === undefined ? "GET" : "POST");
  if (data !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error(
      "Could not connect. Your changes are still here; try again.",
    );
  }
  if (sessionVersion !== state.sessionVersion)
    throw new Error("Your session expired. Sign in with Slack to continue.");
  if (response.status === 401 && state.me?.enrolled) expireSession();
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "The server could not finish this request. Please try again.",
    );
  }
  if (sessionVersion !== state.sessionVersion)
    throw new Error("Your session expired. Sign in with Slack to continue.");
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "This request could not be completed.",
    );
  return body;
}
function date(value, withTime = false) {
  if (!value) return "Date not recorded";
  if (typeof value === "string" && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value))
    return "Time zone not recorded";
  const d = new Date(value);
  if (Number.isNaN(+d)) return "Date not recorded";
  const timeZone = displayZone();
  const year = (value) =>
    new Intl.DateTimeFormat("en", { year: "numeric", timeZone }).format(value);
  return d.toLocaleString(
    undefined,
    withTime
      ? {
          timeZone,
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }
      : {
          month: "short",
          day: "numeric",
          timeZone,
          year: year(d) !== year(new Date()) ? "numeric" : undefined,
        },
  );
}
function displayZone() {
  return (
    state.me?.display_timezone ||
    Intl.DateTimeFormat().resolvedOptions().timeZone ||
    "UTC"
  );
}
function renderToday() {
  $("today").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: displayZone(),
  });
}
function safeImage(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}
function avatar(person = {}) {
  const n = el("span", "", "avatar");
  n.setAttribute("aria-hidden", "true");
  const url = safeImage(person.avatar);
  const initial = (person.name || "?").trim().slice(0, 1).toUpperCase();
  if (url) {
    const img = el("img");
    img.addEventListener(
      "error",
      () => n.replaceChildren(document.createTextNode(initial)),
      { once: true },
    );
    img.src = url;
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    n.append(img);
  } else n.textContent = initial;
  return n;
}
function privacy(value) {
  const p = el(
    "span",
    "",
    `privacy ${value === "public" ? "public" : "private"}`,
  );
  p.append(
    icon(value === "public" ? "shared" : "lock"),
    document.createTextNode(value === "public" ? "Company" : "Private"),
  );
  return p;
}
function profile(id) {
  return (
    state.people.find((p) => p.id === id) || {
      id,
      name: id === state.me?.id ? "You" : "Member",
    }
  );
}
function markdown(target, text) {
  target.innerHTML = DOMPurify.sanitize(
    marked.parse(text || "", { gfm: true }),
    {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["img", "style", "form", "input", "button", "iframe"],
      FORBID_ATTR: ["style"],
    },
  );
  target.querySelectorAll("a").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (href.startsWith("/memory/")) {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openMemory(href.split("/")[2]).catch((e) => toast(explain(e)));
      });
    } else if (/^https?:\/\//i.test(href)) {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    } else {
      a.removeAttribute("href");
    }
  });
}
function expireSession() {
  state.sessionVersion++;
  state.feedVersion++;
  state.chatVersion++;
  state.memoryVersion++;
  state.overviewVersion++;
  state.previousJob = null;
  state.job = null;
  state.people = [];
  state.models = [];
  state.me = null;
  state.settingsLoaded = false;
  state.captureRequest = null;
  state.chatRequest = null;
  state.modelSettings = null;
  state.viewPreferences = null;
  state.schedule = null;
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  document.querySelectorAll("input, textarea").forEach((input) => {
    input.value = "";
  });
  for (const id of [
    "feed",
    "messages",
    "people-list",
    "source-body",
    "source-actions",
    "source-visibility",
    "rule-list",
    "overview-content",
    "view-person-options",
    "view-type-options",
  ])
    $(id).replaceChildren();
  for (const id of [
    "workspace",
    "global-search-button",
    "navigation",
    "header-actions",
  ])
    show(id, false);
  document.body.classList.remove("workspace-ready");
  show("welcome");
  $("welcome-error").textContent =
    "Your session expired. Sign in with Slack to continue.";
}
const viewNames = {
  home: "Activity",
  ask: "Ask",
  people: "People",
  connections: "Connections",
  settings: "Settings",
};
function setView(view, { navigate = true } = {}) {
  if (!viewNames[view]) view = "home";
  const changed = state.view !== view;
  state.view = view;
  document.body.dataset.view = view;
  document.title = `${viewNames[view]} · litemem`;
  for (const name of Object.keys(viewNames))
    show(name + "-view", name === view);
  document.querySelectorAll(".nav-link").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  closeAccountMenu();
  if (navigate && location.hash !== `#${view}`)
    history.pushState(null, "", `/#${view}`);
  if (view === "connections")
    loadConnections().catch((error) => toast(explain(error)));
  if (view === "settings" && !state.settingsLoaded)
    loadSettings().catch((error) => {
      $("settings-status").textContent = explain(error);
    });
  if (view === "people") renderPeople();
  if (changed) {
    window.scrollTo({ top: 0, behavior: "instant" });
    if (view === "ask") $("question").focus({ preventScroll: true });
    else $("main").focus({ preventScroll: true });
  }
}
function closeAccountMenu() {
  show("account-menu", false);
  $("account-button").setAttribute("aria-expanded", "false");
}
function selectSettings(section) {
  if (section === "models" && !state.me?.is_admin) section = "privacy";
  state.settingsSection = section;
  document.querySelectorAll("[data-settings-section]").forEach((group) => {
    group.hidden = group.dataset.settingsSection !== section;
  });
  document.querySelectorAll("[data-settings]").forEach((button) => {
    const selected = button.dataset.settings === section;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
}
window.addEventListener("popstate", () => {
  if (state.me?.enrolled) setView(location.hash.slice(1), { navigate: false });
});
function showEmpty(container, title, description, action) {
  const box = el("div", "", "empty-state");
  box.append(el("h2", title), el("p", description));
  if (action) {
    const button = el("button", action.label, "button secondary");
    button.addEventListener("click", action.run);
    box.append(button);
  }
  container.append(box);
}
function renderCoverage(stats) {
  if (!stats) return;
  state.me.stats = stats;
  $("add-memory").disabled = state.me.capture_enabled === false;
  $("add-memory").title =
    state.me.capture_enabled === false
      ? "Enable recording in Settings to add work."
      : "";
  $("processing-status").textContent = [
    stats.pending ? `${stats.pending} processing` : "",
    stats.failed ? `${stats.failed} need attention` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  $("processing-summary").replaceChildren(
    el("strong", `${stats.ready} of ${stats.total} memories organized`),
    el(
      "small",
      stats.failed
        ? `${stats.failed} items can be retried.`
        : stats.pending
          ? `${stats.pending} items are being processed.`
          : "Up to date.",
    ),
  );
  $("retry-processing").disabled = !stats.failed;
}
async function loadFeed(append = false) {
  if (!state.me?.enrolled) return;
  const hasOverview = state.feed === "mine" || !!state.person;
  show("work-overview", hasOverview);
  $("home-question").placeholder =
    state.person && state.person !== state.me.id
      ? `Ask about ${profile(state.person).name}’s shared work…`
      : state.feed === "shared"
        ? "Ask about your company’s shared work…"
        : "Ask about your work…";
  if (!append) {
    state.overviewVersion++;
    $("overview-content").replaceChildren();
    if (hasOverview && $("work-overview").open) loadOverview();
  }
  const version = ++state.feedVersion;
  show("page-error", false);
  show("retry-feed", false);
  const filterCount = [
    "filter-kind",
    "filter-source",
    "filter-project",
    "filter-from",
    "filter-before",
  ].filter((id) => $(id).value).length;
  $("filter-count").textContent = filterCount;
  show("filter-count", filterCount > 0);
  const query = $("search-query").value.trim();
  const filtered = Boolean(query || filterCount);
  if (
    $("filter-from").value &&
    $("filter-before").value &&
    $("filter-from").value >= $("filter-before").value
  ) {
    $("page-error").textContent = "Choose an end date after the start date.";
    show("page-error");
    return;
  }
  $("feed").setAttribute("aria-busy", "true");
  if (!append) {
    $("feed").replaceChildren(el("p", "Loading memories…", "feed-loading"));
    show("load-more", false);
  }
  try {
    const result = await api("/api/v1/memories/search", {
      query,
      owner: state.person || (state.feed === "mine" ? state.me.id : null),
      visibility: state.feed === "shared" ? "public" : null,
      kind: $("filter-kind").value || null,
      source: $("filter-source").value.trim() || null,
      project: $("filter-project").value.trim() || null,
      created_from: $("filter-from").value || null,
      created_before: $("filter-before").value || null,
      date_timezone: displayZone(),
      offset: append ? state.next || 0 : 0,
      limit: 30,
      mode: query ? "hybrid" : "keywords",
    });
    if (version !== state.feedVersion) return;
    if (!append) $("feed").replaceChildren();
    for (const memory of result.results) $("feed").append(feedCard(memory));
    state.next = result.next_offset;
    show("load-more", state.next !== null);
    renderCoverage(result.coverage);
    show("personal-filters-note", result.personal_filters_active);
    if (!result.total)
      showEmpty(
        $("feed"),
        filtered || result.personal_filters_active
          ? "No matching memories"
          : state.feed === "shared"
            ? "A quiet start"
            : "Your work belongs here",
        filtered || result.personal_filters_active
          ? "Try another phrase or clear your filters."
          : state.feed === "shared"
            ? "Work appears here when someone chooses to share it with the company."
            : "Connect your AI tools to record your work, or add your first memory.",
        result.personal_filters_active && !filtered
          ? { label: "Edit personal filters", run: editViewPreferences }
          : filtered
            ? { label: "Clear filters", run: clearFilters }
            : state.feed === "mine"
              ? {
                  label: "Connect your tools",
                  run: () => setView("connections"),
                }
              : null,
      );
    $("feed-caption").replaceChildren();
    if (state.person) {
      const label = el(
        "span",
        profile(state.person).name + "’s work",
        "person-filter",
      );
      const clear = el("button", "Clear");
      clear.onclick = () => {
        state.person = null;
        loadFeed().catch((e) => toast(explain(e)));
      };
      label.append(clear);
      $("feed-caption").append(label);
    } else
      $("feed-caption").textContent = query
        ? `${result.total} results · ${result.mode === "hybrid" ? "keyword + meaning" : "keyword search"}`
        : "Latest first";
  } catch (error) {
    if (version !== state.feedVersion) return;
    if (!append) $("feed").replaceChildren();
    $("page-error").textContent = explain(error);
    show("page-error");
    show("retry-feed");
  } finally {
    if (version === state.feedVersion) $("feed").removeAttribute("aria-busy");
  }
}
function sourceName(source) {
  return (
    {
      manual: "Added manually",
      codex: "Codex",
      claude: "Claude Code",
      claude_code: "Claude Code",
      legacy: "Imported",
      correction: "Correction",
    }[source] || source
  );
}
function feedCard(memory) {
  const card = el("article", "", "feed-card");
  const person = profile(memory.owner);
  card.append(avatar(person));
  const body = el("div", "", "feed-body");
  const line = el("div", "", "feed-byline");
  const who = el(
    "button",
    memory.owner === state.me.id ? "You" : person.name,
    "person-link",
  );
  who.onclick = () => openPerson(memory.owner);
  const when = memory.created_at || memory.captured_at;
  const timestamp = el("time", date(when));
  if (when) timestamp.dateTime = when;
  timestamp.title = date(when, true);
  line.append(who, privacy(memory.visibility), timestamp);
  body.append(line);
  const heading = el("h2");
  const title = el("button", memory.title || "Recorded work", "memory-title");
  title.onclick = () => openMemory(memory.id).catch((e) => toast(explain(e)));
  heading.append(title);
  body.append(heading, el("p", memory.excerpt));
  const meta = el("div", "", "memory-meta");
  if (memory.source) meta.append(el("span", sourceName(memory.source)));
  if (memory.status !== "ready")
    meta.append(
      el("span", memory.status === "failed" ? "Needs attention" : "Processing"),
    );
  body.append(meta);
  card.append(body);
  return card;
}
function openPerson(id) {
  state.person = id;
  state.feed = id === state.me.id ? "mine" : "shared";
  document
    .querySelectorAll("[data-feed]")
    .forEach((b) => updateFeedTab(b, b.dataset.feed === state.feed));
  setView("home");
  loadFeed().catch((e) => toast(explain(e)));
}

async function loadOverview() {
  if (!state.me?.enrolled || $("work-overview").hidden) return;
  const version = ++state.overviewVersion;
  const target = $("overview-content");
  target.replaceChildren(el("p", "Loading overview…", "muted"));
  try {
    const result = await api(
      `/api/v1/people/${encodeURIComponent(state.person || state.me.id)}/overview`,
      {
        kind: $("overview-kind").value,
        day:
          $("overview-kind").value === "daily"
            ? $("overview-day").value || null
            : null,
        project: $("overview-project").value.trim(),
      },
    );
    if (version !== state.overviewVersion) return;
    target.replaceChildren(
      privacy(result.audience === "owner" ? "private" : "public"),
    );
    if (result.timezone)
      target.append(
        el(
          "p",
          `${result.request.day} · ${result.timezone.replaceAll("_", " ")}`,
          "fine-print",
        ),
      );
    if (!result.view) {
      target.append(
        el(
          "p",
          result.status === "paused"
            ? "Processing is paused for this person."
            : result.status === "failed"
              ? "The overview could not be refreshed. It will retry; the source records below remain searchable."
              : "This overview is being prepared. Your source records below are already available.",
          "muted",
        ),
      );
      return;
    }
    for (const item of result.view.items) {
      const entry = el("div", "", "overview-item");
      entry.append(el("p", item.text));
      const sources = el("div", "", "overview-sources");
      item.sources.forEach((id, index) => {
        const button = el(
          "button",
          `Source ${index + 1}`,
          "button quiet small",
        );
        button.onclick = () => openMemory(id).catch((e) => toast(explain(e)));
        sources.append(button);
      });
      entry.append(sources);
      target.append(entry);
    }
    if (!result.view.items.length)
      target.append(
        el("p", "No organized work is recorded for this view yet.", "muted"),
      );
    const c = result.view.coverage;
    target.append(
      el(
        "p",
        `${c.included} sources included · ${c.pending} still processing${c.older_not_included ? ` · ${c.older_not_included} older sources outside this view` : ""}${c.undated ? ` · ${c.undated} undated records excluded` : ""}. Refreshed ${date(result.view.refreshed_at, true)}.${result.stale ? " New work is available; this overview is updating." : ""}`,
        "fine-print",
      ),
    );
  } catch (error) {
    if (version === state.overviewVersion)
      target.replaceChildren(el("p", explain(error), "inline-error"));
  }
}
async function openMemory(id) {
  const version = ++state.memoryVersion;
  $("source-error").textContent = "";
  $("source-body").replaceChildren(el("p", "Loading memory…", "muted"));
  $("source-actions").replaceChildren();
  $("source-visibility").replaceChildren();
  if (!$("source-dialog").open) $("source-dialog").showModal();
  try {
    const m = await api("/api/v1/memories/" + encodeURIComponent(id));
    if (version !== state.memoryVersion || !$("source-dialog").open) return;
    $("source-visibility").replaceChildren(privacy(m.visibility));
    const body = $("source-body");
    body.replaceChildren(
      el("h1", m.title),
      el(
        "p",
        `${profile(m.owner).name} · ${date(m.created_at || m.captured_at, true)} · ${sourceName(m.source)}`,
        "source-meta",
      ),
    );
    if (m.summary && m.summary.trim() !== (m.content || "").trim()) {
      body.append(el("h2", "Summary"), el("p", m.summary));
      body.append(
        el(
          "h2",
          m.visibility === "public" && m.original
            ? "Original evidence · only you"
            : "Original evidence",
        ),
      );
    }
    if (m.original && m.visibility === "public") {
      body.append(privacy("private"), el("h3", "Original evidence · only you"));
    }
    body.append(el("div", m.content, "source-content"));
    if (m.original) {
      const details = el("details");
      details.append(
        el("summary", "Original evidence & metadata"),
        el("pre", JSON.stringify(m.original, null, 2)),
      );
      body.append(details);
    }
    for (const revision of m.revisions || []) {
      const newer = revision.previous_id === id;
      const link = el(
        "button",
        newer ? "Read later correction" : "Read original entry",
        "button quiet small",
      );
      link.onclick = () =>
        openMemory(newer ? revision.record_id : revision.previous_id).catch(
          (e) => toast(explain(e)),
        );
      body.append(link);
    }
    const actions = $("source-actions");
    actions.replaceChildren();
    if (m.owner === state.me.id) {
      const correct = el("button", "Add correction", "button secondary");
      correct.onclick = () => {
        let correctionRequest = null;
        const form = el("form", undefined, "correction-form");
        const label = el("label", "What should this say now?");
        const input = el("textarea");
        input.required = true;
        input.maxLength = 200000;
        input.rows = 5;
        label.append(input);
        const explanation = el(
          "p",
          "The original stays searchable. This adds a private, dated correction and withdraws the original from sharing.",
          "fine-print",
        );
        const submit = el("button", "Save correction", "button primary");
        const cancel = el("button", "Cancel", "button quiet");
        cancel.type = "button";
        cancel.onclick = () => {
          form.remove();
          correct.disabled = false;
          correct.focus();
        };
        form.append(label, explanation, submit, cancel);
        correct.disabled = true;
        body.append(form);
        input.focus();
        form.onsubmit = (event) => {
          event.preventDefault();
          if (!input.value.trim()) {
            $("source-error").textContent =
              "Add the corrected information first.";
            input.focus();
            return;
          }
          busy(submit, async () => {
            if (correctionRequest?.content !== input.value)
              correctionRequest = {
                content: input.value,
                request_id: crypto.randomUUID(),
                occurred_at: new Date().toISOString(),
              };
            input.disabled = true;
            cancel.disabled = true;
            try {
              const saved = await api(
                `/api/v1/memories/${encodeURIComponent(id)}/corrections`,
                correctionRequest,
              );
              await openMemory(saved.id);
              await loadFeed();
              toast("Private correction added. Original evidence preserved.");
            } catch (error) {
              $("source-error").textContent = explain(error);
            } finally {
              input.disabled = false;
              cancel.disabled = false;
            }
          });
        };
      };
      actions.append(correct);
      if (state.me.sharing_enabled) {
        const share = el(
          "button",
          m.visibility === "public" ? "Make private" : "Preview sharing",
          "button secondary",
        );
        share.onclick = () =>
          busy(share, async () => {
            try {
              if (m.visibility !== "public") {
                const preview = await api(
                  `/api/v1/memories/${encodeURIComponent(id)}/sharing-preview`,
                );
                if (!preview.can_share) {
                  $("source-error").textContent = preview.notice;
                  return;
                }
                if (
                  !(await confirmAction({
                    title: "Share with your company?",
                    description: preview.notice,
                    confirm: "Share summary",
                    preview,
                  }))
                )
                  return;
              }
              await api(
                `/api/v1/memories/${encodeURIComponent(id)}/visibility`,
                {
                  visibility: m.visibility === "public" ? "private" : "public",
                },
                "PUT",
              );
              $("source-dialog").close();
              toast(
                m.visibility === "public"
                  ? "This memory is private."
                  : "Sharing requested. Privacy checks run before publication.",
              );
              await loadFeed();
            } catch (e) {
              $("source-error").textContent = explain(e);
            }
          });
        actions.append(share);
      } else actions.append(privacy("private"));
      const remove = el("button", "Delete memory", "button quiet danger");
      remove.onclick = async () => {
        if (
          !(await confirmAction({
            title: "Delete this memory?",
            description:
              "This deletes the memory and its original evidence. It cannot be undone.",
            confirm: "Delete memory",
            danger: true,
          }))
        )
          return;
        await busy(remove, async () => {
          try {
            await api(
              `/api/v1/memories/${encodeURIComponent(id)}`,
              undefined,
              "DELETE",
            );
            $("source-dialog").close();
            toast("Memory deleted.");
            await loadFeed();
          } catch (e) {
            $("source-error").textContent = explain(e);
          }
        });
      };
      actions.append(remove);
    }
    if (document.activeElement === document.body)
      $("close-source").focus({ preventScroll: true });
  } catch (error) {
    if (version !== state.memoryVersion || !$("source-dialog").open) return;
    $("source-body").replaceChildren(el("h2", "Memory unavailable"));
    $("source-error").textContent = explain(error);
    const retry = el("button", "Try again", "button secondary");
    retry.onclick = () => openMemory(id);
    $("source-actions").append(retry);
  }
}
function renderHomePeople() {
  const target = $("home-people");
  target.replaceChildren();
  for (const person of state.people.slice(0, 5)) {
    const button = el("button", "", "rail-person");
    button.append(
      avatar(person),
      el("span", person.name + (person.is_self ? " (you)" : "")),
    );
    button.onclick = () => openPerson(person.id);
    target.append(button);
  }
  if (!state.people.length)
    target.append(
      el("p", "Your teammates appear here when they join.", "fine-print"),
    );
}
function renderPeople() {
  const list = $("people-list");
  list.replaceChildren();
  const query = $("people-search").value.trim().toLocaleLowerCase();
  const people = state.people.filter((person) =>
    person.name.toLocaleLowerCase().includes(query),
  );
  $("people-count").textContent =
    `${people.length} ${people.length === 1 ? "person" : "people"}`;
  for (const person of people) {
    const row = el("article", "", "person-row");
    const identity = el("button", "", "person-identity");
    const name = el("span");
    name.append(
      el("strong", person.name + (person.is_self ? " (you)" : "")),
      el(
        "small",
        person.is_self ? "Your private and shared work" : "Company-shared work",
      ),
    );
    identity.append(avatar(person), name);
    identity.onclick = () => openPerson(person.id);
    const askPerson = el("button", "Ask", "button secondary small");
    askPerson.setAttribute("aria-label", `Ask about ${person.name}’s work`);
    askPerson.onclick = async () => {
      if (!(await changeScope(person.is_self ? "mine" : `person:${person.id}`)))
        return;
      setView("ask");
    };
    row.append(identity, askPerson);
    list.append(row);
  }
  if (!people.length)
    showEmpty(
      list,
      query ? "No matching people" : "Your team, in time",
      query
        ? "Try another name."
        : "People appear here after they join with Slack.",
    );
}
function populateSelect(select, models, selected, { inherit = false } = {}) {
  select.replaceChildren();
  if (!models.length && !inherit) {
    const unavailable = el("option", "No models available");
    unavailable.value = "";
    unavailable.disabled = true;
    select.append(unavailable);
  }
  if (inherit) {
    const option = el("option", "Use default agent");
    option.value = "";
    select.append(option);
  }
  for (const m of models) {
    const option = el("option", m.id);
    option.value = m.id;
    select.append(option);
  }
  if (selected && !models.some((m) => m.id === selected)) {
    const option = el("option", selected + " (unavailable)");
    option.value = selected;
    option.disabled = true;
    select.append(option);
  }
  select.value = selected || "";
}
async function loadModels() {
  show("chat-model-error", false);
  show("home-model-error", false);
  show("retry-chat-models", false);
  const result = await api("/api/v1/models");
  const current = $("model").value;
  state.models = result.data;
  populateSelect(
    $("model"),
    result.data,
    result.data.some((m) => m.id === current) ? current : result.default,
  );
  if (!result.data.length)
    throw new Error(
      "No chat models are available to this LiteLLM key. Ask your administrator to enable one.",
    );
  chatBusy(Boolean(state.job));
}
function updateScope() {
  const scope = $("chat-scope").value;
  state.chatScope = scope;
  $("scope-icon").replaceChildren(icon(scope === "mine" ? "lock" : "shared"));
  $("chat-privacy").replaceChildren(
    icon("lock"),
    document.createTextNode(" Your question and answer stay private."),
  );
  if (
    state.viewPreferences &&
    Object.values(state.viewPreferences).some((values) => values.length)
  )
    $("chat-privacy").append(
      document.createTextNode(" Personal filters are on."),
    );
  const person = scope.startsWith("person:")
    ? profile(scope.slice(7)).name
    : null;
  $("question").placeholder = person
    ? `Ask about ${person}’s shared work…`
    : scope === "shared"
      ? "Ask about your company’s shared work…"
      : "Ask about your work…";
  $("chat-scope-description").textContent = person
    ? `Answers use only the work ${person} has shared with you.`
    : scope === "shared"
      ? "Explore the updates and decisions shared with your company."
      : "Ask about your work, a teammate’s updates, or a decision worth revisiting.";
  const suggestions = document.querySelectorAll(".suggestions button");
  suggestions[0].textContent = person
    ? `What has ${person} been working on?`
    : scope === "shared"
      ? "What has the team been working on this week?"
      : "What have I been working on this week?";
  suggestions[1].textContent = person
    ? `What decisions has ${person} shared?`
    : scope === "shared"
      ? "What decisions has the team shared recently?"
      : "What decisions did I make recently?";
  suggestions.forEach((button) => button.append(icon("next")));
}
async function changeScope(scope) {
  if (state.job) {
    toast("Stop the current answer before changing memories.");
    return false;
  }
  if (
    scope !== state.chatScope &&
    ($("messages").children.length || $("question").value.trim())
  ) {
    if (
      !(await confirmAction({
        title: "Start a new conversation?",
        description:
          "Changing whose memories you use starts a fresh conversation. This conversation will be cleared from this tab.",
        confirm: "Start new conversation",
      }))
    )
      return false;
    newChat();
  }
  $("chat-scope").value = scope;
  updateScope();
  return true;
}
function newChat({ focus = true } = {}) {
  if (state.job) {
    toast("Stop the current answer before starting a new conversation.");
    return;
  }
  state.chatVersion++;
  state.previousJob = null;
  state.chatRequest = null;
  $("messages").replaceChildren();
  show("chat-empty");
  $("question").value = "";
  $("question").style.height = "";
  if (focus) $("question").focus();
}
function addMessage(role, text) {
  show("chat-empty", false);
  const message = el("article", "", "chat-message");
  message.dataset.role = role;
  message.append(
    el("div", role === "user" ? "You" : "litemem", "message-author"),
  );
  const body = el("div", "", "message-body");
  if (role === "user") body.textContent = text;
  else markdown(body, text);
  const phase = el("div", "", "message-phase");
  phase.setAttribute("role", "status");
  message.append(body, phase);
  $("messages").append(message);
  return { message, body, phase };
}
function chatBusy(value) {
  const locked = value || state.preferencesSaving;
  $("send-question").disabled = locked || !state.models.length;
  $("home-ask-form").querySelector("button").disabled =
    locked || !state.models.length;
  $("model").disabled = locked || !state.models.length;
  document.querySelectorAll(".suggestions button").forEach((button) => {
    button.disabled = locked || !state.models.length;
  });
  show("stop-answer", value);
  $("chat-scope").disabled = locked;
  $("new-chat").disabled = locked;
}
async function ask(question) {
  question = question.trim();
  if (!question || state.job || state.preferencesSaving) return;
  const model = $("model").value;
  if (!model) {
    toast("Choose an available LiteLLM model first.");
    return;
  }
  setView("ask");
  state.cancelRequested = false;
  const generation = state.chatVersion;
  addMessage("user", question);
  const answer = addMessage("assistant", "");
  answer.phase.textContent = "Starting…";
  answer.phase.dataset.working = "true";
  chatBusy(true);
  $("question").value = "";
  $("question").style.height = "";
  answer.message.scrollIntoView({ block: "nearest", behavior: "instant" });
  state.job = "starting";
  try {
    const scope = $("chat-scope").value;
    const fingerprint = JSON.stringify([
      question,
      model,
      scope,
      state.previousJob,
    ]);
    if (state.chatRequest?.fingerprint !== fingerprint)
      state.chatRequest = { fingerprint, id: crypto.randomUUID() };
    const result = await api("/api/v1/chat", {
      question,
      model,
      scope:
        scope === "mine"
          ? state.me.id
          : scope.startsWith("person:")
            ? scope.slice(7)
            : null,
      audience: scope === "shared" ? "public" : "owner",
      previous_job_id: state.previousJob,
      request_id: state.chatRequest.id,
    });
    state.job = result.id;
    if (state.cancelRequested)
      await api(`/api/v1/jobs/${encodeURIComponent(result.id)}/cancel`, {});
    while (generation === state.chatVersion) {
      const job = await api("/api/v1/jobs/" + encodeURIComponent(result.id));
      answer.phase.textContent = job.phase || "Working…";
      if (job.partial || job.answer) {
        const nearBottom =
          window.innerHeight + window.scrollY >=
          document.documentElement.scrollHeight - 160;
        markdown(answer.body, job.answer || job.partial);
        if (nearBottom && state.view === "ask")
          window.scrollTo({
            top: document.documentElement.scrollHeight,
            behavior: "instant",
          });
      }
      if (["done", "failed", "cancelled"].includes(job.status)) {
        answer.phase.textContent =
          job.status === "done"
            ? ""
            : job.status === "cancelled"
              ? "Stopped."
              : job.error || "Could not finish this answer. Try again.";
        state.chatRequest = null;
        if (job.status === "done") {
          state.previousJob = result.id;
          $("announce").textContent = "Answer ready.";
        } else if (job.status === "failed") $("question").value = question;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  } catch (error) {
    answer.phase.textContent = explain(error);
    $("question").value = question;
  } finally {
    state.job = null;
    answer.phase.dataset.working = "false";
    chatBusy(false);
  }
}
async function loadConnections() {
  const data = await api("/api/v1/connections");
  $("mcp-url").textContent = state.me.mcp_url;
  const url = state.me.mcp_url;
  $("codex-command").textContent =
    `codex mcp add litemem --url ${url}\ncodex mcp login litemem`;
  $("claude-command").textContent =
    `claude mcp add --transport http litemem ${url}`;
  const list = $("connection-list");
  list.replaceChildren();
  for (const c of data.connections.filter((c) => !c.revoked)) {
    const row = el("div", "", "connection-row");
    const name = el("div");
    name.append(
      el("strong", c.label),
      el(
        "small",
        c.last_used
          ? "Last used " + date(c.last_used * 1000, true)
          : "Not used yet",
      ),
    );
    const revoke = el("button", "Disconnect", "button quiet small");
    revoke.onclick = () =>
      busy(revoke, async () => {
        try {
          await api(
            "/api/v1/connections/" + encodeURIComponent(c.id),
            undefined,
            "DELETE",
          );
          toast("Connection revoked.");
          await loadConnections();
        } catch (e) {
          toast(explain(e));
        }
      });
    row.append(name, revoke);
    list.append(row);
  }
  if (!list.children.length)
    list.append(el("p", "No connected clients yet.", "muted"));
}
async function loadRules() {
  const data = await api("/api/v1/privacy-rules");
  $("rule-list").replaceChildren();
  for (const r of data.rules) {
    const row = el("div", "", "rule-row");
    row.append(icon("lock"), el("span", r.instruction));
    const remove = el("button", "Remove", "button quiet small");
    remove.onclick = () =>
      busy(remove, async () => {
        try {
          await api(
            "/api/v1/privacy-rules/" + encodeURIComponent(r.id),
            undefined,
            "DELETE",
          );
          await loadRules();
          toast("Rule removed. Existing sharing choices are being checked.");
        } catch (e) {
          toast(explain(e));
        }
      });
    row.append(remove);
    $("rule-list").append(row);
  }
}
async function loadModelSettings(refresh = false) {
  if (!state.me.is_admin) return;
  show("models-tab");
  api("/api/v1/admin/connection")
    .then((connection) => {
      $("current-gateway-url").textContent = connection.base_url;
    })
    .catch((error) => {
      $("current-gateway-url").textContent = explain(error);
    });
  const candidates = state.people.filter((person) => person.id !== state.me.id);
  show(
    "admin-transfer-panel",
    state.me.registration_mode !== "owner_only" && candidates.length > 0,
  );
  $("next-admin").replaceChildren(
    ...candidates.map((person) => {
      const option = el("option", person.name);
      option.value = person.id;
      return option;
    }),
  );
  $("model-settings-status").textContent = "Loading available models…";
  try {
    const config = await api(
      "/api/v1/admin/models" + (refresh ? "?refresh=true" : ""),
    );
    state.modelSettings = config;
    for (const [role, kind, inherit] of [
      ["agent_model", "chat", false],
      ["embedding_model", "embedding", false],
      ["slack_model", "chat", true],
      ["background_model", "generation", true],
    ])
      populateSelect(
        $("setting-" + role),
        config.catalogs[kind],
        config.models[role],
        { inherit },
      );
    $("setting-classifier_model").value = config.models.classifier_model;
    $("model-settings-status").textContent =
      config.checks.jev?.status === "ready"
        ? "Jev connected through LiteLLM."
        : "";
  } catch (error) {
    $("model-settings-status").textContent = explain(error);
  }
}
function renderViewPreferences(preferences, { saved = true } = {}) {
  if (saved) state.viewPreferences = preferences;
  for (const [id, name, choices, selected] of [
    [
      "view-type-options",
      "hidden_types",
      preferences.hidden_types.map((type) => [type, type]),
      preferences.hidden_types,
    ],
    [
      "view-person-options",
      "hidden_people",
      state.people.map((person) => [person.id, person.name]),
      preferences.hidden_people,
    ],
  ]) {
    const fieldset = $(id);
    fieldset.querySelectorAll("label").forEach((label) => label.remove());
    const missing = selected.filter(
      (value) => !choices.some(([key]) => key === value),
    );
    for (const [value, text] of [
      ...choices,
      ...missing.map((value) => [value, value]),
    ]) {
      const label = el("label");
      const input = el("input");
      input.type = "checkbox";
      input.name = name;
      input.value = value;
      input.checked = selected.includes(value);
      label.append(input, document.createTextNode(text));
      fieldset.append(label);
    }
  }
  $("legacy-view-types").hidden = preferences.hidden_types.length === 0;
  $("view-projects").value = preferences.hidden_projects.join("\n");
  $("view-sources").value = preferences.hidden_sources
    .map(sourceName)
    .join("\n");
  if (saved) updateScope();
}
async function loadViewPreferences() {
  renderViewPreferences(await api("/api/v1/view-preferences"));
}
function editViewPreferences() {
  setView("home");
  $("view-preferences-panel").open = true;
  $("view-preferences-panel").scrollIntoView({ block: "start" });
  $("view-preferences-panel").querySelector("summary").focus();
}
$("personal-filters-note").onclick = editViewPreferences;
$("reset-view-preferences").onclick = () =>
  renderViewPreferences(
    {
      hidden_people: [],
      hidden_types: [],
      hidden_projects: [],
      hidden_sources: [],
    },
    { saved: false },
  );
$("view-preferences-form").onsubmit = async (event) => {
  event.preventDefault();
  const status = $("view-preferences-status");
  status.textContent = "";
  if (state.job) {
    status.textContent =
      "Stop the current answer before changing viewing preferences.";
    return;
  }
  if (
    (state.previousJob ||
      $("messages").children.length ||
      $("question").value.trim()) &&
    !(await confirmAction({
      title: "Save filters and start a new conversation?",
      description:
        "Your new filters apply to future answers. This clears the current conversation and draft from this tab.",
      confirm: "Save viewing preferences",
    }))
  )
    return;
  await busy(event.submitter, async () => {
    const form = $("view-preferences-form");
    state.preferencesSaving = true;
    chatBusy(false);
    const selected = (name) =>
      [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(
        (input) => input.value,
      );
    const lines = (id) =>
      $(id)
        .value.split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    const body = {
      hidden_people: selected("hidden_people"),
      hidden_types: selected("hidden_types"),
      hidden_projects: lines("view-projects"),
      hidden_sources: lines("view-sources"),
    };
    const fields = [
      ...form.querySelectorAll("input, textarea, button[type='button']"),
    ];
    fields.forEach((field) => {
      field.disabled = true;
    });
    try {
      renderViewPreferences(await api("/api/v1/view-preferences", body, "PUT"));
      newChat({ focus: false });
      await loadFeed();
      status.textContent =
        "Viewing preferences saved. Your sharing choices are unchanged.";
    } catch (error) {
      status.textContent = explain(error);
    } finally {
      state.preferencesSaving = false;
      chatBusy(Boolean(state.job));
      fields.forEach((field) => {
        field.disabled = false;
      });
    }
  });
};

function populateTimezones(select, selected, automatic = false) {
  const zones = new Set(["UTC", displayZone(), selected].filter(Boolean));
  if (Intl.supportedValuesOf) {
    for (const zone of Intl.supportedValuesOf("timeZone")) zones.add(zone);
  }
  select.replaceChildren();
  if (automatic) {
    const option = el("option", "Use this device's time zone");
    option.value = "";
    select.append(option);
  }
  for (const zone of [...zones].sort()) {
    const option = el("option", zone.replaceAll("_", " "));
    option.value = zone;
    select.append(option);
  }
  select.value = selected || "";
}
function renderSchedule() {
  const schedule = state.schedule;
  populateTimezones($("workspace-timezone"), schedule.timezone);
  $("daily-time").value = schedule.daily_time;
  const next = new Date(schedule.next_run_at).toLocaleString(undefined, {
    timeZone: schedule.timezone,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  $("schedule-summary").textContent =
    `Every day at ${schedule.daily_time} · ${schedule.timezone.replaceAll("_", " ")}. Next: ${next}.`;
  $("schedule-fields").disabled = false;
}
async function loadSchedule() {
  state.schedule = null;
  $("schedule-fields").disabled = true;
  $("schedule-settings-status").textContent = "";
  $("schedule-summary").textContent = "Loading the workspace schedule…";
  show("reload-schedule", false);
  show("schedule-settings-form", state.me.is_admin);
  try {
    state.schedule = await api("/api/v1/schedule");
    renderSchedule();
  } catch (error) {
    $("schedule-summary").textContent =
      "The workspace schedule is unavailable.";
    $("schedule-settings-status").textContent = explain(error);
    show("reload-schedule");
  }
}
$("reload-schedule").onclick = () => loadSchedule();
$("schedule-settings-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state.schedule) return;
  const body = {
    timezone: $("workspace-timezone").value,
    daily_time: $("daily-time").value,
    revision: state.schedule.revision,
  };
  await busy($("save-schedule-settings"), async () => {
    $("schedule-fields").disabled = true;
    $("schedule-settings-status").textContent = "";
    try {
      state.schedule = await api("/api/v1/admin/schedule", body, "PUT");
      renderSchedule();
      $("schedule-settings-status").textContent = "Workspace schedule saved.";
      show("reload-schedule", false);
      if ($("work-overview").open) await loadOverview();
    } catch (error) {
      $("schedule-settings-status").textContent = explain(error);
      show("reload-schedule");
    } finally {
      $("schedule-fields").disabled = false;
    }
  });
};
$("display-settings-form").onsubmit = async (event) => {
  event.preventDefault();
  const body = { timezone: $("display-timezone").value || null };
  await busy($("save-display-settings"), async () => {
    $("display-timezone").disabled = true;
    $("display-settings-status").textContent = "";
    try {
      const result = await api("/api/v1/display-preferences", body, "PUT");
      state.me.display_timezone = result.timezone;
      renderToday();
      $("display-settings-status").textContent = "Your time zone is saved.";
      await loadFeed();
      if ($("work-overview").open) await loadOverview();
    } catch (error) {
      $("display-settings-status").textContent = explain(error);
    } finally {
      $("display-timezone").disabled = false;
    }
  });
};

async function loadSettings() {
  $("settings-status").textContent = "Loading settings…";
  document.querySelectorAll("[data-settings-section]").forEach((group) => {
    group.inert = true;
  });
  try {
    state.me = await api("/api/v1/me");
    populateTimezones($("display-timezone"), state.me.display_timezone, true);
    $("capture-enabled").checked = state.me.capture_enabled;
    $("agent-enabled").checked = state.me.agent_enabled;
    show("sharing-note", !state.me.sharing_enabled);
    show("sharing-form", state.me.sharing_enabled);
    show("workspace-sharing-control", state.me.is_admin);
    $("workspace-sharing-enabled").checked = state.me.sharing_enabled;
    const sharing = await api("/api/v1/sharing-preferences");
    show("legacy-sharing-note", sharing.allowed_types !== null);
    $("auto-share").checked = sharing.auto_share;
    renderCoverage(state.me.stats);
    await Promise.all([
      loadRules(),
      loadModelSettings(),
      loadViewPreferences(),
      loadSchedule(),
    ]);
    state.settingsLoaded = true;
    selectSettings(state.settingsSection);
    $("settings-status").textContent = "";
  } finally {
    document.querySelectorAll("[data-settings-section]").forEach((group) => {
      group.inert = false;
    });
  }
}
async function signOut() {
  try {
    await api("/auth/logout", {}, "POST");
    location.assign("/");
  } catch (e) {
    toast(explain(e));
  }
}
async function initialize() {
  for (const span of document.querySelectorAll("[data-icon]"))
    span.replaceChildren(icon(span.dataset.icon));
  renderToday();
  try {
    if (await initializeSetup({ api, show, el, busy, populateSelect })) return;
    state.me = await api("/api/v1/me");
    renderToday();
    if (!state.me.signed_in) {
      show("welcome");
      if (!state.me.login_available) {
        const welcome = $("welcome");
        welcome.querySelector("a").hidden = true;
        welcome.append(el("p", "This installation is being set up.", "muted"));
      }
      return;
    }
    show("welcome", false);
    if (!state.me.enrolled) {
      if (state.me.can_rejoin) {
        $("member-setup-title").textContent = "Welcome back.";
        $("member-setup-description").textContent =
          "Restore your private history. Previous connections stay revoked, sharing stays off, and recording stays paused until you enable it.";
        $("finish-signup").textContent = "Restore my account";
      }
      show("setup");
      return;
    }
    document.body.classList.add("workspace-ready");
    show("global-search-button");
    show("navigation");
    show("header-actions");
    $("my-avatar").replaceWith(
      Object.assign(avatar(state.me.profile), { id: "my-avatar" }),
    );
    $("account-name").textContent = state.me.profile.name;
    $("sidebar-name").textContent = state.me.profile.name;
    $("search-shortcut").textContent = /Mac|iPhone|iPad/.test(
      navigator.platform,
    )
      ? "⌘ K"
      : "Ctrl K";
    show("workspace-error", false);
    try {
      const people = await api("/api/v1/people");
      state.people = people.people;
    } catch (error) {
      if (!state.me) return;
      $("workspace-error-message").textContent =
        "Couldn’t load the team directory. " + explain(error);
      show("workspace-error");
    }
    renderHomePeople();
    updateScope();
    $("chat-scope")
      .querySelectorAll('[value^="person:"]')
      .forEach((option) => option.remove());
    for (const person of state.people.filter((person) => !person.is_self)) {
      const option = el("option", person.name + "’s shared work");
      option.value = "person:" + person.id;
      $("chat-scope").append(option);
    }
    await Promise.all([
      loadFeed(),
      loadModels().catch(modelError),
      loadViewPreferences(),
    ]);
    if (!state.me?.enrolled) return;
    show("boot-status", false);
    show("workspace");
    setView(location.hash.slice(1) || "home", { navigate: false });
    const match = location.pathname.match(/^\/memory\/([^/]+)$/);
    if (match) await openMemory(decodeURIComponent(match[1]));
  } catch (error) {
    if (state.me?.enrolled) {
      show("workspace");
      $("workspace-error-message").textContent = explain(error);
      show("workspace-error");
    } else {
      show("welcome");
      $("welcome-error").textContent = explain(error);
      show("retry-initialize");
    }
  } finally {
    show("boot-status", false);
  }
}

document
  .querySelectorAll("[data-view]")
  .forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
document.querySelectorAll("[data-feed]").forEach((b) =>
  b.addEventListener("click", () => {
    state.feed = b.dataset.feed;
    state.person = null;
    document
      .querySelectorAll("[data-feed]")
      .forEach((t) => updateFeedTab(t, t === b));
    loadFeed();
  }),
);
$("account-button").onclick = () => {
  const open = $("account-menu").hidden;
  show("account-menu", open);
  $("account-button").setAttribute("aria-expanded", String(open));
  if (open) $("account-menu").querySelector("button").focus();
};
document.addEventListener("click", (e) => {
  if (
    !$("account-menu").contains(e.target) &&
    !$("account-button").contains(e.target)
  ) {
    show("account-menu", false);
    $("account-button").setAttribute("aria-expanded", "false");
  }
});
function focusSearch() {
  setView("home");
  show("search-form");
  $("search-query").focus();
}
$("global-search-button").onclick = focusSearch;
$("feed-search-button").onclick = focusSearch;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("account-menu").hidden) {
    closeAccountMenu();
    $("account-button").focus();
  }
  if (document.querySelector("dialog[open]")) return;
  if ((e.metaKey || e.ctrlKey) && e.key === "k" && state.me?.enrolled) {
    e.preventDefault();
    focusSearch();
  }
});
$("filter-button").onclick = () => {
  const open = $("filters").hidden;
  show("filters", open);
  $("filter-button").setAttribute("aria-expanded", String(open));
};
function clearFilters() {
  $("filter-button").focus();
  for (const id of [
    "filter-kind",
    "filter-source",
    "filter-project",
    "filter-from",
    "filter-before",
    "search-query",
  ])
    $(id).value = "";
  loadFeed();
}
$("clear-filters").onclick = clearFilters;
$("close-search").onclick = () => {
  $("search-query").value = "";
  show("search-form", false);
  $("feed-search-button").focus();
  loadFeed();
};
$("retry-feed").onclick = () => loadFeed();
$("search-query").addEventListener("search", () => {
  if (!$("search-query").value) loadFeed();
});
for (const id of [
  "filter-kind",
  "filter-source",
  "filter-project",
  "filter-from",
  "filter-before",
])
  $(id).onchange = () => loadFeed();
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  loadFeed();
};
$("load-more").onclick = () => busy($("load-more"), () => loadFeed(true));
$("work-overview").ontoggle = () => {
  if ($("work-overview").open) loadOverview();
};
$("overview-kind").onchange = () => {
  show("overview-date-label", $("overview-kind").value === "daily");
  loadOverview();
};
$("overview-form").onsubmit = (event) => {
  event.preventDefault();
  loadOverview();
};
$("home-ask-form").onsubmit = async (event) => {
  event.preventDefault();
  const question = $("home-question").value.trim();
  if (!question || state.job || !state.models.length) return;
  const scope =
    state.person && state.person !== state.me.id
      ? `person:${state.person}`
      : state.feed === "shared"
        ? "shared"
        : "mine";
  if (!(await changeScope(scope))) return;
  $("home-question").value = "";
  await ask(question);
};
$("chat-form").onsubmit = (e) => {
  e.preventDefault();
  ask($("question").value);
};
$("question").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    ask($("question").value);
  }
};
document
  .querySelectorAll(".suggestions button")
  .forEach((b) => (b.onclick = () => ask(b.textContent)));
$("new-chat").onclick = newChat;
$("chat-scope").onchange = async () => {
  const desired = $("chat-scope").value;
  $("chat-scope").value = state.chatScope;
  await changeScope(desired);
};
$("question").oninput = () => {
  $("question").style.height = "auto";
  $("question").style.height = Math.min($("question").scrollHeight, 180) + "px";
};
$("stop-answer").onclick = () =>
  busy($("stop-answer"), async () => {
    state.cancelRequested = true;
    if (state.job && state.job !== "starting") {
      try {
        await api(
          "/api/v1/jobs/" + encodeURIComponent(state.job) + "/cancel",
          {},
        );
      } catch (e) {
        toast(explain(e));
      }
    }
  });
$("add-memory").onclick = () => {
  $("capture-error").textContent = "";
  $("capture-dialog").showModal();
};
$("close-capture").onclick = () => $("capture-dialog").close();
$("close-source").onclick = () => $("source-dialog").close();
$("capture-form").onsubmit = async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => {
    $("capture-error").textContent = "";
    const content = $("capture-content").value;
    if (!content.trim()) {
      $("capture-error").textContent = "Add something you want to remember.";
      $("capture-content").focus();
      return;
    }
    const fingerprint = JSON.stringify([$("capture-title").value, content]);
    if (state.captureRequest?.fingerprint !== fingerprint)
      state.captureRequest = {
        fingerprint,
        id: crypto.randomUUID(),
        occurredAt: new Date().toISOString(),
      };
    const fields = [...$("capture-form").querySelectorAll("input, textarea")];
    fields.forEach((field) => {
      field.disabled = true;
    });
    try {
      await api("/api/v1/memories", {
        source_id: state.captureRequest.id,
        title: $("capture-title").value,
        content: $("capture-content").value,
        occurred_at: state.captureRequest.occurredAt,
        source: "manual",
      });
      state.captureRequest = null;
      $("capture-form").reset();
      $("capture-dialog").close();
      toast("Memory saved privately.");
      state.feed = "mine";
      state.person = null;
      document
        .querySelectorAll("[data-feed]")
        .forEach((button) =>
          updateFeedTab(button, button.dataset.feed === "mine"),
        );
      setView("home");
      await loadFeed();
    } catch (e) {
      $("capture-error").textContent = explain(e);
    } finally {
      fields.forEach((field) => {
        field.disabled = false;
      });
    }
  });
};
$("finish-signup").onclick = () =>
  busy($("finish-signup"), async () => {
    try {
      await api(state.me.can_rejoin ? "/api/v1/rejoin" : "/api/v1/signup", {});
      show("setup", false);
      await initialize();
    } catch (e) {
      $("setup-error").textContent = explain(e);
    }
  });
$("rule-form").onsubmit = async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => {
    try {
      await api("/api/v1/privacy-rules", {
        instruction: $("rule-input").value,
      });
      $("rule-error").textContent = "";
      $("rule-input").value = "";
      await loadRules();
      toast("Rule saved. Existing and future work will be checked.");
    } catch (e) {
      $("rule-error").textContent = explain(e);
    }
  });
};
$("sharing-form").onsubmit = async (event) => {
  event.preventDefault();
  await busy(event.submitter, async () => {
    try {
      await api(
        "/api/v1/sharing-preferences",
        {
          auto_share: $("auto-share").checked,
        },
        "PUT",
      );
      $("sharing-status").textContent = "Sharing preferences saved.";
    } catch (error) {
      $("sharing-status").textContent = explain(error);
    }
  });
};
$("workspace-sharing-enabled").onchange = async () => {
  const input = $("workspace-sharing-enabled");
  const desired = input.checked;
  if (
    !desired &&
    !(await confirmAction({
      title: "Turn off company sharing?",
      description:
        "Existing publications become private and won’t be restored automatically.",
      confirm: "Turn off sharing",
    }))
  ) {
    input.checked = true;
    return;
  }
  await busy(input, async () => {
    try {
      await api("/api/v1/admin/sharing", { enabled: desired }, "PUT");
      await loadSettings();
      toast(
        desired
          ? "Company sharing is available. Everyone's records stay private until they choose to share."
          : "Company sharing is off.",
      );
    } catch (error) {
      input.checked = !desired;
      toast(explain(error));
    }
  });
};
for (const [id, key] of [
  ["capture-enabled", "capture_enabled"],
  ["agent-enabled", "agent_enabled"],
])
  $(id).onchange = async () => {
    const input = $(id);
    const desired = input.checked;
    await busy(input, async () => {
      try {
        await api("/api/v1/preferences", { [key]: desired }, "PUT");
        state.me[key] = desired;
        toast(desired ? "Enabled." : "Paused.");
      } catch (e) {
        input.checked = !desired;
        toast(explain(e));
      }
    });
  };
$("retry-processing").onclick = () =>
  busy($("retry-processing"), async () => {
    try {
      await api("/api/v1/retry-processing", {});
      toast("Failed items queued for retry.");
      await loadSettings();
    } catch (e) {
      toast(explain(e));
    }
  });
$("copy-mcp").onclick = async () => {
  if (!(await copyText(state.me.mcp_url, $("copy-mcp"))))
    toast("Select the connection URL to copy it.");
};
document.querySelectorAll("[data-copy]").forEach((button) => {
  button.onclick = async () => {
    if (!(await copyText($(button.dataset.copy).textContent, button)))
      toast("Select the command to copy it.");
  };
});
$("model-settings-form").onsubmit = async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => {
    if (!state.modelSettings) return;
    const models = {};
    for (const role of [
      "agent_model",
      "embedding_model",
      "slack_model",
      "background_model",
      "classifier_model",
    ])
      models[role] = $("setting-" + role).value;
    $("model-settings-status").textContent =
      "Checking models and Jev through LiteLLM…";
    try {
      state.modelSettings = await api(
        "/api/v1/admin/models",
        { models, revision: state.modelSettings.revision },
        "PUT",
      );
      $("model-settings-status").textContent =
        "Saved. New requests use these models.";
      await loadModels();
    } catch (e) {
      $("model-settings-status").textContent = explain(e);
    }
  });
};
$("reload-models").onclick = () =>
  busy($("reload-models"), () => loadModelSettings(true));
async function downloadExport(options, filename) {
  const sessionVersion = state.sessionVersion;
  const response = await fetch("/api/v1/export", {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  if (response.status === 401 && state.me?.enrolled) expireSession();
  if (!response.ok)
    throw new Error("Export could not be completed. Try again.");
  const blob = await response.blob();
  if (sessionVersion !== state.sessionVersion)
    throw new Error("Your session expired. Sign in with Slack to continue.");
  const url = URL.createObjectURL(blob);
  const link = el("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-data").onclick = () =>
  busy($("export-data"), async () => {
    try {
      await downloadExport({ encrypted: false }, "my-memory-export.jsonl");
      toast("Export downloaded. This file contains your readable memories.");
    } catch (e) {
      toast(explain(e));
    }
  });
$("encrypted-export").onclick = () => $("export-dialog").showModal();
$("close-export").onclick = () => $("export-dialog").close();
$("export-dialog").onclose = () => {
  $("encrypted-export-form").reset();
  $("export-status").textContent = "";
};
$("encrypted-export-form").onsubmit = async (event) => {
  event.preventDefault();
  if ($("export-passphrase").value !== $("export-passphrase-confirm").value) {
    $("export-status").textContent = "The passphrases do not match.";
    return;
  }
  await busy(event.submitter, async () => {
    $("export-status").textContent = "Preparing your encrypted export…";
    try {
      await downloadExport(
        {
          encrypted: true,
          passphrase: $("export-passphrase").value,
        },
        "my-memory-export.litemem",
      );
      $("export-dialog").close();
      toast("Encrypted export downloaded. Keep its passphrase separately.");
    } catch (error) {
      $("export-status").textContent = explain(error);
    }
  });
};
$("leave-workspace").onclick = async () => {
  if (
    !(await confirmAction({
      title: "Leave this workspace?",
      description:
        "Your connections and agent access will be revoked. Export your data first if you want a copy.",
      confirm: "Leave workspace",
      danger: true,
    }))
  )
    return;
  await busy($("leave-workspace"), async () => {
    try {
      await api("/api/v1/leave", {});
      location.assign("/");
    } catch (e) {
      toast(explain(e));
    }
  });
};
$("menu-sign-out").onclick = signOut;
$("rotate-gateway-form").onsubmit = (event) => {
  event.preventDefault();
  busy(event.currentTarget.querySelector("button"), async () => {
    $("gateway-rotation-status").textContent = "Checking your models and Jev…";
    try {
      await api(
        "/api/v1/admin/connection/key",
        { api_key: $("replacement-gateway-key").value },
        "PUT",
      );
      $("replacement-gateway-key").value = "";
      $("gateway-rotation-status").textContent =
        "Key updated. Your model choices are unchanged.";
    } catch (error) {
      $("gateway-rotation-status").textContent = explain(error);
    }
  });
};
$("admin-transfer-form").onsubmit = async (event) => {
  event.preventDefault();
  const submitter = event.submitter;
  const select = $("next-admin");
  if (
    !select.value ||
    !(await confirmAction({
      title: "Transfer administration?",
      description: `${select.selectedOptions[0].textContent} will manage workspace settings. You will no longer be the administrator.`,
      confirm: "Transfer administration",
    }))
  )
    return;
  busy(submitter, async () => {
    try {
      await api("/api/v1/admin/transfer", { owner: select.value });
      location.reload();
    } catch (error) {
      toast(explain(error));
    }
  });
};
function updateFeedTab(button, active) {
  button.classList.toggle("selected", active);
  button.setAttribute("aria-pressed", String(active));
}
function modelError(error) {
  state.models = [];
  populateSelect($("model"), [], "");
  chatBusy(Boolean(state.job));
  $("chat-model-error").textContent = explain(error);
  $("home-model-error").textContent = "Ask is unavailable. " + explain(error);
  show("home-model-error");
  show("chat-model-error");
  show("retry-chat-models");
}
$("retry-chat-models").onclick = () =>
  busy($("retry-chat-models"), () => loadModels().catch(modelError));
$("people-search").oninput = renderPeople;
$("retry-workspace").onclick = () => busy($("retry-workspace"), initialize);
$("retry-initialize").onclick = () => busy($("retry-initialize"), initialize);
document.querySelectorAll("[data-settings]").forEach((button) => {
  button.onclick = () => selectSettings(button.dataset.settings);
  button.onkeydown = (event) => {
    const tabs = [...document.querySelectorAll("[data-settings]")].filter(
      (tab) => !tab.hidden,
    );
    const index = tabs.indexOf(button);
    const next = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    selectSettings(tabs[next].dataset.settings);
    tabs[next].focus();
  };
});
initialize();
