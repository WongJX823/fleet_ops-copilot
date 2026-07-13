"""In-memory vector index over SOP chunks.

Prepare phase: embed every chunk once at startup (OpenAI embeddings).
Runtime: embed the question, cosine similarity, return top-k chunks.

Without an API key it degrades to keyword-overlap scoring so the whole
pipeline still runs offline (demo/tests).
"""
import math
import re

import numpy as np

from ..config import OPENAI_API_KEY, OPENAI_EMBED_MODEL
from .chunker import Chunk


class SopIndex:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._matrix: np.ndarray | None = None
        self._client = None
        if OPENAI_API_KEY:
            from openai import OpenAI

            self._client = OpenAI()
            self._matrix = self._embed([c.text for c in chunks])

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


_WORD = re.compile(r"[a-z]{3,}")
_STOP = {"the", "and", "for", "with", "what", "how", "should", "when", "which", "this", "that"}


def _keyword_score(query: str, text: str) -> float:
    q = {w for w in _WORD.findall(query.lower())} - _STOP
    if not q:
        return 0.0
    t = text.lower()
    hits = sum(1 for w in q if w in t)
    return hits / math.sqrt(len(q))
