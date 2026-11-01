"""Procedural, royalty-free sound effects (CC0, generated locally) and mixing them under renders.

Same approach as CutAgent's tools/generate_sound_pack.py: seeded noise and oscillators, no downloads.
"""

import math
import random
import struct
import subprocess
import wave
from pathlib import Path

from . import config

RATE = 44100

# name -> length in seconds
SOUNDS = {
    "whoosh": 0.6, "whip": 0.28, "pop": 0.18, "key": 0.05, "key2": 0.05, "impact": 0.8,
    "riser": 1.2, "shutter": 0.35, "ding": 0.9, "swoosh_up": 0.45, "tick": 0.04, "boom": 1.4, "typing": 2.6,
    "bat_crack": 0.42, "crowd_cheer": 2.2, "click": 0.07, "soft_click": 0.09, "tap": 0.12,
}


def _synth(name: str, seconds: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    n = round(seconds * RATE)
    out, low, phase, phase2 = [], 0.0, 0.0, 0.0
    for i in range(n):
        t, x = i / RATE, i / max(1, n - 1)
        noise = rng.uniform(-1, 1)
        low += (noise - low) * (0.03 + 0.32 * math.sin(math.pi * x) ** 2)
        if name == "whoosh":
            s = (noise - low) * math.sin(math.pi * x) ** 2 * (0.45 + 0.55 * x)
        elif name == "whip":
            phase += 2 * math.pi * (220 + 2400 * (1 - x) ** 3) / RATE
            s = (0.7 * (noise - low) + 0.25 * math.sin(phase)) * math.sin(math.pi * x) ** 1.8
        elif name == "swoosh_up":
            phase += 2 * math.pi * (300 + 2200 * x ** 2) / RATE
            s = ((noise - low) * 0.6 + 0.3 * math.sin(phase)) * math.sin(math.pi * x) ** 1.5
        elif name == "pop":
            phase += 2 * math.pi * (130 + 650 * math.exp(-35 * t)) / RATE
            s = math.sin(phase) * math.exp(-28 * t) + noise * 0.08 * math.exp(-75 * t)
        elif name in ("key", "key2"):  # mechanical key: click transient + short body
            f = 1900 if name == "key" else 2400
            s = (noise * 0.7 + math.sin(2 * math.pi * f * t) * 0.4) * math.exp(-140 * t) + math.sin(2 * math.pi * 180 * t) * 0.25 * math.exp(-60 * t)
        elif name == "typing":  # irregular key strokes, ~11 per second
            k = int(t * 11.3)
            onset = (k + 0.35 * math.sin(k * 12.9898) ** 2) / 11.3
            dt = t - onset
            f = 1900 + 500 * (k % 3)
            s = 0.0 if dt < 0 else (rng.uniform(-1, 1) * 0.6 + math.sin(2 * math.pi * f * dt) * 0.35) * math.exp(-140 * dt)
        elif name == "click":  # crisp UI click: bright transient over a short plastic body
            s = (noise * 0.35 * math.exp(-420 * t) + math.sin(2 * math.pi * 2600 * t) * 0.75 * math.exp(-210 * t)
                 + math.sin(2 * math.pi * 950 * t) * 0.35 * math.exp(-120 * t))
        elif name == "soft_click":  # the same click, rounded off, for passing changes
            s = math.sin(2 * math.pi * 1250 * t) * 0.8 * math.exp(-130 * t) + math.sin(2 * math.pi * 420 * t) * 0.35 * math.exp(-85 * t)
        elif name == "tap":  # a fingertip on glass: low knock with a little top
            s = math.sin(2 * math.pi * 310 * t) * math.exp(-60 * t) + noise * 0.16 * math.exp(-300 * t)
        elif name == "tick":
            s = (noise * 0.4 + math.sin(2 * math.pi * 3000 * t) * 0.6) * math.exp(-180 * t)
        elif name == "impact":
            phase += 2 * math.pi * (48 + 100 * math.exp(-25 * t)) / RATE
            s = math.sin(phase) * math.exp(-7 * t) + noise * 0.3 * math.exp(-40 * t)
        elif name == "boom":
            phase += 2 * math.pi * (38 + 70 * math.exp(-12 * t)) / RATE
            s = math.sin(phase) * math.exp(-3.2 * t) + low * 0.6 * math.exp(-6 * t)
        elif name == "shutter":  # two mechanical clacks
            s = noise * (math.exp(-90 * t) + (0.8 * math.exp(-90 * (t - 0.12)) if t > 0.12 else 0))
        elif name == "bat_crack":  # hard wooden knock: bright transient plus two short body modes
            s = (noise * math.exp(-260 * t) + math.sin(2 * math.pi * 820 * t) * 0.7 * math.exp(-38 * t)
                 + math.sin(2 * math.pi * 1530 * t) * 0.4 * math.exp(-55 * t))
        elif name == "crowd_cheer":  # swelling wash of band-limited noise with slow chatter
            swell = math.sin(math.pi * x) ** 0.7
            s = (noise - low) * swell * (0.75 + 0.25 * math.sin(2 * math.pi * 5.3 * t) * math.sin(2 * math.pi * 3.1 * t))
        elif name == "ding":
            s = sum(math.sin(2 * math.pi * f * t) * a for f, a in ((1320, 0.6), (2640, 0.25), (3960, 0.1))) * math.exp(-4 * t)
        else:  # riser
            phase += 2 * math.pi * (180 + 1800 * x ** 2) / RATE
            s = ((noise - low) * 0.65 + math.sin(phase) * 0.2) * x ** 1.5
        fade = min(1.0, t / 0.003, (n - 1 - i) / (RATE * 0.015))
        out.append(s * fade)
    mean = sum(out) / n
    peak = max(abs(v - mean) for v in out) or 1.0
    return [(v - mean) / peak * 0.8 for v in out]


def library() -> Path:
    """Generate the pack once into data/sfx and return its directory."""
    dest = config.DATA / "sfx"
    dest.mkdir(parents=True, exist_ok=True)
    for index, (name, seconds) in enumerate(SOUNDS.items()):
        path = dest / f"{name}.wav"
        if path.exists():
            continue
        samples = _synth(name, seconds, 20261026 + index)
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", round(s * 32767)) for s in samples))
        tmp.replace(path)
    return dest


Cue = tuple[float, str, float]  # (time in seconds, sound name, gain)


# Which sound wins when two land close together: payoffs first, then the small clicks that mark an arrival.
_PRIORITY = ("impact", "boom", "ding", "crowd_cheer", "bat_crack", "shutter", "click", "pop", "tap", "soft_click", "typing", "tick", "key", "key2")
_RUSHING = {"whoosh", "whip", "swoosh_up", "riser"}


def calm(cues: list[Cue], gap: float) -> list[Cue]:
    """An edit needs far fewer sounds than it has moments. Keeps the strongest cue in every `gap` (same time unit as
    the cues), and turns rushing air into a quiet click. Order of the result is by time."""
    softened = [(t, "soft_click", round(g * 0.7, 3)) if name in _RUSHING else (t, name, g) for t, name, g in cues]
    rank = {name: i for i, name in enumerate(_PRIORITY)}
    kept: list[Cue] = []
    for cue in sorted(softened, key=lambda c: (rank.get(c[1], len(rank)), c[0])):
        if all(abs(cue[0] - other[0]) >= gap for other in kept):
            kept.append(cue)
    return sorted(kept)


def pulse(seconds: float, dest: Path) -> Path:
    """A low, steady documentary pulse (procedural, royalty-free): soft kick at 84 bpm over a dark drone."""
    rate, beat = 48000, 60 / 84
    frames = bytearray()
    for i in range(int(seconds * rate)):
        t = i / rate
        tb = t % beat
        kick = math.exp(-tb * 9) * math.sin(2 * math.pi * (52 + 60 * math.exp(-tb * 30)) * tb)
        tick = 0.12 * math.exp(-((t + beat / 2) % beat) * 40) * math.sin(2 * math.pi * 1800 * t)
        drone = 0.22 * math.sin(2 * math.pi * 55 * t) + 0.12 * math.sin(2 * math.pi * 82.4 * t + 0.6 * math.sin(2 * math.pi * 0.07 * t))
        swell = 0.75 + 0.25 * math.sin(2 * math.pi * t / 16)
        fade = min(1.0, t / 2.0, (seconds - t) / 2.5)
        value = int(max(-1.0, min(1.0, (0.55 * kick + tick + drone * swell) * 0.5 * fade)) * 32767)
        frames += struct.pack("<hh", value, value)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return dest


def bed(cues: list[Cue], duration: float, dest: Path) -> Path:
    """Mix many cues into one WAV the length of the edit (used when the app's audio-item limit is hit)."""
    sfx_dir = library()
    cues = [c for c in cues if 0 <= c[0] < duration and c[1] in SOUNDS][:120]
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [config.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    for _, name, _ in cues:
        cmd += ["-i", str(sfx_dir / f"{name}.wav")]
    parts = [f"[{i + 1}:a]adelay={round(t * 1000)}:all=1,volume={g}[a{i}]" for i, (t, _, g) in enumerate(cues)]
    mix = "[0:a]" + "".join(f"[a{i}]" for i in range(len(cues)))
    graph = ";".join(parts + [f"{mix}amix=inputs={len(cues) + 1}:normalize=0,atrim=0:{duration:.3f}[out]"])
    subprocess.run(cmd + ["-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-ac", "2", str(dest)],
                   check=True, capture_output=True)
    return dest


def finish(video: Path, cues: list[Cue], duration: float, dest: Path) -> None:
    """Re-encode a render for smooth phone scrubbing (1 s GOP, faststart) and lay sound effects under it."""
    sfx_dir = library()
    cues = [c for c in cues if 0 <= c[0] < duration and c[1] in SOUNDS][:60]
    cmd = [config.tool("ffmpeg"), "-v", "error", "-y", "-i", str(video)]
    for _, name, _ in cues:
        cmd += ["-i", str(sfx_dir / f"{name}.wav")]
    video_opts = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if cues:
        parts = [f"[{i + 1}:a]adelay={round(t * 1000)}|{round(t * 1000)},volume={g}[a{i}]" for i, (t, _, g) in enumerate(cues)]
        mix = "".join(f"[a{i}]" for i in range(len(cues)))
        graph = ";".join(parts) + f";{mix}amix=inputs={len(cues)}:normalize=0,apad,atrim=0:{duration:.3f},aformat=channel_layouts=stereo[aout]"
        cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]", *video_opts, "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    else:
        cmd += ["-map", "0:v", *video_opts, "-an"]
    cmd += ["-t", f"{duration:.3f}", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
