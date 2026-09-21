// Small shared primitives. Native dialogs provide focus trapping and Escape;
// content is always inserted as text, including messages returned by the API.
export function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

export async function busy(control, run) {
  if (!control || control.disabled) return;
  const hadFocus = document.activeElement === control;
  control.disabled = true;
  control.setAttribute("aria-busy", "true");
  try {
    return await run();
  } finally {
    control.disabled = false;
    control.removeAttribute("aria-busy");
    if (
      hadFocus &&
      document.activeElement === document.body &&
      control.getClientRects().length
    ) {
      control.focus({ preventScroll: true });
    }
  }
}

export function confirmAction({
  title,
  description,
  confirm = "Continue",
  danger = false,
  preview,
}) {
  return new Promise((resolve) => {
    const dialog = element("dialog", "", "confirm-dialog");
    const heading = element("h2", title);
    heading.id = "confirm-title";
    const message = element("p", description, "muted");
    message.id = "confirm-description";
    dialog.setAttribute("aria-labelledby", heading.id);
    dialog.setAttribute("aria-describedby", message.id);
    dialog.append(heading, message);
    if (preview) {
      const content = element("div", "", "sharing-preview");
      content.append(
        element("h3", preview.title),
        element("p", preview.summary),
      );
      dialog.append(content);
    }
    const form = element("form", "", "dialog-actions");
    form.method = "dialog";
    const cancel = element("button", "Cancel", "button secondary");
    cancel.value = "cancel";
    cancel.autofocus = true;
    const accept = element(
      "button",
      confirm,
      `button ${danger ? "destructive" : "primary"}`,
    );
    accept.value = "confirm";
    form.append(cancel, accept);
    dialog.append(form);
    dialog.addEventListener(
      "close",
      () => {
        const accepted = dialog.returnValue === "confirm";
        dialog.remove();
        resolve(accepted);
      },
      { once: true },
    );
    document.body.append(dialog);
    dialog.showModal();
  });
}

const copyFeedback = new WeakMap();
export async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const previous = copyFeedback.get(button);
    if (previous) clearTimeout(previous.timer);
    const children = previous?.children || [...button.childNodes];
    button.textContent = "Copied";
    const timer = setTimeout(() => {
      button.replaceChildren(...children);
      copyFeedback.delete(button);
    }, 1800);
    copyFeedback.set(button, { children, timer });
    return true;
  } catch {
    return false;
  }
}
