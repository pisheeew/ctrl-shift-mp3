"""
tests.py
Unit tests for engine.py's pure logic - link classification, filename
handling, title/artist normalization, and duplicate detection. Deliberately
excludes anything that hits the network (Spotify API, YouTube search), so
this suite runs instantly and needs no credentials.

Run with:
    python -m unittest tests.py
or:
    python tests.py
"""

import os
import tempfile
import unittest
from unittest import mock

import engine
from engine import Track
import main
import server
from run_orchestration import RunOrchestrator


class TestClassifyLink(unittest.TestCase):
    def test_spotify(self):
        self.assertEqual(engine.classify_link("https://open.spotify.com/playlist/abc123"), "spotify")

    def test_youtube_full(self):
        self.assertEqual(engine.classify_link("https://www.youtube.com/playlist?list=PL123"), "youtube")

    def test_youtube_short(self):
        self.assertEqual(engine.classify_link("https://youtu.be/abc123"), "youtube")

    def test_unknown(self):
        self.assertEqual(engine.classify_link("https://example.com/whatever"), "unknown")


class TestResolveEntryUrl(unittest.TestCase):
    def test_prefers_explicit_url(self):
        self.assertEqual(engine.resolve_entry_url({"url": "https://youtu.be/xyz"}), "https://youtu.be/xyz")

    def test_falls_back_to_webpage_url(self):
        self.assertEqual(
            engine.resolve_entry_url({"webpage_url": "https://youtube.com/watch?v=abc"}),
            "https://youtube.com/watch?v=abc",
        )

    def test_falls_back_to_id(self):
        self.assertEqual(
            engine.resolve_entry_url({"id": "abc123"}),
            "https://www.youtube.com/watch?v=abc123",
        )

    def test_none_entry(self):
        self.assertIsNone(engine.resolve_entry_url(None))

    def test_empty_entry(self):
        self.assertIsNone(engine.resolve_entry_url({}))


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_illegal_chars(self):
        self.assertEqual(engine.sanitize_filename('AC/DC - T.N.T?'), "AC_DC - T.N.T_")

    def test_truncates_long_names(self):
        long_name = "A" * 300
        result = engine.sanitize_filename(long_name)
        self.assertLessEqual(len(result), engine.MAX_BASENAME_LEN)


class TestNormalizeTitle(unittest.TestCase):
    def test_parenthetical_noise_stripped(self):
        self.assertEqual(
            engine.normalize_title("Bohemian Rhapsody (Official Video)"),
            engine.normalize_title("Bohemian Rhapsody"),
        )

    def test_dash_suffix_remaster_stripped(self):
        self.assertEqual(
            engine.normalize_title("White Christmas - Remastered"),
            engine.normalize_title("White Christmas"),
        )

    def test_dash_suffix_year_remaster_stripped(self):
        self.assertEqual(
            engine.normalize_title("Bohemian Rhapsody - 2011 Remaster"),
            engine.normalize_title("Bohemian Rhapsody"),
        )

    def test_feat_stripped(self):
        self.assertEqual(
            engine.normalize_title("Sicko Mode (feat. Drake)"),
            engine.normalize_title("Sicko Mode"),
        )

    def test_none_returns_empty(self):
        self.assertEqual(engine.normalize_title(None), "")

    def test_unrelated_titles_differ(self):
        self.assertNotEqual(
            engine.normalize_title("Blinding Lights"),
            engine.normalize_title("Save Your Tears"),
        )


class TestNormalizeArtist(unittest.TestCase):
    def test_drops_featured_artist(self):
        self.assertEqual(engine.normalize_artist("Travis Scott, Drake"), "travis scott")

    def test_none_returns_empty(self):
        self.assertEqual(engine.normalize_artist(None), "")


class TestDedupeTracks(unittest.TestCase):
    def test_merges_remaster_and_official_video_variants(self):
        tracks = [
            Track(index=1, title="Bohemian Rhapsody", artist="Queen", duration_sec=354, source_playlist="A"),
            Track(index=2, title="Bohemian Rhapsody - 2011 Remaster", artist="Queen", duration_sec=356,
                  source_playlist="B"),
            Track(index=3, title="Bohemian Rhapsody (Official Video)", artist="Queen", duration_sec=359,
                  source_playlist="C"),
        ]
        deduped, removed = engine.dedupe_tracks(tracks)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(removed, 2)

    def test_keeps_different_songs_separate(self):
        tracks = [
            Track(index=1, title="Somebody to Love", artist="Queen", duration_sec=296),
            Track(index=2, title="Bohemian Rhapsody", artist="Queen", duration_sec=354),
        ]
        deduped, removed = engine.dedupe_tracks(tracks)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(removed, 0)

    def test_live_version_with_different_duration_stays_separate(self):
        tracks = [
            Track(index=1, title="Blinding Lights", artist="The Weeknd", duration_sec=200),
            Track(index=2, title="Blinding Lights (Live at iHeartRadio)", artist="The Weeknd", duration_sec=260),
        ]
        deduped, removed = engine.dedupe_tracks(tracks)
        self.assertEqual(len(deduped), 2)

    def test_feat_formatting_merges(self):
        tracks = [
            Track(index=1, title="Sicko Mode", artist="Travis Scott, Drake", duration_sec=312),
            Track(index=2, title="SICKO MODE (feat. Drake)", artist="Travis Scott", duration_sec=313),
        ]
        deduped, removed = engine.dedupe_tracks(tracks)
        self.assertEqual(len(deduped), 1)

    def test_none_title_does_not_crash(self):
        tracks = [
            Track(index=1, title="Real Song", artist="Real Artist", duration_sec=200),
            Track(index=2, title=None, artist=None, duration_sec=None),
        ]
        deduped, removed = engine.dedupe_tracks(tracks)
        self.assertEqual(len(deduped), 2)


class TestBuildOutputBasename(unittest.TestCase):
    def test_no_collision(self):
        with tempfile.TemporaryDirectory() as d:
            t = Track(index=1, title="Unique Title", artist="Artist")
            name = engine.build_output_basename(t, d)
            self.assertEqual(name, "Artist - Unique Title")

    def test_collision_gets_disambiguated(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Artist - Same Title.mp3"), "w").close()
            t = Track(index=1, title="Same Title", artist="Artist")
            name = engine.build_output_basename(t, d)
            self.assertEqual(name, "Artist - Same Title (2)")

    def test_track_number_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            t = Track(index=7, title="Title", artist="Artist")
            name = engine.build_output_basename(t, d, filename_prefix="07")
            self.assertTrue(name.startswith("07 - "))


class TestIsAlreadyDownloaded(unittest.TestCase):
    def test_fuzzy_match_against_differently_formatted_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "01 - Queen - Bohemian Rhapsody.mp3"), "w").close()
            t = Track(index=5, title="Bohemian Rhapsody - 2011 Remaster", artist="Queen")
            self.assertTrue(engine.is_already_downloaded(t, d))

    def test_different_song_not_matched(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "01 - Queen - Bohemian Rhapsody.mp3"), "w").close()
            t = Track(index=6, title="Somebody to Love", artist="Queen")
            self.assertFalse(engine.is_already_downloaded(t, d))

    def test_empty_output_dir(self):
        with tempfile.TemporaryDirectory() as d:
            t = Track(index=1, title="Anything", artist="Anyone")
            self.assertFalse(engine.is_already_downloaded(t, d))

    def test_nonexistent_output_dir(self):
        t = Track(index=1, title="Anything", artist="Anyone")
        self.assertFalse(engine.is_already_downloaded(t, "/path/that/does/not/exist"))


class TestTrackJsonRoundTrip(unittest.TestCase):
    def test_round_trip_preserves_fields(self):
        original = [
            Track(index=1, title="Song A", artist="Artist A", duration_sec=200,
                  fallback_urls=["https://youtu.be/1", "https://youtu.be/2"], also_in=["Playlist B"]),
        ]
        as_json = engine.tracks_to_json(original)
        restored = engine.tracks_from_json(as_json)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].title, "Song A")
        self.assertEqual(restored[0].fallback_urls, ["https://youtu.be/1", "https://youtu.be/2"])
        self.assertEqual(restored[0].also_in, ["Playlist B"])


class TestProviderAndRunSeams(unittest.TestCase):
    def test_run_orchestrator_launches_and_resets_stop_signal(self):
        orchestrator = RunOrchestrator()
        completed = []
        thread = orchestrator.launch(lambda: completed.append(True), name="test-run")
        thread.join(timeout=1)
        self.assertEqual(completed, [True])
        orchestrator.stop()
        self.assertTrue(orchestrator.stop_requested.is_set())
        orchestrator.reset()
        self.assertFalse(orchestrator.stop_requested.is_set())

    def test_youtube_adapter_is_injectable(self):
        class FakeYouTube:
            def find_matches(self, track, ydl=None):
                return [({"id": "test-video"}, 99.0)]

        providers = engine.MediaProviders(youtube=FakeYouTube())
        track = Track(index=1, title="Test song", artist="Test artist")
        matches = providers.youtube.find_matches(track)
        self.assertEqual(matches[0][0]["id"], "test-video")


class TestPackagedStartup(unittest.TestCase):
    def test_resource_dir_prefers_frozen_bundle_location(self):
        with mock.patch.object(server.sys, "_MEIPASS", r"C:\bundle", create=True):
            self.assertEqual(server.resource_dir(), os.path.join(r"C:\bundle"))

    def test_app_serves_frontend_from_injected_resource_directory(self):
        with tempfile.TemporaryDirectory() as frontend_dir:
            with open(os.path.join(frontend_dir, "index.html"), "w", encoding="utf-8") as page:
                page.write("packaged frontend")
            response = server.create_app(frontend_dir=frontend_dir).test_client().get("/")
            body = response.get_data(as_text=True)

            response.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, "packaged frontend")

    def test_select_port_uses_preferred_when_available(self):
        with mock.patch.object(main, "port_is_available", return_value=True):
            self.assertEqual(main.select_port(8743), 8743)

    def test_select_port_falls_back_to_ephemeral_port(self):
        with mock.patch.object(main, "port_is_available", return_value=False):
            selected = main.select_port(8743)
        self.assertGreater(selected, 0)

    def test_browser_failure_is_logged_and_does_not_raise(self):
        with mock.patch.object(main.webbrowser, "open", side_effect=OSError("no browser")):
            with mock.patch.object(main.log, "warning") as warning:
                main.open_browser("http://127.0.0.1:8743/")
        warning.assert_called_once()


class TestFfmpegResolution(unittest.TestCase):
    def test_prefers_bundled_ffmpeg_directory(self):
        with tempfile.TemporaryDirectory() as bundle:
            open(os.path.join(bundle, "ffmpeg.exe"), "w").close()
            open(os.path.join(bundle, "ffprobe.exe"), "w").close()
            self.assertEqual(engine.resolve_ffmpeg_location(bundle, lambda _: None), bundle)

    def test_falls_back_to_path(self):
        which = lambda name: os.path.join(r"C:\tools", name)
        self.assertEqual(engine.resolve_ffmpeg_location(r"C:\missing", which), r"C:\tools")

    def test_reports_missing_ffmpeg_tools(self):
        with self.assertRaisesRegex(RuntimeError, "ffmpeg and ffprobe"):
            engine.resolve_ffmpeg_location(r"C:\missing", lambda _: None)


if __name__ == "__main__":
    unittest.main()
