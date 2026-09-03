# ctrl+shift+mp3

A local, self-hosted tool that scans a Spotify or YouTube playlist, matches
each track to YouTube, downloads audio as MP3 (with embedded cover art and
tags), and saves it straight to a folder — e.g. a USB drive. Runs a small
Flask server locally and opens a terminal-styled UI in your browser.

> **This is not a hosted service.** It runs entirely on your own machine,
> against your own Spotify API credentials, and saves files to a folder you
> choose.

## ⚠️ Disclaimer

This tool is intended for personal use with content you have the right to
download (e.g. music you own, playlists licensed for offline/personal use,
public-domain or Creative-Commons-licensed tracks). Downloading copyrighted
material you don't have rights to may violate YouTube's and Spotify's Terms
of Service and/or copyright law in your jurisdiction. **You are responsible
for how you use this tool.** The author(s) provide it as-is, for educational
and personal-archival purposes, with no warranty (see [LICENSE](LICENSE)).

## Why YouTube, not Spotify audio

This tool only ever reads **metadata** from Spotify (track names, artists,
album art) via the official Spotify Web API. It never touches Spotify's
actual audio streams, which are DRM-protected. Instead, it searches YouTube
for a matching version of each track and downloads that with `yt-dlp`.

This isn't just a convenience choice — it's a deliberate legal boundary.
Decrypting a DRM-protected stream (like Spotify's) risks running into
anti-circumvention law (17 U.S.C. §1201 in the US, and equivalents
elsewhere), which is a sharper legal issue than ordinary copyright
questions. By sourcing audio from YouTube instead, this project follows the
same approach as [spotDL](https://github.com/spotDL/spotify-downloader) — a
much larger, long-running project (25k+ GitHub stars) built on the same
Spotify-metadata + YouTube-audio + `yt-dlp` pattern.

## Setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```
   `ffmpeg` must also be on your system PATH (required for audio conversion
   and cover-art embedding).

2. **Get Spotify API credentials** (only needed if you'll scan Spotify
   playlists — YouTube playlist links work without this):
   - Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
     and log in with any Spotify account.
   - Click **Create app**. Fill in a name/description; for "Redirect URI"
     you can use `http://127.0.0.1:8743/` (not actually used for this app's
     flow, but the field is required).
   - Once created, copy the **Client ID** and **Client Secret**.
   - Paste them into the app's Settings dialog (`[ SETTINGS ]` button) after
     first launch — they're saved locally to
     `~/.playlist_downloader_config.json`, never committed to this repo.

3. **Run**
   ```
   python main.py
   ```
   This starts a server at `http://127.0.0.1:8743/` and opens it in your
   default browser automatically after ~1 second. Leave the terminal window
   open while you use the app — closing it (or Ctrl+C) stops the server.
   Closing the browser tab does **not** stop it.

## Local data & privacy

Everything this app writes lives in your home directory, not in this repo:

- `~/.playlist_downloader_config.json` — your settings, including Spotify
  API credentials. **This file is plaintext.** It's a Client Credentials
  (app-only) secret, not a user password, but it's still readable by any
  other local process running as your user account. Consider setting
  restrictive permissions on it, e.g. on macOS/Linux:
  `chmod 600 ~/.playlist_downloader_config.json`. The app itself never
  re-sends the saved secret back to the browser after the first save — the
  Settings dialog shows a masked placeholder instead of the real value.
- `~/.playlist_downloader_session.json` — last session's queue/track state
- `~/.playlist_downloader_match_cache.json` — cached YouTube match results,
  so re-scanning the same tracks is faster
- `~/.playlist_downloader.log` — run log

None of these are read from or written to the project folder, and all are
covered by `.gitignore` as an extra safety net.

## Architecture notes

- `server.py` — Flask app: routes, SSE event stream, config/cache
  persistence, scan/download worker threads.
- `engine.py` — core logic: Spotify/YouTube playlist parsing, fuzzy track
  matching (via `rapidfuzz`), YouTube search + download (via `yt-dlp`), MP3
  tagging/cover-art embedding.
- `frontend/` — `index.html`, `app.js`, `style.css`: a single-page terminal-
  styled UI that talks to the Flask backend over `fetch()` and listens for
  live updates via `EventSource` (`/api/events`).
- `main.py` — entry point; starts the Flask server and opens your default
  browser.
- `tests.py` — unit tests covering matching logic, config validation, and
  worker orchestration (mocked network calls — no live Spotify/YouTube
  access needed to run them).

Progress and status updates (scan progress, download progress, toasts,
per-slot concurrent-download rows) are pushed from the backend over
Server-Sent Events rather than polled, so the UI updates live as
`server.py`'s worker threads run.

## Security notes

This app runs a local, unauthenticated Flask server on `127.0.0.1`. Since
any browser tab (including a malicious webpage) can attempt to reach a
localhost server, a few protections are built in:

- **Origin check** — every `/api/*` request that carries an `Origin` header
  must match this app's own origin, or it's rejected with 403. This blocks
  the classic "malicious page blind-POSTs to your local server" pattern.
- **Output/export path validation** — `output_dir` and the CSV export path
  must be absolute, existing, traversal-free paths (normally supplied by
  the native folder/save dialogs), not arbitrary attacker-chosen strings.
- **CSV export is formula-injection-safe** — any exported cell that would
  otherwise start with `=`, `+`, `-`, or `@` is prefixed with `'` so
  Excel/Sheets won't interpret it as a formula.
- **Busy guards** — scan/scan-all/download/retry all refuse to start a
  second overlapping run.

None of this replaces running the app only on a machine/network you trust —
it's still a local dev-style server with no login.

## Known gap / please confirm on your machine

Development happened in a sandbox with no network or display access, so the
Python was compile-checked and logic unit-tested, but not click-tested
end-to-end in a real browser. Please verify:

1. `python main.py` opens your browser to the app.
2. Scanning a playlist populates the table live via the SSE stream.
3. The folder/save dialogs (Browse output folder, Export Log) pop up
   correctly on top of the browser window.
4. Concurrent downloads (Settings → `CONCURRENT_DOWNLOADS`) behave well on
   your actual output drive — especially if it's a USB stick, where higher
   concurrency isn't always faster.

If the SSE connection doesn't seem to receive updates, check the terminal
running `python main.py` for errors. Note that some strict corporate
proxies/antivirus software intercept SSE; a normal home network shouldn't
have any trouble with it.

## License

[MIT](LICENSE) — see the LICENSE file. Third-party dependencies (Flask,
yt-dlp, rapidfuzz, spotipy) keep their own licenses; none of them are
copyleft, so this project is free to license permissively.
