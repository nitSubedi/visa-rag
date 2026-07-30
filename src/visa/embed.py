"""Embeddings via local Ollama. Never leaves the machine."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from .config import settings


# nomic-embed-text is trained with task prefixes; using them materially improves
# asymmetric (short query -> long passage) retrieval.
def _noop(*_: object) -> None:
    return None


DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def _post(path: str, payload: dict[str, object], timeout: int = 300) -> dict[str, object]:
    req = urllib.request.Request(
        f"{settings.ollama_host}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data: dict[str, object] = json.load(r)
    return data


def embed(
    texts: list[str],
    *,
    is_query: bool = False,
    batch: int = 64,
    model: str | None = None,
    progress: Callable[[int, int], None] = _noop,
) -> npt.NDArray[np.float32]:
    model = model or settings.embed_model
    pre = QUERY_PREFIX if is_query else DOC_PREFIX
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = [pre + t for t in texts[i : i + batch]]
        try:
            d = _post("/api/embed", {"model": model, "input": part})
            embeddings = d["embeddings"]
            assert isinstance(embeddings, list)
            out.extend(embeddings)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Ollama embedding failed ({e.code}). Is `ollama serve` running and "
                f"`{model}` pulled?  Try: ollama pull {model}"
            ) from e
        progress(min(i + batch, len(texts)), len(texts))
    a = np.asarray(out, dtype=np.float32)
    n = np.linalg.norm(a, axis=1, keepdims=True)
    normed: npt.NDArray[np.float32] = a / np.clip(n, 1e-9, None)
    return normed  # unit-normalised: cosine == dot product


def embed_query(text: str) -> npt.NDArray[np.float32]:
    vec: npt.NDArray[np.float32] = embed([text], is_query=True)[0]
    return vec
