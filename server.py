"""
server.py
Local Flask backend for the browser-based build. This replaces bridge.py
(the pywebview version) - same engine.py, same handlers, but instead of a
Python<->JS bridge inside an embedded WebView2 window, this runs a small
local HTTP server that the OS's actual default browser (Chrome, in your
case) connects to. That sidesteps WebView2 entirely.

Endpoints (all under /api/):
    GET  /api/init                  -> {config, tracks, queue_urls, ...}
    POST /api/scan                  {url}
    POST /api/queue/add             {url}
    POST /api/queue/remove          {index}
    POST /api/queue/clear
    POST /api/scan_all
    POST /api/download              {output_dir}
    POST /api/retry_failed          {output_dir}
    POST /api/stop
    POST /api/override              {index, url}
    POST /api/settings              {...}
    POST /api/browse_output_folder  -> native folder picker (tkinter dialog)
    POST /api/browse_save_csv       -> native save dialog (tkinter dialog)
    POST /api/export_log            {path}
    GET  /api/events                -> Server-Sent Events stream (progress/status push)
    GET  /                          -> frontend/index.html
"""

from __future__ import annotations

import base64
import concurrent.futures
import ctypes
import json
import logging
import os
import queue
import random
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

from flask import Flask, Response, abort, jsonify, request, send_from_directory

import engine
from engine import Track
from run_orchestration import RunOrchestrator

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".playlist_downloader_config.json")
CACHE_PATH = os.path.join(os.path.expanduser("~"), ".playlist_downloader_session.json")
LOG_PATH = os.path.join(os.path.expanduser("~"), ".playlist_downloader.log")
MATCH_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".playlist_downloader_match_cache.json")

DEFAULT_CONFIG = {
    "output_dir": "",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "quality_kbps": "192",
    "confidence_threshold": 75,
    "skip_existing": True,
    "add_track_numbers": True,
    "embed_cover_art": True,
    "concurrent_downloads": 3,
}

PORT = 8743


def resource_dir() -> str:
    """Return the directory containing application resources."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    return bundle_dir or os.path.dirname(os.path.abspath(__file__))


# SEC-005: placeholder shown in place of the real secret once one is saved,
# so the plaintext value is never re-sent to the browser on page load / the
# Settings dialog reopening. Submitting this unchanged value back is treated
# as "leave the existing secret alone" rather than overwriting it.
SECRET_MASK = "••••••••"

# SEC-001: only this origin (this app's own page) may POST to /api/*. A
# same-origin browser navigation to "/" doesn't send an Origin header on
# simple GETs, but fetch()/XHR calls (same- or cross-origin) do -- so any
# request that *has* an Origin header must match this exactly.
def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("playlist_downloader")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger


log = _setup_logging()


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            log.exception("Failed to load config from %s", CONFIG_PATH)
            # The file exists but cannot be parsed, so leaving it in place
            # would let the next save silently destroy whatever is in it --
            # possibly a recoverable Credential. Rename it aside first and
            # load defaults; the quarantine copy keeps the contents readable.
            _quarantine_config(CONFIG_PATH)
    return _restore_config_secrets(cfg)


def _quarantine_config(path: str) -> Optional[str]:
    """Rename an unparseable config aside so the next save cannot destroy
    its contents. Never overwrites an earlier quarantine copy (numbered
    suffixes instead) and never raises: returns the quarantine path, or
    None if the rename itself failed."""
    base = path + ".corrupt"
    target = base
    n = 1
    while os.path.exists(target):
        target = f"{base}.{n}"
        n += 1
    try:
        os.replace(path, target)
        log.warning("Quarantined unparseable config %s as %s; loading defaults",
                    path, target)
        return target
    except OSError:
        log.exception("Failed to quarantine unparseable config %s", path)
        return None


def save_config(cfg: dict) -> Optional[str]:
    try:
        protected = _protect_config_secrets(cfg)
        engine.write_json_atomically(protected, CONFIG_PATH, mode=0o600, indent=2)
        return None
    except Exception:
        # SEC-007: log full detail (including the path) server-side only;
        # the client only gets a generic message.
        log.exception("Failed to save config to %s", CONFIG_PATH)
        return "Could not save settings. Check the app log for details."


# ---------------------------------------------------------------------- #
# Credentials at rest (ADR-0003 follow-up)
#
# On Windows the secret-bearing config fields are encrypted with DPAPI
# (CryptProtectData, current-user scope) before they touch disk and
# decrypted on load, so file copies taken off the machine are unreadable
# and other local users cannot decrypt them. Encrypted values carry a
# "dpapi:" prefix; values without it (configs written by prior versions)
# load as plaintext and are encrypted on their next save. In-memory config
# always holds plaintext, so the SEC-005 secret masking is unaffected.
# ---------------------------------------------------------------------- #
SECRET_CONFIG_FIELDS = ("spotify_client_id", "spotify_client_secret")
_DPAPI_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True) if sys.platform == "win32" else None


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_call(func, data: bytes) -> bytes:
    blob_in = _DataBlob(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                    ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = _DataBlob()
    if not func(ctypes.byref(blob_in), None, None, None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(f"{func.__name__} failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _encrypt_config_value(value: str) -> str:
    if _crypt32 is None:
        # No DPAPI outside Windows; keep the historical plaintext behavior
        # (chmod 600 on POSIX is applied by save_config instead).
        log.info("Credential encryption unavailable on this platform; "
                 "writing %s unencrypted", CONFIG_PATH)
        return value
    cipher = _dpapi_call(_crypt32.CryptProtectData, value.encode("utf-8"))
    return _DPAPI_PREFIX + base64.b64encode(cipher).decode("ascii")


def _decrypt_config_value(value: str) -> str:
    cipher = base64.b64decode(value[len(_DPAPI_PREFIX):])
    return _dpapi_call(_crypt32.CryptUnprotectData, cipher).decode("utf-8")


def _protect_config_secrets(cfg: dict) -> dict:
    protected = dict(cfg)
    for field in SECRET_CONFIG_FIELDS:
        value = protected.get(field)
        if isinstance(value, str) and value and not value.startswith(_DPAPI_PREFIX):
            protected[field] = _encrypt_config_value(value)
    return protected


def _restore_config_secrets(cfg: dict) -> dict:
    restored = dict(cfg)
    for field in SECRET_CONFIG_FIELDS:
        value = restored.get(field)
        if isinstance(value, str) and value.startswith(_DPAPI_PREFIX):
            if _crypt32 is None:
                # Same platform guard as the encrypt path: outside Windows
                # there is no DPAPI to attempt, so keep the stored value
                # exactly as ADR-0003 requires -- the next save on a
                # Windows machine re-encrypts it. A synced config carrying
                # a "dpapi:" prefix must neither crash nor wedge restore.
                log.info("Credential decryption unavailable on this platform; "
                         "keeping %s in %s as stored", field, CONFIG_PATH)
                continue
            try:
                restored[field] = _decrypt_config_value(value)
            except Exception:
                # Keep the stored value rather than blanking it: the next
                # save re-persists it unchanged, so a transient failure or
                # a plaintext secret that happens to carry the prefix
                # destroys nothing.
                log.exception("Failed to decrypt %s in %s; using stored value",
                              field, CONFIG_PATH)
    return restored


def save_session_cache(tracks: list[Track], queue_urls: list[str]) -> None:
    try:
        payload = {"tracks": [engine.track_to_dict(t) for t in tracks], "queue_urls": queue_urls}
        engine.write_json_atomically(payload, CACHE_PATH)
    except Exception:
        log.exception("Failed to save session cache to %s", CACHE_PATH)


def load_session_cache() -> tuple[list[Track], list[str]]:
    if not os.path.exists(CACHE_PATH):
        return [], []
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        tracks = [engine.track_from_dict(d) for d in payload.get("tracks", [])]
        queue_urls = payload.get("queue_urls", [])
        return tracks, queue_urls
    except Exception:
        log.exception("Failed to load session cache from %s", CACHE_PATH)
        return [], []


# --------------------------------------------------------------------------- #
# SEC-002: path validation for client-supplied output_dir / export path.
# This app has no auth (SEC-001 covers the origin side), so the filesystem
# boundary has to be enforced here: only accept paths that already exist as
# a real directory (output_dir) or whose parent directory already exists
# (export path), and reject anything containing ".." traversal segments.
# This doesn't restrict *where* on disk the user's own folder-picker can
# choose (that's a legitimate, wide-open choice the user made via the native
# OS dialog) -- it only stops a blind POST from a malicious page pointing at
# a path nobody actually selected through the UI.
# --------------------------------------------------------------------------- #
def _validate_output_dir(path: str) -> Optional[str]:
    """Return an error string if path is unsafe/invalid, else None."""
    if not path:
        return "Pick a USB / output folder first."
    if ".." in os.path.normpath(path).split(os.sep):
        return "Invalid output folder path."
    if not os.path.isabs(path):
        return "Output folder must be an absolute path."
    if not os.path.isdir(path):
        return "Output folder does not exist. Use Browse... to pick a real folder."
    return None


def _validate_export_path(path: str) -> Optional[str]:
    """Return an error string if path is unsafe/invalid, else None."""
    if not path:
        return "No path given."
    if ".." in os.path.normpath(path).split(os.sep):
        return "Invalid export path."
    if not os.path.isabs(path):
        return "Export path must be an absolute path."
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        return "Export folder does not exist."
    return None


def _is_local_host(host_header: str) -> bool:
    """Return True only for a Host header naming this machine (ADR-0001).
    The port is ignored -- the app may bind a fallback port -- but the
    hostname itself must be a loopback name; anything else (including a
    rebinding attacker's own DNS name) is rejected. IPv6 literals are not
    allowed since the server binds IPv4 loopback only."""
    hostname = host_header.strip().lower()
    if ":" in hostname:
        hostname, _, port_part = hostname.rpartition(":")
        if not port_part.isdigit():
            return False
    return hostname in ("127.0.0.1", "localhost")


# --------------------------------------------------------------------------- #
# Native file/folder dialogs - a tiny hidden Tk root just for the OS picker.
# The rest of the app has no Tk UI; this is purely for the folder/save dialogs
# a browser can't produce on its own.
#
# SEC-006: Tkinter is not thread-safe and Flask runs threaded=True, so two
# near-simultaneous dialog-opening requests could race two tk.Tk() instances
# on different threads. Serialize all dialog calls behind one lock so at
# most one Tk root ever exists at a time.
# --------------------------------------------------------------------------- #
_dialog_lock = threading.Lock()


def _with_hidden_tk_root(fn):
    """Run fn(root) against a single withdrawn, topmost Tk root, serialized
    behind _dialog_lock, and always clean the root up afterward."""
    import tkinter as tk
    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return fn(root)
        finally:
            root.destroy()


def _pick_folder() -> Optional[str]:
    from tkinter import filedialog
    path = _with_hidden_tk_root(lambda root: filedialog.askdirectory(parent=root))
    return path or None


def _pick_save_csv() -> Optional[str]:
    from tkinter import filedialog
    path = _with_hidden_tk_root(lambda root: filedialog.asksaveasfilename(
        parent=root, defaultextension=".csv", filetypes=[("CSV files", "*.csv")],
        initialfile="playlist_download_log.csv",
    ))
    return path or None


# --------------------------------------------------------------------------- #
# Pub/sub for Server-Sent Events - one broadcast queue per connected tab.
# --------------------------------------------------------------------------- #
_listeners: list[queue.Queue] = []
_listeners_lock = threading.Lock()


def broadcast(event_type: str, payload=None):
    msg = json.dumps({"type": event_type, "payload": payload})
    with _listeners_lock:
        for q in _listeners:
            q.put(msg)


class _StopRequested(Exception):
    """Raised from inside a yt-dlp progress hook to abort an in-flight
    download the moment Stop is pressed, instead of only stopping *new*
    downloads from starting."""


class Core:
    """Holds app state and the same scan/download/retry logic the Tkinter
    and pywebview versions had. Push updates go out via broadcast() instead
    of a Tk queue or window.evaluate_js."""

    def __init__(self, providers=None):
        self.config_data = load_config()
        self.tracks: list[Track] = []
        self.queue_urls: list[str] = []
        self.orchestrator = RunOrchestrator()
        self.stop_requested = self.orchestrator.stop_requested
        self.busy = False
        self.match_cache: dict = engine.load_match_cache(MATCH_CACHE_PATH)
        self.providers = providers or engine.MediaProviders()

        cached_tracks, cached_queue = load_session_cache()
        self.tracks = cached_tracks
        self.queue_urls = cached_queue

    def _emit_track(self, t: Track):
        broadcast("track_update", engine.track_to_dict(t))

    def _set_busy(self, busy: bool):
        self.busy = busy
        broadcast("busy", busy)

    def get_init_state(self) -> dict:
        # SEC-005: never send the plaintext secret to the browser.
        public_config = dict(self.config_data)
        if public_config.get("spotify_client_secret"):
            public_config["spotify_client_secret"] = SECRET_MASK
        return {
            "config": public_config,
            "tracks": [engine.track_to_dict(t) for t in self.tracks],
            "queue_urls": self.queue_urls,
            "config_path": CONFIG_PATH,
            "log_path": LOG_PATH,
        }

    def save_settings(self, new_config: dict) -> dict:
        try:
            threshold = int(new_config.get("confidence_threshold", 75))
        except (TypeError, ValueError):
            return {"error": "Confidence threshold must be a whole number."}
        if not (0 <= threshold <= 100):
            return {"error": "Confidence threshold must be between 0 and 100."}

        quality = str(new_config.get("quality_kbps", "192")).strip()
        if quality not in ("128", "192", "256", "320"):
            return {"error": "Quality must be one of 128, 192, 256, 320."}

        try:
            concurrency = int(new_config.get("concurrent_downloads", 3))
        except (TypeError, ValueError):
            return {"error": "Concurrent downloads must be a whole number."}
        if not (1 <= concurrency <= 10):
            return {"error": "Concurrent downloads must be between 1 and 10."}

        submitted_secret = new_config.get("spotify_client_secret", "").strip()
        if submitted_secret == SECRET_MASK:
            # Dialog reopened without editing the secret field -- keep what's
            # already saved instead of overwriting it with the mask text.
            submitted_secret = self.config_data.get("spotify_client_secret", "")

        self.config_data.update({
            "spotify_client_id": new_config.get("spotify_client_id", "").strip(),
            "spotify_client_secret": submitted_secret,
            "quality_kbps": quality,
            "confidence_threshold": threshold,
            "skip_existing": bool(new_config.get("skip_existing", True)),
            "add_track_numbers": bool(new_config.get("add_track_numbers", True)),
            "embed_cover_art": bool(new_config.get("embed_cover_art", True)),
            "concurrent_downloads": concurrency,
        })
        err = save_config(self.config_data)
        if err:
            return {"error": err}
        return {"config_path": CONFIG_PATH}

    # ------------------------------------------------------------------ #
    # Single-link scan
    # ------------------------------------------------------------------ #
    def scan(self, url: str) -> dict:
        if self.busy:
            return {"error": "Busy -- stop the current scan/download first."}
        url = (url or "").strip()
        if not url:
            return {"error": "Paste a Spotify or YouTube playlist link first."}
        link_type = engine.classify_link(url)
        if link_type == "unknown":
            return {"error": "That doesn't look like a Spotify or YouTube link."}

        self.tracks = []
        broadcast("clear_tracks")
        self._set_busy(True)
        broadcast("status", "Scanning...")
        self.orchestrator.launch(self._scan_worker, url, link_type, name="playlist-scan")
        return {"ok": True}

    def _scan_worker(self, url: str, link_type: str):
        try:
            if link_type == "youtube":
                _name, tracks = self.providers.youtube.get_tracks(url)
                for t in tracks:
                    self._emit_track(t)
                self.tracks = tracks
                broadcast("scan_done")
                return

            cid = self.config_data.get("spotify_client_id", "").strip()
            secret = self.config_data.get("spotify_client_secret", "").strip()
            if not cid or not secret:
                broadcast("error", "Add your Spotify Client ID/Secret in Settings first.")
                broadcast("scan_done")
                return

            _name, tracks = self.providers.spotify.get_tracks(url, cid, secret)
            self.tracks = tracks
            for t in tracks:
                self._emit_track(t)

            threshold = self.config_data.get("confidence_threshold", 75)
            with engine.build_search_ydl() as shared_ydl:
                self._match_tracks_with_progress(tracks, shared_ydl, threshold, progress=True)

            broadcast("scan_done")
        except Exception as e:
            log.exception("Scan failed")
            broadcast("error", f"Scan failed: {e}")
            broadcast("scan_done")
        finally:
            was_stopped = self.stop_requested.is_set()
            self.stop_requested.clear()
            self._set_busy(False)
            if was_stopped:
                broadcast("status", f"Stopped. {len(self.tracks)} track(s) scanned so far.")
            else:
                msg = f"Scan complete: {len(self.tracks)} tracks found."
                broadcast("scan_complete", {"message": msg, "total": len(self.tracks)})
            save_session_cache(self.tracks, self.queue_urls)
            engine.save_match_cache(self.match_cache, MATCH_CACHE_PATH)

    def _match_tracks_with_progress(self, tracks: list[Track], shared_ydl, threshold: int,
                                     status_msg=None, progress: bool = False) -> None:
        """
        Shared "search -> match -> emit -> progress" loop (OPT-005), used by
        _scan_worker, _scan_all_queued_worker_impl's Spotify branch, and
        _retry_failed_worker_impl's needs-rematch loop. Checks
        stop_requested between tracks so a Stop click takes effect promptly.

        status_msg: optional callable(i, total, track) -> str, broadcast as
            a "status" event before each track is matched.
        progress: if True, broadcast "progress" as i/total*100 after each
            track (the per-playlist loop in scan_all_queued instead reports
            progress at the playlist level, so it passes False here).
        """
        total = len(tracks)
        for i, t in enumerate(tracks, start=1):
            if self.stop_requested.is_set():
                break
            if status_msg:
                broadcast("status", status_msg(i, total, t))
            t.status = "Searching"
            self._emit_track(t)
            self._match_one_track(t, shared_ydl, threshold)
            self._emit_track(t)
            if progress:
                broadcast("progress", i / total * 100)

    def _match_one_track(self, t: Track, shared_ydl, threshold: int):
        cache_key = engine.match_cache_key(t)
        cached = self.match_cache.get(cache_key)
        if cached and cached.get("youtube_url"):
            t.youtube_url = cached["youtube_url"]
            t.fallback_urls = list(cached.get("fallback_urls") or [])
            t.match_score = cached.get("match_score")
            t.error = None
            t.status = "Matched" if (t.match_score or 0) >= threshold else "LowConfidence"
            return

        try:
            candidates = self.providers.youtube.find_matches(t, ydl=shared_ydl)
        except Exception as e:
            t.status, t.error = "Failed", str(e)
            log.warning("Match search failed for %r: %s", t.query_string, e)
            return

        if not candidates:
            t.status, t.error = "Failed", "No YouTube results found"
            return

        (best_entry, best_score), *rest = candidates
        t.youtube_url = engine.resolve_entry_url(best_entry)
        t.fallback_urls = [u for (entry, _score) in rest if (u := engine.resolve_entry_url(entry))]
        t.match_score = best_score
        t.error = None
        t.status = "Matched" if best_score >= threshold else "LowConfidence"

        if t.youtube_url:
            self.match_cache[cache_key] = {
                "youtube_url": t.youtube_url,
                "fallback_urls": t.fallback_urls,
                "match_score": t.match_score,
            }

    # ------------------------------------------------------------------ #
    # Playlist queue
    # ------------------------------------------------------------------ #
    def add_to_queue(self, url: str) -> dict:
        url = (url or "").strip()
        if not url:
            return {"queue_urls": self.queue_urls}
        if engine.classify_link(url) == "unknown":
            return {"error": "That doesn't look like a Spotify or YouTube link.", "queue_urls": self.queue_urls}
        self.queue_urls.append(url)
        return {"queue_urls": self.queue_urls}

    def remove_from_queue(self, index: int) -> dict:
        if 0 <= index < len(self.queue_urls):
            del self.queue_urls[index]
        return {"queue_urls": self.queue_urls}

    def clear_queue(self) -> dict:
        self.queue_urls = []
        return {"queue_urls": self.queue_urls}

    def scan_all_queued(self) -> dict:
        if self.busy:
            return {"error": "Busy -- stop the current scan/download first."}
        if not self.queue_urls:
            return {"error": "Add at least one playlist link to the queue first."}
        self.tracks = []
        broadcast("clear_tracks")
        self._set_busy(True)
        broadcast("progress", 0)
        broadcast("status", f"Scanning {len(self.queue_urls)} queued playlists...")
        self.orchestrator.launch(self._scan_all_queued_worker, name="playlist-merge-scan")
        return {"ok": True}

    def _scan_all_queued_worker(self):
        try:
            self._scan_all_queued_worker_impl()
        except Exception as e:
            log.exception("Merged scan failed")
            broadcast("error", f"Scan failed unexpectedly: {e}")
            broadcast("scan_done")
        finally:
            self.stop_requested.clear()
            self._set_busy(False)
            save_session_cache(self.tracks, self.queue_urls)
            engine.save_match_cache(self.match_cache, MATCH_CACHE_PATH)

    def _scan_all_queued_worker_impl(self):
        all_raw_tracks: list[Track] = []
        urls = list(self.queue_urls)
        total = len(urls)

        cid = self.config_data.get("spotify_client_id", "").strip()
        secret = self.config_data.get("spotify_client_secret", "").strip()
        threshold = self.config_data.get("confidence_threshold", 75)

        with engine.build_search_ydl() as shared_ydl:
            for i, url in enumerate(urls, start=1):
                if self.stop_requested.is_set():
                    break
                link_type = engine.classify_link(url)
                broadcast("status", f"[{i}/{total}] Scanning: {url}")

                try:
                    if link_type == "youtube":
                        name, tracks = self.providers.youtube.get_tracks(url)
                    elif link_type == "spotify":
                        if not cid or not secret:
                            broadcast("error", f"Skipped (no Spotify keys in Settings): {url}")
                            continue
                        name, tracks = self.providers.spotify.get_tracks(url, cid, secret)
                        self._match_tracks_with_progress(
                            tracks, shared_ydl, threshold,
                            status_msg=lambda j, jtotal, t, name=name: f'[{i}/{total}] Matching "{name}": {j}/{jtotal}',
                        )
                    else:
                        continue
                except Exception as e:
                    log.exception("Failed scanning %s", url)
                    broadcast("error", f"Failed scanning {url}: {e}")
                    continue

                all_raw_tracks.extend(tracks)
                broadcast("progress", i / total * 100)

        if self.stop_requested.is_set():
            broadcast("status", "Stopped.")
            broadcast("scan_done")
            return

        for idx, t in enumerate(all_raw_tracks, start=1):
            t.index = idx

        deduped, dup_count = engine.dedupe_tracks(all_raw_tracks)
        for idx, t in enumerate(deduped, start=1):
            t.index = idx

        self.tracks = deduped
        for t in self.tracks:
            self._emit_track(t)

        msg = (
            f"Scan complete: {len(deduped)} tracks found. "
            f"({total} playlists merged, {dup_count} duplicate(s) removed)"
        )
        broadcast("scan_complete", {"message": msg, "total": len(deduped)})
        broadcast("scan_done")

    # ------------------------------------------------------------------ #
    # Manual override
    # ------------------------------------------------------------------ #
    def apply_override(self, index: int, url: str) -> dict:
        url = (url or "").strip()
        if not url:
            return {"error": "Paste a YouTube URL first."}
        for t in self.tracks:
            if t.index == index:
                t.youtube_url = url
                t.fallback_urls = []
                t.match_score = 100.0
                t.status = "Matched"
                t.error = None
                self._emit_track(t)
                return {"ok": True}
        return {"error": "Track not found."}

    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #
    def download_all(self, output_dir: str) -> dict:
        if self.busy:
            return {"error": "Busy -- stop the current scan/download first."}
        output_dir = (output_dir or "").strip()
        err = _validate_output_dir(output_dir)
        if err:
            return {"error": err}
        self.config_data["output_dir"] = output_dir
        save_config(self.config_data)

        matched = [t for t in self.tracks if t.youtube_url and t.status in ("Matched", "LowConfidence", "Done")]
        if not matched:
            return {"error": "No matched tracks yet. Scan a playlist first."}

        self._set_busy(True)
        broadcast("progress", 0)
        concurrency = min(self._concurrency(), len(matched))
        broadcast("slots_init", {"count": concurrency})
        broadcast("status", f"Starting download of {len(matched)} tracks ({concurrency} at a time)...")
        self.orchestrator.launch(self._download_worker, matched, output_dir, name="playlist-download")
        return {"ok": True}

    def _concurrency(self) -> int:
        try:
            n = int(self.config_data.get("concurrent_downloads", 3))
        except (TypeError, ValueError):
            n = 3
        return max(1, min(10, n))

    def _download_worker(self, matched: list[Track], output_dir: str):
        try:
            self._download_worker_impl(matched, output_dir)
        except Exception as e:
            log.exception("Download failed unexpectedly")
            broadcast("error", f"Download failed unexpectedly: {e}")
            broadcast("status", "Stopped due to an unexpected error.")
        finally:
            self.stop_requested.clear()
            self._set_busy(False)
            save_session_cache(self.tracks, self.queue_urls)

    def _download_worker_impl(self, matched: list[Track], output_dir: str):
        skip_existing = self.config_data.get("skip_existing", True)
        quality = self.config_data.get("quality_kbps", "192")
        add_numbers = self.config_data.get("add_track_numbers", True)
        embed_cover_art = self.config_data.get("embed_cover_art", True)
        pad_width = max(2, len(str(len(self.tracks))))
        concurrency = min(self._concurrency(), len(matched))
        total = len(matched)
        threshold = self.config_data.get("confidence_threshold", 75)

        counts_lock = threading.Lock()
        counts = {"done": 0, "fail": 0, "skip": 0}
        failed_tracks: list[tuple[str, str]] = []
        work_queue: queue.Queue = queue.Queue()
        for i, t in enumerate(matched, start=1):
            work_queue.put((i, t))

        # OPT-008: list the output directory once up front rather than
        # re-listing it (and re-scoring every existing file) on every single
        # track's skip-existing check.
        existing_files = engine.existing_track_files(output_dir) if skip_existing else []

        # OPT-011: the original ~115-line worker() did thread-pool
        # orchestration, per-track download logic, and progress-callback
        # construction all in one function. Split into a slot-scoped
        # progress-callback builder (_build_progress_cb), a single-track
        # processing step (_process_one_track), and a slim per-slot loop
        # (worker) that just pulls work and delegates.

        def _build_progress_cb(slot: int):
            """A slot is one worker thread's 'lane' in the UI -- it stays
            the same slot across every track that worker picks up, so the
            frontend can show a stable list of N in-progress rows instead of
            them jumping around as work gets handed out."""
            def _hook(d):
                if self.stop_requested.is_set():
                    raise _StopRequested()
                status = d.get("status")
                if status == "downloading":
                    downloaded = d.get("downloaded_bytes") or 0
                    file_total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    percent = (downloaded / file_total * 100) if file_total else 0
                    broadcast("slot_update", {"slot": slot, "percent": percent})
                elif status == "finished":
                    broadcast("slot_update", {"slot": slot, "percent": 100})
            return _hook

        def _process_one_track(slot: int, i: int, t: Track, progress_cb) -> bool:
            """Handle one queued track for one slot: skip-existing check,
            attempt each candidate URL in turn, update status/counts.
            Returns True if the caller's worker loop should keep pulling
            more work, False if it should stop (a stop was requested
            mid-download)."""
            if skip_existing and engine.is_already_downloaded(t, output_dir, existing_files=existing_files):
                t.status = "Skipped"
                self._emit_track(t)
                with counts_lock:
                    counts["skip"] += 1
                    counts["done"] += 1
                    broadcast("progress", counts["done"] / total * 100)
                return True

            t.status = "Downloading"
            self._emit_track(t)
            broadcast("slot_update", {
                "slot": slot, "percent": 0,
                "current_file": f"{i}/{total}: {t.query_string}",
            })

            prefix = f"{t.index:0{pad_width}d}" if add_numbers else None
            candidate_urls = [t.youtube_url] + [u for u in t.fallback_urls if u and u != t.youtube_url]
            last_error = None
            stopped_mid_download = False
            success = False
            for candidate_url in candidate_urls:
                if self.stop_requested.is_set():
                    stopped_mid_download = True
                    break
                try:
                    t.youtube_url = candidate_url
                    self.providers.youtube.download(
                        t, output_dir, quality_kbps=quality, progress_cb=progress_cb,
                        filename_prefix=prefix, embed_cover_art=embed_cover_art,
                    )
                    success = True
                    break
                except _StopRequested:
                    stopped_mid_download = True
                    break
                except Exception as e:
                    last_error = str(e)
                    log.warning("Download attempt failed for %r (%s): %s", t.query_string, candidate_url, e)
                    continue

            if stopped_mid_download:
                # Put the track back to a pickable-up-again state instead of
                # leaving it stuck on "Downloading" -- that status isn't in
                # the set download_all() re-selects from, so a stuck track
                # would silently vanish from every future download run.
                t.status = "Matched" if (t.match_score or 0) >= threshold else "LowConfidence"
                self._emit_track(t)
                return False

            if success:
                t.status = "Done"
                t.error = None
            else:
                t.status = "Failed"
                t.error = last_error or "All candidate URLs failed"
                with counts_lock:
                    counts["fail"] += 1
                    failed_tracks.append((t.query_string, t.error))

            broadcast("slot_update", {"slot": slot, "percent": 100})
            self._emit_track(t)
            with counts_lock:
                counts["done"] += 1
                broadcast("progress", counts["done"] / total * 100)
            return True

        def worker(slot: int):
            # Small random stagger so `concurrency` workers don't all hit
            # YouTube in the same instant on startup.
            time.sleep(random.uniform(0, 1.5))
            progress_cb = _build_progress_cb(slot)

            while not self.stop_requested.is_set():
                try:
                    i, t = work_queue.get_nowait()
                except queue.Empty:
                    return
                if not _process_one_track(slot, i, t, progress_cb):
                    return

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(worker, slot) for slot in range(concurrency)]
            concurrent.futures.wait(futures)

        for slot in range(concurrency):
            broadcast("slot_update", {"slot": slot, "current_file": "", "percent": 0})

        succeeded = counts["done"] - counts["fail"] - counts["skip"]
        summary = f"Done: {succeeded} succeeded, {counts['fail']} failed, {counts['skip']} skipped."
        broadcast("download_complete", {
            "message": summary, "succeeded": succeeded, "failed": counts["fail"], "skipped": counts["skip"],
        })
        if failed_tracks:
            broadcast("download_failures", [{"name": n, "reason": r} for n, r in failed_tracks])

    def stop(self) -> dict:
        self.orchestrator.stop()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # Retry failed
    # ------------------------------------------------------------------ #
    def retry_failed(self, output_dir: str) -> dict:
        if self.busy:
            return {"error": "Busy -- stop the current scan/download first."}
        failed = [t for t in self.tracks if t.status == "Failed"]
        if not failed:
            return {"error": "No failed tracks right now."}
        output_dir = (output_dir or "").strip()
        err = _validate_output_dir(output_dir)
        if err:
            return {"error": err}

        self._set_busy(True)
        broadcast("progress", 0)
        broadcast("slots_init", {"count": min(self._concurrency(), len(failed))})
        broadcast("status", f"Retrying {len(failed)} failed track(s)...")
        self.orchestrator.launch(self._retry_failed_worker, failed, output_dir, name="playlist-retry")
        return {"ok": True}

    def _retry_failed_worker(self, failed: list[Track], output_dir: str):
        try:
            self._retry_failed_worker_impl(failed, output_dir)
        except Exception as e:
            log.exception("Retry failed unexpectedly")
            broadcast("error", f"Retry failed unexpectedly: {e}")
            broadcast("status", "Retry stopped due to an unexpected error.")
            self._set_busy(False)
        finally:
            self.stop_requested.clear()

    def _retry_failed_worker_impl(self, failed: list[Track], output_dir: str):
        threshold = self.config_data.get("confidence_threshold", 75)
        needs_rematch = [t for t in failed if not t.youtube_url]

        if needs_rematch:
            with engine.build_search_ydl() as shared_ydl:
                self._match_tracks_with_progress(
                    needs_rematch, shared_ydl, threshold,
                    status_msg=lambda j, jtotal, t: f"Re-matching {j}/{jtotal}: {t.query_string}",
                )

        engine.save_match_cache(self.match_cache, MATCH_CACHE_PATH)

        retryable = [t for t in failed if t.youtube_url]
        if not retryable:
            broadcast("status", "No failed tracks could be matched to a YouTube video.")
            self._set_busy(False)
            save_session_cache(self.tracks, self.queue_urls)
            return

        self._set_busy(True)
        try:
            self._download_worker_impl(retryable, output_dir)
        finally:
            self._set_busy(False)
            save_session_cache(self.tracks, self.queue_urls)

    # ------------------------------------------------------------------ #
    # Clear actions
    # ------------------------------------------------------------------ #
    def clear_tracks(self) -> dict:
        if self.busy:
            return {"error": "Busy -- stop the current scan/download first."}
        self.tracks = []
        broadcast("clear_tracks")
        broadcast("status", "Cleared all scanned tracks.")
        save_session_cache(self.tracks, self.queue_urls)
        return {"ok": True}

    def clear_match_cache(self) -> dict:
        if self.busy:
            return {"error": "Busy -- stop the current scan/download first."}
        cleared = len(self.match_cache)
        self.match_cache = {}
        engine.save_match_cache(self.match_cache, MATCH_CACHE_PATH)
        broadcast("status", f"Match cache cleared ({cleared} cached match(es) removed).")
        return {"ok": True, "cleared": cleared}

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def export_log(self, path: str) -> dict:
        if not self.tracks:
            return {"error": "Scan a playlist first."}
        err = _validate_export_path(path)
        if err:
            return {"error": err}
        try:
            engine.export_log(self.tracks, path)
        except Exception:
            # SEC-007: don't leak raw exception text/paths to the client.
            log.exception("Failed to export log to %s", path)
            return {"error": "Could not write the export file. Check the log for details."}
        return {"ok": True, "path": path}


# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #
def create_app(frontend_dir: Optional[str] = None, port: int = PORT) -> Flask:
    frontend_dir = frontend_dir or os.path.join(resource_dir(), "frontend")
    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")
    core = Core()

    @app.before_request
    def _check_origin():
        # SEC-001: same-origin browser navigation (GET "/", GET static
        # assets) doesn't send an Origin header. Any fetch()/XHR call --
        # same-origin or cross-origin -- does. So: if an Origin header is
        # present, it must match this app's own origin exactly, or the
        # request is rejected. This blocks the "malicious page blind-POSTs
        # to localhost" drive-by pattern without breaking normal use of the
        # app in a browser.
        origin = request.headers.get("Origin")
        allowed_origin = f"http://127.0.0.1:{port}"
        if origin is not None and origin != allowed_origin:
            abort(403)

        # DNS-rebinding defense (SEC-001, ADR-0001): a rebinding page's
        # requests arrive with the attacker's hostname in the Host header
        # and no Origin header, so the origin check above alone cannot see
        # them. Every request must therefore also name this machine in
        # Host, or it is rejected before routing. The app's own browser
        # frontend always sends Host; a missing one is rejected too.
        if not _is_local_host(request.headers.get("Host") or ""):
            abort(403)

    @app.after_request
    def _security_headers(resp):
        # SEC-008: defense-in-depth; this is a single-page app served from
        # one origin with no third-party script/style sources.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.route("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.route("/api/events")
    def sse_events():
        def stream():
            q: queue.Queue = queue.Queue()
            with _listeners_lock:
                _listeners.append(q)
            try:
                while True:
                    msg = q.get()
                    yield f"data: {msg}\n\n"
            finally:
                with _listeners_lock:
                    if q in _listeners:
                        _listeners.remove(q)
        return Response(stream(), mimetype="text/event-stream")

    @app.route("/api/init")
    def api_init():
        return jsonify(core.get_init_state())

    @app.route("/api/settings", methods=["POST"])
    def api_settings():
        return jsonify(core.save_settings(request.get_json(force=True) or {}))

    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        data = request.get_json(force=True) or {}
        return jsonify(core.scan(data.get("url", "")))

    @app.route("/api/queue/add", methods=["POST"])
    def api_queue_add():
        data = request.get_json(force=True) or {}
        return jsonify(core.add_to_queue(data.get("url", "")))

    @app.route("/api/queue/remove", methods=["POST"])
    def api_queue_remove():
        data = request.get_json(force=True) or {}
        try:
            index = int(data.get("index", -1))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid index"})
        return jsonify(core.remove_from_queue(index))

    @app.route("/api/tracks/clear", methods=["POST"])
    def api_tracks_clear():
        return jsonify(core.clear_tracks())

    @app.route("/api/cache/clear", methods=["POST"])
    def api_cache_clear():
        return jsonify(core.clear_match_cache())

    @app.route("/api/queue/clear", methods=["POST"])
    def api_queue_clear():
        return jsonify(core.clear_queue())

    @app.route("/api/scan_all", methods=["POST"])
    def api_scan_all():
        return jsonify(core.scan_all_queued())

    @app.route("/api/download", methods=["POST"])
    def api_download():
        data = request.get_json(force=True) or {}
        return jsonify(core.download_all(data.get("output_dir", "")))

    @app.route("/api/retry_failed", methods=["POST"])
    def api_retry_failed():
        data = request.get_json(force=True) or {}
        return jsonify(core.retry_failed(data.get("output_dir", "")))

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        return jsonify(core.stop())

    @app.route("/api/override", methods=["POST"])
    def api_override():
        data = request.get_json(force=True) or {}
        try:
            index = int(data.get("index", -1))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid index"})
        return jsonify(core.apply_override(index, data.get("url", "")))

    @app.route("/api/browse_output_folder", methods=["POST"])
    def api_browse_output_folder():
        path = _pick_folder()
        if path:
            core.config_data["output_dir"] = path
            save_config(core.config_data)
        return jsonify({"path": path})

    @app.route("/api/browse_save_csv", methods=["POST"])
    def api_browse_save_csv():
        path = _pick_save_csv()
        return jsonify({"path": path})

    @app.route("/api/export_log", methods=["POST"])
    def api_export_log():
        data = request.get_json(force=True) or {}
        return jsonify(core.export_log(data.get("path", "")))

    return app
