# ctrl+shift+mp3

Turn playlist metadata into tagged MP3 files, locally.

`ctrl+shift+mp3` is a self-hosted desktop utility that reads a Spotify or
YouTube playlist, finds matching videos on YouTube, and saves tagged MP3 files
with embedded cover art to a folder you choose. It runs a small Flask server
on your machine and opens a terminal-styled interface in your browser.

> This is not a hosted service. Your playlists, credentials, cache, and output
> stay on your machine.

## At a glance

|                | Details                                                      |
| -------------- | ------------------------------------------------------------ |
| Inputs         | Spotify or YouTube playlist links                            |
| Audio source   | Matching YouTube videos via `yt-dlp`                         |
| Output         | Tagged MP3 files with cover art                              |
| Interface      | Local browser UI with live progress updates                  |
| Required tools | None for the [Windows release](#download-for-windows); Python + `ffmpeg` from source |
| Network access | Spotify API and YouTube are contacted during scans/downloads |

## Use responsibly

Only download audio you have the right to use, such as music you own,
properly licensed playlists, public-domain works, or Creative Commons content.
Downloading copyrighted material without permission may violate platform terms
or local law. You are responsible for how you use this project. See
[LICENSE](LICENSE) for the warranty disclaimer.

## Download for Windows

The portable release is one ZIP that contains everything the app needs —
Python, dependencies, FFmpeg, frontend assets, and native dialog resources.
No installation is required.

1. On the [Releases page](https://github.com/pisheeew/ctrl-shift-mp3/releases),
   download `ctrl-shift-mp3-windows-v<version>.zip` and the matching
   `ctrl-shift-mp3-windows-v<version>.zip.sha256` checksum file. (A copy of
   `NOTICES.txt` is attached to the release and also ships inside the ZIP.)
2. Verify the download (see [Verify the checksum](#verify-the-checksum)
   below).
3. Extract the ZIP to any folder you like.
4. Double-click `ctrl-shift-mp3.exe` (or run it from a terminal).

A console window opens and stays open while the app runs. It prints the
local URL and tries to open your default browser at
<http://127.0.0.1:8743/>. If that port is busy, the app picks another free
localhost port and prints the URL it is actually using. If the browser does
not open by itself, type the printed URL into your browser manually. Stop
the app with `Ctrl+C` in the console window; closing the browser tab does
not stop it.

For Flask diagnostic output, run `ctrl-shift-mp3.exe --debug` from a
terminal.

### Expected Windows warning on first launch

The release is not code-signed, so Windows SmartScreen may show
"Windows protected your PC". Click **More info** → **Run anyway**. Only run
executables downloaded from this project's GitHub Releases page, and verify
the checksum first.

### Verify the checksum

The `.sha256` file published beside each release contains the expected
SHA-256 digest of the ZIP. In PowerShell, from the folder holding the
download:

```powershell
Get-FileHash .\ctrl-shift-mp3-windows-v1.0.0.zip -Algorithm SHA256
```

Compare the printed digest with the one recorded in the `.sha256` file (or
in the release notes). They must match exactly, ignoring case. If they
differ, the download was corrupted or altered — delete it and download
again.

### What the release includes

- **Bundled FFmpeg.** `ffmpeg.exe` and `ffprobe.exe` ship inside the bundle
  and are used automatically for audio conversion and cover-art embedding.
  If the bundled binaries are unavailable, the app falls back to `ffmpeg`
  on `PATH`.
- **Your data stays in place.** The packaged app reads and writes the same
  home-directory files as a source checkout — settings, Spotify
  credentials, session state, match cache, and log. See
  [Local data and privacy](#local-data-and-privacy).
- **Local-only network posture.** The server binds to `127.0.0.1` only.
  Like the source version, it is unauthenticated: run it only on a machine
  and network you trust.
- **Third-party notices.** `NOTICES.txt` lists the bundled Python packages
  and the bundled FFmpeg build with their licenses, including the retained
  GPL text that covers FFmpeg redistribution.

## Quick start from source: YouTube playlist

YouTube playlist links do not require Spotify credentials.

### 1. Install

Create a virtual environment if desired:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Install `ffmpeg` separately and make sure both `ffmpeg` and `ffprobe` are
available on your system `PATH`. They are needed for audio conversion and
cover-art embedding. A packaged release checks its bundled executables
first, then falls back to `PATH`.

### 2. Start the app

```bash
python main.py
```

The app tries <http://127.0.0.1:8743/> first and selects another available
localhost port if needed. It prints the actual URL and attempts to open your
default browser. Keep the process running while you use the app. Close the
server with `Ctrl+C`; closing the browser tab does not stop it. Use
`python main.py --debug` for Flask diagnostic output.

### 3. Scan and download

1. Paste a YouTube playlist link into the app.
2. Scan the playlist and review the matches.
3. Choose an output folder.
4. Start the download.

Scan and download progress appears live in the browser. Failed tracks can be
retried, and concurrent download count can be adjusted in Settings.

## Optional: Spotify playlists

Spotify support reads playlist metadata only: titles, artists, durations, and
album art. It does not access or download Spotify audio. Audio is matched and
downloaded from YouTube instead.

To enable Spotify scans:

1. Open the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and create an app.
2. Copy its Client ID and Client Secret.
3. Start `ctrl+shift+mp3`, open Settings, and enter the credentials.

The redirect URI field can use `http://127.0.0.1:8743/`; this application uses
the Client Credentials flow and does not use an interactive Spotify login.

## Local data and privacy

The app stores its state in your home directory — never in the repository
or the extracted release folder. These locations are the same whether you
run from source or from a packaged release:

- `~/.playlist_downloader_config.json` stores settings and Spotify credentials.
  The file is plaintext and should be protected from other local processes.
  The app masks the saved secret in the browser and does not send it back on
  page load.
- `~/.playlist_downloader_session.json` stores the last queue and track state.
- `~/.playlist_downloader_match_cache.json` caches YouTube matches.
- `~/.playlist_downloader.log` stores the run log.

These files are covered by `.gitignore`. On macOS/Linux, you can restrict the
credentials file with `chmod 600 ~/.playlist_downloader_config.json`.

## Troubleshooting

### Windows shows "Windows protected your PC"

The release executable is not code-signed. Click **More info** →
**Run anyway**, after verifying the download checksum. See
[Expected Windows warning on first launch](#expected-windows-warning-on-first-launch).

### `ffmpeg` is not found

Install both `ffmpeg` and `ffprobe`, add their executable directory to `PATH`,
then restart the terminal before running the app again. The release build
downloads both binaries from the pinned
[BtbN FFmpeg 9.0.1 win64 GPL archive](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-09-05-13-10).
Packaged releases check their bundled executables first, then fall back to
`PATH`.

### The browser does not open

Check the console window (or terminal) for the URL the app printed — if
port 8743 was busy, the app selected another port and the URL differs from
the default. Open that printed URL manually. If the page still does not
load, check the console for startup errors.

### A scan returns no tracks

Confirm the link is a public Spotify or YouTube playlist and that the machine
can reach the relevant service. Spotify scans also require valid credentials.

### Live progress does not appear

Check the terminal for errors from the `/api/events` stream. A browser
extension, proxy, or antivirus tool that intercepts local Server-Sent Events
can prevent updates from arriving.

### Folder or save dialogs do not appear

Run the app from a normal desktop session with access to the native display.
The Browse and Export Log actions use native dialogs provided by the local
process.

### A download fails or matches the wrong video

Review the selected match and use a fallback or URL override when available.
You can retry failed tracks after correcting the match.

## Development

Run the automated unit tests with:

```bash
python -m unittest tests.py
```

The suite covers link classification, title and artist normalization,
duplicate detection, filename handling, persistence round trips, and run
orchestration. Network calls are mocked, so Spotify or YouTube credentials are
not needed for the test suite.

The main modules are:

- `engine.py` - playlist parsing, fuzzy matching, YouTube downloads, and MP3
  tagging.
- `server.py` - Flask routes, persistence, worker orchestration, and the
  Server-Sent Events stream.
- `frontend/` - the browser UI.
- `main.py` - the local server entry point and browser launcher.
- `tests.py` - automated unit tests.

The local server is unauthenticated and should only be run on a machine and
network you trust. It binds to `127.0.0.1` and includes origin checks and a
Host-header allowlist (so DNS-rebinding pages cannot reach the API), output
path validation, formula-safe CSV export, and guards against overlapping runs.

### Release engineering

To build the portable Windows bundle locally, or to cut and review a tagged
release, see the maintainer guide:
[docs/maintainers/releasing.md](docs/maintainers/releasing.md).

## License

This project is available under the [MIT License](LICENSE). Flask, `yt-dlp`,
RapidFuzz, and Spotipy remain subject to their own licenses. The Windows
release ships `NOTICES.txt` with the full third-party inventory, including
the GPL terms that cover redistribution of the bundled FFmpeg build.
