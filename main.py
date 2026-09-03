"""
main.py
Entry point for ctrl+shift+mp3 (local-server build).

Run with:
    python main.py

This starts a small local Flask server on 127.0.0.1 and opens it in your
OS's actual default browser (Chrome, per your setup) via webbrowser.open().
No embedded WebView2/webview window is involved - this is what you asked
for after WebView2 kept misbehaving.

Close the browser tab and press Ctrl+C in this terminal to stop the server.
"""

import threading
import time
import webbrowser

from server import create_app, PORT, log


def _open_browser_when_ready():
    # Small delay so the server is actually listening before the browser
    # tries to connect.
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


def main():
    app = create_app()
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    print(f"ctrl+shift+mp3 is running at http://127.0.0.1:{PORT}/")
    print("Your browser should open automatically. Press Ctrl+C here to stop the server.")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Fatal error during startup")
        raise
