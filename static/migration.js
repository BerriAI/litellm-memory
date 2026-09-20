// Only recipient-encrypted packets cross installations. Receiving capabilities
// stay in memory; redirects carry them in fragments that are immediately removed.
const node = id => document.getElementById(id);
async function request(path, body, method = body === undefined ? "GET" : "POST") {
  const response = await fetch(path, {method, credentials:"same-origin", headers:{"Content-Type":"application/json"},
    ...(body !== undefined ? {body:JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "The transfer could not continue. Please retry.");
  return result;
}
function installation(value) {
  const url = new URL(value);
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Enter the HTTPS address of the other litemem installation.");
  }
  return url.origin;
}

export function initializeTransfer() {
  const dialog = node("transfer-dialog");
  if (!dialog) return;
  node("transfer-data").onclick = () => {node("transfer-status").textContent = ""; dialog.showModal();};
  node("close-transfer").onclick = () => dialog.close();
  node("transfer-connect").onsubmit = event => {
    event.preventDefault();
    try {
      const destination = installation(node("transfer-url").value.trim());
      if (destination === location.origin) throw new Error("Choose a different installation.");
      location.assign(`${destination}/transfer/receive#${new URLSearchParams({source:location.origin})}`);
    } catch (error) {node("transfer-status").textContent = error.message;}
  };
}
function description(row) {
  const handled = row.restored + row.duplicates + row.excluded;
  if (row.state === "complete") return `Import complete. ${row.restored} added, ${row.duplicates} already present, ${row.excluded} excluded by existing deletions or conflicts. Imported history is private.`;
  if (row.state === "waiting") return row.wait_reason === "recording" ? "Import is waiting for recording to be enabled in the destination settings."
    : row.wait_reason === "slack" ? "Import is waiting for Slack to verify your membership. It will retry automatically."
    : "Encrypted storage is temporarily unavailable. Import will retry automatically.";
  if (row.state === "importing") return `Importing privately · ${handled} of ${row.records} records. You may close this page.`;
  if (row.state === "cancelled") return "Transfer cancelled. Any records already imported remain private.";
  return "Waiting for the encrypted history…";
}
async function identify(status, login) {
  const me = await request("/api/me");
  if (!me.signed_in || !me.enrolled) {
    login.hidden = false;
    login.href = me.signed_in ? "/" : "/auth/slack?" + new URLSearchParams({next:location.pathname + location.search + location.hash});
    status(me.signed_in ? "Join this installation with your Slack account first, then restart the transfer." : "Sign in with the same Slack account you use on the other installation.");
    return null;
  }
  return me;
}

async function receiveTransfer() {
  const params = new URLSearchParams(location.hash.slice(1));
  const status = text => {node("receive-status").textContent = text;};
  let timer, grant = params.get("grant");
  const watch = async () => {
    try {
      const row = await request(`/api/transfers/${grant}`);
      status(description(row));
      node("receive-cancel").hidden = ["complete", "cancelled"].includes(row.state);
      if (["importing", "waiting"].includes(row.state)) timer = setTimeout(watch, 2000);
    } catch (error) {status(error.message);}
  };
  window.addEventListener("pagehide", () => clearTimeout(timer));
  node("receive-cancel").onclick = async () => {
    try {await request(`/api/transfers/${grant}`, undefined, "DELETE"); clearTimeout(timer); await watch();}
    catch (error) {status(error.message);}
  };
  try {
    const me = await identify(status, node("receive-login"));
    if (!me) return;
    node("receive-identity").textContent = `${me.profile.name} · ${me.profile.email}`;
    if (grant) {await watch(); return;}
    if (!params.get("source")) {
      const recent = await request("/api/transfers");
      status("Start a new transfer from Settings → Your data on the source installation.");
      for (const item of recent.transfers.filter(item => item.direction === "receive")) {
        const link = document.createElement("a");
        link.href = "/transfer/receive#" + new URLSearchParams({grant:item.id});
        link.textContent = `${new Date(item.created * 1000).toLocaleString()} · ${item.state}`;
        const line = document.createElement("p"); line.append(link); node("receive-recent").append(line);
      }
      return;
    }
    const source = installation(params.get("source"));
    if (source === location.origin) throw new Error("Choose a different source installation.");
    node("receive-source").textContent = source;
    node("receive-approve").hidden = false;
    status("Confirm this source. You will return there to review the destination before any history is sent.");
    node("receive-approve").onclick = async () => {
      node("receive-approve").disabled = true;
      try {
        const invitation = await request("/api/transfers/receive", {source});
        location.assign(`${source}/transfer/send?${new URLSearchParams({destination:location.origin})}#${new URLSearchParams({invite:JSON.stringify(invitation)})}`);
      } catch (error) {status(error.message); node("receive-approve").disabled = false;}
    };
  } catch (error) {status(error.message);}
}

async function sendTransfer() {
  const params = new URLSearchParams(location.hash.slice(1));
  let invitation, outgoing, timer, busy = false, cancelRequested = false;
  const status = text => {node("send-status").textContent = text;};
  // The capability never enters a query string, server log, or referrer. Remove
  // the fragment immediately rather than retaining it in browser history.
  const bookmark = params.get("outgoing") ? {outgoing:params.get("outgoing")} : params.get("receipt") ? {receipt:params.get("receipt")} : null;
  history.replaceState(null, "", location.pathname + location.search + (bookmark ? "#" + new URLSearchParams(bookmark) : ""));
  const inbox = async (body, method = body === undefined ? "GET" : "POST") => {
    const response = await fetch(`${invitation.destination}/api/transfer-inbox/${invitation.id}`, {
      method, credentials:"omit", headers:{"Content-Type":"application/json", "Authorization":"Bearer " + invitation.upload_token},
      ...(body !== undefined ? {body:JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The receiving grant expired. Start a new transfer.");
    return data;
  };
  const watch = async () => {
    try {
      const row = await inbox(); status(description(row));
      if (row.state === "complete") {
        node("send-cancel").hidden = true;
        history.replaceState(null, "", location.pathname + location.search + "#" + new URLSearchParams({receipt:invitation.id}));
        if (outgoing) {await request(`/api/transfers/${outgoing.id}`, undefined, "DELETE"); outgoing = null;}
      } else if (["importing", "waiting"].includes(row.state)) timer = setTimeout(watch, 2000);
    } catch (error) {status(error.message + " Check recent imports in the destination.");}
  };
  window.addEventListener("pagehide", () => clearTimeout(timer));
  const cancelTransfer = async () => {
    try {
      clearTimeout(timer); await inbox(undefined, "DELETE");
      if (outgoing) await request(`/api/transfers/${outgoing.id}`, undefined, "DELETE");
      node("send-confirm").hidden = true; node("send-cancel").hidden = true;
      status("Transfer cancelled. Your source history stays here. Any records already imported remain private.");
    } catch (error) {status(error.message);}
  };
  node("send-cancel").onclick = async () => {
    cancelRequested = true;
    if (busy) {status("Cancelling after the current packet…"); node("send-cancel").disabled = true; return;}
    await cancelTransfer();
  };
  try {
    const destination = installation(new URLSearchParams(location.search).get("destination"));
    if (params.get("receipt")) {
      node("send-receipt").href = destination + "/transfer/receive#" + new URLSearchParams({grant:params.get("receipt")});
      node("send-receipt").hidden = false;
      status("View this transfer’s current status at the destination.");
      return;
    }
    const me = await identify(status, node("send-login"));
    if (!me) return;
    if (params.get("outgoing")) {
      outgoing = await request(`/api/transfers/${params.get("outgoing")}/outgoing`);
      invitation = outgoing.invitation;
    } else invitation = JSON.parse(params.get("invite") || "null");
    if (!invitation || invitation.destination !== destination || invitation.source !== location.origin || invitation.owner !== me.id) {
      throw new Error("Sign in with the same Slack account on both installations, then restart the transfer from Settings.");
    }
    node("send-destination").textContent = destination;
    node("send-identity").textContent = `${me.profile.name} · ${me.profile.email}`;
    node("send-receipt").href = destination + "/transfer/receive#" + new URLSearchParams({grant:invitation.id});
    node("send-receipt").hidden = false;
    node("send-confirm").hidden = false; node("send-cancel").hidden = false;
    status("Review the destination. It will receive your private history, including original evidence and dates. The source copy stays here.");
    if (outgoing) {
      const progress = await inbox();
      if (["importing", "waiting", "complete"].includes(progress.state)) {
        node("send-confirm").hidden = true; await watch();
      }
    }
    node("send-confirm").onclick = async () => {
      busy = true; node("send-confirm").disabled = true;
      try {
        status("Encrypting your history for this destination…");
        outgoing ||= await request("/api/transfers/send", invitation);
        history.replaceState(null, "", location.pathname + location.search + "#" + new URLSearchParams({outgoing:outgoing.id}));
        if (cancelRequested) {await cancelTransfer(); return;}
        const progress = await inbox();
        if (!["importing", "waiting", "complete"].includes(progress.state)) {
          for (let index = progress.frames; index < outgoing.frames; index++) {
            const packet = await request(`/api/transfers/${outgoing.id}/packets/${index}`);
            if (cancelRequested) {await cancelTransfer(); return;}
            await inbox({action:"packet",packet});
            status(`Sending encrypted history · ${Math.round((index + 1) / outgoing.frames * 100)}%`);
          }
          if (cancelRequested) {await cancelTransfer(); return;}
          await inbox({action:"finish"});
        }
        node("send-confirm").hidden = true;
        await watch();
      } catch (error) {
        if (cancelRequested) await cancelTransfer();
        else {status(error.message + " Retry in a minute to resume from the last received packet."); node("send-confirm").textContent = "Retry transfer";}
      }
      finally {busy = false; node("send-confirm").disabled = false; node("send-cancel").disabled = false;}
    };
  } catch (error) {status(error.message);}
}
if (document.body.hasAttribute("data-transfer-receiver")) receiveTransfer();
if (document.body.hasAttribute("data-transfer-sender")) sendTransfer();
