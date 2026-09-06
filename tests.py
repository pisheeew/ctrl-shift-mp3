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

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import engine
from engine import Track
import main
import package_release
import server
import set_version
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


@unittest.skipUnless(engine.SPOTIPY_AVAILABLE, "spotipy is not installed")
class TestSpotifyTokenStorage(unittest.TestCase):
    def test_authentication_writes_no_token_cache_file_to_working_directory(self):
        token_response = mock.Mock(status_code=200)
        token_response.json.return_value = {
            "access_token": "test-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        with tempfile.TemporaryDirectory() as workdir:
            previous_cwd = os.getcwd()
            os.chdir(workdir)
            try:
                before = set(os.listdir(workdir))
                lister = engine.SpotifyLister("fake-client-id", "fake-client-secret")
                # The mocked POST is the token endpoint, so the real
                # client-credentials flow runs without touching the network.
                with mock.patch("requests.Session.post", return_value=token_response):
                    lister.client.client_credentials_manager.get_access_token(as_dict=False)
                after = set(os.listdir(workdir))
            finally:
                os.chdir(previous_cwd)
        self.assertEqual(after, before)


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


class TestCredentialFileProtection(unittest.TestCase):
    """ADR-0003 follow-up: the credentials file must not be plaintext at
    rest. On Windows the secret-bearing fields are DPAPI-encrypted with a
    "dpapi:" prefix; plaintext configs from prior versions still load and
    are encrypted on their next save."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        patcher = mock.patch.object(server, "CONFIG_PATH", self.config_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_config_file(self, cfg):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _read_config_file(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            return f.read()

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_save_config_encrypts_secret_fields_on_disk(self):
        cfg = dict(server.DEFAULT_CONFIG,
                   spotify_client_id="client-id-123",
                   spotify_client_secret="secret-456")
        self.assertIsNone(server.save_config(cfg))
        raw = self._read_config_file()
        self.assertIn("dpapi:", raw)
        self.assertNotIn("secret-456", raw)
        self.assertNotIn("client-id-123", raw)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_save_then_load_round_trips_secrets(self):
        cfg = dict(server.DEFAULT_CONFIG,
                   spotify_client_id="client-id-123",
                   spotify_client_secret="secret-456",
                   quality_kbps="320")
        self.assertIsNone(server.save_config(cfg))
        loaded = server.load_config()
        self.assertEqual(loaded["spotify_client_id"], "client-id-123")
        self.assertEqual(loaded["spotify_client_secret"], "secret-456")
        self.assertEqual(loaded["quality_kbps"], "320")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_plaintext_config_from_prior_version_still_loads(self):
        self._write_config_file(dict(server.DEFAULT_CONFIG,
                                     spotify_client_id="old-id",
                                     spotify_client_secret="old-secret"))
        loaded = server.load_config()
        self.assertEqual(loaded["spotify_client_id"], "old-id")
        self.assertEqual(loaded["spotify_client_secret"], "old-secret")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_plaintext_config_is_protected_on_next_save(self):
        self._write_config_file(dict(server.DEFAULT_CONFIG,
                                     spotify_client_id="old-id",
                                     spotify_client_secret="old-secret"))
        self.assertIsNone(server.save_config(server.load_config()))
        raw = self._read_config_file()
        self.assertIn("dpapi:", raw)
        self.assertNotIn("old-secret", raw)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_undecryptable_field_is_kept_not_destroyed(self):
        stored = "dpapi:not-a-real-blob"
        self._write_config_file(dict(server.DEFAULT_CONFIG,
                                     spotify_client_secret=stored))
        loaded = server.load_config()
        # A failed decrypt keeps the stored value so the next save
        # re-persists it instead of silently blanking the credential.
        self.assertEqual(loaded["spotify_client_secret"], stored)
        self.assertIsNone(server.save_config(loaded))
        self.assertEqual(server.load_config()["spotify_client_secret"], stored)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_save_config_applies_chmod_600_on_posix(self):
        cfg = dict(server.DEFAULT_CONFIG, spotify_client_secret="secret")
        self.assertIsNone(server.save_config(cfg))
        self.assertEqual(os.stat(self.config_path).st_mode & 0o777, 0o600)


class TestHostHeaderAllowlist(unittest.TestCase):
    """ADR-0001: the browser-facing API must only answer requests whose
    Host header is this machine, so a DNS-rebinding page cannot reach the
    local server under an attacker-controlled hostname. The app may bind a
    fallback port, so any port on a local hostname is fine."""

    def _client(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        with open(os.path.join(d.name, "index.html"), "w", encoding="utf-8") as page:
            page.write("packaged frontend")
        return server.create_app(frontend_dir=d.name).test_client()

    def _get_status(self, client, host):
        response = client.get("/", headers={"Host": host})
        try:
            return response.status_code
        finally:
            response.close()

    def test_foreign_host_is_rejected(self):
        client = self._client()
        for host in ("evil.example", "attacker.example:8743", "127.0.0.1.example.com"):
            with self.subTest(host=host):
                self.assertEqual(self._get_status(client, host), 403)

    def test_local_hostnames_on_any_port_are_accepted(self):
        client = self._client()
        for host in ("127.0.0.1", "127.0.0.1:8743", "127.0.0.1:54321",
                     "localhost", "localhost:8743", "localhost:54321"):
            with self.subTest(host=host):
                self.assertEqual(self._get_status(client, host), 200)

    def test_missing_host_is_rejected(self):
        client = self._client()
        self.assertEqual(self._get_status(client, ""), 403)


class TestFfmpegResolution(unittest.TestCase):
    def test_prefers_bundled_ffmpeg_directory(self):
        # Use the filenames resolve_ffmpeg_location looks for on this
        # platform, so the test passes on both Windows and Linux CI.
        names = ("ffmpeg.exe", "ffprobe.exe") if os.name == "nt" else ("ffmpeg", "ffprobe")
        with tempfile.TemporaryDirectory() as bundle:
            for name in names:
                open(os.path.join(bundle, name), "w").close()
            self.assertEqual(engine.resolve_ffmpeg_location(bundle, lambda _: None), bundle)

    def test_falls_back_to_path(self):
        which = lambda name: os.path.join(r"C:\tools", name)
        self.assertEqual(engine.resolve_ffmpeg_location(r"C:\missing", which), r"C:\tools")

    def test_reports_missing_ffmpeg_tools(self):
        with self.assertRaisesRegex(RuntimeError, "ffmpeg and ffprobe"):
            engine.resolve_ffmpeg_location(r"C:\missing", lambda _: None)


class TestPackageRelease(unittest.TestCase):
    """Release packaging mechanics behind the tag-triggered workflow:
    tag parsing, the versioned ZIP name, the checksum file, third-party
    notices, and the draft release notes."""

    def test_parse_tag_strips_v_prefix(self):
        self.assertEqual(package_release.parse_tag("v1.0.0"), "1.0.0")
        self.assertEqual(package_release.parse_tag("v10.20.30"), "10.20.30")

    def test_parse_tag_rejects_non_version_tags(self):
        for tag in ("1.0.0", "release-1", "v", "v1.x", "v1..0",
                    "v0.2", "v1.2.3.4", "v1.0.0-beta"):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError):
                    package_release.parse_tag(tag)

    def test_release_pins_come_from_the_release_files(self):
        python_pins, ffmpeg_pins = package_release.load_release_pins()
        self.assertIn(("flask", "3.1.3"), python_pins)
        self.assertIn(("yt-dlp", "2026.8.19"), python_pins)
        self.assertIn(("rapidfuzz", "3.14.6"), python_pins)
        self.assertIn(("spotipy", "2.26.0"), python_pins)
        # PyInstaller and its build-only dependency tree are build tooling,
        # not something the bundle ships, so they stay out of the pins (and
        # NOTICES.txt) — mirrors _BUILD_ONLY_PACKAGES in package_release.py.
        build_only = {"pyinstaller", "altgraph", "packaging", "pefile",
                      "pyinstaller-hooks-contrib", "pywin32-ctypes", "setuptools"}
        self.assertFalse(any(name in build_only for name, _ in python_pins))
        self.assertEqual(ffmpeg_pins["version"], "9.0.1")
        self.assertEqual(ffmpeg_pins["sha256"],
                         "a8ebbaf7a99185f5abc3a2d3a657521c38d7966f06b70468d7ab29a67fe8654f")
        self.assertEqual(
            ffmpeg_pins["url"],
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            "autobuild-2026-09-05-13-10/ffmpeg-n9.0.1-26-g5c8e7e2433-win64-gpl-9.0.zip",
        )

    # The lockfile tests re-derive the pins independently of package_release
    # (no shared parser) so a parsing bug there cannot hide a lockfile gap.
    # requirements-release.txt is a pip-compile --generate-hashes lockfile:
    # `name==version \` followed by indented `--hash=sha256:...` lines.
    _LOCKFILE_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s#]+)")

    @staticmethod
    def _canonical_name(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    @classmethod
    def _parse_hashed_lockfile(cls):
        """Return ({canonical name: version}, {names carrying a sha256
        hash}) for every pinned package in requirements-release.txt."""
        versions = {}
        hashed = set()
        current = None
        for line in (Path(__file__).resolve().parent / "requirements-release.txt") \
                .read_text(encoding="utf-8").splitlines():
            pin = cls._LOCKFILE_PIN_RE.match(line)
            if pin:
                current = cls._canonical_name(pin.group("name"))
                versions[current] = pin.group("version")
            elif current is not None and "--hash=sha256:" in line:
                hashed.add(current)
        return versions, hashed

    def test_release_lockfile_hashes_every_entry(self):
        """The transitive closure is hash-pinned: any package added without
        a recorded sha256 (e.g. a hand-edited or half-regenerated lockfile)
        fails here, and --require-hashes would fail the install anyway."""
        versions, hashed = self._parse_hashed_lockfile()
        # The five top-level pins resolve to a 20+-package closure; a much
        # smaller lockfile means the closure was not fully generated.
        self.assertGreaterEqual(len(versions), 20,
                                "the lockfile should pin the full transitive closure")
        unhashed = sorted(set(versions) - hashed)
        self.assertEqual(unhashed, [],
                         "lockfile entries without a --hash=sha256 value")

    def test_release_lockfile_covers_the_top_level_pins(self):
        """Every top-level pin in requirements-release.in must appear at
        exactly that version in the lockfile, so bumping a version in one
        file without regenerating the other fails the suite."""
        versions, _ = self._parse_hashed_lockfile()
        in_text = (Path(__file__).resolve().parent / "requirements-release.in") \
            .read_text(encoding="utf-8")
        for line in in_text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            match = self._LOCKFILE_PIN_RE.match(line)
            self.assertIsNotNone(match, f"unparseable pin in requirements-release.in: {line}")
            name = self._canonical_name(match.group("name"))
            self.assertIn(name, versions,
                          f"{name} is a top-level pin but is missing from the lockfile")
            self.assertEqual(
                versions[name], match.group("version"),
                f"{name} was bumped in requirements-release.in but the lockfile "
                "still pins another version — regenerate requirements-release.txt.",
            )

    def test_assemble_release_builds_versioned_zip_with_notices_inside(self):
        with tempfile.TemporaryDirectory() as dist:
            bundle = Path(dist) / "ctrl-shift-mp3"
            bundle.mkdir()
            (bundle / "ctrl-shift-mp3.exe").write_bytes(b"MZ fake exe")
            assets = package_release.assemble_release("v1.2.3", dist_dir=Path(dist))
            self.assertEqual(assets["zip"].name, "ctrl-shift-mp3-windows-v1.2.3.zip")
            self.assertTrue(assets["zip"].is_file())
            with zipfile.ZipFile(assets["zip"]) as archive:
                names = archive.namelist()
                self.assertIn("ctrl-shift-mp3.exe", names)
                self.assertIn("NOTICES.txt", names)
            self.assertEqual((bundle / "NOTICES.txt").read_bytes(),
                             assets["notices"].read_bytes())
            digest = hashlib.sha256(assets["zip"].read_bytes()).hexdigest()
            self.assertEqual(
                assets["checksum"].read_text(encoding="utf-8").strip(),
                f"{digest}  ctrl-shift-mp3-windows-v1.2.3.zip",
            )
            self.assertTrue(assets["notes"].is_file())
            assets_list = assets["assets_list"].read_text(encoding="utf-8").splitlines()
            self.assertEqual(assets_list,
                             [str(assets["zip"]), str(assets["checksum"]), str(assets["notices"])])

    def test_assemble_release_rejects_missing_built_bundle(self):
        with tempfile.TemporaryDirectory() as dist:
            with self.assertRaisesRegex(RuntimeError, "built bundle directory"):
                package_release.assemble_release("v1.2.3", dist_dir=Path(dist))

    def test_notices_cover_python_pins_and_ffmpeg_source(self):
        python_pins, ffmpeg_pins = package_release.load_release_pins()
        with tempfile.TemporaryDirectory() as dist:
            notices = Path(dist) / "NOTICES.txt"
            package_release.write_notices(notices, python_pins, ffmpeg_pins)
            text = notices.read_text(encoding="utf-8")
        self.assertIn("flask", text.lower())
        self.assertIn("3.1.3", text)
        self.assertIn("yt-dlp", text.lower())
        self.assertIn("BtbN/FFmpeg-Builds", text)
        self.assertIn(ffmpeg_pins["sha256"], text)
        self.assertIn("General Public License", text)
        # The GPL text itself must be retained, not just linked.
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 2, June 1991", text)

    def test_release_notes_cover_required_guidance(self):
        with tempfile.TemporaryDirectory() as dist:
            notes = Path(dist) / "release-notes.md"
            package_release.write_release_notes(
                notes,
                version="1.2.3",
                zip_name="ctrl-shift-mp3-windows-v1.2.3.zip",
                checksum="abc123",
                ffmpeg_version="7.1.1",
            )
            text = notes.read_text(encoding="utf-8")
        for expected in ("extract", "ctrl-shift-mp3.exe", "smartscreen",
                         "sha-256", "abc123", "http://127.0.0.1", "--debug",
                         "notices.txt", "right to use"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text.lower())


class TestSetVersion(unittest.TestCase):
    """Executable metadata stamping: the tag version must reach
    version_info.txt so the exe, ZIP name, and release notes agree."""

    def _copy_template(self, tmp: str) -> Path:
        target = Path(tmp) / "version_info.txt"
        target.write_text(
            (Path(__file__).parent / "version_info.txt").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return target

    def test_stamp_version_updates_metadata_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = self._copy_template(tmp)
            set_version.stamp_version_info(info, (1, 2, 3))
            text = info.read_text(encoding="utf-8")
        self.assertIn("filevers=(1, 2, 3, 0)", text)
        self.assertIn("prodvers=(1, 2, 3, 0)", text)
        self.assertIn("StringStruct('FileVersion', '1.2.3')", text)
        self.assertIn("StringStruct('ProductVersion', '1.2.3')", text)
        # Untouched fields stay untouched.
        self.assertIn("StringStruct('OriginalFilename', 'ctrl-shift-mp3.exe')", text)

    def test_stamp_version_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = self._copy_template(tmp)
            set_version.stamp_version_info(info, (2, 0, 1))
            once = info.read_text(encoding="utf-8")
            set_version.stamp_version_info(info, (2, 0, 1))
            self.assertEqual(info.read_text(encoding="utf-8"), once)

    def test_stamp_version_rejects_non_version_tag(self):
        with self.assertRaises(ValueError):
            set_version.stamp_version_from_tag("v1.x", Path("unused"))

    def test_stamp_version_rejects_unexpected_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = Path(tmp) / "version_info.txt"
            info.write_text("filevers=(1, 0, 0, 0),\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                set_version.stamp_version_info(info, (1, 2, 3))

    def test_stamp_version_from_tag_parses_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = self._copy_template(tmp)
            set_version.stamp_version_from_tag("v4.5.6", info)
            text = info.read_text(encoding="utf-8")
        self.assertIn("filevers=(4, 5, 6, 0)", text)
        self.assertIn("StringStruct('ProductVersion', '4.5.6')", text)


if __name__ == "__main__":
    unittest.main()
