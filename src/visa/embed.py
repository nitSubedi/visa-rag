"""Embeddings via local Ollama. Never leaves the machine."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import numpy as np

from . import config

# nomic-embed-text is trained with task prefixes; using them materially improves
# asymmetric (short query -> long passage) retrieval.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def _post(path: str, payload: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        f"{config.OLLAMA}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def embed(texts: list[str], *, is_query: bool = False, batch: int = 64,
          model: str | None = None, progress=lambda *_: None) -> np.ndarray:
    model = model or config.EMBED_MODEL
    pre = QUERY_PREFIX if is_query else DOC_PREFIX
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = [pre + t for t in texts[i:i + batch]]
        try:
            d = _post("/api/embed", {"model": model, "input": part})
            out.extend(d["embeddings"])
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Ollama embedding failed ({e.code}). Is `ollama serve` running and "
                f"`{model}` pulled?  Try: ollama pull {model}"
            ) from e
        progress(min(i + batch, len(texts)), len(texts))
    a = np.asarray(out, dtype=np.float32)
    n = np.linalg.norm(a, axis=1, keepdims=True)
    return a / np.clip(n, 1e-9, None)      # unit-normalised: cosine == dot product


def embed_query(text: str) -> np.ndarray:
    return embed([text], is_query=True)[0]
