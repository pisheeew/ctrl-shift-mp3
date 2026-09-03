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
| Required tools | Python, project dependencies, and `ffmpeg`                   |
| Network access | Spotify API and YouTube are contacted during scans/downloads |

## Use responsibly

Only download audio you have the right to use, such as music you own,
properly licensed playlists, public-domain works, or Creative Commons content.
Downloading copyrighted material without permission may violate platform terms
or local law. You are responsible for how you use this project. See
[LICENSE](LICENSE) for the warranty disclaimer.

## Quick start: YouTube playlist

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

Install `ffmpeg` separately and make sure it is available on your system
`PATH`. It is needed for audio conversion and cover-art embedding.

### 2. Start the app

```bash
python main.py
```

The app starts at <http://127.0.0.1:8743/> and attempts to open your default
browser. Keep the terminal running while you use the app. Close the server
with `Ctrl+C`; closing the browser tab does not stop it.

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

The app stores its state in your home directory, not in this repository:

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

### `ffmpeg` is not found

Install `ffmpeg`, add its executable directory to `PATH`, then restart the
terminal before running the app again.

### The browser does not open

Open <http://127.0.0.1:8743/> manually. If the page still does not load, check
the terminal for startup errors and confirm that the port is available.

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
network you trust. It binds to `127.0.0.1` and includes origin checks, output
path validation, formula-safe CSV export, and guards against overlapping runs.

## License

This project is available under the [MIT License](LICENSE). Flask, `yt-dlp`,
RapidFuzz, and Spotipy remain subject to their own licenses.
