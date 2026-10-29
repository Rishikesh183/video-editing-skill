"""Moments built from CutAgent's own layers, so every part stays editable on the phone:
keyframes (zoom, pan, opacity, blur), entrance/exit animations, point flashes/shakes, photo frames, looks,
person cut-out, titles (typewriter, word reveal, slam, count-up) and small cricket decor PNGs.

Keyframe x/y are offsets around 0.5 (0.45 = shifted left by 5% of the canvas width). A pan of ±0.05 needs
scale ≥ 1.1 so the photo's edges never show. Image layers paint below titles, later layers above earlier ones.

Colour rules: white type with a black outline on footage; dark type only on yellow or white plates (no
outline); yellow for the payoff word or number. Text that would sit on the creator's face moves below it.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import decor
from .models import Scene

NATIVE_FULLFRAME = {"photo-full", "photo-behind", "photo-frames", "text-punch", "stat-count", "split", "news-cards"}
NATIVE_OVERLAY = {"photo-overlay", "headline", "player-cutout"}
TEXT_MOMENTS = {"text-punch", "stat-count", "headline"}  # big on-screen text: captions step aside
VIDEO_ID = "video-src"
SECOND = 1_000_000
ANTON = "anton"
# Type system (all bundled in the app): heavy display for payoffs/numbers, geometric bold for lines,
# clean grotesk for labels, condensed for broadcast straps. EM = average glyph width in em (bold).
DISPLAY, HEAD, LABEL, STRAP = "archivo_black", "montserrat", "inter", "barlow_condensed"
EM = {DISPLAY: 0.7, HEAD: 0.66, LABEL: 0.62, STRAP: 0.42, ANTON: 0.45}
RED, NAVY = "#E10600", "#0B1026"
ORANGE, YELLOW, INK = "#FF7A1A", "#FFD23F", "#111111"
POLAROID = {"border": 10.0, "bottom": 34.0, "color": "#FFFFFF", "shadowOpacity": 0.55, "shadowBlur": 18.0,
            "shadowX": 0.0, "shadowY": 10.0, "shadowColor": "#000000"}
SLIDE_US = 500_000  # entrances: long enough to read as a glide, not a jump

AddMedia = Callable[[str], str | None]
Box = tuple[float, float, float, float]


@dataclass
class Media:
    """What a moment can pull into the edit. Every call returns a source id for the bundle."""
    photo: AddMedia                          # photo slot -> source id (None if not provided)
    decor: AddMedia                          # decor name -> source id
    still: Callable[[str, Path], str]        # (source id, png) -> source id, for generated PNGs
    face: Callable[[int, int], Box | None]   # face box on the canvas during a timeline span
    canvas: tuple[int, int]
    workdir: Path
    cutout: AddMedia = lambda slot: None    # photo slot -> background-removed PNG source id
    size: Callable[[str], tuple[int, int]] = lambda source_id: (1, 1)  # source pixel size


@dataclass
class Moment:
    main: dict[str, Any] | None = None  # main-track clip for fullframe moments
    images: list[dict[str, Any]] = field(default_factory=list)
    titles: list[dict[str, Any]] = field(default_factory=list)
    cues: list[tuple[int, str, float]] = field(default_factory=list)  # (timeline µs, sound, gain)
    punch: bool = False  # tighten the face shot underneath (overlay moments)
    bottom: float = 0.0  # lowest canvas y the moment's text reaches (for placing a picture under it)


def _point(preset: str, at: int, duration: int, amount: float, length: int) -> list[dict[str, Any]]:
    at = max(0, min(at, length - duration))
    if duration < 1 or at + duration > length:
        return []
    return [{"id": f"{preset}-{at}", "preset": preset, "timeUs": at, "durationUs": duration, "amount": amount,
             "phaseStart": 0.0, "phaseEnd": 1.0}]


def _ken_burns(length: int, direction: str = "right", strength: str = "standard",
               focus_pull: bool = False) -> list[dict[str, Any]]:
    """Zoom + pan across the clip (1.0→1.15 subtle, →1.3 standard). A focus pull starts blurred and
    sharpens over 0.5 s, the way editors land a photo instead of just cutting to it."""
    s0, s1 = (1.12, 1.22) if strength == "subtle" else (1.15, 1.32)
    a, b = (0.45, 0.55) if direction == "right" else (0.55, 0.45)
    keys = [{"timeUs": 0, "scale": s0, "x": a, "easing": "ease_out"}]
    pull = min(500_000, length // 3)
    if focus_pull and pull > 0:
        u = pull / length
        keys[0].update(blur=0.8, opacity=0.4)
        keys.append({"timeUs": pull, "scale": round(s0 + (s1 - s0) * u, 4), "x": round(a + (b - a) * u, 4),
                     "easing": "linear"})
    keys.append({"timeUs": length, "scale": s1, "x": b})
    return keys


def _photo_clip(cid: str, source_id: str, length: int, direction: str, blurred: bool = False) -> dict[str, Any]:
    effects: dict[str, Any] = {"look": "cricket", "lookAmount": 0.7}
    if blurred:
        effects.update({"effect": "blur", "effectAmount": 0.8, "brightness": -0.35})
    else:
        effects.update({"effect": "light_leak", "effectAmount": 0.3})
    clip = {"id": cid, "sourceId": source_id, "inUs": 0, "outUs": length, "muted": True,
            "keys": _ken_burns(length, direction, "subtle" if blurred else "standard", focus_pull=not blurred),
            "effects": effects}
    if not blurred:
        clip["pointEffects"] = _point("flash", 0, 220_000, 0.55, length)
    return clip


def _backdrop(scene: Scene, start: int, length: int, direction: str, portrait: bool, media: Media,
              default: str = "face") -> dict[str, Any]:
    """A related photo (blurred) when there is one; otherwise the creator's own shot defocusing behind the
    text (seamless: same source time); the floodlit ground only when asked for."""
    cid, data = f"bg-{scene.id}", scene.data
    photo = media.photo(str(data["photo"])) if data.get("photo") else None
    if photo:
        return _photo_clip(cid, photo, length, direction, blurred=True)
    kind = data.get("bg") if data.get("bg") in ("face", "field") else default
    if kind == "face":
        pull = min(400_000, length // 3)
        return {"id": cid, "sourceId": VIDEO_ID, "inUs": start, "outUs": start + length, "muted": True,
                "effects": {"brightness": -0.32, "saturation": 0.75, "vignette": 0.5},
                "keys": [{"timeUs": 0, "scale": 1.0, "blur": 0.0, "easing": "ease_out"},
                         {"timeUs": pull, "scale": 1.08, "blur": 1.0, "easing": "linear"},
                         {"timeUs": length, "scale": 1.16, "blur": 1.0}]}
    ground = media.decor("field_portrait" if portrait else "field_landscape")
    return {"id": cid, "sourceId": ground, "inUs": 0, "outUs": length, "muted": True,
            "effects": {"brightness": -0.12, "effect": "light_leak", "effectAmount": 0.35},
            "keys": _ken_burns(length, direction, "subtle")}


PALETTES = ("blue", "crimson", "teal", "purple")


def _stage(scene: Scene, length: int, direction: str, portrait: bool, index: int, media: Media) -> dict[str, Any]:
    """Designed colour backdrop (palette per moment, rotating), drifting slowly so it never looks frozen."""
    palette = scene.data.get("palette") if scene.data.get("palette") in PALETTES else PALETTES[index % len(PALETTES)]
    source = media.decor(f"stage_{palette}_{'portrait' if portrait else 'landscape'}")
    a, b = (0.48, 0.52) if direction == "right" else (0.52, 0.48)
    return {"id": f"bg-{scene.id}", "sourceId": source, "inUs": 0, "outUs": length, "muted": True,
            "effects": {"effect": "noise", "effectAmount": 0.1},
            "keys": [{"timeUs": 0, "scale": 1.06, "x": a, "easing": "ease_in_out"},
                     {"timeUs": length, "scale": 1.14, "x": b}]}


def _has_visual(scene: Scene) -> bool:
    return bool(scene.data.get("players") or scene.data.get("photo"))


def _visual_below(scene: Scene, start: int, end: int, portrait: bool, top: float, media: Media) -> Moment:
    """The related picture under the words: the players cut out and rising from the bottom edge, or a framed
    photo card that glides up and settles with a slight tilt. `top` = where the free area starts."""
    m = Moment()
    data = scene.data
    if data.get("players"):
        m = _player_cutouts(scene, start + 350_000, end, portrait, media, max_h=1.0 - top)
        if m.images:
            return m
    photo = media.photo(str(data["photo"])) if data.get("photo") else None
    if photo is None:
        return m
    room = 1.0 - top - (0.07 if portrait else 0.06)
    width = 0.88 if portrait else 0.46
    if _height(media, photo, width) > room - 0.04:
        width *= (room - 0.04) / _height(media, photo, width)
    h = _height(media, photo, width)
    at = start + 400_000
    m.images.append({
        "id": f"vb-{scene.id}", "sourceId": photo, "startUs": at, "endUs": end,
        "x": 0.5, "y": round(top + 0.02 + room / 2, 4), "width": round(width, 4), "rotation": -2.0,
        "frame": {**POLAROID, "border": 8.0, "bottom": 8.0}, "effects": {"look": "cricket", "lookAmount": 0.5},
        "keys": [{"timeUs": 0, "scale": 0.92, "y": round(0.5 + min(0.3, h), 4), "rotation": -6.0, "opacity": 0.0,
                  "easing": "ease_out"},
                 {"timeUs": 650_000, "scale": 1.0, "y": 0.5, "rotation": 0.0, "opacity": 1.0, "easing": "linear"},
                 {"timeUs": end - at, "scale": 1.05, "y": 0.5, "rotation": 0.0, "opacity": 1.0}],
        "exit": {"preset": "fade", "durationUs": 250_000}})
    m.cues += [(at, "swoosh_up", 0.45), (at + 500_000, "shutter", 0.4)]
    return m


def _title(tid: str, text: str, start: int, end: int, y: float, size: float, **extra: Any) -> dict[str, Any]:
    t = {"id": tid, "text": text, "startUs": start, "endUs": end, "x": 0.5, "y": y, "size": size, "font": HEAD,
         "capitalize": True, "wrapWidth": 0.86, "exit": {"preset": "fade", "durationUs": 250_000},
         "decoration": {"strokeColor": "#000000", "strokeWidth": 3.0}}
    t.update(extra)
    return t


def _plate(background: str, padding: float = 12.0) -> dict[str, Any]:
    """Dark type on a light plate: no outline (an outline on a plate reads as smudge)."""
    return {"background": background, "backgroundOpacity": 1.0, "padding": padding, "cornerRadius": 6.0,
            "strokeColor": "#000000", "strokeWidth": 0.0}


def _kicker(scene: Scene, start: int, end: int, y: float) -> list[dict[str, Any]]:
    text = str(scene.data.get("kicker") or "").strip()
    if not text:
        return []
    return [_title(f"kk-{scene.id}", text[:40], start, end, y, 22.0, color=INK, wrapWidth=0.8, font=LABEL,
                   decoration=_plate(YELLOW), entrance={"preset": "slide_down", "durationUs": SLIDE_US})]


def _lower_third(scene: Scene, start: int, end: int, portrait: bool, x: float, y: float,
                 media: Media) -> tuple[list[dict], list[dict], list[tuple[int, str, float]]]:
    """Broadcast-style name strap: a slanted navy plate glides in from the left, then the name follows it,
    with an optional yellow sub-line. Returns (images, titles, cues)."""
    text = str(scene.data.get("caption") or scene.data.get("title") or "").strip()[:40]
    if not text or end - start < 1_000_000:
        return [], [], []
    base_w = 720 * (9 / 16 if portrait else 16 / 9)  # canvas width in 720-high design pixels
    size = 36.0 if portrait else 40.0
    max_w = 0.9 if portrait else 0.45
    size = min(size, _fit_size(text, portrait, size, lines=1, font=STRAP, span=max_w - 0.08))  # one line, always
    text_w = _text_px(text, size, STRAP) / base_w
    width = min(max_w, max(0.3 if portrait else 0.16, text_w + 0.1))
    plate_h = size * 2.0  # plate height follows the type, its length follows the name
    plate_w = plate_h * 4 / base_w  # the 4:1 plate PNG at that height; stretchX widens it to `width`
    x = min(max(x, width / 2 + 0.03), 0.97 - width / 2)  # keep the whole strap on screen
    bar = media.decor("bar")
    at = start + 250_000
    images = [] if bar is None else [{
        "id": f"dc-lt-{scene.id}", "sourceId": bar, "startUs": at, "endUs": end, "x": x, "y": y,
        "width": round(plate_w, 4), "stretchX": round(min(8.0, max(0.1, width / plate_w)), 4),
        "keys": [{"timeUs": 0, "x": 0.5 - width - 0.1, "opacity": 0.0, "easing": "ease_out"},
                 {"timeUs": SLIDE_US, "x": 0.5, "opacity": 1.0, "easing": "linear"},
                 {"timeUs": end - at, "x": 0.5, "opacity": 1.0}],
        "exit": {"preset": "fade", "durationUs": 250_000}}]
    # Text sits on the plate, nudged right of the yellow accent edge.
    titles = [_title(f"tx-{scene.id}", text, at + 200_000, end, round(y - plate_h * 0.04 / 720, 4), size,
                     x=round(x + width * 0.03, 4), wrapWidth=round(min(1.0, text_w + 0.06), 4), color="#FFFFFF", font=STRAP,
                     decoration={"strokeColor": "#000000", "strokeWidth": 0.0},
                     entrance={"preset": "slide_right", "durationUs": SLIDE_US})]
    sub = str(scene.data.get("sub") or "").strip()[:40]
    if sub:
        titles.append(_title(f"ts-{scene.id}", sub, at + 400_000, end, round(y + plate_h * 0.75 / 720, 4),
                             _fit_size(sub, portrait, 18.0, lines=1, font=LABEL, span=max_w), wrapWidth=max_w,
                             x=x, color=YELLOW, font=LABEL, capitalize=False, entrance={"preset": "slide_right", "durationUs": SLIDE_US}))
    return images, titles, [(at, "swoosh_up", 0.4)]


# Text is measured with the real (bold) font files, at the app's design scale (size = px at 720 high), so a
# line we plan as one line never wraps in the app. Widths are compared with a safety margin for the outline.
_FONT_FILES = {DISPLAY: ("ArchivoBlack-Regular.ttf", None), HEAD: ("Montserrat.ttf", b"Bold"),
               LABEL: ("Inter.ttf", b"Bold"), STRAP: ("BarlowCondensed-Bold.ttf", None), ANTON: ("Anton-Regular.ttf", None)}
_FIT_MARGIN = 0.9
_font_cache: dict[tuple[str, int], Any] = {}


def _font(font: str, size: int) -> Any:
    from PIL import ImageFont

    from . import config

    key = (font, size)
    if key not in _font_cache:
        name, variation = _FONT_FILES.get(font, _FONT_FILES[HEAD])
        f = ImageFont.truetype(str(config.TEMPLATES / "_shared" / "fonts" / name), size)
        if variation:
            try:
                f.set_variation_by_name(variation)
            except (OSError, ValueError):
                pass
        _font_cache[key] = f
    return _font_cache[key]


def _text_px(text: str, size: float, font: str) -> float:
    box = _font(font, max(1, round(size))).getbbox(text.upper())
    return box[2] - box[0]


def _wrap(text: str, size: float, font: str, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and _text_px(trial, size, font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return [*lines, line] if line else lines


def _width_720(portrait: bool, span: float) -> float:
    return (720 * 9 / 16 if portrait else 720 * 16 / 9) * span * _FIT_MARGIN


def _fit_size(text: str, portrait: bool, preferred: float, lines: int = 2, font: str = HEAD,
              span: float = 0.86) -> float:
    """Largest size (≤ preferred) at which the text fits in `lines` lines and no single word overflows."""
    width = _width_720(portrait, span)
    size = int(preferred)
    while size > 20:
        wrapped = _wrap(text, size, font, width)
        if len(wrapped) <= lines and all(_text_px(w, size, font) <= width for w in text.split()):
            break
        size -= 1
    return float(size)


def _lines(text: str, size: float, portrait: bool, font: str = HEAD, span: float = 0.86) -> int:
    return max(1, len(_wrap(text, size, font, _width_720(portrait, span))))


def _icon(name: str, sid: str, start: int, end: int, portrait: bool, x: float, y: float,
          media: Media) -> tuple[list[dict[str, Any]], list[tuple[int, str, float]]]:
    """A small cricket prop that glides in with keyframes: the ball rolls in, the bat swings, the stumps rise."""
    source = media.decor(name) if name in ("ball", "bat", "stumps") else None
    if source is None or end - start < 1_000_000:
        return [], []
    length = end - start
    width = {"ball": 0.16, "bat": 0.11, "stumps": 0.17}[name] * (1 if portrait else 0.5)
    settle = 800_000
    if name == "ball":
        first = {"timeUs": 0, "x": -0.6, "rotation": -360.0, "easing": "ease_out"}
        cue = (start, "whoosh", 0.45)
    elif name == "bat":
        first = {"timeUs": 0, "y": 0.9, "rotation": -60.0, "easing": "ease_out"}
        cue = (start + 400_000, "bat_crack", 0.6)
    else:
        first = {"timeUs": 0, "y": 0.85, "scale": 0.7, "easing": "ease_out"}
        cue = (start + 350_000, "pop", 0.5)
    keys = [{"scale": 1.0, "x": 0.5, "y": 0.5, "rotation": 0.0, "opacity": 0.0, **first},
            {"timeUs": settle, "scale": 1.0, "x": 0.5, "y": 0.5, "rotation": 0.0, "easing": "ease_in_out"},
            {"timeUs": length, "scale": 1.06, "x": 0.5, "y": 0.5, "rotation": 6.0 if name != "ball" else 90.0}]
    layer = {"id": f"dc-{name}-{sid}", "sourceId": source, "startUs": start, "endUs": end, "x": x, "y": y,
             "width": width, "keys": keys, "exit": {"preset": "fade", "durationUs": 250_000}}
    return [layer], [cue]


def _marker(sid: str, start: int, end: int, y: float, portrait: bool, media: Media) -> list[dict[str, Any]]:
    """Brush stroke behind a highlighted line; sweeps in from the left."""
    source = media.decor("stroke")
    if source is None or end - start < 500_000:
        return []
    return [{"id": f"dc-mk-{sid}", "sourceId": source, "startUs": start, "endUs": end, "x": 0.5, "y": y,
             "width": 0.86 if portrait else 0.5,
             "keys": [{"timeUs": 0, "scale": 0.4, "x": 0.3, "opacity": 0.0, "easing": "ease_out"},
                      {"timeUs": 420_000, "scale": 1.0, "x": 0.5, "opacity": 1.0, "easing": "linear"},
                      {"timeUs": end - start, "scale": 1.03, "x": 0.5, "opacity": 1.0}],
             "exit": {"preset": "fade", "durationUs": 250_000}}]


def _words(data: dict[str, Any]) -> tuple[str, str]:
    """(lead-in, payoff). Never the same words twice."""
    text = " ".join(str(data.get("text") or "").split())[:200]
    highlight = " ".join(str(data.get("highlight") or "").split())[:40]
    if highlight and text.lower().endswith(highlight.lower()):
        text = text[: -len(highlight)].strip(" :-,") or text
        highlight = "" if text.lower() == highlight.lower() else highlight
    elif highlight.lower() in text.lower():
        highlight = ""
    return text, highlight


def _text_block(scene: Scene, start: int, end: int, portrait: bool, centre: float, media: Media,
                main_clip: dict[str, Any] | None, clip_start: int, avoid: Box | None = None) -> Moment:
    """Kicker + main line + optional highlighted line on a marker stroke. Shared by text-punch and headline.
    `avoid` is the creator's face on the canvas: the block goes above it if there is room, else below it."""
    m = Moment()
    data = scene.data
    text, highlight = _words(data)
    style = data.get("style") if data.get("style") in ("typewriter", "slam", "reveal") else "typewriter"
    size = _fit_size(text, portrait, 46.0 if highlight else 60.0, lines=2 if len(text) > 14 else 1)
    lines = _lines(text, size, portrait)
    block = lines * size * 1.3 / 720
    hl_size = _fit_size(highlight, portrait, 76.0, lines=1, font=DISPLAY) if highlight else 0
    if highlight and size > hl_size * 0.8:  # the payoff always reads bigger than the lead-in
        size = max(20.0, round(hl_size * 0.8))
        lines = _lines(text, size, portrait)
        block = lines * size * 1.3 / 720
    total = block + (hl_size * 1.5 / 720 if highlight else 0)
    head = 0.07 if data.get("kicker") else 0.0
    if avoid is not None:
        if avoid[1] - 0.06 >= total + head + 0.04:  # room above the face
            centre = 0.04 + head + total / 2
        else:  # below the chin, kept clear of the bottom UI and captions
            centre = min(0.8 - total / 2, avoid[3] + 0.03 + head + total / 2)
    top = max(0.06 + head, centre - total / 2)
    main_y = top + block / 2
    at = start + 200_000

    if style == "typewriter":
        reveal = min(2_600_000, max(400_000, len(text) * 55_000), (end - at) // 2)
        entrance = {"preset": "typewriter", "durationUs": reveal}
        m.cues += [(at, "typing", 0.5)]
    elif style == "reveal":
        reveal = min(2_400_000, max(500_000, len(text.split()) * 250_000), (end - at) // 2)
        entrance = {"preset": "word_reveal", "durationUs": reveal}
        m.cues += [(at, "swoosh_up", 0.4)]
    else:
        reveal = 350_000
        entrance = {"preset": "slam", "durationUs": reveal}
        m.cues += [(at - 120_000, "whip", 0.4), (at + reveal - 60_000, "boom", 0.6)]
        if main_clip is not None:  # the whole frame flashes and shakes as the words land
            length = main_clip["outUs"] - main_clip["inUs"]
            hit = at + reveal - 60_000 - clip_start
            main_clip["pointEffects"] = (main_clip.get("pointEffects", []) + _point("flash", hit, 200_000, 0.5, length)
                                         + _point("shake", hit, 380_000, 0.6, length))
    m.titles += _kicker(scene, start + 100_000, end, round(top - 0.055, 4))
    m.titles.append(_title(f"tp-{scene.id}", text, at, end, round(main_y, 4), size, entrance=entrance))

    if highlight:
        hl_at = min(end - 600_000, at + reveal + 150_000)
        hl_y = round(top + block + hl_size * 0.8 / 720, 4)
        m.images += _marker(scene.id, hl_at, end, hl_y, portrait, media)
        m.titles.append(_title(f"hl-{scene.id}", highlight, hl_at + 150_000, end, hl_y, hl_size, color="#FFFFFF", font=DISPLAY,
                               entrance={"preset": "zoom_in", "durationUs": 350_000},
                               decoration={"strokeColor": "#000000", "strokeWidth": 4.0}))
        m.cues += [(hl_at, "swoosh_up", 0.4), (hl_at + 200_000, "impact", 0.55)]
    elif style == "typewriter":
        m.cues.append((at + reveal, "ding", 0.35))
    m.bottom = top + total
    return m


def _me(scene_id: str, start: int, length: int, aspect: float, portrait: bool, keys: list[dict]) -> dict[str, Any]:
    """The creator as a person cut-out above everything else on the main track. Same source time as the
    voice, so lips stay in sync."""
    canvas = 9 / 16 if portrait else 16 / 9
    return {"id": f"me-{scene_id}", "sourceId": VIDEO_ID, "startUs": start, "endUs": start + length,
            "inUs": start, "sourceOutUs": start + length, "x": 0.5, "y": 0.5, "width": min(1.0, aspect / canvas),
            "muted": True, "cutout": {"mode": "people"}, "keys": keys}


def face_aside(scene_id: str, start: int, length: int, portrait: bool, aspect: float) -> dict[str, Any]:
    """Full size, then a smooth shrink to the side, hold while the photo plays, and grow back."""
    shrink = min(900_000, length // 3)  # smooth, not a snap
    back = min(600_000, length // 4)
    # Shrunk into the bottom-left corner, flush with both edges: the frame's bottom (where the body is cut
    # off) sits on the canvas bottom, so no strip of background shows under the creator.
    canvas = 9 / 16 if portrait else 16 / 9
    full_w = min(1.0, aspect / canvas)
    full_h = full_w * canvas / aspect  # the layer's height as a fraction of the canvas height
    s = 0.58 if portrait else 0.82
    side = {"scale": s, "x": round(s * full_w / 2, 4), "y": round(1 - s * full_h / 2, 4)}
    home = {"scale": 1.0, "x": 0.5, "y": 0.5}
    return _me(scene_id, start, length, aspect, portrait, [
        {"timeUs": 0, **home, "easing": "hold"},
        {"timeUs": 150_000, **home, "easing": "ease_out"},
        {"timeUs": 150_000 + shrink, **side, "easing": "linear"},
        {"timeUs": length - back, **side, "easing": "ease_in_out"},
        {"timeUs": length, **home},
    ])


def _text_behind(scene: Scene, start: int, length: int, portrait: bool, aspect: float, direction: str,
                 media: Media) -> Moment:
    """Poster type *behind* the creator: the wall behind him changes (photo or studio backdrop), big words
    land at head height and his cut-out, eased slightly down and smaller, covers their lower edge.

    The words are a PNG layer (app titles always paint above images), so retyping means re-exporting; the
    layer itself can still be moved, scaled and retimed by hand."""
    m = Moment()
    data, end = scene.data, start + length
    text, highlight = _words(data)
    photo = media.photo(str(data["photo"])) if data.get("photo") else None
    if photo:
        m.main = _photo_clip(f"bg-{scene.id}", photo, length, direction, blurred=True)
    else:
        wall = media.decor("backdrop_portrait" if portrait else "backdrop_landscape")
        m.main = {"id": f"bg-{scene.id}", "sourceId": wall, "inUs": 0, "outUs": length, "muted": True,
                  "effects": {"effect": "noise", "effectAmount": 0.25},
                  "keys": _ken_burns(length, direction, "subtle")}
    W, H = media.canvas
    width_frac = 0.92 if portrait else 0.7
    lines = [(w.upper(), "#FFFFFF") for w in _split(text, 12)] + ([(highlight.upper(), YELLOW)] if highlight else [])
    png = decor.text_png(lines, round(W * width_frac), media.workdir / f"behind-{scene.id}.png",
                         max_line_px=round(H * (0.13 if portrait else 0.2)))
    from PIL import Image

    with Image.open(png) as im:
        text_h = im.height / im.width * width_frac * W / H  # canvas-height fraction
    top = 0.07 if not data.get("kicker") else 0.12
    bottom = top + text_h
    # Ease the cut-out down/smaller so only the hair overlaps the bottom edge of the words.
    face = media.face(start, end) or ((0.3, 0.15, 0.7, 0.5) if portrait else (0.42, 0.2, 0.58, 0.55))
    s = 0.86
    face_h = face[3] - face[1]
    want_top = bottom - 0.3 * text_h + 0.28 * face_h * s
    dy = max(0.0, min(0.3, want_top - (0.5 + (face[1] - 0.5) * s)))
    home = {"scale": 1.0, "x": 0.5, "y": 0.5}
    low = {"scale": s, "x": 0.5, "y": round(0.5 + dy, 4)}
    settle, back = min(600_000, length // 3), min(450_000, length // 4)
    at = start + 300_000
    m.images.append({
        "id": f"tb-{scene.id}", "sourceId": media.still(f"behind-{scene.id}", png), "startUs": at, "endUs": end,
        "x": 0.5, "y": round(top + text_h / 2, 4), "width": width_frac,
        "keys": [{"timeUs": 0, "scale": 0.75, "y": 0.56, "opacity": 0.0, "easing": "ease_out"},
                 {"timeUs": SLIDE_US, "scale": 1.0, "y": 0.5, "opacity": 1.0, "easing": "linear"},
                 {"timeUs": end - at, "scale": 1.05, "y": 0.5, "opacity": 1.0}],
        "exit": {"preset": "fade", "durationUs": 250_000}})
    m.images.append(_me(scene.id, start, length, aspect, portrait, [
        {"timeUs": 0, **home, "easing": "ease_out"},
        {"timeUs": settle, **low, "easing": "linear"},
        {"timeUs": length - back, **low, "easing": "ease_in_out"},
        {"timeUs": length, **home}]))
    m.titles += _kicker(scene, start + 100_000, end, round(top - 0.04, 4))
    m.cues += [(start, "whoosh", 0.5), (at + 300_000, "impact", 0.55)]
    return m


def _split(text: str, per_line: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > per_line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line] if line else lines


def _number(value: Any) -> tuple[float, int] | None:
    try:
        text = str(value).replace(",", "").strip()
        number = float(text)
    except ValueError:
        return None
    decimals = min(3, len(text.split(".")[1])) if "." in text else 0
    return number, decimals


def _height(media: Media, source_id: str, width: float) -> float:
    """Canvas-height fraction of an image layer `width` wide."""
    w, h = media.size(source_id)
    cw, ch = media.canvas
    return width * cw / ch * (h / w if w else 1.0)


def _player_cutouts(scene: Scene, start: int, end: int, portrait: bool, media: Media,
                    max_h: float = 0.62) -> Moment:
    """Players (background removed) rise from the bottom edge beside the creator, one after another."""
    m = Moment()
    slots = [str(s) for s in (scene.data.get("players") or [scene.data.get("photo")]) if s][:2]
    ids = [c for c in (media.cutout(s) for s in slots) if c]
    if not ids:
        return m
    xs = ([0.27, 0.73] if len(ids) == 2 else [0.72]) if portrait else ([0.2, 0.8] if len(ids) == 2 else [0.82])
    width = (0.5 if len(ids) == 2 else 0.56) if portrait else 0.26
    for i, source_id in enumerate(ids):
        at = start + i * 400_000
        if end - at < 800_000:
            break
        if _height(media, source_id, width) > max_h:
            width *= max_h / _height(media, source_id, width)
        h = _height(media, source_id, width)
        m.images.append({
            "id": f"pc-{scene.id}-{i}", "sourceId": source_id, "startUs": at, "endUs": end,
            "x": xs[i], "y": round(1.0 - h / 2 + 0.02, 4), "width": width,
            "keys": [{"timeUs": 0, "y": round(0.5 + h + 0.05, 4), "opacity": 0.0, "easing": "ease_out"},
                     {"timeUs": SLIDE_US, "y": 0.5, "opacity": 1.0, "easing": "linear"},
                     {"timeUs": end - at, "y": 0.5, "scale": 1.04, "opacity": 1.0}],
            "exit": {"preset": "slide_down", "durationUs": 350_000}})
        m.cues += [(at, "swoosh_up", 0.5), (at + SLIDE_US - 100_000, "pop", 0.35)]
    return m


def _pills(scene: Scene, start: int, end: int, ys: list[float], portrait: bool, media: Media,
           x: float = 0.5, badge: bool = True) -> Moment:
    """Callout pills that pop in one by one (white plate; the last one red = the key fact), led by a green arrow."""
    m = Moment()
    callouts = [str(c).strip()[:34] for c in (scene.data.get("callouts") or []) if str(c).strip()][: len(ys)]
    step = min(900_000, max(450_000, (end - start - 1_200_000) // max(1, len(callouts))))
    base_w = 720 * (9 / 16 if portrait else 16 / 9)
    for i, text in enumerate(callouts):
        at = start + 500_000 + i * step
        if end - at < 600_000:
            break
        key = i == len(callouts) - 1 and len(callouts) > 1
        size = _fit_size(text, portrait, 24.0, lines=1, font=LABEL, span=0.7 if portrait else 0.4)
        m.titles.append(_title(f"pl-{scene.id}-{i}", text, at, end, ys[i], size, x=x, font=LABEL, capitalize=False,
                               color="#FFFFFF" if key else INK, wrapWidth=0.9,
                               decoration={**_plate(RED if key else "#FFFFFF", 10.0), "cornerRadius": 22.0},
                               entrance={"preset": "pop", "durationUs": 300_000}))
        m.cues.append((at, "impact" if key else "pop", 0.4))
        arrow = media.decor("arrow") if badge and i == 0 else None
        if arrow:
            half = (_text_px(text, size, LABEL) + 20) / base_w / 2
            m.images.append({"id": f"dc-ar-{scene.id}", "sourceId": arrow, "startUs": at, "endUs": end,
                             "x": round(max(0.05, x - half - 0.07), 4), "y": ys[i], "width": 0.08 if portrait else 0.035,
                             "keys": [{"timeUs": 0, "x": 0.4, "opacity": 0.0, "easing": "ease_out"},
                                      {"timeUs": 350_000, "x": 0.5, "opacity": 1.0, "easing": "linear"},
                                      {"timeUs": end - at, "x": 0.5}]})
    return m


def _split_screen(scene: Scene, start: int, length: int, portrait: bool, media: Media) -> Moment:
    """Analysis split: the creator in one half (still talking), a dark grid panel in the other with a
    screenshot/photo, a counting number or callout pills appearing as he explains."""
    m, data, end = Moment(), scene.data, start + length
    face = media.face(start, end) or (0.25, 0.15, 0.75, 0.55)
    if portrait:  # creator slides into the lower half, panel on top
        dy = round(max(0.0, min(0.45, 0.74 - (face[1] + face[3]) / 2)), 4)
        motion = {"y": round(0.5 + dy, 4)}
        px, panel_y, panel_w, top, bottom = 0.5, 0.25, 1.0, 0.0, 0.5
    else:  # creator slides left, panel on the right
        motion = {"x": 0.25}
        px, panel_y, panel_w, top, bottom = 0.75, 0.5, 0.5, 0.0, 1.0
    m.main = {"id": f"bg-{scene.id}", "sourceId": VIDEO_ID, "inUs": start, "outUs": end, "muted": True,
              "keys": [{"timeUs": 0, "scale": 1.0, "easing": "ease_out"},
                       {"timeUs": min(450_000, length // 4), "scale": 1.0, **motion, "easing": "linear"},
                       {"timeUs": length, "scale": 1.03, **motion}]}
    grid = media.decor("grid")
    if grid:
        enter = {"y": 0.0} if portrait else {"x": 1.0}
        m.images.append({"id": f"dc-grid-{scene.id}", "sourceId": grid, "startUs": start, "endUs": end,
                         "x": px, "y": panel_y, "width": panel_w,
                         "keys": [{"timeUs": 0, **enter, "easing": "ease_out"},
                                  {"timeUs": 450_000, "x": 0.5, "y": 0.5, "easing": "linear"},
                                  {"timeUs": length, "x": 0.5, "y": 0.5}]})
    m.cues.append((start, "whoosh", 0.45))
    mid = (top + bottom) / 2
    photo = media.photo(str(data["photo"])) if data.get("photo") else None
    parsed = _number(data.get("value")) if data.get("value") not in (None, "") else None
    if photo:
        room = (bottom - top) * 0.72
        width = 0.92 * panel_w
        if _height(media, photo, width) > room:
            width *= room / _height(media, photo, width)
        h = _height(media, photo, width)
        at = start + 300_000
        m.images.append({"id": f"sp-{scene.id}", "sourceId": photo, "startUs": at, "endUs": end, "x": px,
                         "y": round(mid - 0.03, 4), "width": round(width, 4),
                         "mask": {"shape": "rounded_rectangle", "size": 0.97},
                         "keys": [{"timeUs": 0, "scale": 0.92, "opacity": 0.0, "easing": "ease_out"},
                                  {"timeUs": 450_000, "scale": 1.0, "opacity": 1.0, "easing": "linear"},
                                  {"timeUs": end - at, "scale": 1.08, "opacity": 1.0}]})
        m.cues.append((at, "shutter", 0.4))
        ys = [round(mid - 0.03 + h / 2, 4), round(mid + h / 2 + 0.035, 4)]
    elif parsed:
        value, decimals = parsed
        prefix, suffix = str(data.get("prefix") or "")[:10], str(data.get("suffix") or "")[:20]
        final = f"{prefix}{value:.{decimals}f}{suffix}"
        size = _fit_size(final, portrait, 110.0, lines=1, font=DISPLAY, span=0.8 * panel_w)
        count = min(1_400_000, max(600_000, (length - 800_000) // 2))
        m.titles.append(_title(f"st-{scene.id}", final, start + 300_000, end, round(mid - 0.06, 4), size, font=DISPLAY,
                               color=YELLOW, x=px, entrance={"preset": "count_up", "durationUs": count},
                               counter={"from": 0.0, "to": value, "decimals": decimals, "prefix": prefix, "suffix": suffix},
                               decoration={"strokeColor": "#000000", "strokeWidth": 0.0}))
        m.cues += [(start + 300_000, "riser", 0.3), (start + 300_000 + count, "impact", 0.55)]
        ys = [round(mid + 0.06, 4), round(mid + 0.13, 4)]
    else:
        ys = [round(top + (bottom - top) * f, 4) for f in (0.34, 0.52, 0.7)]
    title = str(data.get("title") or "").strip()
    if title and not photo:
        m.titles.append(_title(f"tt-{scene.id}", title[:50], start + 150_000, end, round(top + 0.07, 4),
                               _fit_size(title[:50], portrait, 30.0, lines=1, span=0.8 * panel_w), x=px,
                               capitalize=False, decoration={"strokeColor": "#000000", "strokeWidth": 0.0},
                               entrance={"preset": "slide_down", "durationUs": SLIDE_US}))
    pills = _pills(scene, start, end, ys, portrait, media, x=px, badge=not photo)
    m.titles += pills.titles
    m.images += pills.images
    m.cues += pills.cues
    return m


def _news_cards(scene: Scene, start: int, length: int, portrait: bool, direction: str, media: Media) -> Moment:
    """News-desk stack: 1-2 photo cards with a red tag and a headline plate zoom in over a blurred photo."""
    m, end = Moment(), start + length
    cards = [c for c in (scene.data.get("cards") or []) if isinstance(c, dict) and c.get("photo")][:2]
    found = [(media.photo(str(c["photo"])), c) for c in cards]
    found = [(i, c) for i, c in found if i]
    if not found:
        return m
    bg = media.photo(str(scene.data["bg_photo"])) if scene.data.get("bg_photo") else None
    m.main = _photo_clip(f"bg-{scene.id}", bg or found[0][0], length, direction, blurred=True)
    n = len(found)
    room = (0.86 if portrait else 0.84) / n if portrait else 0.84
    for i, (source_id, card) in enumerate(found):
        width = (0.72 if n == 1 else 0.62) if portrait else (0.4 if n == 1 else 0.36)
        text = str(card.get("text") or "").strip()[:70]
        text_size = _fit_size(text, portrait, 20.0, lines=2, font=LABEL, span=width) if text else 0
        text_h = (_lines(text, text_size, portrait, LABEL) * text_size * 1.35 + 22) / 720 if text else 0
        below = 0.03 + text_h + 0.03  # tag overlaps the photo edge; headline plate + gap to the next card
        if _height(media, source_id, width) > room - below:
            width *= (room - below) / _height(media, source_id, width)
        h = _height(media, source_id, width)
        block = h + below
        if portrait:
            cx, cy = 0.5, 0.07 + room * i + (room - block) / 2 + h / 2
        else:
            cx, cy = (0.5 if n == 1 else 0.28 + 0.44 * i), 0.08 + (room - block) / 2 + h / 2
        at = start + 200_000 + i * 600_000
        if end - at < 900_000:
            break
        m.images.append({"id": f"nc-{scene.id}-{i}", "sourceId": source_id, "startUs": at, "endUs": end,
                         "x": cx, "y": round(cy, 4), "width": round(width, 4),
                         "frame": {**POLAROID, "border": 6.0, "bottom": 6.0},
                         "keys": [{"timeUs": 0, "scale": 0.6, "opacity": 0.0, "easing": "ease_out"},
                                  {"timeUs": SLIDE_US, "scale": 1.0, "opacity": 1.0, "easing": "linear"},
                                  {"timeUs": end - at, "scale": 1.04, "opacity": 1.0}],
                         "exit": {"preset": "fade", "durationUs": 250_000}})
        edge = cy + h / 2
        tag = str(card.get("tag") or "").strip()[:20]
        if tag:
            m.titles.append(_title(f"ng-{scene.id}-{i}", tag, at + 300_000, end, round(edge + 0.005, 4), 24.0, x=cx,
                                   font=DISPLAY, color="#FFFFFF", decoration=_plate(RED, 10.0),
                                   entrance={"preset": "slide_up", "durationUs": 400_000}))
        if text:
            m.titles.append(_title(f"nt-{scene.id}-{i}", text, at + 450_000, end, round(edge + 0.035 + text_h / 2, 4),
                                   text_size, x=cx, font=LABEL,
                                   capitalize=False, color="#FFFFFF", wrapWidth=round(width, 4),
                                   decoration=_plate(NAVY, 10.0), entrance={"preset": "fade", "durationUs": 400_000}))
        m.cues += [(at, "whoosh", 0.45), (at + 300_000, "pop", 0.35)]
    return m


DUEL_COLORS = ("#E10600", "#1E6BFF")  # side one red, side two blue: frames and name plates match


def _duel(scene: Scene, start: int, length: int, portrait: bool, ids: list[str], labels: list[Any],
          m: Moment, media: Media) -> Moment:
    """Two photos as a head-to-head: in 9:16 one slides down from the top and one up from the bottom, in
    16:9 from the left and right; each has its own coloured frame and matching name plate, and a VS badge
    lands between them. The first photo is the earlier/first-named side."""
    end = start + length
    title = str(scene.data.get("title") or "").strip()
    top = 0.17 if title else 0.06
    for i, source_id in enumerate(ids[:2]):
        color = DUEL_COLORS[i]
        if portrait:
            gap = 0.14  # room between the cards for the first name plate and the VS badge
            room = (0.92 - top - gap) / 2
            width = 0.86
            if _height(media, source_id, width) > room:
                width *= room / _height(media, source_id, width)
            h = _height(media, source_id, width)
            cx, cy = 0.5, top + room / 2 + i * (room + gap)
            enter = {"y": round(0.5 - (cy + h / 2) - 0.05, 4)} if i == 0 else {"y": round(0.5 + (1.05 - cy + h / 2), 4)}
            label_y = cy + h / 2 + 0.005
        else:
            room = 0.4
            width = room
            if _height(media, source_id, width) > 0.68:
                width *= 0.68 / _height(media, source_id, width)
            h = _height(media, source_id, width)
            cx, cy = (0.27, 0.53) if i == 0 else (0.73, 0.53)
            enter = {"x": -0.4} if i == 0 else {"x": 1.4}
            label_y = cy + h / 2 + 0.01
        at = start + 200_000 + i * 250_000
        if end - at < 800_000:
            break
        m.images.append({
            "id": f"du-{scene.id}-{i}", "sourceId": source_id, "startUs": at, "endUs": end,
            "x": cx, "y": round(cy, 4), "width": round(width, 4),
            "frame": {**POLAROID, "border": 9.0, "bottom": 9.0, "color": color, "shadowOpacity": 0.6},
            "keys": [{"timeUs": 0, **enter, "easing": "ease_out"},
                     {"timeUs": 600_000, "x": 0.5, "y": 0.5, "easing": "linear"},
                     {"timeUs": end - at, "x": 0.5, "y": 0.5, "scale": 1.04}],
            "exit": {"preset": "fade", "durationUs": 250_000}})
        if i < len(labels) and str(labels[i]).strip():
            m.titles.append(_title(f"lb-{scene.id}-{i}", str(labels[i])[:40], at + 500_000, end, round(min(0.95, label_y), 4),
                                   _fit_size(str(labels[i])[:40], portrait, 26.0, lines=1, font=DISPLAY, span=0.8),
                                   x=cx, font=DISPLAY, color="#FFFFFF", decoration=_plate(color, 10.0),
                                   entrance={"preset": "slide_up", "durationUs": 350_000}))
        m.cues += [(at, "whoosh", 0.45)]
    vs_y = top + (0.92 - top - 0.14) / 2 + 0.14 / 2 + 0.02 if portrait else 0.53  # middle of the gap, below plate one
    m.titles.append(_title(f"vs-{scene.id}", "VS", start + 800_000, end, round(vs_y, 4), 56.0, font=DISPLAY,
                           color="#FFFFFF", decoration={"strokeColor": "#000000", "strokeWidth": 6.0},
                           entrance={"preset": "slam", "durationUs": 300_000}))
    m.cues += [(start + 1_050_000, "impact", 0.6)]
    if title:
        m.titles.append(_title(f"tt-{scene.id}", title[:80], start, end, 0.08 if portrait else 0.1,
                               _fit_size(title[:80], portrait, 40.0, lines=1, span=0.9),
                               wrapWidth=0.9, entrance={"preset": "slide_down", "durationUs": SLIDE_US}))
    return m


def build(scene: Scene, start: int, length: int, portrait: bool, aspect: float, index: int,
          media: Media) -> tuple[Moment | None, str]:
    """Returns (moment, problem). A None moment means keep the face video for this span."""
    kind, data, end = scene.template, scene.data, start + length
    direction = str(data.get("pan") or ("right" if index % 2 == 0 else "left"))
    m = Moment()

    if kind in ("photo-full", "photo-behind"):
        photo = media.photo(str(data.get("photo") or ""))
        if photo is None:
            return None, "photo missing"
        m.main = _photo_clip(f"bg-{scene.id}", photo, length, direction)
        if kind == "photo-behind":
            m.images.append(face_aside(scene.id, start, length, portrait, aspect))
            imgs, titles, cues = _lower_third(scene, start + 500_000, end, portrait,
                                              0.64 if portrait else 0.68, 0.7 if portrait else 0.72, media)
            m.cues += [(start, "shutter", 0.45), (start + 150_000, "whoosh", 0.55), (max(start, end - 600_000), "whip", 0.4)]
        else:
            imgs, titles, cues = _lower_third(scene, start, end, portrait, 0.5, 0.7 if portrait else 0.72, media)
            m.cues += [(start, "shutter", 0.6), (start + 60_000, "whoosh", 0.4)]
        m.images += imgs
        m.titles += titles
        m.cues += cues
        return m, ""

    if kind == "photo-frames":
        slots = data.get("photos") or ([data["photo"]] if data.get("photo") else [])
        ids = [p for p in (media.photo(str(s)) for s in list(slots)[:3]) if p]
        if not ids:
            return None, "photos missing"
        m.main = _photo_clip(f"bg-{scene.id}", ids[0], length, direction, blurred=True)
        labels = data.get("labels") or []
        if len(ids) == 2:  # two sides: a duel, never two cards stacked on top of each other
            return _duel(scene, start, length, portrait, ids, labels, m, media), ""
        spots = ([(0.3, 0.33, -7), (0.7, 0.4, 6), (0.5, 0.62, -2)] if portrait
                 else [(0.2, 0.47, -6), (0.5, 0.42, 5), (0.8, 0.47, -3)])
        width = 0.44 if portrait else 0.26
        step = min(500_000, max(150_000, (length - 1_000_000) // max(1, len(ids) - 1)))
        land = 650_000
        for i, source_id in enumerate(ids):
            x, y, rot = spots[i]
            at = start + 250_000 + i * step
            if end - at < 700_000:
                break
            span = end - at
            # Polaroids glide down onto the table with a gentle twist, settle, then a slow push.
            m.images.append({
                "id": f"fr-{scene.id}-{i}", "sourceId": source_id, "startUs": at, "endUs": end,
                "x": x, "y": y, "width": width, "rotation": float(rot), "frame": POLAROID,
                "exit": {"preset": "fade", "durationUs": 250_000},
                "keys": [{"timeUs": 0, "scale": 1.15, "y": 0.3, "rotation": -10.0 if i % 2 == 0 else 10.0,
                          "opacity": 0.0, "easing": "ease_out"},
                         {"timeUs": min(land, span // 2), "scale": 1.0, "y": 0.5, "rotation": 0.0, "opacity": 1.0,
                          "easing": "linear"},
                         {"timeUs": span, "scale": 1.05, "y": 0.5, "rotation": 0.0, "opacity": 1.0}],
            })
            if i < len(labels) and str(labels[i]).strip():
                below = y + _height(media, source_id, width) / 2 + 34 / 720 + 0.025  # under the polaroid strip
                m.titles.append(_title(f"lb-{scene.id}-{i}", str(labels[i])[:40], at + land, end,
                                       round(min(0.95, below), 4), 22.0, x=x, color=INK, font=LABEL,
                                       decoration=_plate("#FFFFFF", 10.0),
                                       entrance={"preset": "slide_up", "durationUs": 400_000}))
            m.cues += [(at, "swoosh_up", 0.4), (at + land - 150_000, "shutter", 0.45)]
        title = str(data.get("title") or "").strip()
        if title:
            m.titles.append(_title(f"tt-{scene.id}", title[:80], start, end, 0.1 if portrait else 0.12,
                                   _fit_size(title[:80], portrait, 40.0, lines=1, span=0.9),
                                   wrapWidth=0.9, entrance={"preset": "slide_down", "durationUs": SLIDE_US}))
        return m, ""

    if kind == "text-punch":
        if not str(data.get("text") or "").strip():
            return None, "no text"
        text, highlight = _words(data)
        short = max(len(w) for w in (text + " " + highlight).split()) <= 12 and len(text) + len(highlight) <= 30
        if data.get("bg") == "behind" or (not data.get("bg") and not _has_visual(scene) and short):
            return _text_behind(scene, start, length, portrait, aspect, direction, media), ""
        main = (_backdrop(scene, start, length, direction, portrait, media) if data.get("bg") in ("face", "field")
                else _stage(scene, length, direction, portrait, index, media))
        visual = _has_visual(scene)
        block = _text_block(scene, start, end, portrait, (0.24 if portrait else 0.26) if visual else
                            (0.42 if portrait else 0.45), media, main, start)
        block.main = main
        below = (_visual_below(scene, start, end, portrait, min(0.6, block.bottom + 0.035), media)
                 if visual else Moment())
        if below.images:
            block.images = below.images + block.images
            block.cues += below.cues
        else:
            icon = data.get("icon") or "none"
            imgs, cues = _icon(str(icon), scene.id, start + 300_000, end, portrait,
                               0.8 if portrait else 0.86, 0.72 if portrait else 0.76, media)
            block.images = imgs + block.images
            block.cues += cues
        return block, ""

    if kind == "stat-count":
        parsed = _number(data.get("value"))
        if parsed is None:
            return None, "no number"
        value, decimals = parsed
        prefix, suffix = str(data.get("prefix") or "")[:10], str(data.get("suffix") or "")[:20]
        if prefix[-1:].isalpha() or prefix.endswith("."):  # "NO." -> "NO. 8", but "₹8"
            prefix += " "
        final = f"{prefix}{value:.{decimals}f}{suffix}"  # same format as the app's counter
        m.main = (_backdrop(scene, start, length, direction, portrait, media) if data.get("bg") in ("face", "field")
                  else _stage(scene, length, direction, portrait, index, media))
        count = min(1_500_000, max(600_000, (length - 600_000) // 2))
        at = start + 250_000
        landed = at + count
        y = (0.22 if portrait else 0.25) if _has_visual(scene) else (0.42 if portrait else 0.44)
        size = _fit_size(final, portrait, 110.0 if _has_visual(scene) else 120.0, lines=1, font=DISPLAY)
        text_bottom = y + size * 0.62 / 720 + 0.045 + (0.035 if data.get("label") else 0)
        visual = (_visual_below(scene, start, end, portrait, min(0.6, text_bottom + 0.04), media)
                  if _has_visual(scene) else Moment())
        burst = media.decor("burst")
        if burst and end - landed > 500_000:
            span = end - landed + 100_000
            m.images.append({
                "id": f"dc-burst-{scene.id}", "sourceId": burst, "startUs": landed - 100_000, "endUs": end,
                "x": 0.5, "y": y, "width": 0.95 if portrait else 0.5,
                "keys": [{"timeUs": 0, "scale": 0.3, "opacity": 0.0, "easing": "back_out"},
                         {"timeUs": 400_000, "scale": 1.0, "opacity": 0.85, "rotation": 0.0, "easing": "linear"},
                         {"timeUs": span, "scale": 1.1, "opacity": 0.6, "rotation": 30.0}],
                "exit": {"preset": "fade", "durationUs": 250_000}})
        label = str(data.get("label") or "").strip()
        lower = label.lower()
        if visual.images:
            m.images = visual.images + m.images
            m.cues += visual.cues
        else:
            icon = data.get("icon") or ("stumps" if "wicket" in lower else "bat" if "run" in lower or "six" in lower else "ball")
            imgs, cues = _icon(str(icon), scene.id, start + 200_000, end, portrait,
                               0.82 if portrait else 0.87, 0.74 if portrait else 0.75, media)
            m.images = imgs + m.images
            m.cues += cues
        m.titles += _kicker(scene, start + 100_000, end, y - size * 0.75 / 720 - 0.05)
        m.titles.append(_title(f"st-{scene.id}", final, at, end, y, size, color=YELLOW, wrapWidth=0.95, font=DISPLAY,
                               entrance={"preset": "count_up", "durationUs": count},
                               counter={"from": 0.0, "to": value, "decimals": decimals, "prefix": prefix, "suffix": suffix},
                               decoration={"strokeColor": "#000000", "strokeWidth": 5.0}))
        if label:
            m.titles.append(_title(f"sl-{scene.id}", label[:60], landed, end, round(y + size * 0.62 / 720 + 0.045, 4),
                                   _fit_size(label[:60], portrait, 30.0, lines=1, span=0.9), wrapWidth=0.92,
                                   entrance={"preset": "slide_up", "durationUs": SLIDE_US}))
        length_us = m.main["outUs"] - m.main["inUs"]
        m.main["pointEffects"] = m.main.get("pointEffects", []) + _point("flash", landed - start, 200_000, 0.45, length_us)
        m.cues += [(at, "riser", 0.35), (landed, "impact", 0.6), (landed + 50_000, "ding", 0.35)]
        if data.get("cheer"):
            m.cues.append((landed + 100_000, "crowd_cheer", 0.35))
        return m, ""

    if kind == "headline":
        if not str(data.get("text") or "").strip():
            return None, "no text"
        face = media.face(start, end)
        block = _text_block(scene, start, end, portrait, 0.7 if portrait else 0.75, media, None, start, avoid=face)
        for t in block.titles:  # over footage: a heavier outline keeps it readable without a box
            if t["id"].startswith("tp-"):
                t["decoration"] = {"strokeColor": "#000000", "strokeWidth": 6.0}
        block.punch = True
        return block, ""

    if kind == "player-cutout":
        m = _player_cutouts(scene, start, end, portrait, media)
        return (m, "") if m.images else (None, "player photos missing")

    if kind == "split":
        return _split_screen(scene, start, length, portrait, media), ""

    if kind == "news-cards":
        m = _news_cards(scene, start, length, portrait, direction, media)
        return (m, "") if m.main else (None, "card photos missing")

    if kind == "photo-overlay":
        photo = media.photo(str(data.get("photo") or ""))
        if photo is None:
            return None, "photo missing"
        face = media.face(start, end)
        if portrait:  # the card sits beside/below the face, never on it
            x, y, width = (0.5, 0.74, 0.5) if face and face[3] < 0.62 else (0.5, 0.4, 0.62)
        else:
            x, y, width = (0.78, 0.45, 0.3) if not face or face[0] < 0.55 else (0.22, 0.45, 0.3)
        m.images.append({
            "id": f"ov-{scene.id}", "sourceId": photo, "startUs": start, "endUs": end,
            "x": x, "y": y, "width": width,
            "frame": {**POLAROID, "bottom": 10.0}, "exit": {"preset": "fade", "durationUs": 300_000},
            # Glides in from the right with a slight tilt and a fade, settles, then keeps a slow push-in.
            "keys": [{"timeUs": 0, "scale": 0.9, "x": 0.62, "rotation": -6.0, "opacity": 0.0, "easing": "ease_out"},
                     {"timeUs": min(600_000, length // 2), "scale": 1.0, "x": 0.5, "rotation": 0.0, "easing": "linear"},
                     {"timeUs": length, "scale": 1.06, "x": 0.5, "rotation": 0.0}],
        })
        name = str(data.get("caption") or "").strip()[:40]
        if name:  # name on a yellow plate at the card's lower edge (photo assumed ~4:3)
            edge = y + width * 0.375 * media.canvas[0] / media.canvas[1]
            m.titles.append(_title(f"tx-{scene.id}", name, start + 500_000, end, round(min(0.86, edge), 4), 22.0,
                                   x=x, color=INK, font=LABEL, decoration=_plate(YELLOW),
                                   entrance={"preset": "slide_up", "durationUs": 400_000}))
        m.cues += [(start, "whoosh", 0.45), (start + 450_000, "shutter", 0.5)]
        return m, ""

    return None, f"unknown native moment {kind!r}"
