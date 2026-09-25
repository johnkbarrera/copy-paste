/* ==========================================================================
   Copy Paste - Blobs
   Folder drag & drop upload (project page) and file viewer (blob page)
   ========================================================================== */

(function () {
  // Keep in sync with copy_paste_app/blobs.py
  const MAX_TOTAL = 50 * 1024 * 1024;
  const MAX_FILE = 5 * 1024 * 1024;
  const MAX_FILES = 3000;
  const IGNORED = new Set([
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".uv-cache",
    ".mypy_cache", ".pytest_cache", ".idea", ".DS_Store", "Thumbs.db",
  ]);

  const toast = (message, type) => window.showToast && window.showToast(message, type);

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  // Server accepts letters, numbers, spaces, hyphens and underscores
  function toBlobName(folderName) {
    return folderName.replace(/[^\p{L}\p{N} _-]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "Folder";
  }

  /* ---------------- Folder reading ---------------- */

  function readEntries(reader) {
    return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
  }

  function entryFile(entry) {
    return new Promise((resolve, reject) => entry.file(resolve, reject));
  }

  // Walk a dropped FileSystemEntry; returns [{file, path}] with paths relative to the dropped item's parent
  async function walk(entry, prefix, out) {
    if (IGNORED.has(entry.name)) return;
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile) {
      out.push({ file: await entryFile(entry), path });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      // readEntries returns results in batches until it returns an empty list
      for (let batch = await readEntries(reader); batch.length; batch = await readEntries(reader)) {
        for (const child of batch) await walk(child, path, out);
      }
    }
  }

  function isIgnored(path) {
    return path.split("/").some((part) => IGNORED.has(part));
  }

  // A single dropped folder becomes the blob: strip its name from every path
  function prepare(items) {
    const roots = new Set(items.map((item) => item.path.split("/")[0]));
    const singleFolder = roots.size === 1 && items.every((item) => item.path.includes("/"));
    const rootName = singleFolder ? [...roots][0] : "Files";

    const accepted = [];
    let skipped = 0;
    let total = 0;
    for (const item of items) {
      const path = singleFolder ? item.path.slice(rootName.length + 1) : item.path;
      if (!path || isIgnored(path) || item.file.size > MAX_FILE) {
        skipped += 1;
        continue;
      }
      total += item.file.size;
      accepted.push({ file: item.file, path });
    }
    return { name: toBlobName(rootName), files: accepted, skipped, total };
  }

  /* ---------------- Upload dropzone (project page) ---------------- */

  const drop = document.getElementById("blobDrop");
  if (drop) {
    const picker = document.getElementById("blobPicker");
    const idle = drop.querySelector(".blob-drop-idle");
    const form = drop.querySelector(".blob-drop-ready");
    const nameInput = document.getElementById("blobName");
    const summary = document.getElementById("blobSummary");
    const progress = drop.querySelector(".blob-drop-progress");
    const progressBar = progress.querySelector("span");
    const submitBtn = form.querySelector('button[type="submit"]');
    let pending = null;
    let uploading = false;

    function reset() {
      pending = null;
      form.hidden = true;
      idle.hidden = false;
      progress.hidden = true;
      submitBtn.disabled = false;
      drop.classList.remove("is-ready");
      picker.value = "";
    }

    function stage(items) {
      const prepared = prepare(items);
      if (!prepared.files.length) {
        toast("Nothing to upload: the folder is empty or everything was ignored", "error");
        return;
      }
      if (prepared.files.length > MAX_FILES) {
        toast(`Too many files (${prepared.files.length}); the limit is ${MAX_FILES}`, "error");
        return;
      }
      if (prepared.total > MAX_TOTAL) {
        toast(`Folder is ${formatSize(prepared.total)}; the limit is ${formatSize(MAX_TOTAL)}`, "error");
        return;
      }
      pending = prepared;
      nameInput.value = prepared.name;
      summary.textContent =
        `${prepared.files.length} file${prepared.files.length === 1 ? "" : "s"} · ${formatSize(prepared.total)}` +
        (prepared.skipped ? ` · ${prepared.skipped} skipped (ignored folders or over 5 MB)` : "");
      idle.hidden = true;
      form.hidden = false;
      drop.classList.add("is-ready");
      nameInput.focus();
      nameInput.select();
    }

    function upload() {
      if (!pending || uploading) return;
      const body = new FormData();
      body.append("name", nameInput.value.trim());
      for (const item of pending.files) {
        body.append("paths", item.path);
        body.append("files", item.file, item.file.name);
      }

      // XHR (not fetch) so we can show upload progress
      const xhr = new XMLHttpRequest();
      xhr.open("POST", drop.dataset.uploadUrl);
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) progressBar.style.width = `${Math.round((e.loaded / e.total) * 100)}%`;
      });
      xhr.addEventListener("load", () => {
        uploading = false;
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (err) {
          // Expired sessions get redirected to the login page (HTML)
        }
        if (xhr.status === 200 && data && data.ok) {
          window.location.href = data.url;
          return;
        }
        submitBtn.disabled = false;
        progress.hidden = true;
        const detail = data && data.detail ? data.detail : xhr.responseURL.includes("/login") ? "session expired, sign in again" : `server error ${xhr.status}`;
        toast(`Blob not uploaded: ${detail}`, "error");
      });
      xhr.addEventListener("error", () => {
        uploading = false;
        submitBtn.disabled = false;
        progress.hidden = true;
        toast("Blob not uploaded: connection error", "error");
      });

      uploading = true;
      submitBtn.disabled = true;
      progressBar.style.width = "0%";
      progress.hidden = false;
      xhr.send(body);
    }

    drop.addEventListener("click", (e) => {
      if (!form.hidden || e.target.closest("form")) return;
      picker.click();
    });
    drop.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && form.hidden) {
        e.preventDefault();
        picker.click();
      }
    });

    picker.addEventListener("change", () => {
      const items = Array.from(picker.files, (file) => ({ file, path: file.webkitRelativePath || file.name }));
      if (items.length) stage(items);
    });

    ["dragenter", "dragover"].forEach((type) =>
      drop.addEventListener(type, (e) => {
        e.preventDefault();
        if (!uploading) drop.classList.add("is-over");
      })
    );
    drop.addEventListener("dragleave", (e) => {
      if (!drop.contains(e.relatedTarget)) drop.classList.remove("is-over");
    });
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.classList.remove("is-over");
      if (uploading) return;
      // Entries must be grabbed synchronously, before any await
      const entries = Array.from(e.dataTransfer.items || [])
        .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
        .filter(Boolean);
      const items = [];
      try {
        for (const entry of entries) await walk(entry, "", items);
      } catch (err) {
        console.error("Could not read dropped folder:", err);
        toast("Could not read the dropped folder", "error");
        return;
      }
      if (items.length) stage(items);
    });

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      upload();
    });
    form.querySelector("[data-blob-cancel]").addEventListener("click", reset);
  }

  /* ---------------- File viewer (blob page) ---------------- */

  const viewer = document.getElementById("blobContent");
  const gutter = document.getElementById("lineGutter");
  if (viewer && gutter) {
    const lines = viewer.value.split("\n").length;
    gutter.textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
    gutter.parentElement.style.setProperty("--gutter-w", `calc(${Math.max(2, String(lines).length)}ch + 28px)`);
    viewer.addEventListener("scroll", () => {
      gutter.scrollTop = viewer.scrollTop;
    });
  }

  const copyFileBtn = document.getElementById("copyFileBtn");
  if (copyFileBtn && viewer) {
    copyFileBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(viewer.value);
        toast("File copied to clipboard", "success");
      } catch (err) {
        toast("Could not copy to clipboard", "error");
      }
    });
  }
})();
