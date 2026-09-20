import { initializeSetup } from "./setup.js?v=2";
import { marked } from "./vendor/marked.esm.js";
import DOMPurify from "./vendor/purify.es.mjs";

// Private content lives in this tab's memory only, never local/session storage.
const $ = (id) => document.getElementById(id);
const state = {
  me: null,
  people: [],
  view: "home",
  feed: "mine",
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
};
const icons = {
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  arrow: '<path d="M12 20V4m-6 6 6-6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  filter: '<path d="M4 6h16M7 12h10m-7 6h4"/>',
  shared:
    '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
};
function icon(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.lock}</svg>`;
  return span;
}
function el(tag, text = "", cls = "") {
  const n = document.createElement(tag);
  n.textContent = text;
  if (cls) n.className = cls;
  return n;
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
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "The server could not finish this request. Please try again.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "This request could not be completed.",
    );
  return body;
}
async function busy(button, fn) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    return await fn();
  } finally {
    button.disabled = false;
  }
}
function date(value, withTime = false) {
  if (!value) return "Date not recorded";
  const d = new Date(value);
  if (Number.isNaN(+d)) return "Date not recorded";
  return d.toLocaleString(
    undefined,
    withTime
      ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
      : {
          month: "short",
          day: "numeric",
          year:
            d.getFullYear() !== new Date().getFullYear()
              ? "numeric"
              : undefined,
        },
  );
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
  const url = safeImage(person.avatar);
  if (url) {
    const img = el("img");
    img.src = url;
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    n.append(img);
  } else n.textContent = (person.name || "?").trim().slice(0, 1).toUpperCase();
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
function setView(view) {
  state.view = view;
  for (const name of ["home", "ask", "people", "connections", "settings"])
    show(name + "-view", name === view);
  document.querySelectorAll(".nav-link").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
  });
  show("account-menu", false);
  $("account-button").setAttribute("aria-expanded", "false");
  if (view === "connections") loadConnections().catch((e) => toast(explain(e)));
  if (view === "settings") loadSettings().catch((e) => toast(explain(e)));
  if (view === "people") renderPeople();
  if (view === "ask") $("question").focus();
  window.scrollTo({ top: 0, behavior: "instant" });
}
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
  $("processing-status").textContent = stats.pending
    ? `${stats.pending} organizing`
    : stats.failed
      ? `${stats.failed} need attention`
      : "";
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
  show("work-overview", state.feed === "mine" || !!state.person);
  if (!append && $("work-overview").open) loadOverview();
  const version = ++state.feedVersion;
  show("page-error", false);
  const query = $("search-query").value.trim();
  if (!append) {
    $("feed").replaceChildren(el("p", "Loading memories…", "muted"));
    show("load-more", false);
  }
  try {
    const result = await api("/api/memories/search", {
      query,
      owner: state.person || (state.feed === "mine" ? state.me.id : null),
      visibility: state.feed === "shared" ? "public" : null,
      kind: $("filter-kind").value || null,
      created_from: $("filter-from").value || null,
      created_before: $("filter-before").value || null,
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
    if (!result.total)
      showEmpty(
        $("feed"),
        query
          ? "No matching memories"
          : state.feed === "shared"
            ? "A quiet start"
            : "Your work belongs here",
        query
          ? "Try another phrase or clear your filters."
          : state.feed === "shared"
            ? "Work appears here when someone chooses to share it with the company."
            : "Connect your AI tools to record your work, or add your first memory.",
        !query && state.feed === "mine"
          ? { label: "Connect your tools", run: () => setView("connections") }
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
  }
}
function feedCard(memory) {
  const card = el("article", "", "feed-card");
  const person = profile(memory.owner);
  card.append(avatar(person));
  const body = el("div", "", "feed-body");
  const line = el("div", "", "feed-byline");
  const who = el("button", memory.owner === state.me.id ? "You" : person.name);
  who.onclick = () => openPerson(memory.owner);
  line.append(
    who,
    el("span", date(memory.created_at || memory.captured_at)),
    privacy(memory.visibility),
  );
  body.append(line);
  const heading = el("h2");
  const title = el("button", memory.title || "Recorded work");
  title.onclick = () => openMemory(memory.id).catch((e) => toast(explain(e)));
  heading.append(title);
  body.append(heading, el("p", memory.excerpt));
  const meta = el("div", "", "feed-meta");
  meta.append(
    el(
      "span",
      [
        memory.kind,
        memory.source,
        memory.status !== "ready"
          ? memory.status === "failed"
            ? "Needs attention"
            : "Organizing"
          : null,
      ]
        .filter(Boolean)
        .join(" · "),
    ),
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
    .forEach((b) =>
      b.classList.toggle("selected", b.dataset.feed === state.feed),
    );
  setView("home");
  loadFeed().catch((e) => toast(explain(e)));
}

async function loadOverview() {
  const version = ++state.overviewVersion;
  const target = $("overview-content");
  target.replaceChildren(el("p", "Loading overview…", "muted"));
  try {
    const result = await api(
      `/api/people/${encodeURIComponent(state.person || state.me.id)}/overview`,
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
      entry.append(el("span", item.kind, "eyebrow"), el("p", item.text));
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
  const m = await api("/api/memories/" + encodeURIComponent(id));
  $("source-visibility").replaceChildren(privacy(m.visibility));
  const body = $("source-body");
  body.replaceChildren(
    el("h1", m.title),
    el(
      "p",
      `${profile(m.owner).name} · ${date(m.created_at || m.captured_at, true)} · ${m.source}`,
      "source-meta",
    ),
  );
  if (m.summary) body.append(el("p", m.summary));
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
    const link = el("button", newer ? "Read later correction" : "Read original entry", "button quiet small");
    link.onclick = () => openMemory(newer ? revision.record_id : revision.previous_id).catch(e => toast(explain(e)));
    body.append(link);
  }
  const actions = $("source-actions");
  actions.replaceChildren();
  if (m.owner === state.me.id) {
    const correct = el("button", "Add correction", "button secondary");
    correct.onclick = () => {
      const form = el("form", undefined, "correction-form");
      const label = el("label", "What should this say now?");
      const input = el("textarea"); input.required = true; input.maxLength = 200000; input.rows = 5;
      label.append(input);
      const explanation = el("p", "The original stays searchable. This adds a private, dated correction and withdraws the original from sharing.", "fine-print");
      const submit = el("button", "Save correction", "button primary");
      const cancel = el("button", "Cancel", "button quiet"); cancel.type = "button";
      cancel.onclick = () => { form.remove(); correct.disabled = false; };
      form.append(label, explanation, submit, cancel);
      correct.disabled = true; body.append(form); input.focus();
      form.onsubmit = event => {
        event.preventDefault();
        busy(submit, async () => {
          try {
            const saved = await api(`/api/memories/${encodeURIComponent(id)}/corrections`, {content:input.value});
            await openMemory(saved.id); await loadFeed(); toast("Private correction added. Original evidence preserved.");
          } catch (error) { toast(explain(error)); }
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
                `/api/memories/${encodeURIComponent(id)}/sharing-preview`,
              );
              if (!preview.can_share) {
                toast(preview.notice);
                return;
              }
              if (
                !confirm(
                  `${preview.title}\n\n${preview.summary}\n\n${preview.notice}\n\nShare this summary with the company?`,
                )
              )
                return;
            }
            await api(
              `/api/memories/${encodeURIComponent(id)}/visibility`,
              { visibility: m.visibility === "public" ? "private" : "public" },
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
            toast(explain(e));
          }
        });
      actions.append(share);
    } else actions.append(privacy("private"));
    const remove = el("button", "Delete memory", "button quiet danger");
    remove.onclick = async () => {
      if (
        !confirm(
          "Delete this memory and its original evidence? This cannot be undone.",
        )
      )
        return;
      await busy(remove, async () => {
        try {
          await api(
            `/api/memories/${encodeURIComponent(id)}`,
            undefined,
            "DELETE",
          );
          $("source-dialog").close();
          toast("Memory deleted.");
          await loadFeed();
        } catch (e) {
          toast(explain(e));
        }
      });
    };
    actions.append(remove);
  }
  if (!$("source-dialog").open) $("source-dialog").showModal();
}
function renderPeople() {
  const list = $("people-list");
  list.replaceChildren();
  for (const person of state.people) {
    const row = el("button", "", "person-row");
    row.append(avatar(person));
    const name = el("span");
    name.append(
      el("strong", person.name + (person.is_self ? " (you)" : "")),
      el(
        "small",
        person.is_self ? "Your private and shared work" : "Company-shared work",
      ),
    );
    row.append(name, el("span", "↗", "arrow"));
    row.onclick = () => openPerson(person.id);
    list.append(row);
  }
  if (!state.people.length)
    showEmpty(
      list,
      "Your team, in time",
      "People appear here after they join with Slack.",
    );
}
function populateSelect(select, models, selected, { inherit = false } = {}) {
  select.replaceChildren();
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
  const result = await api("/api/models");
  const current = $("model").value;
  state.models = result.data;
  populateSelect(
    $("model"),
    result.data,
    result.data.some((m) => m.id === current) ? current : result.default,
  );
  $("send-question").disabled = !result.data.length;
}
function newChat() {
  if (state.job) {
    toast("Stop the current answer before starting a new conversation.");
    return;
  }
  state.chatVersion++;
  state.previousJob = null;
  $("messages").replaceChildren();
  show("chat-empty");
  $("question").value = "";
}
function addMessage(role, text) {
  show("chat-empty", false);
  const message = el("article", "", "chat-message");
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
  $("send-question").disabled = value;
  show("stop-answer", value);
  $("chat-scope").disabled = value;
  $("new-chat").disabled = value;
}
async function ask(question) {
  question = question.trim();
  if (!question || state.job) return;
  const model = $("model").value;
  if (!model) {
    toast("Choose an available LiteLLM model first.");
    return;
  }
  setView("ask");
  const generation = state.chatVersion;
  addMessage("user", question);
  const answer = addMessage("assistant", "");
  answer.phase.textContent = "Starting…";
  chatBusy(true);
  $("question").value = "";
  state.job = "starting";
  try {
    const scope = $("chat-scope").value;
    const result = await api("/api/chat", {
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
      request_id: crypto.randomUUID(),
    });
    state.job = result.id;
    while (generation === state.chatVersion) {
      const job = await api("/api/jobs/" + encodeURIComponent(result.id));
      answer.phase.textContent = job.phase || "Working…";
      if (job.partial || job.answer)
        markdown(answer.body, job.answer || job.partial);
      if (["done", "failed", "cancelled"].includes(job.status)) {
        answer.phase.textContent =
          job.status === "done"
            ? ""
            : job.status === "cancelled"
              ? "Stopped."
              : job.error || "Could not finish this answer. Try again.";
        if (job.status === "done") state.previousJob = result.id;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  } catch (error) {
    answer.phase.textContent = explain(error);
    $("question").value = question;
  } finally {
    state.job = null;
    chatBusy(false);
  }
}
async function loadConnections() {
  const data = await api("/api/connections");
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
            "/api/connections/" + encodeURIComponent(c.id),
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
  const data = await api("/api/privacy-rules");
  $("rule-list").replaceChildren();
  for (const r of data.rules) {
    const row = el("div", "", "rule-row");
    row.append(icon("lock"), el("span", r.instruction));
    const remove = el("button", "Remove", "button quiet small");
    remove.onclick = () =>
      busy(remove, async () => {
        try {
          await api(
            "/api/privacy-rules/" + encodeURIComponent(r.id),
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
  show("model-settings");
  api("/api/admin/connection").then(connection => {
    $("current-gateway-url").textContent = connection.base_url;
  }).catch(error => { $("current-gateway-url").textContent = explain(error); });
  const candidates = state.people.filter(person => person.id !== state.me.id);
  show("admin-transfer-panel", state.me.registration_mode !== "owner_only" && candidates.length > 0);
  $("next-admin").replaceChildren(...candidates.map(person => {
    const option = el("option", person.name); option.value = person.id; return option;
  }));
  $("model-settings-status").textContent = "Loading available models…";
  try {
    const config = await api(
      "/api/admin/models" + (refresh ? "?refresh=true" : ""),
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
async function loadSettings() {
  state.me = await api("/api/me");
  $("capture-enabled").checked = state.me.capture_enabled;
  $("agent-enabled").checked = state.me.agent_enabled;
  show("sharing-note", !state.me.sharing_enabled);
  show("sharing-form", state.me.sharing_enabled);
  show("workspace-sharing-control", state.me.is_admin);
  $("workspace-sharing-enabled").checked = state.me.sharing_enabled;
  const sharing = await api("/api/sharing-preferences");
  document.querySelectorAll('[name="sharing-type"]').forEach((input) => {
    input.checked = sharing.allowed_types.includes(input.value);
  });
  $("auto-share").checked = sharing.auto_share;
  renderCoverage(state.me.stats);
  await Promise.all([loadRules(), loadModelSettings()]);
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
  $("today").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  try {
    if (await initializeSetup({ api, show, el, busy, populateSelect })) return;
    state.me = await api("/api/me");
    if (!state.me.signed_in) {
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
        $("member-setup-description").textContent = "Restore your private history. Previous connections stay revoked, sharing stays off, and recording stays paused until you enable it.";
        $("finish-signup").textContent = "Restore my account";
      }
      show("setup");
      return;
    }
    show("workspace");
    show("navigation");
    show("header-actions");
    $("my-avatar").replaceWith(
      Object.assign(avatar(state.me.profile), { id: "my-avatar" }),
    );
    $("account-name").textContent = state.me.profile.name;
    const people = await api("/api/people");
    state.people = people.people;
    await Promise.all([
      loadFeed(),
      loadModels().catch((e) => toast(explain(e))),
    ]);
    const match = location.pathname.match(/^\/memory\/([^/]+)$/);
    if (match) await openMemory(decodeURIComponent(match[1]));
  } catch (error) {
    toast(explain(error));
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
      .forEach((t) => t.classList.toggle("selected", t === b));
    loadFeed();
  }),
);
$("account-button").onclick = () => {
  const open = $("account-menu").hidden;
  show("account-menu", open);
  $("account-button").setAttribute("aria-expanded", String(open));
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
$("clear-filters").onclick = () => {
  for (const id of [
    "filter-kind",
    "filter-from",
    "filter-before",
    "search-query",
  ])
    $(id).value = "";
  loadFeed();
};
for (const id of ["filter-kind", "filter-from", "filter-before"])
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
$("home-ask-form").onsubmit = (e) => {
  e.preventDefault();
  const question = $("home-question").value;
  $("home-question").value = "";
  ask(question);
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
$("chat-scope").onchange = newChat;
$("stop-answer").onclick = () =>
  busy($("stop-answer"), async () => {
    if (state.job && state.job !== "starting") {
      try {
        await api("/api/jobs/" + encodeURIComponent(state.job) + "/cancel", {});
      } catch (e) {
        toast(explain(e));
      }
    }
  });
$("add-memory").onclick = () => $("capture-dialog").showModal();
$("close-capture").onclick = () => $("capture-dialog").close();
$("close-source").onclick = () => $("source-dialog").close();
$("capture-form").onsubmit = async (e) => {
  e.preventDefault();
  await busy(e.submitter, async () => {
    try {
      await api("/api/memories", {
        source_id: crypto.randomUUID(),
        title: $("capture-title").value,
        content: $("capture-content").value,
        occurred_at: new Date().toISOString(),
        source: "manual",
      });
      $("capture-form").reset();
      $("capture-dialog").close();
      toast("Memory saved privately.");
      await loadFeed();
    } catch (e) {
      toast(explain(e));
    }
  });
};
$("finish-signup").onclick = () =>
  busy($("finish-signup"), async () => {
    try {
      await api(state.me.can_rejoin ? "/api/rejoin" : "/api/signup", {});
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
      await api("/api/privacy-rules", { instruction: $("rule-input").value });
      $("rule-input").value = "";
      await loadRules();
      toast("Rule saved. Existing and future work will be checked.");
    } catch (e) {
      toast(explain(e));
    }
  });
};
$("sharing-form").onsubmit = async (event) => {
  event.preventDefault();
  await busy(event.submitter, async () => {
    try {
      await api(
        "/api/sharing-preferences",
        {
          allowed_types: [
            ...document.querySelectorAll('[name="sharing-type"]:checked'),
          ].map((input) => input.value),
          auto_share: $("auto-share").checked,
        },
        "PUT",
      );
      toast(
        "Sharing preferences saved. Cached classifications will be reused.",
      );
    } catch (error) {
      toast(explain(error));
    }
  });
};
$("workspace-sharing-enabled").onchange = async () => {
  const input = $("workspace-sharing-enabled");
  const desired = input.checked;
  if (
    !desired &&
    !confirm(
      "Turn off company sharing? Existing publications become private and won't be restored automatically.",
    )
  ) {
    input.checked = true;
    return;
  }
  await busy(input, async () => {
    try {
      await api("/api/admin/sharing", { enabled: desired }, "PUT");
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
        await api("/api/preferences", { [key]: desired }, "PUT");
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
      await api("/api/retry-processing", {});
      toast("Failed items queued for retry.");
      await loadSettings();
    } catch (e) {
      toast(explain(e));
    }
  });
$("copy-mcp").onclick = async () => {
  try {
    await navigator.clipboard.writeText(state.me.mcp_url);
    toast("MCP URL copied.");
  } catch {
    toast("Select the connection URL to copy it.");
  }
};
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
        "/api/admin/models",
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
$("export-data").onclick = () =>
  busy($("export-data"), async () => {
    try {
      const response = await fetch("/api/export", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Export could not be completed.");
      const url = URL.createObjectURL(await response.blob());
      const a = el("a");
      a.href = url;
      a.download = "my-memory-export.jsonl";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("Export downloaded. This file contains your readable memories.");
    } catch (e) {
      toast(explain(e));
    }
  });
$("encrypted-export").onclick = () => $("export-dialog").showModal();
$("close-export").onclick = () => $("export-dialog").close();
$("export-dialog").onclose = () => { $("encrypted-export-form").reset(); $("export-status").textContent = ""; };
$("encrypted-export-form").onsubmit = async event => {
  event.preventDefault();
  if ($("export-passphrase").value !== $("export-passphrase-confirm").value) {
    $("export-status").textContent = "The passphrases do not match."; return;
  }
  await busy(event.submitter, async () => {
    $("export-status").textContent = "Preparing your encrypted export…";
    try {
      const response = await fetch("/api/export", {method:"POST", credentials:"same-origin",
        headers:{"Content-Type":"application/json"}, body:JSON.stringify({encrypted:true,passphrase:$("export-passphrase").value})});
      if (!response.ok) throw new Error("Export could not be completed. Try again.");
      const url = URL.createObjectURL(await response.blob());
      const link = el("a"); link.href = url; link.download = "my-memory-export.litemem"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $("export-dialog").close();
      toast("Encrypted export downloaded. Keep its passphrase separately.");
    } catch (error) { $("export-status").textContent = explain(error); }
  });
};
$("leave-workspace").onclick = async () => {
  if (
    !confirm(
      "Leave litemem? Your connections and agent access will be revoked. Export your data first if you want a copy.",
    )
  )
    return;
  await busy($("leave-workspace"), async () => {
    try {
      await api("/api/leave", {});
      location.assign("/");
    } catch (e) {
      toast(explain(e));
    }
  });
};
$("sign-out").onclick = signOut;
$("menu-sign-out").onclick = signOut;
$("rotate-gateway-form").onsubmit = event => {
  event.preventDefault();
  busy(event.currentTarget.querySelector("button"), async () => {
    $("gateway-rotation-status").textContent = "Checking your models and Jev…";
    try {
      await api("/api/admin/connection/key", {api_key:$("replacement-gateway-key").value}, "PUT");
      $("replacement-gateway-key").value = "";
      $("gateway-rotation-status").textContent = "Key updated. Your model choices are unchanged.";
    } catch (error) { $("gateway-rotation-status").textContent = explain(error); }
  });
};
$("admin-transfer-form").onsubmit = event => {
  event.preventDefault();
  const select = $("next-admin");
  if (!select.value || !confirm(`Transfer workspace settings to ${select.selectedOptions[0].textContent}? You will no longer be the administrator.`)) return;
  busy(event.currentTarget.querySelector("button"), async () => {
    try {
      await api("/api/admin/transfer", {owner:select.value});
      location.reload();
    } catch (error) { toast(explain(error)); }
  });
};
initialize();
