// The server supplies the exact registered callback after approval. A document
// navigation avoids the cross-origin form redirect blocked by form-action CSP.
// The same link remains usable if scripts are unavailable.
const callback = document.getElementById("client-callback");
if (callback) window.location.replace(callback.href);
