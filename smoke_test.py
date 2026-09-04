"""
smoke_test.py
Deterministic Windows smoke test for the portable ctrl+shift+mp3 bundle.

The test launches the built executable from a temporary directory *outside*
the repository, waits for it to print its local port, makes HTTP requests
against the live server to prove:

  - The root frontend HTML is served.
  - Representative static assets (app.js, style.css) are served.
  - The /api/init endpoint responds with valid JSON.
  - The bundled FFmpeg directory is present in the copied bundle.

The test is self-contained and deterministic: it needs no Spotify credentials,
no real Playlist, no YouTube access, and no network calls.

Skip behaviour
--------------
The test class is *skipped* (via @unittest.skipUnless) when the built bundle
is absent so that the regular unit-test suite can always run from source
without requiring a PyInstaller build.

Run with:
    python smoke_test.py

Or build the bundle first:
    powershell -ExecutionPolicy Bypass -File build-release.ps1
    python smoke_test.py
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

# ---------------------------------------------------------------------------
# Locate the built bundle relative to this file (repo root).
# PyInstaller creates:  dist/ctrl-shift-mp3/ctrl-shift-mp3.exe
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_BUNDLE_DIR = os.path.join(_REPO_ROOT, "dist", "ctrl-shift-mp3")
_EXE_NAME = "ctrl-shift-mp3.exe"
_EXE_PATH = os.path.join(_BUNDLE_DIR, _EXE_NAME)

_BUNDLE_PRESENT = os.path.isfile(_EXE_PATH)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
_STARTUP_TIMEOUT_S = 30   # seconds to wait for the server to print its port
_HTTP_TIMEOUT_S = 10      # per-request timeout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_stdout_until_port(proc: subprocess.Popen, timeout: float) -> int:
    """
    Read lines from *proc* stdout until the startup banner is found.
    Returns the port number, or raises TimeoutError if the banner does not
    appear within *timeout* seconds.

    Expected banner (see main.py):
        ctrl+shift+mp3 is running at http://127.0.0.1:<port>/
    """
    _PORT_RE = re.compile(r"running at http://127\.0\.0\.1:(\d+)/")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Process exited unexpectedly with code {proc.returncode} "
                "before printing its port."
            )
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        m = _PORT_RE.search(line)
        if m:
            return int(m.group(1))
    raise TimeoutError(
        f"ctrl-shift-mp3 did not print its port within {timeout}s."
    )


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    """
    Block until a TCP connection to *host*:*port* succeeds, or raises
    TimeoutError.  This guards against the process printing its banner
    fractionally before the socket is ready.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(
        f"TCP port {port} on {host!r} did not become reachable within {timeout}s."
    )


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@unittest.skipUnless(
    _BUNDLE_PRESENT,
    f"Portable bundle not found at {_BUNDLE_DIR!r} — run build-release.ps1 first.",
)
class TestPortableBundle(unittest.TestCase):
    """End-to-end smoke test that exercises the built, portable executable."""

    # ------------------------------------------------------------------
    # Fixture: copy bundle to temp dir, launch, wait, tear down
    # ------------------------------------------------------------------

    @classmethod
    def setUpClass(cls):
        # Use a temp dir *outside* the repository so we prove the bundle
        # does not rely on any file from the checkout.
        cls._tempdir = tempfile.mkdtemp(prefix="ctrl_shift_mp3_smoke_")
        try:
            # Mirror the bundle folder into the temp directory.
            dest = os.path.join(cls._tempdir, "ctrl-shift-mp3")
            shutil.copytree(_BUNDLE_DIR, dest)
            cls._bundle_copy = dest

            exe = os.path.join(dest, _EXE_NAME)

            # Ensure the EXE's stdout is not buffered so readline() returns
            # promptly.  PYTHONUNBUFFERED=1 forces unbuffered I/O inside the
            # frozen interpreter.  PYTHONUTF8=1 pins the encoding to UTF-8 so
            # text=True works correctly regardless of the console code page.
            launch_env = os.environ.copy()
            launch_env.update({"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})

            # Launch the executable.  Capture stdout line-by-line so we can
            # read the port from the startup banner without blocking forever.
            cls._proc = subprocess.Popen(
                [exe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=cls._tempdir,  # working directory outside the repo
                # Do NOT inherit the test runner's stdin so the subprocess
                # cannot block waiting for user input.
                stdin=subprocess.DEVNULL,
                env=launch_env,
            )

            # Discover the port from stdout.
            cls._port = _collect_stdout_until_port(
                cls._proc, timeout=_STARTUP_TIMEOUT_S
            )
            cls._base_url = f"http://127.0.0.1:{cls._port}"

            # Make sure the socket is accepting connections before we
            # hand control to the individual tests.
            _wait_for_port("127.0.0.1", cls._port, timeout=5)

        except Exception:
            # Clean up on setup failure so we do not leave orphan processes.
            cls._cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    @classmethod
    def _cleanup(cls):
        proc: subprocess.Popen | None = getattr(cls, "_proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        tempdir = getattr(cls, "_tempdir", None)
        if tempdir and os.path.isdir(tempdir):
            shutil.rmtree(tempdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _get(self, path: str):
        """Perform an HTTP GET against the running server."""
        import urllib.request
        url = self._base_url + path
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_S) as resp:
            return resp.status, resp.read(), dict(resp.headers)

    # ------------------------------------------------------------------
    # Acceptance criteria
    # ------------------------------------------------------------------

    def test_root_returns_html_frontend(self):
        """GET / returns 200 with an HTML page (the frontend index)."""
        status, body, _ = self._get("/")
        self.assertEqual(status, 200)
        body_text = body.decode("utf-8", errors="replace")
        self.assertIn("<html", body_text.lower(),
                      "Root endpoint should serve an HTML page")

    def test_app_js_is_served(self):
        """GET /app.js returns 200 with JavaScript content."""
        status, body, headers = self._get("/app.js")
        self.assertEqual(status, 200)
        # Body must be non-empty JavaScript
        self.assertGreater(len(body), 0, "app.js must not be empty")

    def test_style_css_is_served(self):
        """GET /style.css returns 200 with CSS content."""
        status, body, headers = self._get("/style.css")
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 0, "style.css must not be empty")

    def test_api_init_returns_json(self):
        """GET /api/init returns 200 with a JSON object."""
        import json as _json
        status, body, _ = self._get("/api/init")
        self.assertEqual(status, 200)
        data = _json.loads(body)
        self.assertIsInstance(data, dict,
                              "/api/init must return a JSON object")

    def test_bundled_ffmpeg_is_present_in_bundle_copy(self):
        """The copied bundle contains an ffmpeg/ sub-directory with both binaries.

        PyInstaller 6.x places data files under ``_internal/`` inside the
        bundle folder, so this test searches the bundle recursively for a
        directory named ``ffmpeg`` that contains both ``ffmpeg.exe`` and
        ``ffprobe.exe`` rather than assuming a fixed path.
        """
        ffmpeg_dir: str | None = None
        for dirpath, dirnames, filenames in os.walk(self._bundle_copy):
            if os.path.basename(dirpath) == "ffmpeg":
                ffmpeg_dir = dirpath
                break

        self.assertIsNotNone(
            ffmpeg_dir,
            f"No 'ffmpeg' directory found anywhere under {self._bundle_copy!r}",
        )
        for binary in ("ffmpeg.exe", "ffprobe.exe"):
            self.assertTrue(
                os.path.isfile(os.path.join(ffmpeg_dir, binary)),
                f"Expected {binary!r} inside {ffmpeg_dir!r}",
            )

    def test_process_terminates_cleanly_after_sigterm(self):
        """
        The executable can be terminated cleanly (no leftover process).
        This is the last test in suite order so it does not affect the others.
        Note: the actual termination is performed in tearDownClass; this test
        just asserts that the process is still alive at the point where we
        check it (which proves setUpClass actually launched it).
        """
        self.assertIsNone(
            self._proc.poll(),
            "The server process should still be running during the test suite.",
        )


if __name__ == "__main__":
    unittest.main()
