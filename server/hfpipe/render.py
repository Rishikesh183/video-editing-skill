"""Scene -> HyperFrames render. Templates are filled with variables; no tokens are spent here."""

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import config, native
from .cues import cues
from .models import CANVAS, ORIENT, Job, Ratio, RenderResult, Scene
from .plan import slug
from .sfx import calm, finish
from .store import job_dir

_VARS_ATTR = re.compile(r"data-composition-variables='(.*?)'", re.S)
# Scenes the exporter builds natively in the app (no HyperFrames render).
NATIVE_TEMPLATES = native.NATIVE_FULLFRAME | native.NATIVE_OVERLAY
RENDER_VERSION = 3  # bump when post-processing changes so cached renders refresh
SCENE_SOUND_GAP = 1.2  # seconds between sounds inside a rendered graphic


class TemplateError(RuntimeError):
    pass


def template_path(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise TemplateError(f"bad template name {name!r}")
    return config.TEMPLATES / name / "template.html"


def available_templates() -> list[str]:
    return sorted(p.parent.name for p in config.TEMPLATES.glob("*/template.html"))


def declared_variables(template_html: str) -> dict[str, dict[str, Any]]:
    match = _VARS_ATTR.search(template_html)
    if not match:
        return {}
    return {v["id"]: v for v in json.loads(html.unescape(match.group(1)))}


def coerce_variables(declared: dict[str, dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    """Keep only declared keys and match declared types. Lists/objects travel as JSON strings."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        spec = declared.get(key)
        if spec is None or value is None:
            continue
        kind = spec.get("type")
        if kind == "number":
            try:
                out[key] = float(value) if "." in str(value) else int(value)
            except (TypeError, ValueError):
                continue
        elif kind == "boolean":
            out[key] = bool(value)
        elif isinstance(value, (list, dict)):
            out[key] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = str(value)
    return out


def materialise(template_html: str, assets: Path, out: Path, ratio: Ratio, duration: float) -> None:
    w, h = CANVAS[ratio]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shared = config.TEMPLATES / "_shared"
    if shared.exists():
        shutil.copytree(shared, out / "assets")
    if assets.exists():
        shutil.copytree(assets, out / "assets", dirs_exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    filled = (
        template_html.replace("__W__", str(w))
        .replace("__H__", str(h))
        .replace("__ORIENT__", ORIENT[ratio])
        .replace("__DUR__", f"{duration:g}")
    )
    (out / "index.html").write_text(filled, encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"id": out.name, "name": out.name}), encoding="utf-8")
    (out / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "assets"}}), encoding="utf-8")


def shared_fingerprint() -> str:
    """Hash of templates/_shared so a CSS/JS/font change invalidates cached renders."""
    h = hashlib.sha256()
    for p in sorted((config.TEMPLATES / "_shared").rglob("*")):
        if p.is_file():
            h.update(p.relative_to(config.TEMPLATES).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def photo_file(job: Job, slot: str) -> Path | None:
    """A job upload wins over the shared library."""
    if job.plan:
        for a in job.plan.assets_needed:
            if a.slot == slot and a.file and (job_dir(job.id) / a.file).exists():
                return job_dir(job.id) / a.file
    # An earlier upload to this job under the same slot survives a fresh plan.
    for earlier in sorted((job_dir(job.id) / "assets").glob(f"{slot}.*")):
        if earlier.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return earlier
    lib = config.LIBRARY / f"{slot}.png"
    return lib if lib.exists() else None


def run_hyperframes(project: Path, output: Path, variables_file: Path) -> None:
    env = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    cmd = [
        config.tool("npx"), "-y", f"hyperframes@{config.HYPERFRAMES_VERSION}", "render", str(project),
        "-o", str(output), "--quality", "delivery", "--quiet",
        "--variables-file", str(variables_file), "--strict-variables",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
    if proc.returncode != 0 or not output.exists():
        raise TemplateError(f"hyperframes render failed: {(proc.stderr or proc.stdout)[-800:]}")


def render_scene(job: Job, scene: Scene, ratio: Ratio) -> RenderResult:
    base = RenderResult(scene_id=scene.id, ratio=ratio, status="skipped")
    if scene.template in NATIVE_TEMPLATES:
        return base.model_copy(update={"detail": "built natively by the app exporter"})
    if scene.needs_input:
        return base.model_copy(update={"detail": "needs input: " + ", ".join(scene.needs_input)})
    path = template_path(scene.template)
    if not path.exists():
        return base.model_copy(update={"detail": f"template {scene.template!r} not built yet"})

    template_html = path.read_text(encoding="utf-8")
    declared = declared_variables(template_html)
    variables = coerce_variables(declared, scene.data)
    # Template defaults are demo content ("ODI chases, men", sample bars). A field the plan left out renders
    # empty instead of leaking that sample into a real video; colours keep their defaults.
    for key, spec in declared.items():
        if key in variables or key in ("photo", "photos", "cutouts", "cutout") or spec.get("type") != "string":
            continue
        default = str(spec.get("default", ""))
        variables[key] = "[]" if default.lstrip().startswith("[") else ""
    files: dict[str, Path] = {}  # assets/<name> -> local file
    notes: list[str] = []
    try:
        if "photo" in declared:
            variables.pop("photo", None)
            slot = str(scene.data.get("photo") or "")
            photo = photo_file(job, slot) if slot else None
            if slot and photo is None:
                notes.append(f"rendered without photo {slot!r}")
            if photo:
                files["photo.png"] = photo
                variables["photo"] = "assets/photo.png"
                if "cutout" in declared:
                    files["cutout.png"] = cutout_file(photo)
                    variables["cutout"] = "assets/cutout.png"
        if "photos" in declared:
            paths = []
            for i, slot in enumerate(_slots(scene.data.get("photos"))[:3]):
                found = photo_file(job, slot)
                if found:
                    files[f"p{i}.png"] = found
                    paths.append(f"assets/p{i}.png")
                else:
                    notes.append(f"missing photo {slot!r}")
            variables["photos"] = json.dumps(paths)
        if "cutouts" in declared:  # players with the background removed; "" keeps a side's place when missing
            cuts = []
            for i, slot in enumerate(_slots(scene.data.get("players") or scene.data.get("cutouts"))[:3]):
                found = photo_file(job, slot)
                if found:
                    files[f"c{i}.png"] = cutout_file(found)
                    cuts.append(f"assets/c{i}.png")
                else:
                    cuts.append("")
                    notes.append(f"missing photo {slot!r}")
            variables["cutouts"] = json.dumps(cuts)
    except (TemplateError, OSError, subprocess.SubprocessError) as e:
        return RenderResult(scene_id=scene.id, ratio=ratio, status="error", detail=f"photo prep failed: {e}"[-800:])

    sounds = calm(cues(scene.template, variables, scene.duration), SCENE_SOUND_GAP)
    key_src = json.dumps([template_html, variables, ratio, scene.duration, shared_fingerprint(), sounds, RENDER_VERSION], sort_keys=True)
    for f in sorted(files.values()):
        key_src += hashlib.sha256(f.read_bytes()).hexdigest()
    key = hashlib.sha256(key_src.encode()).hexdigest()[:20]
    cached = config.RENDER_CACHE / f"{key}.mp4"

    rel = Path("renders") / f"{scene.id}-{ORIENT[ratio]}.mp4"
    dest = job_dir(job.id) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not cached.exists():
            build = job_dir(job.id) / "build" / f"{scene.id}-{ORIENT[ratio]}"
            materialise(template_html, path.parent / "assets", build, ratio, scene.duration)
            for name, local in files.items():
                shutil.copyfile(local, build / "assets" / name)
            vars_file = build / "variables.json"
            vars_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
            cached.parent.mkdir(parents=True, exist_ok=True)
            raw = cached.with_suffix(".raw.mp4")
            run_hyperframes(build, raw, vars_file)
            tmp = cached.with_suffix(".partial.mp4")
            finish(raw, sounds, scene.duration, tmp)
            raw.unlink(missing_ok=True)
            tmp.replace(cached)
        shutil.copyfile(cached, dest)
    except (TemplateError, OSError, subprocess.SubprocessError) as e:
        return RenderResult(scene_id=scene.id, ratio=ratio, status="error", detail=str(e)[-800:])
    return RenderResult(scene_id=scene.id, ratio=ratio, status="ok", file=rel.as_posix(), detail="; ".join(notes))


def _slots(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [v.strip() for v in value.split(",")]
    return [slug(str(v)) for v in value or [] if str(v).strip()] if isinstance(value, list) else []


def cutout_file(photo: Path) -> Path:
    """Player cut-out (transparent PNG) via HyperFrames' local background removal, cached by photo hash."""
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()[:24]
    out = config.DATA / "cutouts" / f"{digest}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".partial.png")
    env = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    cmd = [config.tool("npx"), "-y", f"hyperframes@{config.HYPERFRAMES_VERSION}", "remove-background", str(photo), "-o", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
    if proc.returncode != 0 or not tmp.exists():
        raise TemplateError(f"background removal failed: {(proc.stderr or proc.stdout)[-400:]}")
    tmp.replace(out)
    return out
