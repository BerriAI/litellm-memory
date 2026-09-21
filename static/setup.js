// The installation form shares one page, keeps errors next to the action, and
// saves completed steps on the server. Secrets never go into browser storage.
export async function initializeSetup({ api, show, el, busy, populateSelect }) {
  const $ = (id) => document.getElementById(id);
  let status = await api("/api/setup/status");
  if (!status.required) return false;
  show("welcome", false);
  show("deployment-setup");
  show("boot-status", false);
  let modelState = null;
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const error = (id, e) =>
    ($(id).textContent = e.message || "Please try again.");
  function jev(result) {
    if (!result) return;
    $("jev-status").textContent =
      result.status === "ready"
        ? "TypeSafe Jev is connected through your gateway."
        : result.message;
    show("setup-recheck-jev", result.status !== "ready");
    show("jev-help", result.status !== "ready");
  }
  async function preferences() {
    await api(
      "/api/setup/preferences",
      { allowed_email_domain: $("setup-domain").value.trim(), timezone },
      "PUT",
    );
  }
  async function models() {
    modelState = await api("/api/setup/models");
    for (const [role, kind, inherit] of [
      ["agent_model", "chat", false],
      ["embedding_model", "embedding", false],
      ["slack_model", "chat", true],
      ["background_model", "generation", true],
    ])
      populateSelect(
        $("setup-" + role),
        modelState.catalogs[kind],
        modelState.models[role],
        { inherit },
      );
    $("setup-classifier_model").value = modelState.models.classifier_model;
    show("setup-models");
  }
  async function refresh() {
    status = await api("/api/setup/status");
    show("claim-form", !status.claimed);
    show("setup-content", status.claimed);
    if (!status.claimed) return;
    $("gateway-url").value = status.base_url || "";
    show("gateway-form", !status.gateway_connected);
    show("gateway-summary", status.gateway_connected);
    $("gateway-summary-url").textContent = status.base_url || "";
    $("gateway-key").placeholder = status.gateway_connected
      ? "Saved securely. Leave empty to keep it."
      : "Your LiteLLM key";
    $("gateway-badge").textContent = status.gateway_connected
      ? "Connected"
      : "Not connected";
    $("gateway-badge").classList.toggle("ready", status.gateway_connected);
    $("setup-domain").value = status.allowed_email_domain || "berri.ai";
    jev(status.jev);
    if (status.gateway_connected) await models();
    show("slack-panel", status.models_checked);
    if (status.models_checked) {
      const manifest = await api("/api/setup/slack-manifest");
      $("create-slack-app").href = manifest.create_url;
    }
    show("slack-create-options", !status.slack_configured);
    show("install-slack", status.slack_configured && !status.slack_installed);
    show(
      "verify-slack-identity",
      status.slack_installed && !status.identity_verified,
    );
    const slackReady = status.slack_installed && status.identity_verified;
    $("slack-badge").textContent = slackReady
      ? "Connected"
      : status.slack_installed
        ? "Verify your account"
        : status.slack_configured
          ? "Ready to install"
          : "Not connected";
    $("slack-badge").classList.toggle("ready", slackReady);
    show("setup-finish", status.models_checked && slackReady);
    $("storage-status").textContent = status.storage_configured
      ? "Encrypted storage will be checked before you enter."
      : "Waiting for encrypted storage. Your setup progress is saved.";
  }
  async function saveModels() {
    if (!modelState) return;
    const selected = {};
    for (const role of [
      "agent_model",
      "embedding_model",
      "slack_model",
      "background_model",
      "classifier_model",
    ])
      selected[role] = $("setup-" + role).value;
    $("setup-model-status").textContent = "Checking models through LiteLLM…";
    try {
      modelState = await api(
        "/api/setup/models",
        { models: selected, revision: modelState.revision },
        "PUT",
      );
      $("setup-model-status").textContent =
        "Models are ready. You can change them later.";
      await refresh();
    } catch (e) {
      error("setup-model-status", e);
    }
  }
  $("edit-gateway").onclick = () => {
    show("gateway-form");
    show("gateway-summary", false);
    $("gateway-url").focus();
  };
  $("claim-form").onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.submitter, async () => {
      try {
        await api("/api/setup/claim", { code: $("claim-code").value });
        $("claim-code").value = "";
        $("claim-error").textContent = "";
        await refresh();
      } catch (e) {
        error("claim-error", e);
      }
    });
  };
  $("gateway-form").onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.submitter, async () => {
      $("gateway-error").textContent = "";
      $("gateway-badge").textContent = "Connecting…";
      try {
        const result = await api(
          "/api/setup/gateway",
          { base_url: $("gateway-url").value, api_key: $("gateway-key").value },
          "PUT",
        );
        $("gateway-key").value = "";
        await refresh();
        jev(result.jev);
        if (result.jev.status === "ready") await saveModels();
      } catch (e) {
        $("gateway-badge").textContent = "Needs attention";
        error("gateway-error", e);
      }
    });
  };
  $("setup-model-form").onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.submitter, saveModels);
  };
  $("setup-recheck-jev").onclick = () =>
    busy($("setup-recheck-jev"), async () => {
      try {
        const result = await api("/api/setup/check-jev", {});
        jev(result);
        if (result.status === "ready") await saveModels();
      } catch (e) {
        error("jev-status", e);
      }
    });
  $("create-slack-app").onclick = () => ($("slack-credentials").open = true);
  $("slack-form").onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.submitter, async () => {
      try {
        await preferences();
        await api(
          "/api/setup/slack",
          {
            app_id: $("slack-app-id").value.trim(),
            client_id: $("slack-client-id").value.trim(),
            client_secret: $("slack-client-secret").value,
            signing_secret: $("slack-signing-secret").value,
          },
          "PUT",
        );
        $("slack-client-secret").value = "";
        $("slack-signing-secret").value = "";
        $("slack-setup-error").textContent = "";
        await refresh();
      } catch (e) {
        error("slack-setup-error", e);
      }
    });
  };
  $("auto-slack-form").onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.submitter, async () => {
      try {
        await preferences();
        await api("/api/setup/slack-create", {
          configuration_token: $("slack-config-token").value,
        });
        $("slack-config-token").value = "";
        await refresh();
      } catch (e) {
        error("slack-setup-error", e);
      }
    });
  };
  $("install-slack").onclick = () =>
    busy($("install-slack"), async () => {
      try {
        await preferences();
        const result = await api("/api/setup/slack-install", {});
        location.assign(result.url);
      } catch (e) {
        error("slack-setup-error", e);
      }
    });
  $("verify-slack-identity").onclick = () =>
    busy($("verify-slack-identity"), async () => {
      try {
        await preferences();
        location.assign("/auth/slack");
      } catch (e) {
        error("slack-setup-error", e);
      }
    });
  $("finish-deployment").onclick = () =>
    busy($("finish-deployment"), async () => {
      try {
        await api("/api/setup/finish", {
          allowed_email_domain: $("setup-domain").value.trim(),
          timezone,
        });
        location.assign("/");
      } catch (e) {
        error("finish-error", e);
      }
    });
  await refresh();
  return true;
}
