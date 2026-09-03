"""Run lifecycle primitives shared by the local application orchestration."""

from __future__ import annotations

import threading
from collections.abc import Callable


class RunOrchestrator:
    """Launch background runs and expose one stop signal to their workers."""

    def __init__(self):
        self.stop_requested = threading.Event()

    def launch(self, target: Callable, *args, name: str = "run") -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True, name=name)
        thread.start()
        return thread

    def stop(self) -> None:
        self.stop_requested.set()

    def reset(self) -> None:
        self.stop_requested.clear()
