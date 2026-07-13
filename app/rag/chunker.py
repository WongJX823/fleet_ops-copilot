"""Split markdown SOPs into retrievable passages.

Chunks on headings so each passage stays a coherent procedure section; long
sections are further split on paragraph boundaries.
"""
import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 900


@dataclass
class Chunk:
    doc: str      # source filename, e.g. "vehicle_breakdown.md"
    heading: str  # nearest heading path, e.g. "SOP-01 ... > Dispatch steps"
    text: str


def chunk_markdown(doc_name: str, text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    title = ""
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        label = " > ".join(p for p in (title, heading) if p)
        for piece in _split_long(body):
            chunks.append(Chunk(doc=doc_name, heading=label, text=piece))

    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            if len(m.group(1)) == 1:
                title = m.group(2).strip()
                heading = ""
            else:
                heading = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    return chunks


def _split_long(body: str) -> list[str]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    pieces, current = [], ""
    for para in body.split("\n\n"):
        if current and len(current) + len(para) > MAX_CHUNK_CHARS:
            pieces.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        pieces.append(current.strip())
    return pieces
