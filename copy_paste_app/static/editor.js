/* ==========================================================================
   Copy Paste - Editor Engine
   Auto-save, Live Metrics, Indentation & Keyboard Shortcuts
   ========================================================================== */

(function () {
  const form = document.querySelector("#editorForm");
  const statusPill = document.querySelector("#saveStatus");
  const statusText = statusPill ? statusPill.querySelector(".status-text") : null;
  const content = document.querySelector("#content");
  const statsLabel = document.querySelector("#editorStats");
  const copyAllBtn = document.querySelector("#copyAllBtn");
  const gutter = document.querySelector("#lineGutter");
  let gutterLines = 0;

  let saveTimer = null;
  let isSaving = false;

  function setStatus(state, message) {
    if (!statusPill) return;
    statusPill.classList.remove("saving", "unsaved", "error");
    if (state) {
      statusPill.classList.add(state);
    }
    if (statusText) {
      statusText.textContent = message;
    } else {
      statusPill.textContent = message;
    }
  }

  function updateGutter() {
    if (!gutter || !content) return;
    const lines = content.value.split("\n").length;
    if (lines !== gutterLines) {
      gutterLines = lines;
      gutter.textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
      const digits = Math.max(2, String(lines).length);
      gutter.parentElement.style.setProperty("--gutter-w", `calc(${digits}ch + 28px)`);
    }
    gutter.scrollTop = content.scrollTop;
  }

  function updateMetrics() {
    if (!content || !statsLabel) return;
    const text = content.value;
    const lines = text.length === 0 ? 0 : text.split("\n").length;
    const words = text.trim() === "" ? 0 : text.trim().split(/\s+/).length;
    const chars = text.length;
    updateGutter();

    statsLabel.innerHTML = `<strong>${lines}</strong> line${lines === 1 ? "" : "s"} &bull; <strong>${words}</strong> word${words === 1 ? "" : "s"} &bull; <strong>${chars.toLocaleString()}</strong> char${chars === 1 ? "" : "s"}`;
  }

  let pendingManualSave = false;
  let lastSaveFailed = false;

  function toast(message, type) {
    if (window.showToast) window.showToast(message, type);
  }

  function reportFailure(statusMessage, toastMessage, manual) {
    setStatus("error", statusMessage);
    // Autosave retries on every pause; only toast once until a save succeeds
    if (manual || !lastSaveFailed) toast(toastMessage, "error");
    lastSaveFailed = true;
  }

  async function save(manual = false) {
    if (!form || !content) return;
    if (content.readOnly) {
      if (manual) toast("This project is disabled, enable it to make changes", "error");
      return;
    }
    if (isSaving) {
      // A manual save while autosave is in flight runs right after it
      if (manual) pendingManualSave = true;
      return;
    }
    isSaving = true;
    setStatus("saving", "Saving...");

    try {
      const body = new FormData();
      body.append("content", content.value);

      const response = await fetch(form.dataset.saveUrl, {
        method: "POST",
        body: body,
      });

      // An expired session redirects to /login, which fetch follows silently
      if (!response.ok || response.redirected) {
        const reason = response.redirected
          ? "session expired, sign in again"
          : response.status === 423
            ? "project is disabled"
            : `server error ${response.status}`;
        reportFailure("Failed to save", `Changes not saved: ${reason}`, manual);
        return;
      }

      const data = await response.json();
      const savedTime = new Date(data.saved_at || Date.now()).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
      setStatus("", `Saved at ${savedTime}`);
      if (manual || lastSaveFailed) toast("Changes saved", "success");
      lastSaveFailed = false;
    } catch (err) {
      console.error("Auto-save error:", err);
      reportFailure("Connection error", "Changes not saved: connection error", manual);
    } finally {
      isSaving = false;
      if (pendingManualSave) {
        pendingManualSave = false;
        save(true);
      }
    }
  }

  // Auto-save on typing + metrics
  if (content) {
    content.addEventListener("scroll", () => {
      if (gutter) gutter.scrollTop = content.scrollTop;
    });

    content.addEventListener("input", () => {
      setStatus("unsaved", "Unsaved changes");
      updateMetrics();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 850);
    });

    // Keyboard Shortcuts: Tab indentation & Cmd/Ctrl + S
    content.addEventListener("keydown", (e) => {
      // Tab key indentation
      if (e.key === "Tab" && !content.readOnly) {
        e.preventDefault();
        const start = content.selectionStart;
        const end = content.selectionEnd;
        const indent = "  "; // 2 spaces

        // Insert indent
        content.value = content.value.substring(0, start) + indent + content.value.substring(end);
        content.selectionStart = content.selectionEnd = start + indent.length;
        
        setStatus("unsaved", "Unsaved changes");
        updateMetrics();
        clearTimeout(saveTimer);
        saveTimer = setTimeout(save, 850);
      }

      // Ctrl + S or Cmd + S
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        clearTimeout(saveTimer);
        save(true);
      }
    });

    // Initial metrics calculation
    updateMetrics();
  }

  // Form submission handler
  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      clearTimeout(saveTimer);
      save(true);
    });
  }

  // Copy All button
  if (copyAllBtn && content) {
    copyAllBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(content.value);
        if (window.showToast) {
          window.showToast("All content copied to clipboard!", "success");
        }
        const originalHtml = copyAllBtn.innerHTML;
        copyAllBtn.innerHTML = `
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--success-text);">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
          <span style="color: var(--success-text);">Copied!</span>
        `;
        setTimeout(() => {
          copyAllBtn.innerHTML = originalHtml;
        }, 1800);
      } catch (err) {
        console.error("Clipboard copy failed:", err);
        toast("Could not copy to clipboard", "error");
      }
    });
  }
})();
