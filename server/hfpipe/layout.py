"""Where the creator's face is, so text goes around it (or behind the head on purpose).

The upright source is sampled once per second and run through OpenCV's frontal-face detector; the result is
cached next to the job. Boxes are in source-frame fractions (x0, y0, x1, y1) and mapped to the canvas on use.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from . import config

Box = tuple[float, float, float, float]
VERSION = 1


def _detector():
    import cv2

    return cv2, cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))


def track(video: Path, duration_s: float, cache: Path) -> list[tuple[float, Box]]:
    """[(time, face box)] for frames where one face was found. Cached as JSON in `cache`."""
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("version") == VERSION and data.get("video") == video.name:
            return [(t, tuple(b)) for t, b in data["faces"]]
    cv2, detector = _detector()
    faces: list[tuple[float, Box]] = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([config.tool("ffmpeg"), "-v", "error", "-i", str(video), "-vf", "fps=1,scale=480:-2",
                        str(Path(tmp) / "f%04d.png")], check=True, capture_output=True)
        for frame in sorted(Path(tmp).glob("f*.png")):
            t = int(frame.stem[1:]) - 1 + 0.5
            if t > duration_s:
                break
            img = cv2.imread(str(frame), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            h, w = img.shape
            found = detector.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(w // 10, w // 10))
            if len(found):
                x, y, fw, fh = max(found, key=lambda f: f[2] * f[3])  # the biggest face is the creator
                faces.append((t, (x / w, y / h, (x + fw) / w, (y + fh) / h)))
    cache.write_text(json.dumps({"version": VERSION, "video": video.name, "faces": faces}), encoding="utf-8")
    return faces


def face_in(faces: list[tuple[float, Box]], start_s: float, end_s: float) -> Box | None:
    """Union of the face boxes seen during a span (falls back to the nearest sample)."""
    hits = [b for t, b in faces if start_s - 0.5 <= t <= end_s + 0.5]
    if not hits and faces:
        hits = [min(faces, key=lambda f: abs(f[0] - (start_s + end_s) / 2))[1]]
    if not hits:
        return None
    return (min(b[0] for b in hits), min(b[1] for b in hits), max(b[2] for b in hits), max(b[3] for b in hits))


def to_canvas(box: Box, source_aspect: float, canvas_aspect: float, cover: bool) -> Box:
    """Source-fraction box -> canvas-fraction box for a centred fit (cover crops, contain letterboxes)."""
    sx = sy = 1.0
    ratio = source_aspect / canvas_aspect
    if (ratio > 1) == cover:  # source is wider than the canvas: its width spills or shrinks
        sx = ratio
    else:
        sy = 1 / ratio
    x0, y0, x1, y1 = box
    return (0.5 + (x0 - 0.5) * sx, 0.5 + (y0 - 0.5) * sy, 0.5 + (x1 - 0.5) * sx, 0.5 + (y1 - 0.5) * sy)
