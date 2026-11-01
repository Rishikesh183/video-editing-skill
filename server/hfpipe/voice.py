"""Voice videos: the creator records only their voice and the whole picture is generated.

Plan: Claude splits the captions into beats, picks a colour theme and asks for images. Author: once the images are
in, one Claude call per beat writes either an animated scene (HTML + GSAP on templates/_voice) or a camera move over
a screenshot. Those are rendered, self-reviewed from their frames, fixed once, and assembled into an editable
CutAgent project: scenes on the main track, voice, captions, sound effects and a quiet pulse as audio and text.
"""

import hashlib
import html
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import config, media, sfx, store, tighten, usage
from .export import MAX_LAYERS, MAX_TITLES, MIN_CLIP_US, _caption_titles, _fit_still_durations, _max_active, _pack_titles, source_record, us
from .models import CANVAS, ORIENT, AssetSlot, Cue, Job, Plan, Ratio, RenderResult, Scene
from .plan import frame, slug
from .planner import PlannerError
from .render import TemplateError, run_hyperframes
from .review import _font, _sheet

VOICE_DIR = config.PLANNER_DIR / "voice"
KIT_DIR = config.TEMPLATES / "_voice"
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".webm", ".amr", ".3gp"}
VOICE_FILE = "voice.m4a"
MIN_BEAT = 2.0  # seconds
TAIL = 0.8  # the picture holds a moment after the last word
RENDER_FPS = 30
PULSE_VOLUME = 0.16
SFX_VOLUME = 0.9
TRANSITION_US = 300_000
MISSED_SPEECH = 3.0  # seconds of sound with no captions before the creator is warned
MIN_CUE_GAP = 1.0  # seconds between sound effects
MAX_ZOOM = 2.6

# Same look in every video, recoloured to fit the script.
THEMES: dict[str, dict[str, str]] = {
    "hazard": {"for": "accidents, safety, investigations, evidence, law", "bg": "#17191d", "panel": "#22252b", "panel-hi": "#2b2f37",
               "accent": "#ffd000", "accent-ink": "#111111", "accent2": "#ef4444", "ink": "#ffffff", "muted": "#a3a9b5", "stripe": "#111111",
               "display": "Barlow"},
    "ember": {"for": "startups, launches, energy, sport, speed", "bg": "#1a1411", "panel": "#2a201a", "panel-hi": "#352a22",
              "accent": "#ff7a1a", "accent-ink": "#160c05", "accent2": "#ffd08a", "ink": "#fff8f1", "muted": "#b9a99c", "stripe": "#160c05",
              "display": "Barlow"},
    "crimson": {"for": "crime, scandal, risk, failure, warnings", "bg": "#190f13", "panel": "#2a1a20", "panel-hi": "#35222a",
                "accent": "#ff4d5e", "accent-ink": "#1b0508", "accent2": "#ffb4a2", "ink": "#fff5f5", "muted": "#bda5ab", "stripe": "#1b0508",
                "display": "Anton"},
    "signal": {"for": "money, markets, business, growth", "bg": "#0f1714", "panel": "#1a2620", "panel-hi": "#22312a",
               "accent": "#2ee59d", "accent-ink": "#04140d", "accent2": "#d6f06a", "ink": "#f4fff9", "muted": "#9db3a8", "stripe": "#04140d",
               "display": "Barlow"},
    "ocean": {"for": "technology, software, data, infrastructure, science", "bg": "#0c1620", "panel": "#15242f", "panel-hi": "#1d2f3c",
              "accent": "#38d6ff", "accent-ink": "#04131b", "accent2": "#8fb4ff", "ink": "#f3fbff", "muted": "#9db1bf", "stripe": "#04131b",
              "display": "Barlow"},
    "violet": {"for": "AI, the future, creativity, imagination", "bg": "#14101f", "panel": "#211a33", "panel-hi": "#2b2342",
               "accent": "#b388ff", "accent-ink": "#120a22", "accent2": "#ff7ad9", "ink": "#faf7ff", "muted": "#ada5c0", "stripe": "#120a22",
               "display": "Montserrat"},
    "gold": {"for": "history, legacy, luxury, great achievements", "bg": "#16130c", "panel": "#241f14", "panel-hi": "#2f291b",
             "accent": "#ffc857", "accent-ink": "#171104", "accent2": "#e8dcc0", "ink": "#fffaf0", "muted": "#b8ad97", "stripe": "#171104",
             "display": "Archivo Black"},
    "paper": {"for": "learning, how-to guides, calm personal stories", "bg": "#f4efe6", "panel": "#fffaf1", "panel-hi": "#ece5d8",
              "accent": "#d9480f", "accent-ink": "#ffffff", "accent2": "#1d6f5f", "ink": "#1c1a17", "muted": "#6b6459", "stripe": "#1c1a17",
              "display": "Barlow"},
}
DEFAULT_THEME = "hazard"
SOUNDS = {"click": 0.5, "soft_click": 0.4, "tap": 0.45, "tick": 0.45, "pop": 0.4, "key": 0.35, "typing": 0.2, "ding": 0.32, "impact": 0.45}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


_NUM, _STR = {"type": "number"}, {"type": "string"}
_SOUND_LIST = {"type": "array", "items": _obj({"at": _NUM, "sound": {"type": "string", "enum": list(SOUNDS)}})}
PLAN_SCHEMA = _obj({
    "story": _STR,
    "theme": {"type": "string", "enum": list(THEMES)},
    "beats": {"type": "array", "items": _obj({
        "id": _STR, "start": _NUM, "end": _NUM, "kind": {"type": "string", "enum": ["story", "screen"]},
        "title": _STR, "brief": _STR, "images": {"type": "array", "items": _STR}, "reason": _STR})},
    "assets": {"type": "array", "items": _obj({"slot": _STR, "description": _STR})},
    "corrections": {"type": "array", "items": _obj({"wrong": _STR, "right": _STR})},
})
STORY_SCHEMA = _obj({"css": _STR, "html": _STR, "js": _STR, "sounds": _SOUND_LIST})
SCREEN_SCHEMA = _obj({
    "crop": {"type": "array", "items": _NUM},
    "moves": {"type": "array", "items": _obj({"at": _NUM, "x": _NUM, "y": _NUM, "zoom": _NUM})},
    "ring": _obj({"show": {"type": "boolean"}, "at": _NUM, "end": _NUM, "x": _NUM, "y": _NUM}),
    "callouts": {"type": "array", "items": _obj({"at": _NUM, "end": _NUM, "text": _STR, "y": _NUM})},
    "sounds": _SOUND_LIST, "note": _STR,
})
REVIEW_SCHEMA = _obj({
    "summary": _STR,
    "edit_issues": {"type": "array", "items": _obj({"scene_id": _STR, "problem": _STR, "fix": _STR})},
    "photo_issues": {"type": "array", "items": _obj({"slot": _STR, "problem": _STR})},
})
REVIEW_BRIEF = """You are reviewing an explainer video before the creator sees it. Read every image file listed below.
Each tile is a frame from the video, labelled with its scene id and time. Captions are drawn later over the bottom 16%,
so an empty strip there is correct. Frames of "screen" scenes show the final camera position.

Report only real defects a viewer would notice: elements overlapping or colliding; text cut off, running off screen,
wrapping badly or breaking mid-word; code or HTML showing as text; unreadably small text; poor contrast; a nearly
empty or lopsided frame; a picture that does not fit what is being said; anything in the bottom 16% that captions would
cover; a photo or screenshot that shows the wrong thing; elements closer than about 50 px to the left, right or top edge;
a statement, name or number that was not said (placeholders that look like real facts count). Say what is wrong and give one concrete fix for that scene.
If a scene is fine, leave it out. If everything is clean, return empty lists."""
_BANNED = {
    r"Date\.now|new Date\b": "Date", r"Math\.random": "Math.random", r"setTimeout|setInterval|requestAnimationFrame": "timers",
    r"\bfetch\s*\(|XMLHttpRequest": "network calls", r"\btl\.call\s*\(|\.call\(\s*\(\)": "tl.call", r"className\s*:": "tweening className",
}
_CLIP_CLASS = re.compile(r"""class\s*=\s*["'][^"']*\bclip\b|\.clip\b""")
_ARROWS = re.compile(r"[←-⇿■-◿➔-➿]")


def _text(name: str) -> str:
    return (VOICE_DIR / name).read_text(encoding="utf-8")


def _theme_lines() -> str:
    return "\n".join(f"- {name}: {t['for']}" for name, t in THEMES.items())


def plan_system() -> str:
    return f"{_text('system.md')}\n\nThemes:\n{_theme_lines()}\n\n{_text('lessons.md')}"


def author_system() -> str:
    return f"{_text('author.md')}\n\n{_text('lessons.md')}\n\n{_text('example.md')}"


def remember(lesson: str) -> bool:
    lesson = " ".join(lesson.split())
    path = VOICE_DIR / "lessons.md"
    current = path.read_text(encoding="utf-8")
    if not lesson or lesson.lower() in current.lower():
        return False
    if "## Added from re-plan notes" not in current:
        current = current.rstrip() + "\n\n## Added from re-plan notes\n"
    path.write_text(current.rstrip() + f"\n- {lesson}\n", encoding="utf-8")
    return True


def ask_claude(job_id: str, step: str, system: str, prompt: str, schema: dict[str, Any], cwd: Path | None = None,
               read_files: bool = False) -> dict[str, Any]:
    """One headless Claude call with a schema-checked answer. The system prompt goes through a file: these briefs are
    too long for a Windows command line. Without `cwd` it runs in an empty folder, so no project settings load."""
    with tempfile.TemporaryDirectory(prefix="hf-voice-") as tmp:
        system_file = Path(tmp) / "system.md"
        system_file.write_text(system, encoding="utf-8")
        cmd = [config.claude_executable(), "-p", "Do the task described in the input.", "--model", config.VOICE_MODEL,
               "--tools", "Read" if read_files else "", *(["--allowedTools", "Read"] if read_files else []),
               "--system-prompt-file", str(system_file), "--json-schema", json.dumps(schema), "--output-format", "json",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--setting-sources", ""]
        started = time.monotonic()
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=str(cwd or tmp), timeout=900)
    if proc.returncode != 0:
        raise PlannerError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[-600:]}")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PlannerError(f"claude returned non-JSON output: {proc.stdout[-300:]}") from e
    result.setdefault("duration_ms", round((time.monotonic() - started) * 1000))
    usage.record(job_id, step, result)
    answer = result.get("structured_output")
    if result.get("is_error") or not isinstance(answer, dict):
        raise PlannerError(f"claude gave no usable answer: {str(result.get('result'))[:300]}")
    return answer


# ---- planning ---------------------------------------------------------------------------------------------------


def _srt(captions: list[Cue]) -> str:
    return "\n".join(f"{c.start:.2f}-{c.end:.2f} {c.text}" for c in captions)


def apply_corrections(captions: list[Cue], fixes: list[dict[str, str]]) -> list[Cue]:
    """Fix names the transcription misheard. Whole words only, so 'NBS' cannot match inside another word."""
    out = []
    for cue in captions:
        text = cue.text
        for fix in fixes:
            wrong, right = fix.get("wrong", "").strip(), fix.get("right", "").strip()
            if wrong and right:
                text = re.sub(rf"(?<!\w){re.escape(wrong)}(?!\w)", lambda _: right, text, flags=re.I)
        out.append(cue.model_copy(update={"text": text}))
    return out


def tidy(scenes: list[Scene], captions: list[Cue], duration: float) -> list[Scene]:
    """Beats fill the timeline exactly: start on a caption where one is near, no gaps, no slivers, tail at the end."""
    starts = sorted(c.start for c in captions)

    def snap(t: float) -> float:
        near = min(starts, key=lambda s: abs(s - t), default=t)
        return near if abs(near - t) <= 0.8 else t

    ordered = sorted((s for s in scenes if s.start < duration), key=lambda s: s.start)
    if not ordered:
        raise ValueError("the plan has no beats")
    kept: list[tuple[float, Scene]] = []
    for scene in ordered:
        at = 0.0 if not kept else frame(snap(scene.start))
        if kept and at - kept[-1][0] < MIN_BEAT:
            kept[-1] = (kept[-1][0], scene)  # the previous beat was a sliver: this one takes over from its start
            continue
        kept.append((at, scene))
    total = frame(duration + TAIL)
    if len(kept) > 1 and total - kept[-1][0] < MIN_BEAT:
        kept.pop()
    seen: set[str] = set()
    out = []
    for i, (at, scene) in enumerate(kept):
        sid = slug(scene.id) or f"b{i + 1}"
        sid = sid if sid not in seen else f"{sid}-{i + 1}"
        seen.add(sid)
        out.append(scene.model_copy(update={"id": sid, "start": at, "end": kept[i + 1][0] if i + 1 < len(kept) else total}))
    return out


def timeline(job: Job) -> list[Scene]:
    """The scenes that are on, stretched to close the gaps left by any the creator switched off."""
    assert job.plan is not None
    scenes = [s for s in job.plan.scenes if s.approved]
    if not scenes:
        raise ValueError("switch at least one scene on")
    out = []
    for i, scene in enumerate(scenes):
        out.append(scene.model_copy(update={"start": 0.0 if i == 0 else scene.start,
                                            "end": scenes[i + 1].start if i + 1 < len(scenes) else job.plan.scenes[-1].end}))
    return out


def speech_gaps(job: Job) -> list[tuple[float, float]]:
    """Stretches with sound but no captions: transcription services can silently skip seconds of speech."""
    duration = job.video.duration_us / 1e6
    quiet = sorted((max(0.0, a), min(duration, b)) for a, b in tighten.silences(store.job_dir(job.id) / job.video.file))
    gaps, cursor = [], 0.0
    for start, end in [*quiet, (duration, duration)]:  # sound runs from cursor to start
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    for cue in job.captions:
        pieces = []
        for a, b in gaps:
            if cue.end <= a or cue.start >= b:
                pieces.append((a, b))
            else:
                pieces += [p for p in ((a, cue.start), (cue.end, b)) if p[1] > p[0]]
        gaps = pieces
    return [g for g in gaps if g[1] - g[0] >= MISSED_SPEECH]


def gap_note(job: Job) -> str:
    try:
        gaps = speech_gaps(job)
    except (OSError, subprocess.SubprocessError):
        return ""
    if not gaps:
        return ""
    spans = ", ".join(f"{a // 60:.0f}:{a % 60:02.0f}-{b // 60:.0f}:{b % 60:02.0f}" for a, b in gaps[:4])
    return f"The captions may have missed speech at {spans}. Add them in the app or give the script, or those seconds stay uncaptioned."


def plan_job(job: Job, notes: str = "", revise: bool = False) -> tuple[Plan, list[Cue]]:
    duration = job.video.duration_us / 1e6
    parts = [f"Video duration: {duration:.1f}s.", "Captions (seconds):", _srt(job.captions)]
    if job.script.strip():
        parts += ["Creator's script:", job.script.strip()]
    if notes.strip():
        parts.append("Creator's notes: " + notes.strip())
    if revise and job.plan is not None:
        parts += ["This is a REVISION. Current plan:", job.plan.model_dump_json()]
    answer = ask_claude(job.id, "voice plan", plan_system(), "\n\n".join(parts), PLAN_SCHEMA)
    assets = [AssetSlot(slot=slug(a["slot"]), description=a["description"]) for a in answer["assets"] if slug(a["slot"])]
    if job.plan is not None:  # images already uploaded stay attached to their slots
        held = {a.slot: a.file for a in job.plan.assets_needed}
        assets = [a.model_copy(update={"file": held.get(a.slot)}) for a in assets]
    known = {a.slot for a in assets}
    scenes = []
    for beat in answer["beats"]:
        images = [slug(i) for i in beat["images"] if slug(i) in known]
        kind = beat["kind"] if beat["kind"] == "story" or images else "story"
        images = images[:1] if kind == "screen" else images  # one camera, one picture
        scenes.append(Scene(id=beat["id"], start=beat["start"], end=beat["end"], mode="fullframe", template=kind, reason=beat["reason"],
                            data={"title": beat["title"], "brief": beat["brief"], "images": images}))
    used = {i for s in scenes for i in s.data["images"]}
    theme = answer["theme"] if answer["theme"] in THEMES else DEFAULT_THEME
    plan = Plan(story=answer["story"], theme=theme, scenes=tidy(scenes, job.captions, duration),
                assets_needed=[a for a in assets if a.slot in used])
    return plan, apply_corrections(job.captions, answer["corrections"])


# ---- authoring ---------------------------------------------------------------------------------------------------


def slot_file(job: Job, slot: str) -> Path | None:
    assert job.plan is not None
    entry = next((a for a in job.plan.assets_needed if a.slot == slot), None)
    path = store.job_dir(job.id) / entry.file if entry and entry.file else None
    return path if path and path.exists() else None


def kind_of(job: Job, scene: Scene) -> str:
    """A screen beat whose image never arrived becomes an animated scene instead."""
    images = [slot_file(job, s) for s in scene.data.get("images", [])]
    return "screen" if scene.template == "screen" and images and images[0] else "story"


def _scene_words(job: Job, scene: Scene) -> str:
    cues = [c for c in job.captions if c.end > scene.start and c.start < scene.end]
    return "\n".join(f"{max(0.0, c.start - scene.start):.2f} {c.text}" for c in cues)


def _scene_file(job: Job, scene: Scene) -> Path:
    return store.job_dir(job.id) / "voice" / f"{scene.id}.json"


def _scene_key(job: Job, scene: Scene) -> str:
    assert job.plan is not None
    parts = [kind_of(job, scene), job.plan.theme, scene.data.get("brief", ""), f"{scene.end - scene.start:.3f}", _scene_words(job, scene),
             _text("author.md"), _text("example.md"), _text("screen.md")]  # a better brief refreshes old scenes on the next render
    for slot in scene.data.get("images", []):
        path = slot_file(job, slot)
        parts.append(hashlib.sha256(path.read_bytes()).hexdigest() if path else "-")
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:20]


def _lint(doc: dict[str, Any]) -> str:
    source = f"{doc.get('css', '')}\n{doc.get('html', '')}\n{doc.get('js', '')}"
    for pattern, label in _BANNED.items():
        if re.search(pattern, source):
            return f"{label} is not allowed in a scene"
    if _CLIP_CLASS.search(source):
        return 'the class name "clip" is reserved by the renderer'
    if re.search(r"<script|https?://(?!www\.w3\.org)", source):
        return "scenes cannot contain <script> tags or external URLs"
    if not str(doc.get("js", "")).strip():
        return "the scene has no animation"
    return ""


def _author_prompt(job: Job, scene: Scene, feedback: str, current: dict[str, Any] | None) -> tuple[str, list[str]]:
    assert job.plan is not None
    w, h = CANVAS[job.ratios[0]]
    length = scene.end - scene.start
    images = []
    for slot in scene.data.get("images", []):
        path = slot_file(job, slot)
        if path:
            with Image.open(path) as im:
                images.append(f"{slot}.png ({im.width}x{im.height})")
    others = [f"{s.id} ({s.start:.0f}-{s.end:.0f}s): {s.data.get('title', '')}" for s in job.plan.scenes]
    parts = [f"Canvas: W={w} H={h}. Scene length: {length:.2f}s (dur).", f"Theme: {job.plan.theme}.",
             "The film: " + job.plan.story, "All beats: " + "; ".join(others),
             *([f"Creator's script (the source for any wording on screen): {job.script.strip()}"] if job.script.strip() else []),
             f"This beat ({scene.id}): " + scene.data.get("brief", ""), "Timed words (seconds from the start of this scene):",
             _scene_words(job, scene) or "(no speech in this beat)"]
    parts.append("Images in the current folder, read each one: " + ", ".join(images) if images else "No images for this beat.")
    if current and feedback:
        parts += ["Current scene:", json.dumps(current, ensure_ascii=False)]
    if feedback:
        parts.append("Fix these: " + feedback)
    return "\n\n".join(parts), [i.split(" ")[0] for i in images]


def _ask_scene(job: Job, scene: Scene, prompt: str, images: list[str]) -> dict[str, Any]:
    screen = kind_of(job, scene) == "screen"
    with tempfile.TemporaryDirectory(prefix="hf-scene-") as tmp:
        work = Path(tmp)
        for name in images:
            source = slot_file(job, Path(name).stem)
            if source:
                shutil.copyfile(source, work / name)
        system = _text("screen.md") if screen else author_system()
        schema = SCREEN_SCHEMA if screen else STORY_SCHEMA
        return ask_claude(job.id, f"voice {'screen' if screen else 'scene'}", system, prompt, schema, work, bool(images))


def fallback_doc(scene: Scene) -> dict[str, Any]:
    """A plain title card, so one bad scene can never sink the whole video."""
    title = html.escape(str(scene.data.get("title") or ""))
    return {"css": ".t { left: 8%; right: 8%; top: 38%; text-align: center; font-size: 110px; }", "html": f'<div class="grain"></div><div class="abs t head" id="t">{title}</div>',
            "js": 'KIT.slide(tl, "#t", 0.2);', "sounds": [{"at": 0.2, "sound": "click"}], "fallback": True}


def author(job: Job, scene: Scene, feedback: str = "", error: str = "") -> dict[str, Any]:
    """The scene's content, reused from disk until its brief, length, words, theme or images change."""
    path = _scene_file(job, scene)
    key = _scene_key(job, scene)
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if current and current.get("key") == key and not feedback and not error:
        return current
    note = feedback or error
    prompt, images = _author_prompt(job, scene, note, current if current and current.get("key") == key else None)
    doc = _ask_scene(job, scene, prompt, images)
    if kind_of(job, scene) == "story":
        problem = _lint(doc)
        if problem:  # one retry with the reason, then a plain card rather than a dead job
            prompt, images = _author_prompt(job, scene, f"The scene was rejected: {problem}.", doc)
            doc = _ask_scene(job, scene, prompt, images)
            if _lint(doc):
                doc = fallback_doc(scene)
    doc["key"] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


# ---- rendering ---------------------------------------------------------------------------------------------------


def theme_css(name: str, w: int, h: int) -> str:
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    r, g, b = (int(t["accent"][i:i + 2], 16) for i in (1, 3, 5))
    return (f':root {{ --w: {w}px; --h: {h}px; --bg: {t["bg"]}; --panel: {t["panel"]}; --panel-hi: {t["panel-hi"]}; '
            f'--line: {"rgba(0,0,0,.12)" if name == "paper" else "rgba(255,255,255,.12)"}; --accent: {t["accent"]}; --accent-ink: {t["accent-ink"]}; '
            f'--accent2: {t["accent2"]}; --ink: {t["ink"]}; --muted: {t["muted"]}; --stripe: {t["stripe"]}; --ok: #22c55e; --bad: #ef4444; '
            f'--warn: #f59e0b; --glow: rgba({r},{g},{b},.28); --display: "{t["display"]}"; }}')


def scene_page(doc: dict[str, Any], theme: str, ratio: Ratio, seconds: float, last: bool = False) -> str:
    w, h = CANVAS[ratio]
    fade = 0.45 if last else 0.25
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={w}, height={h}" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <link rel="stylesheet" href="assets/kit.css" />
  <style>{theme_css(theme, w, h)}</style>
  <style>{doc.get("css", "")}</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{seconds:g}" data-width="{w}" data-height="{h}">
    <div id="scene" class="scene clip" data-start="0" data-duration="{seconds:g}" data-track-index="0">
{doc.get("html", "")}
    </div>
  </div>
  <script src="assets/kit.js"></script>
  <script>
    const W = {w}, H = {h}, dur = {seconds:g};
    const tl = KIT.timeline();
{doc.get("js", "")}
    tl.to("#scene", {{ opacity: 0, duration: {fade}, ease: "power2.in" }}, Math.max(0, dur - {fade}));
    tl.seek(0);
  </script>
</body>
</html>
"""


def _render_seconds(scene: Scene) -> float:
    """A frame longer than the beat: the clip is trimmed to the beat, so the voice can never drift."""
    return (math.ceil((scene.end - scene.start) * RENDER_FPS) + 2) / RENDER_FPS


def render_story(job: Job, scene: Scene, doc: dict[str, Any], last: bool = False) -> Path:
    seconds = _render_seconds(scene)
    ratio = job.ratios[0]
    page = scene_page(doc, job.plan.theme if job.plan else DEFAULT_THEME, ratio, seconds, last)
    images = [p for p in (slot_file(job, s) for s in scene.data.get("images", [])) if p]
    digest = hashlib.sha256(page.encode())
    for part in [KIT_DIR / "kit.css", KIT_DIR / "kit.js", *images]:
        digest.update(part.read_bytes())
    cached = config.RENDER_CACHE / f"voice-{digest.hexdigest()[:20]}.mp4"
    dest = store.job_dir(job.id) / "renders" / f"{scene.id}-{ORIENT[ratio]}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not cached.exists():
        build = store.job_dir(job.id) / "build" / scene.id
        if build.exists():
            shutil.rmtree(build)
        (build / "assets").mkdir(parents=True)
        shutil.copytree(config.TEMPLATES / "_shared" / "fonts", build / "assets" / "fonts")
        for part in (KIT_DIR / "kit.css", KIT_DIR / "kit.js"):
            shutil.copyfile(part, build / "assets" / part.name)
        for image in images:
            shutil.copyfile(image, build / "assets" / image.name)
        (build / "index.html").write_text(page, encoding="utf-8")
        (build / "meta.json").write_text(json.dumps({"id": scene.id, "name": scene.id}), encoding="utf-8")
        (build / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "assets"}}), encoding="utf-8")
        variables = build / "variables.json"
        variables.write_text("{}", encoding="utf-8")
        cached.parent.mkdir(parents=True, exist_ok=True)
        raw = cached.with_suffix(".raw.mp4")
        run_hyperframes(build, raw, variables)
        partial = cached.with_suffix(".partial.mp4")
        sfx.finish(raw, [], seconds, partial)
        raw.unlink(missing_ok=True)
        partial.replace(cached)
    shutil.copyfile(cached, dest)
    return dest


def _fit(values: Any, default: list[float]) -> list[float]:
    try:
        crop = [min(1.0, max(0.0, float(v))) for v in values][:4]
    except (TypeError, ValueError):
        return default
    good = len(crop) == 4 and crop[2] - crop[0] >= 0.2 and crop[3] - crop[1] >= 0.2
    return crop if good else default


def screen_still(job: Job, scene: Scene, doc: dict[str, Any]) -> tuple[Path, dict[str, float]]:
    """The cropped screenshot placed on a canvas-sized still. Near the canvas shape it fills the frame; otherwise it
    sits whole on the theme's ground. Returns the file and where the crop landed, for mapping image points."""
    assert job.plan is not None
    w, h = CANVAS[job.ratios[0]]
    source = slot_file(job, scene.data["images"][0])
    assert source is not None
    crop = _fit(doc.get("crop"), [0.0, 0.0, 1.0, 1.0])
    with Image.open(source) as opened:
        im = opened.convert("RGB")
    l, t, r, b = crop[0] * im.width, crop[1] * im.height, crop[2] * im.width, crop[3] * im.height
    cw, ch = r - l, b - t
    aspect = (cw / ch) / (w / h)
    scale = max(w / cw, h / ch) if 0.88 <= aspect <= 1.14 else min(w / cw, h / ch)
    dw, dh = cw * scale, ch * scale
    place = {"x": (w - dw) / 2, "y": (h - dh) / 2, "w": dw, "h": dh, "crop": crop}  # type: ignore[dict-item]
    canvas = Image.new("RGB", (w, h), THEMES.get(job.plan.theme, THEMES[DEFAULT_THEME])["bg"])
    canvas.paste(im.crop((round(l), round(t), round(r), round(b))).resize((round(dw), round(dh)), Image.LANCZOS), (round(place["x"]), round(place["y"])))
    dest = store.job_dir(job.id) / "voice" / f"still-{scene.id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)
    return dest, place


def camera(place: dict[str, Any], w: int, h: int, x: float, y: float, zoom: float) -> dict[str, float]:
    """Keyframe offsets that centre image point (x, y) at `zoom`, stopping before an edge of the frame shows."""
    l, t, r, b = place["crop"]
    u = (place["x"] + (x - l) / max(1e-6, r - l) * place["w"]) / w
    v = (place["y"] + (y - t) / max(1e-6, b - t) * place["h"]) / h
    room = (zoom - 1) / 2
    return {"scale": round(zoom, 3), "x": round(min(0.5 + room, max(0.5 - room, 0.5 - (u - 0.5) * zoom)), 4),
            "y": round(min(0.5 + room, max(0.5 - room, 0.5 - (v - 0.5) * zoom)), 4)}


def screen_keys(doc: dict[str, Any], place: dict[str, Any], w: int, h: int, length_us: int) -> list[dict[str, Any]]:
    moves = sorted(doc.get("moves") or [], key=lambda m: m.get("at", 0))
    keys: list[dict[str, Any]] = []
    for move in moves:
        at = min(length_us, max(0, us(float(move.get("at", 0)))))
        if keys and at <= keys[-1]["timeUs"]:
            continue
        zoom = min(MAX_ZOOM, max(1.0, float(move.get("zoom", 1.0))))
        keys.append({"timeUs": at, **camera(place, w, h, float(move.get("x", 0.5)), float(move.get("y", 0.5)), zoom), "easing": "ease_in_out"})
    if not keys or keys[0]["timeUs"] > 0:
        keys.insert(0, {"timeUs": 0, "scale": 1.0, "x": 0.5, "y": 0.5, "easing": "ease_in_out"})
    if keys[-1]["timeUs"] < length_us:
        end = keys[-1]
        keys.append({**end, "timeUs": length_us, "scale": round(end["scale"] * 1.02, 3)})  # the slight drift of a held shot
    keys[-1]["easing"] = "linear"
    return keys


def ring_file(job: Job) -> Path:
    assert job.plan is not None
    dest = store.job_dir(job.id) / "voice" / "ring.png"
    color = THEMES.get(job.plan.theme, THEMES[DEFAULT_THEME])["accent"]
    im = Image.new("RGBA", (420, 420), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.ellipse((14, 14, 406, 406), outline=(0, 0, 0, 120), width=26)
    draw.ellipse((18, 18, 402, 402), outline=color, width=16)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    return dest


def clean_callout(text: str) -> str:
    return " ".join(_ARROWS.sub(" ", str(text)).split())


def _key_at(keys: list[dict[str, Any]], at_us: int) -> dict[str, Any]:
    return next((k for k in reversed(keys) if k["timeUs"] <= at_us), keys[0])


def _point(place: dict[str, Any], w: int, h: int, x: float, y: float, key: dict[str, Any]) -> tuple[float, float]:
    l, t, r, b = place["crop"]
    u = (place["x"] + (x - l) / max(1e-6, r - l) * place["w"]) / w
    v = (place["y"] + (y - t) / max(1e-6, b - t) * place["h"]) / h
    return 0.5 + (u - 0.5) * key["scale"] + key["x"] - 0.5, 0.5 + (v - 0.5) * key["scale"] + key["y"] - 0.5


def build(job: Job, ratio: Ratio) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Path], list[str]]:
    """Returns (project, sources, {bundle_path: local_file}, warnings), same as export.build."""
    from .media import probe, sha256

    assert job.plan is not None
    jdir = store.job_dir(job.id)
    w, h = CANVAS[ratio]
    theme = THEMES.get(job.plan.theme, THEMES[DEFAULT_THEME])
    files: dict[str, Path] = {}
    sources: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add(source_id: str, local: Path) -> str:
        path = f"media/{source_id}{local.suffix}"
        if path not in files:
            files[path] = local
            sources.append(source_record(source_id, probe(local), path, sha256(local)))
        return source_id

    scenes = timeline(job)
    clips: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    titles: list[dict[str, Any]] = []
    cues: list[tuple[float, str, float]] = []
    for scene in scenes:
        start, length = us(scene.start), us(scene.end) - us(scene.start)
        doc = json.loads(_scene_file(job, scene).read_text(encoding="utf-8"))
        cues += [(scene.start + float(c["at"]), c["sound"], SOUNDS[c["sound"]]) for c in doc.get("sounds", []) if c.get("sound") in SOUNDS]
        if doc.get("note"):
            warnings.append(f"{scene.id}: {doc['note']}")
        if kind_of(job, scene) == "story":
            sid = add(f"gfx-{scene.id}", jdir / "renders" / f"{scene.id}-{ORIENT[ratio]}.mp4")
            clips.append({"id": f"v-{scene.id}", "sourceId": sid, "inUs": 0, "outUs": length, "muted": True})
            continue
        still, place = screen_still(job, scene, doc)
        keys = screen_keys(doc, place, w, h, length)
        clips.append({"id": f"v-{scene.id}", "sourceId": add(f"shot-{scene.id}", still), "inUs": 0, "outUs": length, "muted": True, "keys": keys})
        ring = doc.get("ring") or {}
        if ring.get("show"):
            at, end = start + us(float(ring.get("at", 0))), min(start + length, start + us(float(ring.get("end", 0))))
            if end - at >= MIN_CLIP_US:
                rx, ry = _point(place, w, h, float(ring.get("x", 0.5)), float(ring.get("y", 0.5)), _key_at(keys, at - start))
                images.append({"id": f"ring-{scene.id}", "sourceId": add("ring", ring_file(job)), "startUs": at, "endUs": end,
                               "x": round(rx, 4), "y": round(ry, 4), "width": 0.075 if w > h else 0.16, "muted": True,
                               "keys": [{"timeUs": 0, "scale": 2.2, "opacity": 0.0, "easing": "back_out"},
                                        {"timeUs": 350_000, "scale": 1.0, "opacity": 1.0, "easing": "linear"},
                                        {"timeUs": end - at, "scale": 1.0, "opacity": 1.0}],
                               "exit": {"preset": "fade", "durationUs": 200_000}})
        for n, call in enumerate(doc.get("callouts") or []):
            text = clean_callout(call.get("text", ""))
            at, end = start + us(float(call.get("at", 0))), min(start + length, start + us(float(call.get("end", 0))))
            if text and end - at >= 300_000:
                titles.append({"id": f"say-{scene.id}-{n}", "text": text, "startUs": at, "endUs": end, "x": 0.5,
                               "y": round(min(0.8, max(0.08, float(call.get("y", 0.12)))), 4), "size": 40.0, "font": "barlow_condensed", "bold": True,
                               "color": theme["accent-ink"], "wrapWidth": 0.86, "capitalize": True,
                               "decoration": {"background": theme["accent"], "backgroundOpacity": 1.0, "padding": 12.0, "cornerRadius": 6.0,
                                              "strokeColor": "#000000", "strokeWidth": 0.0},
                               "entrance": {"preset": "pop", "durationUs": 250_000}, "exit": {"preset": "fade", "durationUs": 200_000}})
    boundary = 0
    for i, (left, right) in enumerate(zip(clips, clips[1:])):
        boundary += left["outUs"] - left["inUs"]
        if kind_of(job, scenes[i + 1]) == "screen":
            left["transition"] = {"rightId": right["id"], "preset": "zoom_punch", "durationUs": TRANSITION_US}
        elif kind_of(job, scenes[i]) == "screen":
            left["transition"] = {"rightId": right["id"], "preset": ("whip_left", "whip_up")[i % 2], "durationUs": TRANSITION_US}
        cues.append((boundary / 1e6, "soft_click", 0.3))
    project_len = sum(c["outUs"] - c["inUs"] for c in clips)
    captions = _caption_titles(job, project_len, [], MAX_TITLES - len(titles))
    for cue in captions:
        cue["wordStyle"] = {"pattern": "alternating", "secondaryColor": theme["accent"].upper()}
    spans = [(x["startUs"], x["endUs"]) for x in images + titles]
    captions = [c for c in captions if _max_active(spans + [(c["startUs"], c["endUs"])]) <= MAX_LAYERS]
    if len(captions) < len(job.captions):
        warnings.append(f"{len(job.captions) - len(captions)} captions left out to stay within the app's text limit")
    voice = add("voice", jdir / job.video.file)
    voice_len = next(s["durationUs"] for s in sources if s["id"] == voice)
    bed = add("sfx-bed", sfx.bed(sfx.calm(cues, MIN_CUE_GAP), project_len / 1e6 + 0.25, jdir / "voice" / "sfx-bed.wav"))
    pulse = add("music", sfx.pulse(project_len / 1e6 + 0.25, jdir / "voice" / "pulse.wav"))
    music = [{"id": "voice", "sourceId": voice, "inUs": 0, "startUs": 0, "endUs": min(project_len, voice_len), "volume": 1.0},
             {"id": "sfx", "sourceId": bed, "inUs": 0, "startUs": 0, "endUs": project_len, "volume": SFX_VOLUME},
             {"id": "music", "sourceId": pulse, "inUs": 0, "startUs": 0, "endUs": project_len, "volume": PULSE_VOLUME}]
    _fit_still_durations(clips, sources)
    project = {"format": "cutagent-native", "version": 15, "clips": clips, "titles": _pack_titles(titles + captions), "music": music, "images": images,
               "output": {"width": w, "height": h, "fps": RENDER_FPS, "cover": True, "ratio": ratio, "background": "#000000"}, "muted": False}
    return project, sources, files, warnings


# ---- self-review -------------------------------------------------------------------------------------------------


def _frame_of_screen(job: Job, scene: Scene, doc: dict[str, Any]) -> Image.Image:
    """What the screen beat shows when the camera has settled, drawn here because the app does the real animation."""
    w, h = CANVAS[job.ratios[0]]
    still, place = screen_still(job, scene, doc)
    keys = screen_keys(doc, place, w, h, us(scene.end - scene.start))
    key = keys[-1]
    a = 1 / key["scale"]
    with Image.open(still) as im:
        shot = im.convert("RGB").transform((w, h), Image.AFFINE, (a, 0, w / 2 - key["x"] * w * a, 0, a, h / 2 - key["y"] * h * a), Image.BILINEAR)
    draw = ImageDraw.Draw(shot)
    font = _font(40)
    ring = doc.get("ring") or {}
    if ring.get("show"):
        rx, ry = _point(place, w, h, float(ring.get("x", 0.5)), float(ring.get("y", 0.5)), key)
        r = (0.0375 if w > h else 0.08) * w
        draw.ellipse((rx * w - r, ry * h - r, rx * w + r, ry * h + r), outline=(255, 255, 0), width=6)
    for call in doc.get("callouts") or []:
        y, text = float(call.get("y", 0.12)) * h, clean_callout(call.get("text", "")).upper()[:60]
        width = font.getlength(text) + 36
        draw.rectangle(((w - width) / 2, y - 34, (w + width) / 2, y + 34), fill=(255, 208, 0))
        draw.text((w / 2, y), text, fill=(0, 0, 0), font=font, anchor="mm")
    return shot


def review(job: Job) -> dict[str, Any]:
    """Claude looks at frames of every scene and lists what is broken."""
    assert job.plan is not None
    ratio = job.ratios[0]
    tiles: list[tuple[Image.Image, str]] = []
    with tempfile.TemporaryDirectory(prefix="hf-voice-review-") as tmp:
        work = Path(tmp)
        for scene in timeline(job):
            doc = json.loads(_scene_file(job, scene).read_text(encoding="utf-8"))
            length = scene.end - scene.start
            if kind_of(job, scene) == "screen":
                tiles.append((_frame_of_screen(job, scene, doc), f"{scene.id} screen"))
                continue
            for share in (0.55, 0.93):
                at = length * share
                shot = work / f"{scene.id}-{share}.png"
                subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{at:.2f}", "-i",
                                str(store.job_dir(job.id) / "renders" / f"{scene.id}-{ORIENT[ratio]}.mp4"), "-frames:v", "1", str(shot)], check=True)
                with Image.open(shot) as im:
                    tiles.append((im.convert("RGB"), f"{scene.id} {at:.1f}s"))
        for n in range(0, len(tiles), 9):
            _sheet(tiles[n:n + 9], work / f"frames-{n // 9 + 1}.png", cols=3, tile_w=520)
        for shot in work.glob("*-0.*.png"):  # only the labelled sheets are left to read
            shot.unlink()
        beats = [{"id": s.id, "kind": s.template, "start": s.start, "end": s.end, "brief": s.data.get("brief")} for s in job.plan.scenes]
        sheets = ", ".join(sorted(p.name for p in work.glob("frames-*.png")))  # Claude can read files but not list them
        prompt = "\n\n".join([REVIEW_BRIEF, f"Image files to read: {sheets}", "The beats:", json.dumps(beats, ensure_ascii=False),
                               "What is said (seconds):", _srt(job.captions)])
        return ask_claude(job.id, "voice review", _text("lessons.md"), prompt, REVIEW_SCHEMA, work, True)


def scene_results(job: Job) -> list[RenderResult]:
    ratio = job.ratios[0]
    results = []
    for scene in timeline(job):
        story = kind_of(job, scene) == "story"
        rel = f"renders/{scene.id}-{ORIENT[ratio]}.mp4"
        results.append(RenderResult(scene_id=scene.id, ratio=ratio, status="ok", file=rel if story else None))
    return results


def make_scene(job: Job, scene: Scene, last: bool, feedback: str = "") -> None:
    """Write (or fix) one scene and render it; a render failure gets one rewrite, then a plain card."""
    doc = author(job, scene, feedback)
    if kind_of(job, scene) == "screen":
        return
    try:
        render_story(job, scene, doc, last)
    except (TemplateError, OSError, subprocess.SubprocessError) as e:
        doc = author(job, scene, error=f"The scene failed to render: {str(e)[-500:]}")
        try:
            render_story(job, scene, doc, last)
        except (TemplateError, OSError, subprocess.SubprocessError):
            plain = {**fallback_doc(scene), "key": doc.get("key")}
            _scene_file(job, scene).write_text(json.dumps(plain, ensure_ascii=False, indent=1), encoding="utf-8")
            render_story(job, scene, plain, last)


def produce(job_id: str, review_rounds: int = 1) -> None:
    """Author, render, self-review and fix, then export. Progress shows up as job.renders in the app."""
    job = store.load(job_id)
    if job.plan is None:
        raise ValueError("job has no plan")
    scenes = timeline(job)
    done: list[RenderResult] = []
    for i, scene in enumerate(scenes):
        make_scene(job, scene, i == len(scenes) - 1)
        done.append(RenderResult(scene_id=scene.id, ratio=job.ratios[0], status="ok"))
        job = store.load(job_id)
        job.renders = done
        store.save(job)
    found: dict[str, Any] = {}
    for n in range(review_rounds):
        found = review(job)
        found["round"] = n + 1
        issues = found.get("edit_issues") or []
        for scene in scenes:
            mine = [f"{i['problem']} -> {i['fix']}" for i in issues if i.get("scene_id") == scene.id]
            if mine:
                make_scene(job, scene, scene is scenes[-1], "; ".join(mine))
        if issues:
            found["round"] = n + 2
    from .export import export_bundle

    ratio = job.ratios[0]
    rel, warnings = export_bundle(job, ratio)
    job = store.load(job_id)
    job.renders, job.exports, job.status = scene_results(job), {ratio: rel}, "rendered"
    job.error = "; ".join(warnings)[-1000:]
    job.review = found or None
    store.save(job)


def prepare(job: Job) -> Job:
    """The voice at the loudness the phone and YouTube expect (once per job)."""
    if job.video.file == VOICE_FILE:
        return job
    jdir = store.job_dir(job.id)
    dest = jdir / VOICE_FILE
    subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-i", str(jdir / job.video.file), "-vn",
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(dest)], check=True)
    job = store.load(job.id)
    job.video = media.probe(dest)
    return store.save(job)
