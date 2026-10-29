"""Sound-effect cues per template. Times mirror the GSAP timelines in templates/*/template.html,
so keep the constants here in step with the comments marked "Shared with sound cues" there."""

import json
from typing import Any

from .sfx import Cue


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else [parsed]
    except (TypeError, ValueError):
        return [p for p in str(value).split("|") if p.strip()]


def _ticks(start: float, end: float, every: float = 0.08, gain: float = 0.25) -> list[Cue]:
    out, t = [], start
    while t < end:
        out.append((round(t, 3), "tick", gain))
        t += every
    return out


def cues(template: str, v: dict[str, Any], dur: float) -> list[Cue]:
    entry: list[Cue] = [(0.0, "whoosh", 0.55)]
    if template == "player-spotlight":
        chips = len(_list(v.get("stat_lines")))
        return entry + [(0.55, "impact", 0.8)] + [(1.0 + 0.25 * i, "pop", 0.45) for i in range(min(3, chips))] + [(1.6, "swoosh_up", 0.25)]
    if template == "innings-compare":
        return entry + _ticks(0.9, 1.7) + [(1.2, "pop", 0.4)] + _ticks(1.4, 2.4) + [(2.4, "impact", 0.75), (2.6, "swoosh_up", 0.25)]
    if template == "bowling-figures":
        return entry + _ticks(0.75, 1.75) + [(1.3, "pop", 0.35), (1.45, "pop", 0.35), (1.75, "impact", 0.75), (1.9, "pop", 0.5)]
    if template == "team-total":
        return entry + _ticks(0.35, 1.55) + [(1.55, "impact", 0.7), (1.6, "whip", 0.35), (2.4, "swoosh_up", 0.25)]
    if template == "chase-meter":
        return entry + [(0.5, "riser", 0.35)] + _ticks(0.5, 1.6) + [(1.65, "boom", 0.8), (1.9, "whoosh", 0.3)]
    if template == "milestone":
        return [(0.0, "riser", 0.5), (0.65, "boom", 0.9), (0.66, "impact", 0.6), (1.2, "pop", 0.45), (1.35, "whip", 0.35)]
    if template == "ground-map":  # ground swells in, a pop per fielder, the shot whips out and lands
        n = min(11, len(_list(v.get("fielders"))))
        step = min(0.14, max(0.06, (dur - 3.2) / max(1, n)))
        shot_at = 0.9 + n * step + 0.25
        out = entry + [(round(0.9 + i * step, 3), "pop", 0.3) for i in range(n)]
        if v.get("shot"):
            label = str(v.get("shot_label", "")).upper()
            out += [(round(shot_at, 3), "bat_crack", 0.7), (round(shot_at + 0.05, 3), "whoosh", 0.45),
                    (round(shot_at + 0.8, 3), "boom" if "SIX" in label or "6" in label else "impact", 0.7)]
            if "SIX" in label or "6" in label:
                out.append((round(shot_at + 0.85, 3), "crowd_cheer", 0.3))
        return out
    if template == "stat-hero":  # dial swells, count ticks, the number lands, chips pop
        chips = min(3, len(_list(v.get("facts"))))
        land = 0.6 + 1.4
        out = entry + [(0.6, "riser", 0.3)] + _ticks(0.65, land - 0.1, 0.09, 0.2) + [(land, "impact", 0.7), (land + 0.02, "ding", 0.3)]
        if _list(v.get("cutouts")):
            out.append((0.45, "swoosh_up", 0.45))
        return out + [(round(land + 0.15 + 0.12 * i, 3), "pop", 0.35) for i in range(chips)]
    if template == "versus":
        rows = min(3, len(_list(v.get("rows"))))
        return [(0.0, "whoosh", 0.5), (0.35, "swoosh_up", 0.45), (0.43, "swoosh_up", 0.4), (0.9, "whip", 0.45),
                (1.05, "boom", 0.75)] + [(round(1.4 + 0.35 * i, 3), "pop", 0.4) for i in range(rows)]
    if template == "kinetic-statement":
        words = str(v.get("text", "")).split()[:12]
        step = min(0.11, (dur - 2.2) / max(1, len(words)))
        land = 0.35 + len(words) * step + 0.15
        out = [(0.05, "whoosh", 0.4)] + [(round(0.35 + i * step, 3), "tick", 0.22) for i in range(len(words))]
        out.append((round(land, 3), "impact", 0.6) if v.get("highlight") else (round(land, 3), "ding", 0.3))
        return out + ([(0.25, "swoosh_up", 0.4)] if _list(v.get("cutouts")) else [])
    if template == "compare-bars":
        n = min(5, len(_list(v.get("items"))))
        landed = 0.7 + (n - 1) * 0.18 + 1.3 * 0.6
        return entry + [(round(0.75 + 0.18 * i, 3), "swoosh_up", 0.3) for i in range(n)] + _ticks(0.75, landed - 0.1) + \
            [(round(landed, 3), "impact", 0.65)]
    if template == "timeline":
        n = min(5, len(_list(v.get("points"))))
        draw = min(2.4, max(1.2, dur - 2.2))
        hits = []
        for i in range(n):
            pos = 0.5 if n == 1 else 0.08 + 0.84 * i / (n - 1)
            frac = (pos / 2) ** 0.5 if pos < 0.5 else 1 - ((1 - pos) / 2) ** 0.5
            hits.append((round(0.6 + draw * frac, 3), "pop", 0.4))
        return entry + [(0.6, "riser", 0.25)] + hits
    if template == "photo-reveal":
        return [(0.0, "shutter", 0.8), (0.02, "impact", 0.45), (0.6, "whoosh", 0.5), (1.4, "swoosh_up", 0.25)]
    if template == "photo-stack":
        n = max(1, min(3, len(_list(v.get("photos"))) or len(_list(v.get("labels")))))
        return [(0.1, "whip", 0.4)] + [c for i in range(n) for c in ((0.35 + 0.45 * i, "whoosh", 0.4), (0.77 + 0.45 * i, "impact", 0.55))]
    if template == "quote-card":
        words = str(v.get("text", "")).split()
        step = min(0.12, (dur - 2) / max(1, len(words)))
        return [(0.0, "pop", 0.5)] + [(round(0.3 + i * step, 3), "tick", 0.3) for i in range(len(words))]
    if template == "kinetic-text":
        text = " ".join(str(v.get("text", "")).upper().split())
        style = v.get("style") if v.get("style") in ("slam", "countup", "typewriter") else "typewriter"
        if style == "typewriter":
            step = min(0.055, (dur - 1.6) / max(1, len(text)))
            keys = [(round(0.3 + i * step, 3), "key" if i % 2 else "key2", 0.55) for i, ch in enumerate(text) if ch != " "]
            stride = max(1, len(keys) // 50)  # keep the mix light for long lines
            return [(0.0, "whoosh", 0.35)] + keys[::stride] + [(round(0.3 + len(text) * step + 0.05, 3), "ding", 0.35)]
        words = text.split()
        if style == "slam":
            step = min(0.32, (dur - 1.2) / max(1, len(words)))
            out = [(round(0.15 + i * step, 3), "whip", 0.35) for i in range(len(words))]
            return out + [(round(0.37 + i * step, 3), "impact", 0.8) for i, w in enumerate(words) if any(c.isdigit() for c in w)]
        end = 0.4 + min(1.5, dur / 3)
        return entry + _ticks(0.4, end) + [(round(end, 3), "ding", 0.5)]
    return entry
