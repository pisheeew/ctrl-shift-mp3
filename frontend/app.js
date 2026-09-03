"use strict";

// ------------------------------------------------------------------ //
// State
// ------------------------------------------------------------------ //
let tracks = {};      // index -> track dict
let queueUrls = [];
let config = {};

const $ = (id) => document.getElementById(id);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function apiCall(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(path);
  return res.json();
}

// ------------------------------------------------------------------ //
// Bootstrap
// ------------------------------------------------------------------ //
document.addEventListener("DOMContentLoaded", async () => {
  await typewriter($("boot-title"), "CTRL+SHIFT+MP3");
  logLine("$ connecting to shell...");

  const state = await apiGet("/api/init");
  config = state.config;
  queueUrls = state.queue_urls;
  tracks = {};
  for (const t of state.tracks) tracks[t.index] = t;
  renderQueue();
  renderAllTracks();
  setSlotCount(config.concurrent_downloads || 3);
  $("output-input").value = config.output_dir || "";
  wireEvents();
  connectEvents();

  logLine(`[OK] session restored -- ${queueUrls.length} queued playlist(s), ${state.tracks.length} track(s)`);
});

function typewriter(el, text, speed = 42) {
  return new Promise((resolve) => {
    if (reducedMotion) {
      el.textContent = text;
      resolve();
      return;
    }
    let i = 0;
    el.textContent = "";
    const timer = setInterval(() => {
      i++;
      el.textContent = text.slice(0, i);
      if (i >= text.length) {
        clearInterval(timer);
        resolve();
      }
    }, speed);
  });
}

function logLine(line) {
  const el = $("sys-log");
  if (el) el.textContent = "> " + line;
}

// ------------------------------------------------------------------ //
// Server-Sent Events - push updates from background scan/download threads
// ------------------------------------------------------------------ //
let sseConnected = null; // null = unknown/first connect

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onopen = () => setConnStatus(true);
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    handleBridgeEvent(msg);
  };
  es.onerror = () => {
    // Browser's EventSource auto-reconnects; just reflect state in the UI.
    setConnStatus(false);
  };
}

function setConnStatus(connected) {
  const el = $("conn-status");
  if (!el) return;
  if (connected) {
    el.textContent = "[ CONN: LIVE ]";
    el.className = "pill pill-ok";
    if (sseConnected === false) {
      toast("ok", "Reconnected to server stream.");
      logLine("[OK] event stream reconnected");
    }
  } else {
    el.textContent = "[ CONN: RECONNECTING ]";
    el.className = "pill pill-warn";
    if (sseConnected === true || sseConnected === null) {
      toast("warn", "Lost connection to server -- retrying...");
      logLine("[WARN] event stream lost, retrying");
    }
  }
  sseConnected = connected;
}

function handleBridgeEvent(msg) {
  const { type, payload } = msg;
  switch (type) {
    case "track_update":
      tracks[payload.index] = payload;
      renderTrackRow(payload);
      break;
    case "clear_tracks":
      tracks = {};
      $("track-body").innerHTML = "";
      break;
    case "status":
      $("status-text").textContent = payload;
      logLine(payload);
      break;
    case "progress":
      setAsciiBar("overall-progress", payload);
      break;
    case "slot_update":
      setSlot(payload.slot, payload.current_file, payload.percent);
      break;
    case "slots_init":
      setSlotCount(payload.count);
      break;
    case "busy":
      setBusy(payload);
      break;
    case "error":
      toast("error", payload);
      break;
    case "scan_done":
      // OPT-001: previously a no-op. Scanning is over (whether it finished,
      // was stopped, or errored) -- the overall bar was tracking scan
      // progress, so reset it rather than leaving a stale 100%/partial fill
      // sitting there before the next scan or download run.
      setAsciiBar("overall-progress", 0);
      break;
    case "scan_complete":
      // OPT-002: use payload.total for a small structured summary alongside
      // the message, instead of leaving it unread on the wire.
      $("status-text").textContent = payload.message;
      logLine(`[OK] ${payload.message} (total tracks: ${payload.total})`);
      toast("ok", payload.message);
      blinkStatus();
      break;
    case "download_complete":
      // OPT-002: same treatment for succeeded/skipped -- shown in the log
      // line as a structured breakdown rather than only inside the string.
      $("status-text").textContent = payload.message;
      logLine(
        `[${payload.failed > 0 ? "WARN" : "OK"}] ${payload.message} ` +
        `(succeeded: ${payload.succeeded}, failed: ${payload.failed}, skipped: ${payload.skipped})`
      );
      toast(payload.failed > 0 ? "warn" : "ok", payload.message);
      blinkStatus();
      break;
    case "download_failures":
      showDownloadFailures(payload);
      break;
  }
}

// Makes the status line blink briefly (reusing the same blink keyframe the
// [WARN] connection pill uses) to flag "this run just finished" without
// requiring the user to be staring at the log line when it happens.
let blinkTimer = null;
function blinkStatus() {
  const el = $("status-text");
  if (!el) return;
  el.classList.remove("blink");
  void el.offsetWidth; // force reflow so re-adding the class restarts the animation
  el.classList.add("blink");
  clearTimeout(blinkTimer);
  blinkTimer = setTimeout(() => el.classList.remove("blink"), 6000);
}

function setBusy(busy) {
  for (const id of ["btn-scan", "btn-scan-all", "btn-download-all", "btn-retry-failed", "btn-clear-tracks", "btn-clear-cache"]) {
    $(id).disabled = busy;
  }
  $("btn-stop").disabled = !busy;
}

// ------------------------------------------------------------------ //
// ASCII progress bars
// ------------------------------------------------------------------ //
// OPT-006: single shared renderer, used by both the overall progress bar
// (width 24) and each per-slot bar (width 14) -- previously duplicated with
// only the width constant differing.
function renderAsciiBar(percent, width) {
  const pct = Math.max(0, Math.min(100, Math.round(percent || 0)));
  const filled = Math.round((pct / 100) * width);
  const bar = "|".repeat(filled) + ".".repeat(width - filled);
  return { text: `[${bar}] ${pct}%`, pct };
}

function setAsciiBar(id, percent) {
  const el = $(id);
  if (!el) return;
  // The overall bar is the primary progress signal now that per-slot rows
  // no longer show a "remaining in queue" count (it went stale the instant
  // another slot finished a track), so give it more width than the
  // per-slot bars (14) to make it easier to read at a glance.
  const width = id === "overall-progress" ? 48 : 24;
  const { text, pct } = renderAsciiBar(percent, width);
  el.textContent = text;
  el.setAttribute("aria-valuenow", String(pct));
}

// ------------------------------------------------------------------ //
// Download slots -- one row per concurrent worker ("lane"). A slot stays
// assigned to the same row across every track that worker picks up, so
// with concurrent_downloads=N you get a stable list of N in-progress rows
// instead of a single line that jumps between tracks.
// ------------------------------------------------------------------ //
function ensureSlotCount(n) {
  setSlotCount(Math.max(n, $("download-slots")?.children.length || 0));
}

function setSlotCount(n) {
  const container = $("download-slots");
  if (!container) return;
  const current = container.children.length;
  for (let i = current; i < n; i++) {
    const row = document.createElement("div");
    row.className = "slot-row idle";
    row.id = `slot-${i}`;
    row.innerHTML = `
      <span class="slot-label">[${String(i).padStart(2, "0")}]</span>
      <span class="slot-file"></span>
      <span class="slot-bar ascii-bar"></span>
    `;
    container.appendChild(row);
  }
  while (container.children.length > n) {
    container.removeChild(container.lastChild);
  }
}

function setSlot(slot, currentFile, percent) {
  ensureSlotCount(slot + 1);
  const row = $(`slot-${slot}`);
  if (!row) return;
  if (currentFile !== undefined) {
    row.querySelector(".slot-file").textContent = currentFile;
    row.classList.toggle("idle", !currentFile);
  }
  if (percent !== undefined) {
    row.querySelector(".slot-bar").textContent = renderAsciiBar(percent, 14).text;
  }
}

// ------------------------------------------------------------------ //
// Toasts (replaces alert())
// ------------------------------------------------------------------ //
const TOAST_TAGS = { ok: "[OK]", info: "[INFO]", warn: "[WARN]", error: "[ERR]" };
const TOAST_DURATIONS = { ok: 5000, info: 5000, warn: 7000, error: 9000 };

function toast(kind, message) {
  const container = $("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `
    <span class="toast-tag">${TOAST_TAGS[kind] || "[INFO]"}</span>
    <span class="toast-msg"></span>
    <button class="toast-close" aria-label="Dismiss">[x]</button>
  `;
  el.querySelector(".toast-msg").textContent = message;
  const remove = () => el.remove();
  el.querySelector(".toast-close").addEventListener("click", remove);
  container.appendChild(el);
  setTimeout(remove, TOAST_DURATIONS[kind] || 6000);

  // cap the stack so a burst of events doesn't flood the screen
  while (container.children.length > 4) container.removeChild(container.firstChild);
}

// ------------------------------------------------------------------ //
// Rendering
// ------------------------------------------------------------------ //
function renderAllTracks() {
  $("track-body").innerHTML = "";
  const ordered = Object.values(tracks).sort((a, b) => a.index - b.index);
  for (const t of ordered) renderTrackRow(t);
}

function renderTrackRow(t) {
  let row = document.getElementById(`row-${t.index}`);
  if (!row) {
    row = document.createElement("tr");
    row.id = `row-${t.index}`;
    row.dataset.index = t.index;
    row.addEventListener("click", () => selectRow(t.index));
    $("track-body").appendChild(row);
  }
  row.className = `status-${t.status}`;
  if (selectedIndex === t.index) row.classList.add("selected");
  const fromPlaylists = [t.source_playlist, ...(t.also_in || [])].filter(Boolean).join(", ");
  const matchPct = t.match_score != null ? Math.round(t.match_score) : "";
  row.innerHTML = `
    <td>${t.index}</td>
    <td>${escapeHtml(t.title || "")}</td>
    <td>${escapeHtml(t.artist || "")}</td>
    <td>${escapeHtml(fromPlaylists)}</td>
    <td class="status-cell">${t.status}${t.also_in && t.also_in.length ? ` (+${t.also_in.length} more)` : ""}</td>
    <td>${matchPct}</td>
  `;
  if (t.status === "Downloading" || t.status === "Searching") row.scrollIntoView({ block: "nearest" });
}

let selectedIndex = null;
function selectRow(index) {
  selectedIndex = index;
  document.querySelectorAll("#track-body tr").forEach((r) => r.classList.remove("selected"));
  document.getElementById(`row-${index}`)?.classList.add("selected");
}

function renderQueue() {
  const sel = $("queue-list");
  sel.innerHTML = "";
  queueUrls.forEach((url, index) => {
    const opt = document.createElement("option");
    opt.textContent = url;
    opt.dataset.index = String(index); // TRC-009: real array index, not DOM position
    sel.appendChild(opt);
  });
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function showDownloadFailures(failures) {
  $("failures-summary").textContent =
    `${failures.length} track(s) failed to download. Skipped after trying any backup matches ` +
    `so the rest of the queue could finish. Use RETRY FAILED, or paste a manual override URL for a track.`;
  const list = $("failures-list");
  list.innerHTML = "";
  for (const f of failures) {
    const li = document.createElement("li");
    li.innerHTML = `${escapeHtml(f.name)} <span>-- ${escapeHtml(f.reason)}</span>`;
    list.appendChild(li);
  }
  $("failures-dialog").showModal();
  toast("warn", `${failures.length} track(s) failed to download -- see failures log.`);
}

// ------------------------------------------------------------------ //
// Event wiring
// ------------------------------------------------------------------ //
function wireEvents() {
  $("btn-scan").addEventListener("click", async () => {
    const url = $("link-input").value.trim();
    const res = await apiCall("/api/scan", { url });
    if (res && res.error) toast("error", res.error);
  });

  $("btn-add-queue").addEventListener("click", async () => {
    const url = $("link-input").value.trim();
    if (!url) return;
    const res = await apiCall("/api/queue/add", { url });
    if (res.error) { toast("error", res.error); return; }
    queueUrls = res.queue_urls;
    renderQueue();
    $("link-input").value = "";
  });

  $("btn-remove-queue").addEventListener("click", async () => {
    const sel = $("queue-list");
    const indices = Array.from(sel.selectedOptions)
      .map((o) => Number(o.dataset.index))
      .sort((a, b) => b - a);
    for (const i of indices) {
      const res = await apiCall("/api/queue/remove", { index: i });
      if (res && res.error) { toast("error", res.error); continue; }
      queueUrls = res.queue_urls;
    }
    renderQueue();
  });

  $("btn-clear-queue").addEventListener("click", async () => {
    const res = await apiCall("/api/queue/clear");
    queueUrls = res.queue_urls;
    renderQueue();
  });

  $("btn-clear-tracks").addEventListener("click", async () => {
    if (!Object.keys(tracks).length) return;
    if (!confirm("Clear all scanned tracks from the table? This can't be undone.")) return;
    const res = await apiCall("/api/tracks/clear");
    if (res && res.error) { toast("error", res.error); return; }
    tracks = {};
    selectedIndex = null;
    $("track-body").innerHTML = "";
    toast("ok", "Cleared all scanned tracks.");
  });

  $("btn-clear-cache").addEventListener("click", async () => {
    if (!confirm("Clear the cached YouTube match links? Future scans will re-search from scratch.")) return;
    const res = await apiCall("/api/cache/clear");
    if (res && res.error) { toast("error", res.error); return; }
    toast("ok", `Match cache cleared (${res.cleared} cached match${res.cleared === 1 ? "" : "es"} removed).`);
  });

  $("btn-scan-all").addEventListener("click", async () => {
    const res = await apiCall("/api/scan_all");
    if (res && res.error) toast("error", res.error);
  });

  $("btn-browse-output").addEventListener("click", async () => {
    const res = await apiCall("/api/browse_output_folder");
    if (res.path) $("output-input").value = res.path;
  });

  $("btn-apply-override").addEventListener("click", async () => {
    if (selectedIndex == null) { toast("warn", "Select a track and paste a YouTube URL first."); return; }
    const url = $("override-input").value.trim();
    const res = await apiCall("/api/override", { index: selectedIndex, url });
    if (res.error) toast("error", res.error);
    else $("override-input").value = "";
  });

  $("btn-download-all").addEventListener("click", async () => {
    const res = await apiCall("/api/download", { output_dir: $("output-input").value.trim() });
    if (res && res.error) toast("error", res.error);
  });

  $("btn-retry-failed").addEventListener("click", async () => {
    const res = await apiCall("/api/retry_failed", { output_dir: $("output-input").value.trim() });
    if (res && res.error) toast("error", res.error);
  });

  $("btn-stop").addEventListener("click", () => apiCall("/api/stop"));

  $("btn-export").addEventListener("click", async () => {
    const picked = await apiCall("/api/browse_save_csv");
    if (!picked.path) return;
    const res = await apiCall("/api/export_log", { path: picked.path });
    if (res.error) toast("error", res.error);
    else toast("ok", `Log saved to ${res.path}`);
  });

  $("btn-config-path").addEventListener("click", async () => {
    const state = await apiGet("/api/init");
    toast("info", `Settings: ${state.config_path}\nLog file: ${state.log_path}`);
  });

  $("btn-settings").addEventListener("click", openSettings);
  $("btn-close-settings").addEventListener("click", () => $("settings-dialog").close());
  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-close-failures").addEventListener("click", () => $("failures-dialog").close());
}

function openSettings() {
  $("cfg-client-id").value = config.spotify_client_id || "";
  $("cfg-client-secret").value = config.spotify_client_secret || "";
  $("cfg-quality").value = config.quality_kbps || "192";
  $("cfg-threshold").value = config.confidence_threshold ?? 75;
  $("cfg-concurrency").value = config.concurrent_downloads ?? 3;
  $("cfg-skip-existing").checked = !!config.skip_existing;
  $("cfg-track-numbers").checked = !!config.add_track_numbers;
  $("cfg-cover-art").checked = !!config.embed_cover_art;
  $("settings-msg").textContent = "";
  $("settings-dialog").showModal();
}

async function saveSettings() {
  const payload = {
    spotify_client_id: $("cfg-client-id").value,
    spotify_client_secret: $("cfg-client-secret").value,
    quality_kbps: $("cfg-quality").value,
    confidence_threshold: $("cfg-threshold").value,
    concurrent_downloads: $("cfg-concurrency").value,
    skip_existing: $("cfg-skip-existing").checked,
    add_track_numbers: $("cfg-track-numbers").checked,
    embed_cover_art: $("cfg-cover-art").checked,
  };
  const res = await apiCall("/api/settings", payload);
  if (res.error) {
    $("settings-msg").textContent = res.error;
    return;
  }
  config = { ...config, ...payload };
  setSlotCount(parseInt(config.concurrent_downloads, 10) || 3);
  $("settings-dialog").close();
  toast("ok", "Config saved.");
}
