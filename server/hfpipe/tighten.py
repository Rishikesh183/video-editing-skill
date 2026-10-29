"""Optional tightening of a talking-head take before planning: long silences are shortened (a breath is
kept on each side) and restarted sentences are dropped, then the captions are moved onto the new timeline.

Silences come from the audio itself (ffmpeg silencedetect), not caption gaps: the captions are translated
phrases whose timings are estimates. A retake is a caption whose opening words repeat the previous spoken
caption's opening (the first attempt is cut). Nothing is cut inside speech.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import tool
from .models import Cue

FILLER_ONLY = re.compile(r"^[\s,.-]*((um+|uh+|er+|ah+|so|okay|ok|right|yeah|like|anyway|basically|actually|"
                         r"you know|i mean)[\s,.-]*)+$", re.I)


@dataclass
class Cut:
    start: float
    end: float
    why: str


def silences(video: Path, noise_db: float = -35.0, min_len: float = 0.6) -> list[tuple[float, float]]:
    out = subprocess.run([tool("ffmpeg"), "-hide_banner", "-i", str(video), "-af",
                          f"silencedetect=noise={noise_db}dB:d={min_len}", "-vn", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    found, start = [], None
    for line in out.splitlines():
        if (m := re.search(r"silence_start: ([\d.]+)", line)):
            start = float(m.group(1))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and start is not None:
            found.append((start, float(m.group(1))))
            start = None
    return found


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z ]", "", text.lower()).split()


def plan_cuts(gaps: list[tuple[float, float]], captions: list[Cue], keep: float = 0.25,
              retakes: bool = True) -> list[Cut]:
    """Cuts: the middle of each long silence (keeping `keep` s of breath on both sides), filler-only
    captions, and the first attempt of a restarted sentence."""
    cuts = [Cut(a + keep, b - keep, f"{b - a:.1f}s pause") for a, b in gaps if b - a > 2 * keep + 0.15]
    spoken = [c for c in captions if c.text.strip() and not FILLER_ONLY.match(c.text)]
    cuts += [Cut(c.start, c.end, f"filler: {c.text.strip()[:30]}") for c in captions if FILLER_ONLY.match(c.text)]
    if retakes:
        for prev, cur in zip(spoken, spoken[1:]):
            a, b = _words(prev.text)[:4], _words(cur.text)[:4]
            if len(a) >= 4 and a == b and cur.start - prev.end < 3.0:
                cuts.append(Cut(prev.start, cur.start, f"retake: {' '.join(a)}"))
    cuts.sort(key=lambda c: c.start)
    merged: list[Cut] = []
    for c in cuts:
        if merged and c.start <= merged[-1].end:
            merged[-1] = Cut(merged[-1].start, max(merged[-1].end, c.end), merged[-1].why)
        else:
            merged.append(c)
    return [c for c in merged if c.end - c.start >= 0.1]


def remap(t: float, cuts: list[Cut]) -> float:
    """A time on the original take -> the same moment on the tightened take (inside a cut: its start)."""
    shift = 0.0
    for c in cuts:
        if t >= c.end:
            shift += c.end - c.start
        elif t > c.start:
            return c.start - shift
    return t - shift


def remap_captions(captions: list[Cue], cuts: list[Cut]) -> list[Cue]:
    out = []
    for c in captions:
        start, end = remap(c.start, cuts), remap(c.end, cuts)
        if end - start >= 0.2:
            out.append(Cue(start=round(start, 3), end=round(end, 3), text=c.text))
    return out


def render(video: Path, cuts: list[Cut], duration: float, dest: Path) -> Path:
    """Keep everything between the cuts, re-encoded with the same edit-friendly settings as normalize."""
    keeps, cursor = [], 0.0
    for c in cuts:
        if c.start > cursor:
            keeps.append((cursor, c.start))
        cursor = c.end
    if duration > cursor:
        keeps.append((cursor, duration))
    parts = []
    for i, (a, b) in enumerate(keeps):
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    joined = "".join(f"[v{i}][a{i}]" for i in range(len(keeps)))
    graph = ";".join(parts) + f";{joined}concat=n={len(keeps)}:v=1:a=1[v][a]"
    tmp = dest.with_suffix(".partial.mp4")
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(video), "-filter_complex", graph, "-map", "[v]",
                    "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                    str(tmp)], check=True, capture_output=True)
    tmp.replace(dest)
    return dest
