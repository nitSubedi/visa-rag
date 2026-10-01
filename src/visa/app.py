"""The desktop app: a native window over the same pipeline as the CLI.

Starts the bundled model runtime when the app carries one (runtime.py), otherwise uses
Ollama; builds the local law library on first launch, with progress; then answers
questions through service.py. The page (ui/index.html) talks to `Api` through pywebview's
JavaScript bridge — nothing listens on a network port of its own.
"""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path

from . import library, profile, runtime, service, updates
from .config import settings
from .search import Index

UI = Path(__file__).parent / "ui" / "index.html"


class Api:
    """Methods the page can call. Each returns plain JSON-able data."""

    def __init__(self) -> None:
        self._index: Index | None = None
        self._setup: dict[str, object] = {
            "running": False,
            "message": "",
            "progress": 0.0,
            "error": "",
        }
        self._lock = threading.Lock()

    def status(self) -> dict[str, object]:
        return {"built": library.is_built(), "backend": settings.backend, **self._setup}

    def start_setup(self) -> dict[str, object]:
        """Build the library in the background; the page polls status()."""
        with self._lock:
            if self._setup["running"]:
                return self.status()
            self._setup.update(running=True, message="Starting…", progress=0.0, error="")

        def progress(message: str, frac: float) -> None:
            self._setup.update(message=message, progress=max(0.0, min(1.0, frac)))

        def work() -> None:
            try:
                built = library.build(progress)
                if built.failed:
                    names = ", ".join(s for s, _ in built.failed)
                    self._setup["error"] = (
                        f"Some sources could not be downloaded ({names}); answers will "
                        "have gaps. Check the connection and run setup again."
                    )
                self._index = None
            except Exception as e:  # shown to the person, never swallowed
                self._setup["error"] = f"Setup failed: {e}"
            finally:
                self._setup["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return self.status()

    def ask(
        self, question: str, replies: dict[str, str] | None = None
    ) -> dict[str, object]:
        if self._index is None:
            self._index = Index.load()
        return service.ask(question.strip(), self._index, replies or None).to_dict()

    def get_profile(self) -> dict[str, str]:
        out = {}
        for k, v in profile.load().items():
            out[k] = v.isoformat() if isinstance(v, dt.date) else str(v)
        return out

    def set_profile(self, key: str, value: str) -> dict[str, str]:
        if key in profile.FIELDS:
            profile.set_value(key, value)
        return self.get_profile()


def main() -> None:
    import webview  # the desktop extra; the CLI does not need it

    updates.refresh_all()  # quiet, once a day, keeps the shipped copy on any failure
    if not runtime.missing():
        runtime.start()  # bundled llama.cpp; otherwise settings stay on Ollama
    webview.create_window(
        "Visa Research",
        url=str(UI),
        js_api=Api(),
        width=1000,
        height=840,
        min_size=(720, 560),
    )
    webview.start()


if __name__ == "__main__":
    main()
