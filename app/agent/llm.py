"""OpenAI chat wrapper with vision input and an offline stub fallback.

Images are passed as data URLs. Videos are sampled into a few frames via
OpenCV when available (optional dependency); otherwise they are skipped with
a note so the answer never silently ignores an attachment.
"""
import base64

from ..config import MAX_VIDEO_FRAMES, OPENAI_API_KEY, OPENAI_CHAT_MODEL
from .prompts import SYSTEM_PROMPT


class LLMClient:
    def __init__(self) -> None:
        self.model = OPENAI_CHAT_MODEL if OPENAI_API_KEY else "stub"
        self._client = None
        if OPENAI_API_KEY:
            from openai import OpenAI

            self._client = OpenAI()

    def answer(self, packed_prompt: str, images: list[tuple[str, bytes]]) -> str:
        """images: list of (mime_type, raw_bytes) already reduced to stills."""
        if self._client is None:
            return _stub_answer(packed_prompt, len(images))

        content: list[dict] = [{"type": "text", "text": packed_prompt}]
        for mime, raw in images:
            b64 = base64.b64encode(raw).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""


def extract_video_frames(raw: bytes, suffix: str) -> tuple[list[tuple[str, bytes]], str | None]:
    """Sample up to MAX_VIDEO_FRAMES stills from a video. Returns (frames, note)."""
    try:
        import cv2
    except ImportError:
        return [], "A video was attached but frame extraction is unavailable (install opencv-python-headless)."

    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(raw)
        tmp = Path(f.name)
    try:
        cap = cv2.VideoCapture(str(tmp))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if total <= 0:
            return [], "The attached video could not be decoded."
        frames: list[tuple[str, bytes]] = []
        for i in range(MAX_VIDEO_FRAMES):
            pos = int(total * (i + 0.5) / MAX_VIDEO_FRAMES)
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if not ok:
                continue
            ok, jpg = cv2.imencode(".jpg", frame)
            if ok:
                frames.append(("image/jpeg", jpg.tobytes()))
        cap.release()
        note = f"Video sampled into {len(frames)} frame(s) for analysis." if frames else None
        return frames, note
    finally:
        tmp.unlink(missing_ok=True)


def _stub_answer(packed_prompt: str, image_count: int) -> str:
    lines = [
        "(Stub LLM - set OPENAI_API_KEY for real answers.)",
        "I received your question and the following governed evidence was retrieved:",
    ]
    for ln in packed_prompt.splitlines():
        if '"source"' in ln or '"summary"' in ln:
            lines.append("  " + ln.strip().rstrip(","))
    if image_count:
        lines.append(f"  plus {image_count} attached image(s)/frame(s).")
    lines.append("A real model would now compose a grounded answer with sources and timestamps.")
    return "\n".join(lines)
