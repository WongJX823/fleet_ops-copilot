"""Prepare phase: read data/sops/*.md, chunk, and build the vector index."""
from ..config import SOP_DIR
from .chunker import Chunk, chunk_markdown
from .index import SopIndex


def build_index() -> SopIndex:
    chunks: list[Chunk] = []
    for path in sorted(SOP_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(path.name, path.read_text(encoding="utf-8")))
    return SopIndex(chunks)
