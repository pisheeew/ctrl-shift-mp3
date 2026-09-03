"""
engine.py
Core logic: parsing playlist links, fetching track lists, matching tracks to
YouTube results, and downloading audio. No GUI code lives here so it can be
tested / reused independently of tkinter.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from rapidfuzz import fuzz
import yt_dlp

# spotipy is a hard dependency in requirements.txt today, so this ImportError
# branch won't trigger in a correctly set-up install. It's kept intentionally
# so a "YouTube-only, no Spotify" install remains possible in the future
# (drop spotipy from requirements.txt, and YouTube-playlist scanning still
# works unaffected -- only SpotifyLister() raises, exactly as it already
# does today when no client id/secret are configured).
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIPY_AVAILABLE = True
except ImportError:  # spotipy not installed
    SPOTIPY_AVAILABLE = False

log = logging.getLogger("playlist_downloader.engine")

# Minimum gap between YouTube searches, to stay well clear of throttling on
# big playlists. Applied inside find_best_youtube_match after each search.
RATE_LIMIT_DELAY_SEC = 0.4

# Keep generated filenames comfortably under Windows' ~260 char path limit
# even once a drive letter, folder path, and " (2)" collision suffix are added.
MAX_BASENAME_LEN = 150


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Track:
    index: int
    title: str
    artist: str = ""
    album: str = ""
    duration_sec: Optional[int] = None          # expected duration, if known
    source: str = "youtube"                      # "spotify" or "youtube"
    youtube_url: Optional[str] = None             # resolved after matching
    fallback_urls: list = field(default_factory=list)  # runner-up candidates
    match_score: Optional[float] = None           # 0-100 confidence
    status: str = "Pending"                       # Pending/Searching/Matched/
                                                    # LowConfidence/Downloading/
                                                    # Done/Failed
    output_path: Optional[str] = None
    error: Optional[str] = None
    source_playlist: Optional[str] = None          # name of playlist it came from
    also_in: list = field(default_factory=list)    # other playlist names with a duplicate

    @property
    def query_string(self) -> str:
        title = self.title or ""
        if self.artist:
            return f"{self.artist} - {title}"
        return title


def track_to_dict(t: Track) -> dict:
    return dataclasses.asdict(t)


def track_from_dict(d: dict) -> Track:
    known = {f.name for f in dataclasses.fields(Track)}
    return Track(**{k: v for k, v in d.items() if k in known})


def tracks_to_json(tracks: list[Track]) -> str:
    return json.dumps([track_to_dict(t) for t in tracks])


def tracks_from_json(data: str) -> list[Track]:
    raw = json.loads(data)
    return [track_from_dict(d) for d in raw]


# --------------------------------------------------------------------------- #
# Link parsing
# --------------------------------------------------------------------------- #

SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"open\.spotify\.com/album/([A-Za-z0-9]+)")
YOUTUBE_LIST_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")


def classify_link(url: str) -> str:
    """Return 'spotify' or 'youtube' or 'unknown'."""
    if "spotify.com" in url:
        return "spotify"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "unknown"


def resolve_entry_url(entry: dict) -> Optional[str]:
    """Turn a yt-dlp search result entry into a stable watch URL."""
    if not entry:
        return None
    url = entry.get("url") or entry.get("webpage_url")
    if url:
        return url
    vid = entry.get("id")
    return f"https://www.youtube.com/watch?v={vid}" if vid else None


# --------------------------------------------------------------------------- #
# Spotify track listing
# --------------------------------------------------------------------------- #

class SpotifyLister:
    def __init__(self, client_id: str, client_secret: str):
        if not SPOTIPY_AVAILABLE:
            raise RuntimeError("spotipy is not installed")
        auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        self.client = spotipy.Spotify(client_credentials_manager=auth)

    def get_tracks(self, url: str) -> tuple[str, list[Track]]:
        tracks: list[Track] = []
        playlist_name = "Spotify Playlist"

        if "/playlist/" in url:
            try:
                meta = self.client.playlist(url, fields="name")
                playlist_name = meta.get("name") or playlist_name
            except Exception:
                log.warning("Could not fetch Spotify playlist name for %s", url, exc_info=True)
            results = self.client.playlist_items(url, additional_types=["track"])
        elif "/album/" in url:
            try:
                meta = self.client.album(url)
                playlist_name = meta.get("name") or "Spotify Album"
            except Exception:
                log.warning("Could not fetch Spotify album name for %s", url, exc_info=True)
                playlist_name = "Spotify Album"
            results = self.client.album_tracks(url)
        else:
            raise ValueError("Unsupported Spotify link type (need /playlist/ or /album/)")

        idx = 1
        while results:
            items = results["items"]
            for item in items:
                t = item.get("track", item)  # album_tracks items ARE the track
                if t is None:
                    continue
                name = t.get("name", "")
                artists = ", ".join(a["name"] for a in t.get("artists", []))
                album = t.get("album", {}).get("name", "") if "album" in t else ""
                dur_ms = t.get("duration_ms")
                dur_sec = int(dur_ms / 1000) if dur_ms else None
                tracks.append(Track(
                    index=idx, title=name, artist=artists, album=album,
                    duration_sec=dur_sec, source="spotify",
                    source_playlist=playlist_name,
                ))
                idx += 1
            if results.get("next"):
                results = self.client.next(results)
            else:
                break
        log.info("Listed %d Spotify tracks from %r", len(tracks), playlist_name)
        return playlist_name, tracks


# --------------------------------------------------------------------------- #
# YouTube playlist listing (no download)
# --------------------------------------------------------------------------- #

def get_youtube_playlist_tracks(url: str) -> tuple[str, list[Track]]:
    ydl_opts = {
        "extract_flat": True,   # don't resolve each video fully, just list them
        "quiet": True,
        "no_warnings": True,
    }
    tracks: list[Track] = []
    playlist_name = "YouTube Playlist"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        playlist_name = (info.get("title") if info else None) or playlist_name
        entries = info.get("entries", []) if info else []
        for idx, entry in enumerate(entries, start=1):
            if entry is None:
                continue
            title = entry.get("title")
            video_id = entry.get("id") or entry.get("url")
            if not title or not video_id:
                # Private/deleted/unavailable video in the playlist - can't
                # be searched or downloaded, so skip it rather than crash.
                continue
            duration = entry.get("duration")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            tracks.append(Track(
                index=idx, title=title, duration_sec=duration,
                source="youtube", youtube_url=video_url,
                match_score=100.0,
                status="Matched",
                source_playlist=playlist_name,
            ))
    log.info("Listed %d YouTube tracks from %r", len(tracks), playlist_name)
    return playlist_name, tracks


# --------------------------------------------------------------------------- #
# Matching Spotify tracks -> best YouTube candidates
# --------------------------------------------------------------------------- #

PREFERRED_CHANNEL_HINTS = ("- topic", "official", "vevo")


class SpotifyAdapter:
    """Provider adapter for Spotify playlist metadata."""

    def get_tracks(self, url: str, client_id: str, client_secret: str) -> tuple[str, list[Track]]:
        return SpotifyLister(client_id, client_secret).get_tracks(url)


class YouTubeAdapter:
    """Provider adapter for YouTube listing, matching, and downloads."""

    def get_tracks(self, url: str) -> tuple[str, list[Track]]:
        return get_youtube_playlist_tracks(url)

    def find_matches(self, track: Track, ydl: Optional[yt_dlp.YoutubeDL] = None):
        return find_best_youtube_match(track, ydl=ydl)

    def download(self, track: Track, output_dir: str, **kwargs) -> None:
        download_track(track, output_dir, **kwargs)


class MediaProviders:
    """Concrete provider set used by the run orchestration module."""

    def __init__(self, spotify=None, youtube=None):
        self.spotify = spotify or SpotifyAdapter()
        self.youtube = youtube or YouTubeAdapter()


def build_search_ydl() -> yt_dlp.YoutubeDL:
    """
    A single reusable YoutubeDL instance for searches within one scan batch.
    Avoids paying instantiation overhead per-track; the search query itself
    (via the "ytsearchN:" prefix) carries the result-count limit, so this
    instance's options don't need to change between calls.
    """
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"})


def _score_candidate(track: Track, candidate: dict) -> float:
    """Combine title similarity + duration closeness + channel trust into 0-100."""
    cand_title = (candidate.get("title") or "").lower()
    query = track.query_string.lower()

    title_score = fuzz.token_set_ratio(query, cand_title)  # 0-100

    duration_score = 100.0
    cand_dur = candidate.get("duration")
    if track.duration_sec and cand_dur:
        diff = abs(track.duration_sec - cand_dur)
        if diff <= 3:
            duration_score = 100.0
        elif diff <= 6:
            duration_score = 85.0
        elif diff <= 15:
            duration_score = 60.0
        else:
            duration_score = max(0.0, 100.0 - diff * 2)

    channel = (candidate.get("channel") or candidate.get("uploader") or "").lower()
    channel_bonus = 8.0 if any(h in channel for h in PREFERRED_CHANNEL_HINTS) else 0.0

    # Weighted blend; duration matters a lot for avoiding remixes/extended cuts
    score = (title_score * 0.55) + (duration_score * 0.35) + channel_bonus
    return min(100.0, score)


def find_best_youtube_match(
    track: Track, search_limit: int = 5, top_n: int = 3,
    ydl: Optional[yt_dlp.YoutubeDL] = None,
) -> list[tuple[dict, float]]:
    """
    Search YouTube for a track and return up to top_n (candidate_info, score)
    tuples, best first. Pass a shared `ydl` (from build_search_ydl()) when
    matching many tracks in one batch to avoid re-instantiating per call.
    """
    query = f"ytsearch{search_limit}:{track.query_string}"

    if ydl is not None:
        info = ydl.extract_info(query, download=False)
    else:
        with build_search_ydl() as tmp_ydl:
            info = tmp_ydl.extract_info(query, download=False)

    entries = info.get("entries", []) if info else []
    time.sleep(RATE_LIMIT_DELAY_SEC)  # be polite to avoid throttling on big playlists

    if not entries:
        return []

    scored = [(e, _score_candidate(track, e)) for e in entries if e]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_n]


# --------------------------------------------------------------------------- #
# YouTube-match cache
# A persistent title/artist -> matched-YouTube-URL cache so re-scanning a
# playlist (or scanning a different playlist that shares tracks) doesn't
# re-run a fresh YouTube search + score for songs we've already matched.
# Keyed on normalize_title/normalize_artist (defined below) so formatting
# noise like "(Remastered 2011)" or "feat. X" doesn't cause cache misses.
# --------------------------------------------------------------------------- #

def match_cache_key(track: Track) -> str:
    """Stable cache key for a track, tolerant of title/artist formatting noise."""
    return f"{normalize_artist(track.artist)}::{normalize_title(track.title)}"


def load_match_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.exception("Failed to load match cache from %s", path)
        return {}


def save_match_cache(cache: dict, path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        log.exception("Failed to save match cache to %s", path)


# --------------------------------------------------------------------------- #
# Duplicate detection across merged playlists
# --------------------------------------------------------------------------- #

# Parenthetical/bracketed noise that doesn't change which song it is
_NOISE_PATTERNS = [
    r"\(feat[^)]*\)", r"\[feat[^\]]*\]",
    r"\(with[^)]*\)", r"\[with[^\]]*\]",
    r"\(.*?remaster(ed)?[^)]*\)", r"\[.*?remaster(ed)?[^\]]*\]",
    r"\(.*?live[^)]*\)", r"\[.*?live[^\]]*\]",
    r"\(.*?official[^)]*\)", r"\[.*?official[^\]]*\]",
    r"\(.*?video[^)]*\)", r"\[.*?video[^\]]*\]",
    r"\(.*?audio[^)]*\)", r"\[.*?audio[^\]]*\]",
    r"\(.*?lyrics?[^)]*\)", r"\[.*?lyrics?[^\]]*\]",
    r"\(.*?\bhd\b[^)]*\)", r"\[.*?\bhd\b[^\]]*\]",
    r"\(.*?\bhq\b[^)]*\)", r"\[.*?\bhq\b[^\]]*\]",
    r"\(.*?deluxe[^)]*\)", r"\[.*?deluxe[^\]]*\]",
    r"\(.*?mono\)", r"\(.*?stereo\)",
    r"\(.*?explicit[^)]*\)", r"\[.*?explicit[^\]]*\]",
    r"\(.*?clean[^)]*\)", r"\[.*?clean[^\]]*\]",
    r"\(.*?bonus[^)]*\)", r"\[.*?bonus[^\]]*\]",
    r"\(.*?radio edit[^)]*\)", r"\[.*?radio edit[^\]]*\]",
    r"\(.*?anniversary[^)]*\)", r"\[.*?anniversary[^\]]*\]",
    r"\(.*?\d{4}[^)]*\)",       # e.g. "(2011 Remaster)" catch-all for year tags
]
_NOISE_RE = [re.compile(p, re.IGNORECASE) for p in _NOISE_PATTERNS]
# Spotify commonly suffixes with " - Remastered 2011", " - Live", " - Radio Edit"
# etc. instead of parentheses. Strip everything from the dash onward if it
# looks like a version/edition descriptor rather than part of the song title.
_DASH_SUFFIX_RE = re.compile(
    r"\s-\s[^-]*\b(remaster(ed)?|live|radio edit|mono|stereo|deluxe|bonus track|"
    r"explicit|clean|anniversary|single version|album version|extended( mix)?|"
    r"mix|edit|version|\d{4})\b.*$",
    re.IGNORECASE,
)
_FEAT_WORD_RE = re.compile(r"\b(feat\.?|ft\.?|featuring)\b.*", re.IGNORECASE)
_LEADING_TRACK_NUM_RE = re.compile(r"^\d+\s*-\s*")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_ARTIST_SPLIT_RE = re.compile(r",|&|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b| x |/", re.IGNORECASE)


def normalize_title(title: str) -> str:
    """Strip remaster/live/official/feat/year noise so title variants collapse."""
    if not title:
        return ""
    t = title
    t = _DASH_SUFFIX_RE.sub("", t)
    for pat in _NOISE_RE:
        t = pat.sub("", t)
    t = _FEAT_WORD_RE.sub("", t)
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def normalize_artist(artist: str) -> str:
    """Keep only the primary artist (drop featured/collab artists) for comparison."""
    if not artist:
        return ""
    a = artist.lower()
    a = _ARTIST_SPLIT_RE.split(a)[0]
    a = _PUNCT_RE.sub(" ", a)
    a = _WS_RE.sub(" ", a).strip()
    return a


def _is_same_song(a: Track, b: Track, title_threshold: int, artist_threshold: int,
                   duration_tolerance_sec: int) -> bool:
    title_sim = fuzz.token_sort_ratio(normalize_title(a.title), normalize_title(b.title))
    if title_sim < title_threshold:
        return False

    a_artist, b_artist = normalize_artist(a.artist), normalize_artist(b.artist)
    if a_artist and b_artist:
        artist_sim = fuzz.token_sort_ratio(a_artist, b_artist)
        if artist_sim < artist_threshold:
            return False

    if a.duration_sec and b.duration_sec:
        if abs(a.duration_sec - b.duration_sec) > duration_tolerance_sec:
            return False

    return True


def _choose_canonical(group: list[Track]) -> Track:
    """Pick the best representative from a group of duplicate tracks."""
    def rank(t: Track):
        # Prefer a confidently-matched track, then Spotify metadata (cleaner
        # title/artist/duration than raw YouTube titles), then earliest seen.
        return (t.match_score or 0, 1 if t.source == "spotify" else 0)

    canonical = max(group, key=rank)
    other_sources = sorted({
        t.source_playlist for t in group
        if t.source_playlist and t.source_playlist != canonical.source_playlist
    })
    canonical.also_in = other_sources
    return canonical


def dedupe_tracks(tracks: list[Track], title_threshold: int = 87,
                   artist_threshold: int = 80,
                   duration_tolerance_sec: int = 6) -> tuple[list[Track], int]:
    """
    Merge tracks that are the same song under different title formatting
    (e.g. "Song (Remastered 2011)" vs "Song - Live" vs "Song feat. X").
    Returns (deduped_tracks, number_of_duplicates_removed).
    """
    groups: list[list[Track]] = []

    for t in tracks:
        placed = False
        for group in groups:
            if _is_same_song(t, group[0], title_threshold, artist_threshold, duration_tolerance_sec):
                group.append(t)
                placed = True
                break
        if not placed:
            groups.append([t])

    deduped = [_choose_canonical(g) for g in groups]
    duplicates_removed = len(tracks) - len(deduped)
    return deduped, duplicates_removed


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    if len(cleaned) > MAX_BASENAME_LEN:
        cleaned = cleaned[:MAX_BASENAME_LEN].rstrip()
    return cleaned


def build_output_basename(track: Track, output_dir: str, filename_prefix: Optional[str] = None) -> str:
    """
    Compute a collision-safe base filename (no extension) for this track in
    output_dir: sanitized, length-capped, optionally track-number-prefixed
    (for correct playback order on car head units that sort alphabetically),
    and disambiguated with " (2)", " (3)" etc. if something else already
    claimed that exact name.
    """
    base = sanitize_filename(track.query_string) or f"track_{track.index}"
    if filename_prefix:
        base = sanitize_filename(f"{filename_prefix} - {base}")

    candidate = base
    n = 2
    while os.path.exists(os.path.join(output_dir, f"{candidate}.mp3")):
        candidate = f"{base} ({n})"
        n += 1
    return candidate


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #

def download_track(track: Track, output_dir: str, quality_kbps: str = "192",
                    progress_cb: Optional[Callable[[dict], None]] = None,
                    filename_prefix: Optional[str] = None,
                    embed_cover_art: bool = True) -> None:
    """Download `track.youtube_url` as an MP3 with embedded tags (and,
    optionally, cover art) into output_dir."""
    if not track.youtube_url:
        raise ValueError("Track has no resolved YouTube URL")

    os.makedirs(output_dir, exist_ok=True)
    base_name = build_output_basename(track, output_dir, filename_prefix)
    out_template = os.path.join(output_dir, f"{base_name}.%(ext)s")

    def _hook(d):
        if progress_cb:
            progress_cb(d)

    # NOTE: EmbedThumbnail must run LAST. yt-dlp postprocessors run in list
    # order and each one that touches the mp3 (FFmpegMetadata included)
    # re-muxes the file; if it runs *after* EmbedThumbnail it silently
    # drops the just-embedded cover art. Putting FFmpegMetadata first and
    # EmbedThumbnail last means nothing downstream can strip it.
    postprocessors = [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality_kbps},
        {"key": "FFmpegMetadata"},
    ]
    if embed_cover_art:
        postprocessors.append({"key": "EmbedThumbnail"})

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
        "writethumbnail": embed_cover_art,
        "postprocessors": postprocessors,
        # Scoped to the Metadata postprocessor specifically. The generic
        # "ffmpeg" key gets applied to *every* ffmpeg-based postprocessor
        # (FFmpegExtractAudio and EmbedThumbnail included), which can land
        # these -metadata flags in the wrong position in their ffmpeg
        # command lines and break postprocessing outright.
        "postprocessor_args": {"Metadata": []},
    }
    if track.artist:
        ydl_opts["postprocessor_args"]["Metadata"] = [
            "-metadata", f"artist={track.artist}",
            "-metadata", f"title={track.title}",
        ]
        if track.album:
            ydl_opts["postprocessor_args"]["Metadata"] += ["-metadata", f"album={track.album}"]

    log.info("Downloading %r -> %s.mp3", track.query_string, base_name)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([track.youtube_url])

    final_path = os.path.join(output_dir, f"{base_name}.mp3")
    track.output_path = final_path if os.path.exists(final_path) else None
    if not track.output_path:
        # yt-dlp reported success but the expected file isn't there - treat
        # as a failure rather than silently leaving output_path unset.
        raise RuntimeError("Download completed but the expected MP3 file was not found")


# --------------------------------------------------------------------------- #
# Duplicate detection / skip-existing helpers
# --------------------------------------------------------------------------- #

def _pseudo_track_from_filename(name_no_ext: str) -> Track:
    """Turn an existing 'NN - Artist - Title.mp3' filename back into a Track
    for fuzzy comparison against a candidate download."""
    name = _LEADING_TRACK_NUM_RE.sub("", name_no_ext)
    if " - " in name:
        artist, title = name.split(" - ", 1)
    else:
        artist, title = "", name
    return Track(index=0, title=title, artist=artist)


def existing_track_files(output_dir: str) -> list[Track]:
    if not os.path.isdir(output_dir):
        return []
    return [
        _pseudo_track_from_filename(os.path.splitext(f)[0])
        for f in os.listdir(output_dir)
        if f.lower().endswith(".mp3")
    ]


def is_already_downloaded(track: Track, output_dir: str, title_threshold: int = 87,
                           artist_threshold: int = 80,
                           existing_files: Optional[list[Track]] = None) -> bool:
    """
    Fuzzy check (same normalization/matching used for cross-playlist dedupe)
    instead of a brittle exact filename match, so re-scanning with slightly
    different title formatting still recognizes what's already on disk.

    Pass `existing_files` (from a single prior call to
    `existing_track_files(output_dir)`) when checking many tracks against
    the same output_dir in a loop, so the directory isn't re-listed and
    re-parsed into pseudo-Tracks on every single call.
    """
    candidates = existing_track_files(output_dir) if existing_files is None else existing_files
    for existing in candidates:
        if _is_same_song(track, existing, title_threshold, artist_threshold, duration_tolerance_sec=6):
            return True
    return False


# --------------------------------------------------------------------------- #
# CSV export for auditing matches
# --------------------------------------------------------------------------- #

# SEC-004: cell values that begin with one of these characters can be
# interpreted as a formula by Excel/Sheets when the CSV is opened (CWE-1236).
# Track titles/errors are attacker-influenceable-ish (YouTube/Spotify
# metadata, error text), so every text cell is neutralized before writing.
_CSV_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in _CSV_FORMULA_TRIGGER_CHARS:
        return "'" + s
    return s


def export_log(tracks: list[Track], csv_path: str) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "index", "source", "query", "matched_youtube_url",
            "match_score", "status", "output_path", "error",
            "source_playlist", "also_in_playlists", "fallback_candidates",
        ])
        for t in tracks:
            writer.writerow([
                t.index, t.source, _csv_safe(t.query_string), _csv_safe(t.youtube_url or ""),
                f"{t.match_score:.1f}" if t.match_score is not None else "",
                t.status, _csv_safe(t.output_path or ""), _csv_safe(t.error or ""),
                _csv_safe(t.source_playlist or ""), _csv_safe("; ".join(t.also_in)), len(t.fallback_urls),
            ])
