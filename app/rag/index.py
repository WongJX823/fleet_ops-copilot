"""In-memory vector index over SOP chunks, cached to disk.

Prepare phase: embed every chunk once (OpenAI embeddings) and cache the
resulting matrix under runtime/, keyed by a fingerprint of the embed model
and every chunk's content. Later startups reuse the cache instead of
re-embedding — the SOPs only change when someone edits them, not every time
the process restarts.

Runtime: embed the question, cosine similarity, return top-k chunks.

Without an API key it degrades to keyword-overlap scoring so the whole
pipeline still runs offline (demo/tests) — no cache is used in that mode.
"""
import hashlib
import math
import re
import zipfile
from pathlib import Path

import numpy as np

from ..config import OPENAI_API_KEY, OPENAI_EMBED_MODEL, RUNTIME_DIR
from .chunker import Chunk

CACHE_PATH = RUNTIME_DIR / "sop_index_cache.npz"


class SopIndex:
    def __init__(self, chunks: list[Chunk], cache_path: Path = CACHE_PATH):
        self.chunks = chunks
        self._matrix: np.ndarray | None = None
        self._client = None
        if OPENAI_API_KEY:
            from openai import OpenAI

            self._client = OpenAI()
            fingerprint = _fingerprint(chunks, OPENAI_EMBED_MODEL)
            self._matrix = _load_cache(cache_path, fingerprint, len(chunks))
            if self._matrix is None:
                self._matrix = self._embed([c.text for c in chunks])
                _save_cache(cache_path, fingerprint, self._matrix)

    @property
    def mode(self) -> str:
        return "embeddings" if self._matrix is not None else "keyword"

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        if self._matrix is not None:
            q = self._embed([query])[0]
            scores = self._matrix @ q
        else:
            scores = np.array([_keyword_score(query, c.text + " " + c.heading) for c in self.chunks])
        order = np.argsort(scores)[::-1][:k]
        return [(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]

    def _embed(self, texts: list[str]) -> np.ndarray:
        resp = self._client.embeddings.create(model=OPENAI_EMBED_MODEL, input=texts)
        mat = np.array([d.embedding for d in resp.data], dtype=np.float32)
        return mat / np.linalg.norm(mat, axis=1, keepdims=True)


def _fingerprint(chunks: list[Chunk], model: str) -> str:
    """Hash of the embed model + every chunk's content, so the cache is
    invalidated whenever the SOPs are edited or the model changes."""
    h = hashlib.sha256(model.encode("utf-8"))
    for c in chunks:
        h.update(c.doc.encode("utf-8"))
        h.update(c.heading.encode("utf-8"))
        h.update(c.text.encode("utf-8"))
    return h.hexdigest()


def _load_cache(path: Path, fingerprint: str, n_chunks: int) -> np.ndarray | None:
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            if str(data["fingerprint"][0]) != fingerprint:
                return None
            matrix = data["matrix"]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    if matrix.shape[0] != n_chunks:
        return None
    return matrix


def _save_cache(path: Path, fingerprint: str, matrix: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, matrix=matrix, fingerprint=np.array([fingerprint]))


_WORD = re.compile(r"[a-z]{3,}")
_STOP = {"the", "and", "for", "with", "what", "how", "should", "when", "which", "this", "that"}


def _keyword_score(query: str, text: str) -> float:
    q = {w for w in _WORD.findall(query.lower())} - _STOP
    if not q:
        return 0.0
    t = text.lower()
    hits = sum(1 for w in q if w in t)
    return hits / math.sqrt(len(q))
