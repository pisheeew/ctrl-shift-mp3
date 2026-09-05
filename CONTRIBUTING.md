# Contributing to ctrl+shift+mp3

Thanks for your interest in contributing. This is a small, focused project;
contributions that keep it that way are especially welcome.

## Reporting bugs and proposing features

Please open an issue before building a large change. Design discussions —
matching behavior, packaging, the frontend, release engineering — happen in
issues first, so the reasoning is visible and agreed before code lands. Use
the issue templates; security issues are different: see
[SECURITY.md](SECURITY.md).

## Setup

You need Python 3 and `ffmpeg` (with `ffprobe`) on your `PATH`.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the app with `python main.py`; it serves the local UI at
<http://127.0.0.1:8743/>.

## Running the tests

```bash
python -m unittest tests.py
```

The suite mocks all network calls, so no Spotify or YouTube credentials are
needed. Please run it before submitting.

## Pull requests

Pull requests are welcome. To make review quick:

- Link the issue that describes the problem or the agreed design.
- Keep the change focused; one thing per pull request.
- Add or adjust tests for the behavior you changed.
- Run `python -m unittest tests.py` and confirm it passes.
- Match the existing style of the file you are editing.
