# Domain Glossary

- **Playlist**: an ordered source of track metadata, supplied by Spotify or YouTube.
- **Track**: one playable item from a playlist, carrying title, artist, duration, match state, and download state.
- **Match**: the selected YouTube candidate for a track, including confidence and fallback candidates.
- **Download**: the process that turns a matched track into a tagged MP3 in the chosen output folder.
- **Run**: one scan, merged playlist scan, download, or retry operation and its emitted progress and status.
- **Credential**: the Spotify API credentials (client ID and secret) the user supplies in Settings; long-lived, stored in the user profile, required only for Spotify scans. _Avoid_: API key, login.
- **Access token**: a short-lived Spotify authorization the app derives from a Credential; it exists only while the app is running and is never written to disk. _Avoid_: session, key.
