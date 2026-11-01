import subprocess
from pathlib import Path

import pytest

from hfpipe import config, usage


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "LIBRARY", tmp_path / "library")
    monkeypatch.setattr(config, "USAGE_DB", tmp_path / "usage.db")
    monkeypatch.setattr(config, "RENDER_CACHE", tmp_path / "render-cache")
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(usage, "reading", lambda: None)  # would call Claude
    return tmp_path


def make_video(path: Path, seconds: float, w: int, h: int, audio: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [config.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def make_png(path: Path, w: int = 400, h: int = 500) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=orange:size={w}x{h}",
                    "-frames:v", "1", str(path)], check=True)
    return path
