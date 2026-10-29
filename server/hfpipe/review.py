"""Self-review of an export before the creator sees it: the planner LOOKS at frames of the actual edit and
at the uploaded photos, against the editing rules and the creator's lessons, and lists concrete defects with
fixes. A defect list can then drive one revise pass (fix only what is listed) instead of another full round.

Frames come from the PC preview of the export (approximate: the creator's cut-out is a still, captions and
entrance animations are not drawn), which the prompt states so those are not reported as defects.
"""

import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import config, usage
from .models import Job
from .planner import PlannerError, system_prompt
from .render import photo_file
from .store import job_dir

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "edit_issues", "photo_issues"],
    "properties": {
        "summary": {"type": "string"},
        "edit_issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["scene_id", "problem", "fix"],
            "properties": {"scene_id": {"type": "string"}, "problem": {"type": "string"}, "fix": {"type": "string"}}}},
        "photo_issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["slot", "problem"],
            "properties": {"slot": {"type": "string"}, "problem": {"type": "string"}}}},
    },
}

BRIEF = """You are reviewing a cricket edit before the creator sees it. Read every image file in this folder:
edit-*.png are frames from the edit (each tile is labelled with scene id, time and template) and photos.png
shows every uploaded photo with its slot name and what it was requested as.

The frames come from an APPROXIMATE PC preview: the creator's cut-out is a still frame, captions and entrance
animations are not drawn, and titles appear at their final position. Do not report those as defects.

Report only real, specific defects a viewer would notice, against the editing rules and the creator's
preferences below, for example: two elements overlapping or stacked; text covering the creator's face; text
wrapping onto an unexpected line or running off screen; a picture unrelated to its moment; an empty or
unbalanced frame; the same person/photo on both sides of a comparison; a photo whose team kit contradicts
its slot (an Australia player slot showing a South Africa kit); a screenshot, social post, collage or
watermark used as a player photo. Do not identify people from faces; judge kits, captions, text and layout.
Each fix must be a concrete change to the plan (scene id, field, new value or layout). If the edit is clean,
say so and return empty lists."""


def _font(size: int) -> Any:
    try:
        return ImageFont.truetype(str(config.TEMPLATES / "_shared" / "fonts" / "Inter.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _sheet(tiles: list[tuple[Image.Image, str]], dest: Path, cols: int = 4, tile_w: int = 300) -> Path:
    tile_imgs = []
    for img, label in tiles:
        im = img.convert("RGB")
        im.thumbnail((tile_w, tile_w * 2))
        canvas = Image.new("RGB", (tile_w, im.height + 34), (20, 20, 20))
        canvas.paste(im, ((tile_w - im.width) // 2, 34))
        ImageDraw.Draw(canvas).text((6, 6), label[:46], fill=(255, 230, 80), font=_font(16))
        tile_imgs.append(canvas)
    rows = -(-len(tile_imgs) // cols)
    h = max(t.height for t in tile_imgs)
    sheet = Image.new("RGB", (cols * tile_w, rows * h), (0, 0, 0))
    for i, t in enumerate(tile_imgs):
        sheet.paste(t, ((i % cols) * tile_w, (i // cols) * h))
    sheet.save(dest)
    return dest


def frames(job: Job, bundle: Path, workdir: Path) -> list[Path]:
    """Render the PC preview of the bundle and sample each scene at its settled moment."""
    preview = workdir / "preview.mp4"
    subprocess.run([sys.executable, str(config.ROOT / "scripts" / "preview_edit.py"), str(bundle), "-o", str(preview)],
                   check=True, capture_output=True, cwd=str(config.ROOT / "server"))
    assert job.plan is not None
    tiles = []
    for scene in sorted((s for s in job.plan.scenes if s.approved), key=lambda s: s.start):
        at = min(scene.end - 0.2, scene.start + max(0.8, (scene.end - scene.start) * 0.6))
        shot = workdir / f"f-{scene.id}.png"
        subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(preview),
                        "-frames:v", "1", str(shot)], check=True)
        if shot.exists():
            tiles.append((Image.open(shot), f"{scene.id} {at:.1f}s {scene.template}"))
    sheets = []
    for n in range(0, len(tiles), 8):
        sheets.append(_sheet(tiles[n:n + 8], workdir / f"edit-{n // 8 + 1}.png"))
    return sheets


def photos_sheet(job: Job, workdir: Path) -> Path | None:
    assert job.plan is not None
    tiles = []
    for a in job.plan.assets_needed:
        path = photo_file(job, a.slot)
        if path:
            tiles.append((Image.open(path), f"{a.slot}"))
    return _sheet(tiles, workdir / "photos.png", cols=5, tile_w=260) if tiles else None


def review(job: Job, bundle: Path) -> dict[str, Any]:
    """Look at the edit and the photos; returns {summary, edit_issues, photo_issues}."""
    assert job.plan is not None
    with tempfile.TemporaryDirectory(prefix="hf-review-") as tmp:
        work = Path(tmp)
        frames(job, bundle, work)
        photos_sheet(job, work)
        plan = json.dumps({"scenes": [s.model_dump(include={"id", "start", "end", "mode", "template", "data"})
                                      for s in job.plan.scenes if s.approved],
                           "photos": [a.model_dump() for a in job.plan.assets_needed]}, ensure_ascii=False)
        captions = "\n".join(f"{c.start:.1f}-{c.end:.1f} {c.text}" for c in job.captions)
        prompt = "\n\n".join([BRIEF, "What the creator says (captions, seconds):", captions, "The plan:", plan])
        cmd = [config.claude_executable(), "-p", prompt, "--model", config.PLANNER_MODEL,
               "--tools", "Read", "--allowedTools", "Read", "--system-prompt", system_prompt(),
               "--json-schema", json.dumps(SCHEMA), "--output-format", "json",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--setting-sources", ""]
        started = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=str(work), timeout=900)
    if proc.returncode != 0:
        raise PlannerError(f"review failed: {(proc.stderr or proc.stdout)[-600:]}")
    result = json.loads(proc.stdout)
    result.setdefault("duration_ms", round((time.monotonic() - started) * 1000))
    usage.record(job.id, "review", result)
    found = result.get("structured_output")
    if not isinstance(found, dict):
        raise PlannerError(f"review returned no findings: {str(result.get('result'))[:300]}")
    return found


def fix_notes(found: dict[str, Any]) -> str:
    """Turn review findings into re-plan notes for one revise pass."""
    lines = [f"- {i['scene_id']}: {i['problem']} -> {i['fix']}" for i in found.get("edit_issues", [])]
    lines += [f"- photo {i['slot']}: {i['problem']} (do not use it until the creator replaces it)"
              for i in found.get("photo_issues", [])]
    return ("Fix exactly these review findings and change nothing else:\n" + "\n".join(lines)) if lines else ""


def bundle_for(job: Job, ratio: str = "9:16") -> Path:
    rel = job.exports.get(ratio) or next(iter(job.exports.values()), "")
    if not rel:
        raise PlannerError("render the graphics before reviewing")
    path = job_dir(job.id) / rel
    with zipfile.ZipFile(path):  # fail early on a broken bundle
        pass
    return path
