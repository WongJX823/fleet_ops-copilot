"""Generate synthetic image/video attachments for testing the chat UI's
attachment path — no real incident photos needed.

There's no real fleet to photograph for this demo, so this draws a plausible
dashboard warning-light image and a short "engine smoke + hazard lights"
clip with OpenCV primitives. Not photorealistic, but real enough that the
vision model reads the on-screen text and the scene composition, which is
what actually exercises the attachment pipeline (upload -> [video: frame
sampling] -> base64 -> vision API -> grounded SOP answer).

Usage:
    python tools/generate_demo_attachments.py
    # then attach demo_assets/fault_image.jpg or fault_video.mp4 in the chat UI

Requires opencv-python-headless (optional dependency, same one used for
video-attachment frame extraction — see requirements.txt).
"""
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:
    sys.exit("Requires opencv-python-headless: pip install opencv-python-headless")

OUT_DIR = Path(__file__).resolve().parent.parent / "demo_assets"
W, H = 480, 320


def make_fault_image(path: Path) -> None:
    img = np.full((H, W, 3), (40, 40, 40), np.uint8)  # dark dashboard background
    cv2.rectangle(img, (0, H - 60), (W, H), (20, 20, 20), -1)  # dash trim
    pts = np.array([[W // 2, 60], [W // 2 - 70, 190], [W // 2 + 70, 190]], np.int32)
    cv2.fillPoly(img, [pts], (0, 0, 220))  # red warning triangle (BGR)
    cv2.putText(img, "!", (W // 2 - 12, 165), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 5)
    cv2.putText(img, "CHECK ENGINE", (60, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(img, "COOLANT TEMP HIGH", (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 140, 255), 2)
    ok, jpg = cv2.imencode(".jpg", img)
    path.write_bytes(jpg.tobytes())


def make_fault_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (W, H))
    for i in range(30):
        frame = np.full((H, W, 3), (60, 90, 60), np.uint8)  # roadside background
        cv2.rectangle(frame, (80, 140), (400, 240), (30, 30, 30), -1)  # vehicle body
        cv2.rectangle(frame, (60, 160), (90, 220), (10, 10, 10), -1)  # hood area
        overlay = frame.copy()
        cv2.circle(overlay, (75, 150), int(10 + i * 2.2), (200, 200, 200), -1)  # growing smoke
        frame = cv2.addWeighted(overlay, 0.5, frame, 0.5, 0)
        if i > 20:  # hazard lights flashing near the end of the clip
            color = (0, 0, 255) if i % 4 < 2 else (0, 165, 255)
            cv2.circle(frame, (395, 150), 8, color, -1)
            cv2.circle(frame, (85, 150), 8, color, -1)
        writer.write(frame)
    writer.release()


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    make_fault_image(OUT_DIR / "fault_image.jpg")
    make_fault_video(OUT_DIR / "fault_video.mp4")
    print(f"Wrote {OUT_DIR / 'fault_image.jpg'}")
    print(f"Wrote {OUT_DIR / 'fault_video.mp4'}")
    print("Attach either file in the chat UI (drag & drop or the paperclip button) to test the vision path.")


if __name__ == "__main__":
    main()
