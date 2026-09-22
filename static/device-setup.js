"use strict";

const title = document.getElementById("setup-title");
const message = document.getElementById("setup-message");
const userCode = new URLSearchParams(location.search).get("user_code");
const deadline = Date.now() + 10 * 60 * 1000;

async function checkSetup() {
  if (Date.now() > deadline) {
    title.textContent = "Check your terminal";
    message.textContent = "This page stopped waiting. If setup has finished, open Connections below. Otherwise, run the setup command again.";
    return;
  }
  try {
    const response = await fetch("/oauth/device/status?" + new URLSearchParams({ user_code: userCode }), { cache: "no-store" });
    if (response.status === 401) {
      title.textContent = "Sign in to continue";
      message.textContent = "Your browser session ended. Reload this page to sign in again.";
      return;
    }
    if (!response.ok) throw new Error("Unavailable");
    const result = await response.json();
    if (result.status === "complete") {
      location.replace(result.next);
      return;
    }
    if (["failed", "denied", "expired"].includes(result.status)) {
      title.textContent = result.status === "denied" ? "Setup cancelled" : "Check your terminal";
      message.textContent = result.status === "failed"
        ? "Your coding tools could not finish connecting. Your terminal has the next step. Run setup again after fixing the issue."
        : result.status === "denied"
          ? "No connection was added. Run the setup command again when you are ready."
          : "This setup request has ended. If your terminal says Connected, open Connections below to test it. Otherwise, run the setup command again.";
      return;
    }
    message.textContent = "Your terminal is configuring your coding tools. Keep it running; this page will update when setup is saved.";
  } catch {
    message.textContent = "Reconnecting to the website… Keep your terminal running.";
  }
  setTimeout(checkSetup, 2000);
}
checkSetup();
