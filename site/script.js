// flowforge landing — copy-to-clipboard for install/command pills.
// Mirrors the codegraph pattern: swap the button glyph to "Copied" briefly.

const COPIED_MS = 1400;

function flashCopied(button) {
  const original = button.innerHTML;
  button.innerHTML = '<span class="copied">Copied</span>';
  button.disabled = true;
  window.setTimeout(() => {
    button.innerHTML = original;
    button.disabled = false;
  }, COPIED_MS);
}

async function copyText(text, button) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    flashCopied(button);
  } catch {
    /* clipboard unavailable — no-op */
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest(".copy");
  if (!button) return;
  const holder = button.closest("[data-install]");
  if (!holder) return;
  copyText(holder.getAttribute("data-install"), button);
});
