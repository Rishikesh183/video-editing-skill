"""Minimal SRT parser for the CutAgent caption download."""

import re

from .models import Cue

_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return round(int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000, 3)


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip().lstrip("﻿")):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        for i, line in enumerate(lines):
            match = _TIME.search(line)
            if match:
                g = match.groups()
                body = " ".join(lines[i + 1 :])
                if body:
                    cues.append(Cue(start=_secs(*g[:4]), end=_secs(*g[4:]), text=body))
                break
    return sorted(cues, key=lambda c: c.start)


def to_srt(cues: list[Cue]) -> str:
    def fmt(t: float) -> str:
        ms = round(t * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "\n".join(f"{i}\n{fmt(c.start)} --> {fmt(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1))
