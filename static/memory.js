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
  searchVersion: 0,
  searchRequest: null,
  searchNext: null,
  searchLoading: false,
  models: [],
  modelSettings: null,
  previousJob: null,
  job: null,
  chatVersion: 0,
  toastTimer: null,
  memoryVersion: 0,
  settingsLoaded: false,
  cancelRequested: false,
  settingsSection: "privacy",
  chatScope: "shared",
  chatRequest: null,
  sessionVersion: 0,
  viewPreferences: null,
  preferencesSaving: false,
  recording: null,
  recordingLoading: false,
  connectionsVersion: 0,
  listedClientCount: null,
  firstReceipt: false,
  guideInitialized: false,
};
// Lucide paths: one 24px grid and one stroke system, no nested icon wrappers.
const icons = {
  refresh:
    '<path d="M3 11a9 9 0 0 1 15-6l3 3M21 3v5h-5M21 13a9 9 0 0 1-15 6l-3-3M3 21v-5h5" />',
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
  state.searchVersion++;
  state.searchRequest = null;
  state.chatVersion++;
  state.memoryVersion++;
  state.previousJob = null;
  state.job = null;
  state.people = [];
  state.models = [];
  state.me = null;
  state.settingsLoaded = false;
  state.chatRequest = null;
  state.modelSettings = null;
  state.viewPreferences = null;
  state.recording = null;
  state.connectionsVersion++;
  state.listedClientCount = null;
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  document.querySelectorAll("input, textarea").forEach((input) => {
    input.value = "";
  });
  for (const id of [
    "feed",
    "search-results",
    "messages",
    "people-list",
    "source-body",
    "source-actions",
    "source-visibility",
    "rule-list",
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
  document.title = `${viewNames[view]} · LiteLLM Memory`;
  for (const name of Object.keys(viewNames))
    show(name + "-view", name === view);
  document.querySelectorAll(".nav-link").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  closeAccountMenu();
  const route =
    view === "home"
      ? state.person && state.person !== state.me.id
        ? `person/${encodeURIComponent(state.person)}`
        : state.feed === "mine"
          ? "mine"
          : "home"
      : view;
  if (navigate && location.hash !== `#${route}`)
    history.pushState(null, "", `/#${route}`);
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
function openFeed(feed = "shared", person = null, { navigate = true } = {}) {
  state.feed = feed;
  state.person = person;
  resetFilterFields();
  document
    .querySelectorAll("[data-feed]")
    .forEach((button) => updateFeedTab(button, button.dataset.feed === feed));
  setView("home", { navigate });
  loadFeed();
}
function restoreRoute() {
  const route = location.hash.slice(1);
  if (route === "mine") return openFeed("mine", null, { navigate: false });
  if (route.startsWith("person/")) {
    let id;
    try {
      id = decodeURIComponent(route.slice(7));
    } catch {
      id = null;
    }
    if (state.people.some((person) => person.id === id))
      return openFeed(id === state.me.id ? "mine" : "shared", id, {
        navigate: false,
      });
    toast("This person is no longer in the directory.");
  }
  if (!route || route === "home" || route.startsWith("person/"))
    return openFeed("shared", null, { navigate: false });
  setView(route, { navigate: false });
}
window.addEventListener("popstate", () => {
  if (state.me?.enrolled) restoreRoute();
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
  $("processing-status").textContent = [
    stats.pending ? `${stats.pending} processing` : "",
    stats.failed ? `${stats.failed} need attention` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  $("processing-summary").replaceChildren(
    el(
      "strong",
      `${stats.ready} of ${stats.total} ${stats.total === 1 ? "memory" : "memories"} organized`,
    ),
    el(
      "small",
      stats.failed
        ? `${stats.failed} ${stats.failed === 1 ? "item can" : "items can"} be retried.`
        : stats.pending
          ? `${stats.pending} ${stats.pending === 1 ? "item is" : "items are"} being processed.`
          : "Up to date.",
    ),
  );
  show("processing-status", state.feed === "mine");
  show("processing-section", Boolean(stats.pending || stats.failed));
  show("retry-processing", Boolean(stats.failed));
  $("retry-processing").disabled = !stats.failed;
}
async function loadFeed(append = false) {
  if (!state.me?.enrolled) return;
  const selected =
    state.person && state.person !== state.me.id ? profile(state.person) : null;
  show("person-context", Boolean(selected));
  if (selected) {
    const identity = $("person-context-identity");
    const copy = el("div");
    copy.append(
      el("h2", selected.name),
      el("p", "Work shared with your company"),
    );
    identity.replaceChildren(avatar(selected), copy);
  }
  $("home-question").placeholder = selected
    ? `Ask about ${selected.name}’s shared work…`
    : state.feed === "shared"
      ? "Ask about your company’s shared work…"
      : "Ask about your work…";
  $("home-question").setAttribute(
    "aria-label",
    selected
      ? `Ask about ${selected.name}’s shared work`
      : state.feed === "shared"
        ? "Ask about Company work"
        : "Ask about My work",
  );
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
  const filtered = Boolean(filterCount);
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
      query: "",
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
      mode: "keywords",
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
            ? selected
              ? "No shared work yet"
              : "No company updates yet"
            : "Your first work will appear here",
        filtered || result.personal_filters_active
          ? "Try another phrase or clear your filters."
          : state.feed === "shared"
            ? selected
              ? `${selected.name} hasn’t shared work with your company yet.`
              : "This feed fills as teammates share summaries. Your own recordings stay in My work until you choose to share."
            : "Connect your coding tool. Your agent saves useful work memories privately as you work.",
        result.personal_filters_active && !filtered
          ? { label: "Edit personal filters", run: editViewPreferences }
          : filtered
            ? { label: "Clear filters", run: clearFilters }
            : state.feed === "mine"
              ? {
                  label: "Connect your tools",
                  run: () => setView("connections"),
                }
              : selected
                ? { label: "Browse Company", run: () => openFeed() }
                : { label: "View my work", run: () => openFeed("mine") },
      );
    $("feed-caption").textContent = "Latest first";
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
  openFeed(id === state.me.id ? "mine" : "shared", id);
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
              if ($("search-dialog").open) searchWork();
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
      const more = el("details", "", "source-more");
      more.append(el("summary", "More actions"));
      const maintenance = el("div", "", "action-row");
      maintenance.append(correct);
      more.append(maintenance);
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
              if ($("search-dialog").open) searchWork();
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
            if ($("search-dialog").open) searchWork();
          } catch (e) {
            $("source-error").textContent = explain(e);
          }
        });
      };
      maintenance.append(remove);
      actions.append(more);
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
  show(
    "chat-filter-notice",
    Boolean(
      state.viewPreferences &&
        Object.values(state.viewPreferences).some((values) => values.length),
    ),
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
    el("div", role === "user" ? "You" : "LiteLLM Memory", "message-author"),
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
function recordingMessage(status) {
  if (!status.capture_enabled)
    return {
      title: "Recording is paused",
      copy: "Your saved work is still here. Resume recording in Connections when you’re ready.",
      action: "Review recording",
      destination: "recording",
    };
  if (!status.connected_clients)
    return {
      title: "Bring your work into LiteLLM Memory",
      copy: "Connect Codex or Claude Code once. Your agent saves useful work memories privately as you work.",
      action: "Connect a tool",
      destination: "connections",
    };
  if (!status.received)
    return {
      title: "Connected. Waiting for work.",
      copy: "Finish tool setup, then ask your agent to remember what you’re working on. This updates when a memory arrives.",
      action: "Finish setup",
      destination: "connections",
    };
  if (!status.agent_enabled || status.stats.failed)
    return {
      title: !status.agent_enabled
        ? "Organization is paused"
        : "Some work needs attention",
      copy: "Your work has been saved. Review recording controls in Connections to make it ready for questions.",
      action: "Review recording",
      destination: "recording",
    };
  return {
    title: state.firstReceipt ? "Your first work is here" : "Work received",
    copy: "Open My work to read it, ask about it, or preview a summary to share.",
    action: "View my work",
    destination: "mine",
  };
}
function renderRecording() {
  const status = state.recording;
  if (!status) return;
  const message = recordingMessage(status);
  if (!state.guideInitialized) {
    $("connector-guide").open = !status.connected_clients || !status.received;
    $("recording-controls").open =
      !status.capture_enabled || !status.agent_enabled;
    state.guideInitialized = true;
  }
  $("connector-guide-title").textContent = !status.connected_clients
    ? "Connect a tool"
    : status.received
      ? "Connect another tool"
      : "Finish tool setup";
  $("recording-title").textContent =
    status.capture_enabled && !status.connected_clients
      ? "No tool connected"
      : message.title;
  $("recording-copy").textContent = !status.capture_enabled
    ? "Your existing history is kept. Turn recording back on below when you’re ready."
    : !status.connected_clients
      ? "Follow the setup below to start saving useful work memories."
      : !status.received
        ? "Finish setup, then ask your agent to remember what you’re working on. Turns with nothing useful to add may be skipped."
        : !status.agent_enabled
          ? "Your work is saved. Enable your agent below to organize and ask about it."
          : status.stats.failed
            ? "Your work is saved. Retry the failed items below."
            : "Open My work to browse your saved memories.";
  $("recording-last-received").textContent = status.last_received_at
    ? `Last work received ${date(status.last_received_at * 1000, true)}.`
    : status.stats.total
      ? "Imported history is available. No new work has arrived yet."
      : "No work received yet.";
  show(
    "recording-last-received",
    Boolean(status.last_received_at || status.stats.total),
  );
  show("see-my-work", status.stats.total > 0);
  show("manage-recording", !status.capture_enabled || !status.agent_enabled);
  show("recording-nudge", state.firstReceipt || message.destination !== "mine");
  $("recording-nudge-title").textContent = message.title;
  $("recording-nudge-copy").textContent = message.copy;
  $("recording-nudge-action").textContent = message.action;
  $("recording-nudge-action").onclick = () => {
    if (message.destination === "mine") {
      state.firstReceipt = false;
      renderRecording();
      openFeed("mine");
    } else if (message.destination === "recording") {
      reviewRecording();
    } else {
      $("connector-guide").open = true;
      setView("connections");
    }
  };
}
async function refreshRecording({ loadClients = true } = {}) {
  if (!state.me?.enrolled || state.recordingLoading) return;
  state.recordingLoading = true;
  try {
    const status = await api("/api/v1/recording-status");
    if (!state.me?.enrolled) return;
    const first = state.recording?.received === 0 && status.received > 0;
    state.firstReceipt ||= first;
    state.recording = status;
    state.me.capture_enabled = status.capture_enabled;
    state.me.agent_enabled = status.agent_enabled;
    $("capture-enabled").checked = status.capture_enabled;
    $("agent-enabled").checked = status.agent_enabled;
    $("recording-preferences").inert = false;
    renderCoverage(status.stats);
    renderRecording();
    if (first) $("connector-guide").open = false;
    if (
      loadClients &&
      state.listedClientCount !== status.connected_clients &&
      state.view === "connections"
    )
      await loadConnectionList();
    $("recording-error").textContent = "";
    if (first && state.view === "home" && state.feed === "mine")
      await loadFeed();
  } catch (error) {
    if (state.me?.enrolled) {
      $("recording-error").textContent = explain(error);
      if (!state.recording) {
        $("recording-title").textContent = "Recording status unavailable";
        $("recording-copy").textContent =
          "You can still connect a tool below, then check again.";
      }
    }
  } finally {
    state.recordingLoading = false;
  }
}
function renderConnectorGuide() {
  const host = $("capture-host").value;
  // Shell-quote the installation address, including deployments with a base path.
  const url = state.me.mcp_url.replace(/\/mcp\/?$/, "");
  const quoted = "'" + url.replaceAll("'", "'\"'\"'") + "'";
  $("capture-command").textContent =
    `uv tool install --force 'git+https://github.com/moe-berri/litellm-memory.git@main'\nlitellm-memory-connector connect ${quoted} --host ${host}`;
  $("capture-restart-title").textContent =
    host === "codex" ? "Finish setup in Codex" : "Finish setup in Claude Code";
  $("capture-restart").textContent =
    host === "codex"
      ? "Restart Codex and sign in to its LiteLLM Memory MCP when prompted. Open /hooks and review and trust the LiteLLM Memory reminder hooks."
      : "Restart Claude Code and sign in to its LiteLLM Memory MCP when prompted. The connector has added memory reminder hooks.";
  renderBackfillGuide();
}
function renderBackfillGuide() {
  $("backfill-command").textContent =
    $("backfill-host").value === "codex"
      ? "litellm-memory-connector backfill ~/.codex/sessions --host codex"
      : "litellm-memory-connector backfill ~/.claude/projects --host claude";
}
function reviewRecording() {
  $("connector-guide").open = false;
  $("recording-controls").open = true;
  setView("connections");
  $("recording-controls").scrollIntoView({ block: "nearest" });
  $("recording-controls").querySelector("summary").focus();
}
async function loadConnections() {
  renderConnectorGuide();
  await refreshRecording({ loadClients: false });
  await loadConnectionList();
}
async function loadConnectionList() {
  const version = ++state.connectionsVersion;
  const data = await api("/api/v1/connections");
  if (version !== state.connectionsVersion || !state.me) return;
  state.listedClientCount = data.connections.filter(
    (client) => client.connected,
  ).length;
  const list = $("connection-list");
  list.replaceChildren();
  for (const c of data.connections.filter((c) => !c.revoked)) {
    const row = el("div", "", "connection-row");
    const name = el("div");
    name.append(
      el("strong", c.label),
      el("small", !c.connected ? "Expired · reconnect this tool" : "Connected"),
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
          state.guideInitialized = false;
          await loadConnections();
        } catch (error) {
          toast(explain(error));
        }
      });
    row.append(name, revoke);
    list.append(row);
  }
  show("connected-tools", list.children.length > 0);
}
$("capture-host").onchange = renderConnectorGuide;
$("backfill-host").onchange = renderBackfillGuide;
$("manage-recording").onclick = reviewRecording;
$("check-recording").onclick = () =>
  busy($("check-recording"), async () => {
    try {
      await loadConnections();
    } catch (error) {
      $("recording-error").textContent = explain(error);
    }
  });
$("see-my-work").onclick = () => {
  state.firstReceipt = false;
  renderRecording();
  openFeed("mine");
};
setInterval(() => {
  if (!document.hidden && ["home", "connections"].includes(state.view))
    refreshRecording();
}, 15000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && ["home", "connections"].includes(state.view))
    refreshRecording();
});

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
  state.settingsSection = "general";
  setView("settings");
  selectSettings("general");
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
    await Promise.all([loadModels().catch(modelError), loadViewPreferences()]);
    if (!state.me?.enrolled) return;
    show("boot-status", false);
    show("workspace");
    restoreRoute();
    refreshRecording();
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

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.view === "home") openFeed();
    else setView(button.dataset.view);
  });
});
document
  .querySelectorAll("[data-feed]")
  .forEach((button) =>
    button.addEventListener("click", () => openFeed(button.dataset.feed)),
  );
$("clear-person").onclick = () => openFeed();
$("refresh-feed").onclick = () =>
  busy($("refresh-feed"), async () => {
    await Promise.all([loadFeed(), refreshRecording()]);
  });
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
function resetSearch() {
  state.searchVersion++;
  state.searchRequest = null;
  state.searchNext = null;
  state.searchLoading = false;
  $("search-submit").disabled = false;
  $("search-more").disabled = false;
  $("search-results").removeAttribute("aria-busy");
  $("search-results").replaceChildren();
  $("search-status").textContent =
    "Enter a word or phrase to find recorded work.";
  for (const id of [
    "search-error",
    "search-retry",
    "search-more",
    "search-personal-filters",
  ])
    show(id, false);
  const scope = $("search-scope").value;
  $("search-description").textContent =
    scope === "mine"
      ? "Your own recorded work, including private memories."
      : scope.startsWith("person:")
        ? `Work ${profile(scope.slice(7)).name} has shared with your company.`
        : "Work shared with your company.";
}
function focusSearch() {
  if (!state.me?.enrolled) return;
  closeAccountMenu();
  const person =
    state.view === "home" && state.person && state.person !== state.me.id
      ? state.person
      : null;
  $("search-scope")
    .querySelectorAll("option[data-person]")
    .forEach((option) => option.remove());
  if (person) {
    const option = el("option", `${profile(person).name}’s shared work`);
    option.value = `person:${person}`;
    option.dataset.person = "true";
    $("search-scope").append(option);
  }
  $("search-scope").value = person
    ? `person:${person}`
    : state.view === "home" && state.feed === "mine"
      ? "mine"
      : "shared";
  resetSearch();
  $("search-dialog").showModal();
  $("search-query").focus();
  $("search-query").select();
}
function searchResult(memory) {
  const result = el("button", "", "work-search-result");
  result.type = "button";
  const meta = el("span", "", "work-search-meta");
  meta.append(
    el(
      "span",
      memory.owner === state.me.id ? "You" : profile(memory.owner).name,
    ),
    privacy(memory.visibility),
    el("span", date(memory.created_at || memory.captured_at)),
  );
  result.append(
    el("span", memory.title || "Recorded work", "work-search-title"),
    meta,
  );
  if (memory.excerpt)
    result.append(el("span", memory.excerpt, "work-search-excerpt"));
  result.onclick = () =>
    openMemory(memory.id).catch((error) => toast(explain(error)));
  return result;
}
async function searchWork(append = false) {
  const query = $("search-query").value.trim();
  if (
    !query ||
    state.searchLoading ||
    !$("search-dialog").open ||
    !state.me?.enrolled
  )
    return;
  if (append && (state.searchNext === null || !state.searchRequest)) return;
  const scope = $("search-scope").value;
  const payload = append
    ? state.searchRequest
    : {
        query,
        owner:
          scope === "mine"
            ? state.me.id
            : scope.startsWith("person:")
              ? scope.slice(7)
              : null,
        visibility: scope === "mine" ? null : "public",
        mode: "hybrid",
        limit: 20,
      };
  const version = ++state.searchVersion;
  state.searchRequest = payload;
  state.searchLoading = true;
  $("search-submit").disabled = true;
  $("search-more").disabled = true;
  $("search-results").setAttribute("aria-busy", "true");
  $("search-status").textContent = append
    ? "Loading more results…"
    : "Searching…";
  show("search-error", false);
  show("search-retry", false);
  if (!append) {
    $("search-results").replaceChildren();
    show("search-more", false);
    show("search-personal-filters", false);
  }
  try {
    const result = await api("/api/v1/memories/search", {
      ...payload,
      offset: append ? state.searchNext : 0,
    });
    if (version !== state.searchVersion || !$("search-dialog").open) return;
    for (const memory of result.results)
      $("search-results").append(searchResult(memory));
    state.searchNext = result.next_offset;
    $("search-status").textContent = result.total
      ? `${result.total} ${result.total === 1 ? "result" : "results"}`
      : "No matching work. Try another phrase or search scope.";
    show("search-personal-filters", result.personal_filters_active);
    show(
      "search-more",
      state.searchNext !== null && state.searchNext !== undefined,
    );
  } catch (error) {
    if (version !== state.searchVersion || !$("search-dialog").open) return;
    $("search-status").textContent = "";
    $("search-error").textContent = explain(error);
    show("search-error");
    show("search-retry");
    $("search-retry").onclick = () => searchWork(append);
  } finally {
    if (version === state.searchVersion) {
      state.searchLoading = false;
      $("search-submit").disabled = false;
      $("search-more").disabled = false;
      $("search-results").removeAttribute("aria-busy");
    }
  }
}
$("global-search-button").onclick = focusSearch;
$("feed-search-button").onclick = focusSearch;
$("search-query").oninput = resetSearch;
$("search-scope").onchange = resetSearch;
$("close-search").onclick = () => $("search-dialog").close();
$("search-dialog").addEventListener("close", resetSearch);
$("search-dialog").addEventListener("click", (event) => {
  if (event.target !== $("search-dialog")) return;
  const bounds = $("search-dialog").getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    $("search-dialog").close();
});
$("search-more").onclick = () => searchWork(true);
$("search-edit-filters").onclick = () => {
  $("search-dialog").close();
  editViewPreferences();
};
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
function resetFilterFields() {
  for (const id of [
    "filter-kind",
    "filter-source",
    "filter-project",
    "filter-from",
    "filter-before",
  ])
    $(id).value = "";
}
function clearFilters() {
  $("filter-button").focus();
  resetFilterFields();
  loadFeed();
}
$("clear-filters").onclick = clearFilters;
$("retry-feed").onclick = () => loadFeed();
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
  searchWork();
};
$("load-more").onclick = () => busy($("load-more"), () => loadFeed(true));
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
$("close-source").onclick = () => $("source-dialog").close();
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
        await refreshRecording();
        toast(
          key === "capture_enabled"
            ? desired
              ? "Recording resumed."
              : "Recording paused."
            : desired
              ? "Agent access enabled."
              : "Agent access paused.",
        );
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
      await refreshRecording();
    } catch (e) {
      toast(explain(e));
    }
  });
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
        "my-memory-export.litellm-memory",
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
