"""Plan + renders -> CutAgent native v15 bundle (project.json, sources.json, media/) as a zip.

Timeline model: the creator's voice is one music item spanning the whole edit; the main track
alternates muted face segments (source time == timeline time) with full-frame graphics, so
every cut keeps lip-sync. Overlay scenes become images[]/titles[] above the main track.
"""

import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from . import config, decor, layout, native, sfx
from .media import probe, sha256
from .models import CANVAS, ORIENT, Job, MediaInfo, Ratio, Scene, now
from .render import cutout_file, photo_file
from .store import job_dir

MIN_CLIP_US = 50_000
OUTPUT_FPS = 30
VIDEO_ID = "video-src"
SILENT_ID = "video-src-silent"
# Graphics open with their own flash, so only the return to the face gets a (soft) join effect;
# two visuals back to back get a whip push with a whip sound.
OUT_TRANSITION = "zoom_fade"
TRANSITION_US = 400_000  # long enough to read as a glide, not a jolt
# Visual->visual joins use the same zoom fade: it reads smoother than a push; the whip sound marks the cut.
WHIP_TRANSITION = "zoom_fade"
WHIP_US = 400_000
FLASH_US = 320_000
FLASH_IN = {"photo-full", "news-cards", "photo-frames"}  # reveals that open on a white flash
# Jump zooms on the talking head: wide and tight framings alternate at caption boundaries.
TIGHT_SCALE = 1.1  # a gentle punch-in; bigger jumps read as rough cuts
JUMP_EVERY_US = 2_800_000
MIN_FACE_PIECE_US = 900_000
MAX_LAYERS = 8  # app validator: simultaneous image + text layers
MAX_IMAGES = 32
MAX_TITLES = 200
QUIET_OPENING_US = 3_500_000  # no sound effects in the first 3.5 s
STILL_US = 600_000_000  # placeholder while building; _fit_still_durations sets the real length
STILL_MIN_US = 5_000_000  # what CutAgent itself gives an imported photo
MAX_MUSIC = 8  # app validator: independent audio items per project
MAX_CAPTION_TITLES = 180  # app validator allows 200 titles in total


class ExportError(RuntimeError):
    pass


def us(seconds: float) -> int:
    return round(seconds * 1_000_000)


def source_record(source_id: str, info: MediaInfo, bundle_path: str, digest: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "path": bundle_path,
        "name": info.file,
        "mime": info.mime,
        "durationUs": info.duration_us,
        "width": info.width,
        "height": info.height,
        "hasAudio": info.has_audio,
        "digest": digest,
        "rotationDegrees": info.rotation,
        "metadataVersion": 2,
    }


def _display_aspect(info: MediaInfo) -> float:
    w, h = (info.height, info.width) if info.rotation % 180 else (info.width, info.height)
    return w / h if h else 1.0


def _face_clip(cid: str, start: int, end: int, tight: bool = False) -> dict[str, Any]:
    clip: dict[str, Any] = {"id": cid, "sourceId": VIDEO_ID, "inUs": start, "outUs": end, "muted": True}
    dur = end - start
    if tight:  # punch-in: same take, closer framing, cut on a sentence boundary
        clip["keys"] = [{"timeUs": 0, "scale": TIGHT_SCALE, "easing": "linear"},
                        {"timeUs": dur, "scale": TIGHT_SCALE + 0.03}]
    elif dur >= 2_000_000:  # slow push keeps long talking-head stretches alive
        clip["keys"] = [
            {"timeUs": 0, "scale": 1.0, "easing": "ease_in_out"},
            {"timeUs": dur, "scale": 1.06},
        ]
    return clip


def _face_pieces(a: int, b: int, punches: list[tuple[int, int]], beats: list[int]) -> list[tuple[int, int, bool | None]]:
    """Split a face stretch into (start, end, tight); tight=None means 'alternate with the previous shot'.
    Punch spans (headline overlays) are always tight; long stretches cut at caption starts about every 3 s."""
    marks = sorted({p for s, e in punches for p in (s, e) if a < p < b})
    bounds = [a, *marks, b]
    pieces: list[tuple[int, int, bool | None]] = []
    for s, e in zip(bounds, bounds[1:]):
        if any(ps <= s and e <= pe for ps, pe in punches):
            pieces.append((s, e, True))
            continue
        cur = s
        for t in beats:
            if t - cur >= JUMP_EVERY_US and e - t >= 2_000_000:
                pieces.append((cur, t, None))
                cur = t
        pieces.append((cur, e, None))
    merged: list[tuple[int, int, bool | None]] = []
    for piece in pieces:  # no flicker: fold slivers into a neighbour (never into a punch)
        if merged and piece[1] - piece[0] < MIN_FACE_PIECE_US and merged[-1][2] is not True:
            merged[-1] = (merged[-1][0], piece[1], merged[-1][2])
        elif merged and merged[-1][1] - merged[-1][0] < MIN_FACE_PIECE_US and piece[2] is not True:
            merged[-1] = (merged[-1][0], piece[1], merged[-1][2])
        else:
            merged.append(piece)
    while len(merged) > 1 and merged[-1][1] - merged[-1][0] < MIN_FACE_PIECE_US:  # tail sliver: fold it back
        tail = merged.pop()
        merged[-1] = (merged[-1][0], tail[1], merged[-1][2])
    return merged


# Small decorations drawn as cached still textures instead of video streams. CutAgent gives every animated
# image layer its own full-length decoder stream; a dozen of them exhausts phone GPU memory (black preview,
# export crash). Static layers at the top of the stack become cheap textures, but share the overlay shader's
# 15-slot budget with text, so they are capped. Order = priority when the budget runs short.
STATIC_DECOR = ("dc-lt-", "dc-burst-", "dc-ar-", "dc-mk-")
OVERLAY_SLOTS = 15
_ANIMATION_KEYS = ("keys", "entrance", "exit", "effects", "mask", "frame", "pointEffects")


def _title_groups(titles: list[dict[str, Any]]) -> int:
    groups, batch = 0, []
    for t in titles:  # same rule as the app: a new batch whenever a title overlaps the current one
        if any(b["startUs"] < t["endUs"] and t["startUs"] < b["endUs"] for b in batch):
            groups, batch = groups + 1, []
        batch.append(t)
    return groups + (1 if batch else 0)


def _lighten(images: list[dict[str, Any]], title_groups: int, warnings: list[str]) -> list[dict[str, Any]]:
    decor = sorted((i for i in images if i["id"].startswith(STATIC_DECOR)),
                   key=lambda i: next(n for n, p in enumerate(STATIC_DECOR) if i["id"].startswith(p)))
    room = max(0, OVERLAY_SLOTS - 1 - title_groups)
    keep, dropped = decor[:room], decor[room:]
    if dropped:
        warnings.append(f"{len(dropped)} decorations left out to stay within the phone's overlay limit")
    still = [{k: v for k, v in i.items() if k not in _ANIMATION_KEYS} for i in keep]
    return [i for i in images if not i["id"].startswith(STATIC_DECOR)] + still


def _fit_still_durations(clips: list[dict[str, Any]], sources: list[dict[str, Any]]) -> None:
    """Give each still image the length it is actually used for (at least the app's own 5 s for photos).
    The app hands this duration to Media3, which generates that much silent audio for a still on the main
    track. A huge value (minutes) puts the audio far ahead of the video and the export stalls."""
    longest: dict[str, int] = {}
    for c in clips:
        if c.get("sourceId"):
            longest[c["sourceId"]] = max(longest.get(c["sourceId"], 0), c["outUs"])
    for s in sources:
        if s["mime"].startswith("image/"):
            s["durationUs"] = max(STILL_MIN_US, longest.get(s["id"], 0))


def _silent_copy(video: Path, dest: Path) -> Path:
    """The creator's video without its audio track (stream copy, no re-encode), cached next to the exports."""
    if not dest.exists() or dest.stat().st_mtime < video.stat().st_mtime:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp.mp4")
        subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-i", str(video), "-map", "0:v:0", "-c", "copy",
                        "-an", "-movflags", "+faststart", str(tmp)], check=True)
        tmp.replace(dest)
    return dest


def _pack_titles(titles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order text so the app batches it into the fewest GPU overlays (its overlay shader takes at most 15).
    Each title goes to the lowest group above every earlier title it overlaps; groups are then written one
    after another. Overlapping titles keep their paint order, and the app's batching (which starts a new
    batch only when a title overlaps the current one) ends up with one batch per group."""
    groups: list[list[dict[str, Any]]] = []
    placed: list[tuple[dict[str, Any], int]] = []
    for t in titles:
        g = 1 + max((gi for o, gi in placed if o["startUs"] < t["endUs"] and t["startUs"] < o["endUs"]), default=-1)
        while len(groups) <= g:
            groups.append([])
        groups[g].append(t)
        placed.append((t, g))
    return [t for group in groups for t in group]


def _max_active(spans: list[tuple[int, int]]) -> int:
    active = peak = 0
    for _, delta in sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans]):
        active += delta
        peak = max(peak, active)
    return peak


def _overlay_layers(scene: Scene, source_id: str) -> tuple[dict, None]:
    """A rendered graphic used as an overlay (full canvas, muted)."""
    return {"id": f"ov-{scene.id}", "sourceId": source_id, "startUs": us(scene.start), "endUs": us(scene.end),
            "x": 0.5, "y": 0.5, "width": 1.0, "muted": True}, None


def _caption_titles(job: Job, project_len: int, quiet: list[tuple[int, int]] = (),
                    limit: int = MAX_CAPTION_TITLES) -> list[dict[str, Any]]:
    """The creator's captions as app caption titles (same look as the app's 'alternating' preset).

    Face clips sit at source time and the voice runs unbroken, so caption times carry over as-is.
    Captions step aside while big text is on screen (quiet spans) so the viewer never reads two things.
    """
    titles = []
    for i, cue in enumerate(job.captions):
        if len(titles) >= min(limit, MAX_CAPTION_TITLES):
            break
        start, end = us(cue.start), min(us(cue.end), project_len)
        mid = (start + end) // 2
        if any(a <= mid < b for a, b in quiet):
            continue
        for a, b in quiet:  # trim the part that runs into big text
            if start < a < end:
                end = a
            if start < b < end:
                start = b
        if end - start < MIN_CLIP_US or not cue.text.strip():
            continue
        titles.append({
            "id": f"cap-{i}", "text": cue.text.strip()[:1000], "startUs": start, "endUs": end,
            "x": 0.5, "y": 0.9, "size": 36.0, "wrapWidth": 0.85, "font": "poppins", "bold": True, "color": "#FFFFFF",
            "entrance": {"preset": "pop", "durationUs": max(50_000, min(180_000, (end - start) // 4))},
            "wordStyle": {"pattern": "alternating"}, "decoration": {"strokeWidth": 2.0},
            "captionGroupId": "ai-captions", "capitalize": True,
        })
    return titles


def build(job: Job, ratio: Ratio) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Path], list[str]]:
    """Returns (project, sources, {bundle_path: local_file}, warnings)."""
    if job.plan is None:
        raise ExportError("job has no plan")
    jdir = job_dir(job.id)
    warnings: list[str] = []
    rendered = {r.scene_id: jdir / r.file for r in job.renders if r.ratio == ratio and r.status == "ok" and r.file}
    total = job.video.duration_us

    files: dict[str, Path] = {}
    sources: list[dict[str, Any]] = []
    video_path = jdir / job.video.file
    files[f"media/{job.video.file}"] = video_path
    sources.append(source_record(VIDEO_ID, job.video, f"media/{job.video.file}", sha256(video_path)))

    def add_source(source_id: str, local: Path) -> str:
        bundle_path = f"media/{source_id}{local.suffix}"
        if bundle_path not in files:
            info = probe(local)
            files[bundle_path] = local
            sources.append(source_record(source_id, info, bundle_path, sha256(local)))
        return source_id

    def add_decor(name: str) -> str | None:
        try:
            path = decor.file(name)
        except (KeyError, OSError):
            return None
        source_id = add_source(f"decor-{name}", path)
        next(s for s in sources if s["id"] == source_id)["durationUs"] = STILL_US
        return source_id

    def add_photo(slot: str) -> str | None:
        photo = photo_file(job, slot) if slot else None
        if photo is None:
            return None
        source_id = add_source(f"photo-{slot}", photo)
        # Stills have no natural length; allow them to hold the main track for as long as needed.
        next(s for s in sources if s["id"] == source_id)["durationUs"] = STILL_US
        return source_id

    sfx_cues: list[tuple[int, str, float]] = []
    clips: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    titles: list[dict[str, Any]] = []
    quiet: list[tuple[int, int]] = []  # spans with big text: captions step aside
    cursor = 0
    approved = [s for s in job.plan.scenes if s.approved]
    portrait = ratio == "9:16"
    aspect = _display_aspect(job.video)
    w, h = CANVAS[ratio]
    cover = abs(aspect - w / h) / (w / h) < 0.1
    faces: list | None = None

    def face(a: int, b: int):
        nonlocal faces
        if faces is None:
            try:
                faces = layout.track(video_path, total / 1e6, jdir / "faces.json")
            except Exception as e:  # layout is a nicety; never fail an export over it
                warnings.append(f"face tracking unavailable: {e}")
                faces = []
        box = layout.face_in(faces, a / 1e6, b / 1e6)
        return layout.to_canvas(box, aspect, w / h, cover) if box else None

    def add_still(source_id: str, path: Path) -> str:
        add_source(source_id, path)
        next(s for s in sources if s["id"] == source_id)["durationUs"] = STILL_US
        return source_id

    def add_cutout(slot: str) -> str | None:
        """Background-removed copy of a photo slot, trimmed to the subject (cached per photo)."""
        photo = photo_file(job, slot) if slot else None
        if photo is None:
            return None
        trimmed = jdir / "exports" / "cutouts" / f"{slot}.png"
        if not trimmed.exists() or trimmed.stat().st_mtime < photo.stat().st_mtime:
            try:
                cut = cutout_file(photo)
            except Exception as e:  # noqa: BLE001 - background removal is optional polish
                warnings.append(f"cut-out of {slot} failed: {e}")
                return None
            from PIL import Image

            with Image.open(cut) as im:
                im = im.convert("RGBA")
                box = im.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
                trimmed.parent.mkdir(parents=True, exist_ok=True)
                (im.crop(box) if box else im).save(trimmed)
        return add_still(f"cut-{slot}", trimmed)

    def size(source_id: str) -> tuple[int, int]:
        rec = next((s for s in sources if s["id"] == source_id), None)
        if not rec:
            return (1, 1)
        return (rec["height"], rec["width"]) if rec.get("rotationDegrees", 0) % 180 else (rec["width"], rec["height"])

    media = native.Media(photo=add_photo, decor=add_decor, still=add_still, face=face, canvas=(w, h),
                         workdir=jdir / "exports" / ORIENT[ratio], cutout=add_cutout, size=size)
    beats = sorted(us(c.start) for c in job.captions)
    fullframes = sorted((s for s in approved if s.mode == "fullframe"), key=lambda s: s.start)
    fullframe_spans: list[tuple[int, int]] = []
    flash_ids: set[str] = set()

    # Overlay moments go first: they decide where the face gets punched in (headline over a tight shot).
    overlay_moments: list[native.Moment] = []
    punches: list[tuple[int, int]] = []
    for scene in (s for s in approved if s.mode == "overlay" and s.template in native.NATIVE_OVERLAY):
        start, end = us(scene.start), min(us(scene.end), total)
        if any(start < us(f.end) and us(f.start) < end for f in fullframes):
            warnings.append(f"{scene.id}: overlay overlaps a full-screen moment, skipped")
            continue
        moment, problem = native.build(scene, start, end - start, portrait, aspect, 0, media)
        if moment is None or end - start < MIN_CLIP_US:
            warnings.append(f"{scene.id}: {problem or 'too short'}, overlay skipped")
            continue
        overlay_moments.append(moment)
        if moment.punch:
            punches.append((start, end))
        if scene.template in native.TEXT_MOMENTS:
            quiet.append((start, end))

    tight_prev = True  # the hook starts on the wide shot

    def add_face(a: int, b: int) -> None:
        nonlocal tight_prev
        for s, e, tight in _face_pieces(a, b, punches, beats):
            tight = (not tight_prev) if tight is None else tight
            clips.append(_face_clip(f"face-{len(clips)}", s, e, tight))
            tight_prev = tight

    for index, scene in enumerate(fullframes):
        start, end = us(scene.start), min(us(scene.end), total)
        if start < cursor:
            warnings.append(f"{scene.id}: overlaps the previous scene, skipped")
            continue
        if 0 < start - cursor < MIN_CLIP_US:
            warnings.append(f"{scene.id}: {start - cursor}us face sliver absorbed")
            start = cursor
        if scene.template in native.NATIVE_FULLFRAME:
            moment, problem = native.build(scene, start, end - start, portrait, aspect, index, media)
            if moment is None or moment.main is None:
                warnings.append(f"{scene.id}: {problem}, kept face video")
                continue
            main, length = moment.main, end - start
        else:
            gfx = rendered.get(scene.id)
            if gfx is None:
                warnings.append(f"{scene.id}: no render for {ratio}, kept face video")
                continue
            source_id = add_source(f"gfx-{scene.id}", gfx)
            src = next(s for s in sources if s["id"] == source_id)
            length = min(end - start, src["durationUs"])
            # Graphic clips carry their own entrance flash and sound effects.
            main, moment = {"id": f"gfx-{scene.id}", "sourceId": source_id, "inUs": 0, "outUs": length,
                            "muted": not src["hasAudio"], "volume": 0.9}, None
        if start > cursor:
            add_face(cursor, start)
        clips.append(main)
        if scene.template in FLASH_IN:
            flash_ids.add(main["id"])
        if moment:
            images += moment.images
            titles += moment.titles
            sfx_cues += moment.cues
        if scene.template in native.TEXT_MOMENTS:
            quiet.append((start, start + length))
        fullframe_spans.append((start, start + length))
        tight_prev = True  # come back from a visual on the wide shot
        cursor = start + length

    if total - cursor >= MIN_CLIP_US:
        add_face(cursor, total)
    elif total > cursor:
        warnings.append(f"tail of {total - cursor}us dropped")
    boundary = 0
    for left, right in zip(clips, clips[1:]):
        boundary += left["outUs"] - left["inUs"]
        if left["id"].startswith("face-"):
            if right["id"] in flash_ids:  # face -> photo reveal: quick white flash (jump zooms stay hard cuts)
                left["transition"] = {"rightId": right["id"], "preset": "flash_white", "durationUs": FLASH_US}
            continue
        if right["id"].startswith("face-"):
            left["transition"] = {"rightId": right["id"], "preset": OUT_TRANSITION, "durationUs": TRANSITION_US}
        else:  # visual straight into visual: whip pan
            left["transition"] = {"rightId": right["id"], "preset": WHIP_TRANSITION, "durationUs": WHIP_US}
            sfx_cues.append((max(0, boundary - WHIP_US // 2), "whip", 0.45))
    project_len = sum(c["outUs"] - c["inUs"] for c in clips)

    for moment in overlay_moments:
        for layer in moment.images + moment.titles:
            layer["endUs"] = min(layer["endUs"], project_len)
        images += [i for i in moment.images if i["endUs"] - i["startUs"] >= MIN_CLIP_US]
        titles += [t for t in moment.titles if t["endUs"] - t["startUs"] >= MIN_CLIP_US]
        sfx_cues += moment.cues

    for scene in (s for s in approved if s.mode == "overlay" and s.template not in native.NATIVE_OVERLAY):
        if any(us(scene.start) < e and s < us(scene.end) for s, e in fullframe_spans):
            warnings.append(f"{scene.id}: overlay overlaps a full-screen moment, skipped")
            continue
        gfx = rendered.get(scene.id)
        if gfx is None:
            warnings.append(f"{scene.id}: no render for {ratio}, overlay skipped")
            continue
        layer, title = _overlay_layers(scene, add_source(f"gfx-{scene.id}", gfx))
        layer["endUs"] = min(layer["endUs"], project_len)
        if not layer["sourceId"].startswith("photo-"):
            src_len = next(s["durationUs"] for s in sources if s["id"] == layer["sourceId"])
            layer["endUs"] = min(layer["endUs"], layer["startUs"] + src_len)
            layer["sourceOutUs"] = layer["endUs"] - layer["startUs"]
        if layer["endUs"] - layer["startUs"] < MIN_CLIP_US:
            continue
        images.append(layer)
        if title:
            title["endUs"] = layer["endUs"]
            titles.append(title)

    if len(images) > MAX_IMAGES:  # decorations go first
        extra = len(images) - MAX_IMAGES
        drop = {i["id"] for i in images if i["id"].startswith("dc-")}
        drop = set(sorted(drop)[:extra])
        images = [i for i in images if i["id"] not in drop][:MAX_IMAGES]
        warnings.append(f"too many layers: dropped {extra} (decorations first)")
    captions = _caption_titles(job, project_len, quiet, MAX_TITLES - len(titles))
    spans = [(x["startUs"], x["endUs"]) for x in images + titles]
    captions = [c for c in captions if _max_active(spans + [(c["startUs"], c["endUs"])]) <= MAX_LAYERS]
    titles = _pack_titles(titles + captions)  # later titles paint on top
    images = _lighten(images, _title_groups(titles), warnings)
    if any(i["sourceId"] == VIDEO_ID for i in images):
        # Overlay copies of the creator (cut-outs) use a video-only file: the app gives every overlay with
        # audio its own audio stream plus silent gaps, and Media3's audio mixer stalls on that during export.
        silent = add_source(SILENT_ID, _silent_copy(video_path, jdir / "exports" / "creator-video-only.mp4"))
        for i in images:
            if i["sourceId"] == VIDEO_ID:
                i["sourceId"] = silent
                i.pop("volume", None)
    music = [{"id": "voice", "sourceId": VIDEO_ID, "inUs": 0, "startUs": 0, "endUs": project_len, "volume": 1.0}]
    # The opening stays clean: the hook is the creator's voice, no whooshes or booms under it.
    cues = sorted(c for c in sfx_cues if QUIET_OPENING_US <= c[0] < project_len - MIN_CLIP_US)
    if len(cues) <= MAX_MUSIC - 1:
        # One audio item per sound: each can be moved, re-timed or muted by hand.
        sfx_dir = sfx.library()
        for i, (at, name, gain) in enumerate(cues):
            source_id = add_source(f"sfx-{name}", sfx_dir / f"{name}.wav")
            length = min(next(s["durationUs"] for s in sources if s["id"] == source_id), project_len - at)
            music.append({"id": f"sfx-{i}", "sourceId": source_id, "inUs": 0, "startUs": at, "endUs": at + length, "volume": gain})
    elif cues:
        # The app allows 8 audio items; beyond that, sounds share one "sound effects" track.
        bed = jdir / "exports" / f"sfx-bed-{ORIENT[ratio]}.wav"
        # A little longer than the edit: the app rejects audio spans that run past their source.
        sfx.bed([(at / 1e6, name, gain) for at, name, gain in cues], project_len / 1e6 + 0.25, bed)
        source_id = add_source(f"sfx-bed-{ORIENT[ratio]}", bed)
        music.append({"id": "sfx-bed", "sourceId": source_id, "inUs": 0, "startUs": 0, "endUs": project_len, "volume": 1.0})
    _fit_still_durations(clips, sources)
    project = {
        "format": "cutagent-native",
        "version": 15,
        "clips": clips,
        "titles": titles,
        "music": music,
        "images": images,
        "output": {"width": w, "height": h, "fps": OUTPUT_FPS,
                   "cover": cover,
                   "ratio": ratio, "background": "#000000"},
        "muted": False,
    }
    return project, sources, files, warnings


def validate(project_file: Path, sources_file: Path) -> dict[str, Any]:
    if not config.VALIDATOR.exists():
        raise ExportError(f"validator not built: {config.VALIDATOR} (run gradlew installDist in validator/)")
    proc = subprocess.run([str(config.VALIDATOR), str(project_file), str(sources_file)],
                          capture_output=True, text=True, timeout=120)
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise ExportError(f"validator crashed: {(proc.stderr or proc.stdout)[-500:]}")
    return json.loads(lines[-1])


def export_bundle(job: Job, ratio: Ratio) -> tuple[str, list[str]]:
    project, sources, files, warnings = build(job, ratio)
    stage = job_dir(job.id) / "exports" / ORIENT[ratio]
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "project.json").write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    (stage / "sources.json").write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
    verdict = validate(stage / "project.json", stage / "sources.json")
    if not verdict.get("ok"):
        raise ExportError(f"CutAgent validator rejected {ratio} export: {verdict.get('error')}")

    manifest = {
        "format": "cutagent-bundle", "version": 1, "jobId": job.id, "ratio": ratio, "createdAt": now(),
        "project": "project.json", "sources": "sources.json",
        "media": [{"sourceId": s["id"], "path": s["path"], "sha256": s["digest"],
                   "bytes": files[s["path"]].stat().st_size} for s in sources],
        "warnings": warnings,
    }
    rel = f"exports/{job.id}-{ORIENT[ratio]}.zip"
    with zipfile.ZipFile(job_dir(job.id) / rel, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False), zipfile.ZIP_DEFLATED)
        z.write(stage / "project.json", "project.json", zipfile.ZIP_DEFLATED)
        z.write(stage / "sources.json", "sources.json", zipfile.ZIP_DEFLATED)
        for bundle_path, local in files.items():
            z.write(local, bundle_path, zipfile.ZIP_STORED)  # media is already compressed
    return rel, warnings
