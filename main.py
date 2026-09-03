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

import argparse
import socket
import threading
import time
import webbrowser

from server import create_app, PORT, log


def port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def select_port(preferred_port: int = PORT, host: str = "127.0.0.1") -> int:
    if port_is_available(preferred_port, host):
        return preferred_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def open_browser(url: str) -> bool:
    try:
        opened = webbrowser.open(url)
    except Exception:
        log.warning("Could not open browser at %s", url, exc_info=True)
        return False
    if not opened:
        log.warning("Browser declined to open %s", url)
    return bool(opened)


def _open_browser_when_ready(url: str):
    # Small delay so the server is actually listening before the browser
    # tries to connect.
    time.sleep(1.0)
    open_browser(url)


def main(debug: bool = False):
    selected_port = select_port()
    url = f"http://127.0.0.1:{selected_port}/"
    app = create_app(port=selected_port)
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    print(f"ctrl+shift+mp3 is running at {url}")
    print("Your browser should open automatically. Press Ctrl+C here to stop the server.")
    app.run(host="127.0.0.1", port=selected_port, threaded=True, debug=debug, use_reloader=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the local ctrl+shift+mp3 server")
    parser.add_argument("--debug", action="store_true", help="enable Flask debug diagnostics")
    try:
        main(debug=parser.parse_args().debug)
    except Exception:
        log.exception("Fatal error during startup")
        raise
