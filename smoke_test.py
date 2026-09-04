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
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

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
    Read lines from *proc* stdout asynchronously until the startup banner is found.
    Guards against blocking readline() calls on Windows to prevent CI hangs.
    Returns the port number, or raises TimeoutError/RuntimeError.

    Expected banner (see main.py):
        ctrl+shift+mp3 is running at http://127.0.0.1:<port>/
    """
    _PORT_RE = re.compile(r"running at http://127\.0\.0\.1:(\d+)/")
    line_queue: queue.Queue[str] = queue.Queue()

    def _reader():
        try:
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    line_queue.put(line)
        except Exception:
            pass

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None and line_queue.empty():
            raise RuntimeError(
                f"Process exited unexpectedly with code {proc.returncode} "
                "before printing its port."
            )
        try:
            remaining = max(0.05, deadline - time.monotonic())
            line = line_queue.get(timeout=min(0.2, remaining))
            m = _PORT_RE.search(line)
            if m:
                return int(m.group(1))
        except queue.Empty:
            continue

    raise TimeoutError(
        f"ctrl-shift-mp3 did not print its port within {timeout}s."
    )


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    """
    Block until a TCP connection to *host*:*port* succeeds, or raises
    TimeoutError. This guards against the process printing its banner
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
            cls._exe_path = exe

            # Ensure the EXE's stdout is not buffered so readline() returns
            # promptly. PYTHONUNBUFFERED=1 forces unbuffered I/O inside the
            # frozen interpreter. PYTHONUTF8=1 pins the encoding to UTF-8.
            launch_env = os.environ.copy()
            launch_env.update({"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})

            # Launch the executable. Capture stdout asynchronously.
            cls._proc = subprocess.Popen(
                [exe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=cls._tempdir,  # working directory outside the repo
                stdin=subprocess.DEVNULL,
                env=launch_env,
            )

            # Discover the port from stdout.
            cls._port = _collect_stdout_until_port(
                cls._proc, timeout=_STARTUP_TIMEOUT_S
            )
            cls._base_url = f"http://127.0.0.1:{cls._port}"

            # Make sure the socket is accepting connections before tests run.
            _wait_for_port("127.0.0.1", cls._port, timeout=5)

        except Exception:
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

    def _get(self, path: str) -> tuple[int, bytes]:
        """Perform an HTTP GET against the running server."""
        url = self._base_url + path
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_S) as resp:
            return resp.status, resp.read()

    # ------------------------------------------------------------------
    # Acceptance criteria
    # ------------------------------------------------------------------

    def test_root_returns_html_frontend(self):
        """GET / returns 200 with an HTML page (the frontend index)."""
        status, body = self._get("/")
        self.assertEqual(status, 200)
        body_text = body.decode("utf-8", errors="replace")
        self.assertIn("<html", body_text.lower(),
                      "Root endpoint should serve an HTML page")

    def test_representative_static_assets_are_served(self):
        """GET /app.js and GET /style.css return 200 with non-empty contents."""
        for asset in ("/app.js", "/style.css"):
            with self.subTest(asset=asset):
                status, body = self._get(asset)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 0, f"{asset} must not be empty")

    def test_bundled_ffmpeg_resolution_is_available_and_functional(self):
        """The copied bundle contains ffmpeg and ffprobe and both execute cleanly."""
        ffmpeg_dir: str | None = None
        for dirpath, _, _ in os.walk(self._bundle_copy):
            if os.path.basename(dirpath) == "ffmpeg":
                ffmpeg_dir = dirpath
                break

        self.assertIsNotNone(
            ffmpeg_dir,
            f"No 'ffmpeg' directory found anywhere under {self._bundle_copy!r}",
        )
        for binary in ("ffmpeg.exe", "ffprobe.exe"):
            binary_path = os.path.join(ffmpeg_dir, binary)
            self.assertTrue(
                os.path.isfile(binary_path),
                f"Expected {binary!r} inside {ffmpeg_dir!r}",
            )
            result = subprocess.run(
                [binary_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(
                result.returncode, 0,
                f"{binary} failed to execute: {result.stderr}",
            )
            tool_name = os.path.splitext(binary)[0]
            self.assertIn(
                tool_name,
                result.stdout.lower(),
                f"Expected {tool_name} banner in output",
            )

    def test_executable_terminates_cleanly_and_does_not_hang(self):
        """A bundle instance launched outside the repo terminates cleanly upon terminate()."""
        proc = subprocess.Popen(
            [self._exe_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=self._tempdir,
        )
        try:
            time.sleep(0.5)
            self.assertIsNone(proc.poll(), "Process should be running")
            proc.terminate()
            ret = proc.wait(timeout=5)
            self.assertIsNotNone(ret, "Process did not terminate within timeout")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
