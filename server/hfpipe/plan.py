"""Plan cleanup: clamp, de-overlap and snap scenes so the exported timeline is contiguous."""

import re
from difflib import SequenceMatcher

from .models import AssetSlot, Cue, Plan, Scene

MIN_SCENE = 1.0  # seconds; shorter graphics are unreadable
MIN_FACE_GAP = 0.6  # seconds; shorter face cuts between graphics look like glitches
FPS = 30  # render frame rate; scene edges sit on frames so graphic lengths match the plan


def frame(t: float) -> float:
    return round(round(t * FPS) / FPS, 4)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "asset"


def normalize(plan: Plan, duration: float) -> Plan:
    scenes: list[Scene] = []
    for raw in sorted(plan.scenes, key=lambda s: s.start):
        s = raw.model_copy(deep=True)
        s.start = frame(max(0.0, min(s.start, duration)))
        s.end = frame(max(0.0, min(s.end, duration)))
        if s.end - s.start < MIN_SCENE:
            continue
        scenes.append(s)

    # Full-frame scenes replace the main track, so they must not overlap each other.
    kept: list[Scene] = []
    for s in (s for s in scenes if s.mode == "fullframe"):
        if kept and s.start < kept[-1].end:
            s.start = kept[-1].end
            if s.end - s.start < MIN_SCENE:
                continue
        kept.append(s)

    # Absorb slivers of face video between, before or after graphics.
    for prev, nxt in zip(kept, kept[1:]):
        if 0 < nxt.start - prev.end < MIN_FACE_GAP:
            prev.end = nxt.start
    if kept and 0 < kept[0].start < MIN_FACE_GAP:
        kept[0].start = 0.0
    if kept and 0 < duration - kept[-1].end < MIN_FACE_GAP:
        kept[-1].end = round(duration, 3)

    used = sorted(kept + [s for s in scenes if s.mode == "overlay"], key=lambda s: s.start)
    seen: set[str] = set()
    for s in used:
        if s.id in seen:
            s.id = f"{s.id}-{len(seen)}"
        seen.add(s.id)
        if isinstance(s.data.get("photo"), str):
            s.data["photo"] = slug(s.data["photo"])
    assets = [a.model_copy(update={"slot": slug(a.slot)}) for a in plan.assets_needed]
    # Every photo a scene uses gets a slot, so the app can ask for it (or show it as already in the library).
    known = {a.slot for a in assets}
    for scene in used:
        for slot in scene_photo_slots(scene):
            if slot not in known:
                known.add(slot)
                assets.append(AssetSlot(slot=slot, description=f"Photo for {slot.replace('_', ' ')} (used in {scene.template})"))
    return Plan(story=plan.story, scenes=used, assets_needed=assets)


def scene_photo_slots(scene: Scene) -> list[str]:
    """Photo slot names a scene refers to, across every template's fields."""
    d, found = scene.data, []
    for key in ("photo", "bg_photo"):
        if isinstance(d.get(key), str) and d[key].strip():
            found.append(d[key])
    for key in ("photos", "players"):
        if isinstance(d.get(key), list):
            found += [v for v in d[key] if isinstance(v, str) and v.strip()]
    for card in d.get("cards") or []:
        if isinstance(card, dict) and isinstance(card.get("photo"), str):
            found.append(card["photo"])
    return [slug(v) for v in found]


# Words in photo slots and names that are not the subject itself.
_GENERIC = {"team", "test", "photo", "player", "players", "celebration", "celebrating", "night", "stadium", "ground",
            "win", "wins", "match", "series", "clash", "the", "and", "with", "bg", "now", "then", "ipl", "odi", "t20"}


def _subjects(scene: Scene) -> set[str]:
    """Name-like words a scene is about: from its photo slots and name fields."""
    words: set[str] = set()
    texts = [s.replace("_", " ") for s in scene_photo_slots(scene)]
    texts += [str(scene.data.get(k) or "") for k in ("caption", "left", "right", "player", "team")]
    for text in texts:
        for w in re.findall(r"[a-z]+", text.lower()):
            if len(w) >= 4 and w not in _GENERIC:
                words.add(w)
    return words


def _mentions(word: str, caption: str) -> bool:
    """Fuzzy: translated captions misspell names (Bavuma -> BAHOMA), so near matches count."""
    for token in re.findall(r"[a-z]+", caption.lower()):
        if token == word or (len(token) >= 4 and (token.startswith(word) or word.startswith(token))):
            return True
        if token[:1] == word[:1] and abs(len(token) - len(word)) <= 2 and SequenceMatcher(None, word, token).ratio() >= 0.66:
            return True
    return False


def align_to_mentions(plan: Plan, captions: list[Cue], before: float = 4.0, after: float = 1.0,
                      min_len: float = 1.5) -> Plan:
    """Start each visual on the caption where its subject is first named (within a few seconds of where the
    planner put it) and keep it on while the creator is still talking about it. Planners estimate timings from
    caption text and tend to land a beat early; this moves them onto the actual mention."""
    scenes = sorted(plan.scenes, key=lambda s: s.start)
    for i, scene in enumerate(scenes):
        subjects = _subjects(scene)
        if not subjects:
            continue
        hits = [c for c in captions if scene.start - before <= c.start <= scene.end + after
                and any(_mentions(w, c.text) for w in subjects)]
        if not hits:
            continue
        first = min(hits, key=lambda c: c.start)
        if abs(first.start - scene.start) < 0.4:
            continue
        length = scene.end - scene.start
        start = first.start
        end = max(start + length, min(max(c.end for c in hits), start + length + 3.0))
        same_mode = [s for s in scenes if s is not scene and s.mode == scene.mode == "fullframe"]
        prev_end = max((s.end for s in same_mode if s.start < scene.start), default=0.0)
        next_start = min((s.start for s in same_mode if s.start > scene.start), default=end)
        start, end = max(start, prev_end), min(end, next_start)
        if end - start >= min_len:
            scenes[i] = scene.model_copy(update={"start": round(start, 3), "end": round(end, 3)})
    # A moved full-screen visual must not swallow the next overlay (the exporter would drop the overlay):
    # it ends where the next subject's overlay begins, or the overlay waits for it to finish.
    for i, full in enumerate(scenes):
        if full.mode != "fullframe":
            continue
        for over in scenes:
            if over.mode == "overlay" and full.start < over.start < full.end:
                if over.start - full.start >= min_len:
                    full = full.model_copy(update={"end": over.start})
                else:
                    over_i = scenes.index(over)
                    scenes[over_i] = over.model_copy(update={"start": full.end})
        scenes[i] = full
    scenes = [s for s in scenes if s.end - s.start >= 0.5]
    return plan.model_copy(update={"scenes": scenes})
