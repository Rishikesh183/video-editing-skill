"""ffprobe/ffmpeg helpers."""

import hashlib
import json
import subprocess
from pathlib import Path

from .config import tool
from .models import MediaInfo

_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg",
         ".wav": "audio/wav", ".m4a": "audio/mp4"}


def probe(path: Path) -> MediaInfo:
    out = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    mime = _MIME.get(path.suffix.lower(), "video/mp4")
    is_image = mime.startswith("image/")
    duration = float(info.get("format", {}).get("duration") or 0)
    if video and not is_image and float(video.get("duration") or 0) > 0:
        # A clip ends where its pictures end. The container can be longer (audio padded past the last
        # frame); asking the editor for video beyond the last frame stalls its exporter.
        duration = min(duration or float(video["duration"]), float(video["duration"]))
    rotation = 0
    fps = 30.0
    if video:
        # Android reports clockwise display rotation (e.g. 90); ffprobe's display matrix
        # gives the counter-clockwise angle (e.g. -90), the legacy tag the Android value.
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(-float(side["rotation"])) % 360
        if "rotate" in video.get("tags", {}):
            rotation = int(video["tags"]["rotate"]) % 360
        num, _, den = (video.get("avg_frame_rate") or "30/1").partition("/")
        if float(den or 1) and float(num or 0):
            fps = float(num) / float(den or 1)
    return MediaInfo(
        file=path.name,
        mime=mime,
        # Stills follow the app's import convention of a nominal five-second duration.
        duration_us=5_000_000 if is_image else int(duration * 1_000_000),
        width=int(video["width"]) if video else 0,
        height=int(video["height"]) if video else 0,
        rotation=rotation,
        has_audio=has_audio and not is_image,
        fps=fps,
    )


def normalize(src: Path, dest: Path) -> Path:
    """Upright (rotation baked in), constant 30 fps, 1 s keyframes, short side ≤ 1080.

    Phones store portrait video sideways with a rotation flag; baking it in avoids per-clip
    rotation handling in the editor, and dense keyframes make cuts and scrubbing smooth.
    """
    scale = "scale='if(gte(iw,ih),-2,min(1080,iw))':'if(gte(iw,ih),min(1080,ih),-2)'"
    tmp = dest.with_suffix(".partial.mp4")
    subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-vf", f"{scale},fps=30,setsar=1",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "30", "-pix_fmt", "yuv420p",
         # Voice at YouTube's loudness target (-14 LUFS, -1.5 dBTP) so it sits above the sound effects.
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(tmp)],
        check=True, capture_output=True,
    )
    tmp.replace(dest)
    return match_audio_length(dest)


def _durations(path: Path) -> dict[str, float]:
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    found: dict[str, float] = {}
    for line in out.splitlines():
        kind, _, value = line.partition(",")
        try:
            found.setdefault(kind, float(value))
        except ValueError:
            pass
    return found


def match_audio_length(path: Path) -> Path:
    """Pad the audio with silence to at least the video's length. Phone recordings often end their audio a
    few ms early; the editor plays the voice as one item for the full video length, and Media3's exporter
    then waits forever for audio that does not exist (the export hangs near the end)."""
    d = _durations(path)
    if "audio" not in d or "video" not in d or d["audio"] >= d["video"] - 0.0005:
        return path
    tmp = path.with_suffix(".padded.mp4")
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                    "-af", "apad", "-t", f"{d['video'] + 0.03:.3f}", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", str(tmp)], check=True, capture_output=True)
    tmp.replace(path)
    return path


def to_png(src: Path, dest: Path, max_side: int = 1600) -> Path:
    """Normalise an uploaded photo to a bounded PNG. Reads the bytes, not the file name, so jpg/png/webp/
    avif/heic and extension-less downloads all work; phone EXIF rotation is applied."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageOps

        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            im.thumbnail((max_side, max_side))
            im.save(dest, "PNG")
        return dest
    except Exception:  # noqa: BLE001 - fall back to ffmpeg for anything Pillow cannot open
        pass
    scale = f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease"
    subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-vf", scale, "-frames:v", "1", str(dest)],
        check=True,
    )
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
