"""The bundled model runtime: llama.cpp's server, started and stopped by the app.

The desktop app ships everything — no Ollama to install. It carries llama.cpp's server
binary and two model files, laid out as

    <runtime_dir>/bin/llama-server[.exe]   (plus its libraries, as released)
    <runtime_dir>/models/chat.gguf          qwen3-4b-instruct-2507, Q4_K_M
    <runtime_dir>/models/embed.gguf         nomic-embed-text v1.5

and starts one server for each on a free localhost port, one slot each (a laptop has one
user; parallel slots only cost memory). Measured against Ollama on the same files: chat
output identical word for word, embeddings at cosine 1.000000 — so the app answers
exactly as the evals measured.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import settings


@dataclass
class Runtime:
    chat: subprocess.Popen[bytes]
    embed: subprocess.Popen[bytes]

    def stop(self) -> None:
        for p in (self.chat, self.embed):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()


def runtime_dir() -> Path:
    """Inside a packaged app the files sit beside the code (PyInstaller's _MEIPASS);
    otherwise VISA_RUNTIME_DIR points at them."""
    if env := os.environ.get("VISA_RUNTIME_DIR"):
        return Path(env)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "runtime"


def _binary(root: Path) -> Path:
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    return root / "bin" / name


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(url: str, proc: subprocess.Popen[bytes], timeout: float) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"model server exited (code {proc.returncode})")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if b"ok" in r.read():
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError("model server did not become ready in time")


def missing(root: Path | None = None) -> list[str]:
    """Files the runtime needs that are not there."""
    root = root or runtime_dir()
    need = [_binary(root), root / "models" / "chat.gguf", root / "models" / "embed.gguf"]
    return [str(p) for p in need if not p.exists()]


def start(root: Path | None = None, timeout: float = 180) -> Runtime:
    """Start both servers and point this process at them. Stopped at exit."""
    root = root or runtime_dir()
    if gone := missing(root):
        raise FileNotFoundError(f"bundled runtime incomplete: {gone}")
    exe = str(_binary(root))
    common = ["--host", "127.0.0.1", "--parallel", "1", "-ngl", "99"]
    chat_port, embed_port = _free_port(), _free_port()
    log = subprocess.DEVNULL
    chat = subprocess.Popen(
        [
            exe,
            "-m",
            str(root / "models" / "chat.gguf"),
            "--port",
            str(chat_port),
            "-c",
            str(settings.num_ctx),
            "--jinja",
            *common,
        ],
        stdout=log,
        stderr=log,
    )
    embed = subprocess.Popen(
        [
            exe,
            "-m",
            str(root / "models" / "embed.gguf"),
            "--port",
            str(embed_port),
            "--embedding",
            "-c",
            "8192",
            "-ub",
            "8192",
            *common,
        ],
        stdout=log,
        stderr=log,
    )
    rt = Runtime(chat, embed)
    atexit.register(rt.stop)
    settings.llamacpp_chat_url = f"http://127.0.0.1:{chat_port}"
    settings.llamacpp_embed_url = f"http://127.0.0.1:{embed_port}"
    try:
        _wait(settings.llamacpp_chat_url, chat, timeout)
        _wait(settings.llamacpp_embed_url, embed, timeout)
    except Exception:
        rt.stop()
        raise
    settings.backend = "llamacpp"
    return rt
