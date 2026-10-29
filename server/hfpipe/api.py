"""HTTP API used by the CutAgent phone app (over LAN or Tailscale)."""

import hmac
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config, pipeline, planner, render, store, usage
from .media import probe, to_png
from .models import ORIENT, Job, Plan
from .plan import normalize, slug
from .srt import parse_srt

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
DASHBOARD = Path(__file__).with_name("dashboard.html")


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    if not config.API_TOKEN or request.url.path == "/dashboard":  # the page itself is static; its data calls need the token
        return
    supplied = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, config.API_TOKEN):
        raise HTTPException(401, "bad or missing token")


app = FastAPI(title="hfpipe", dependencies=[Depends(require_token)])


def _job(job_id: str) -> Job:
    try:
        return store.load(job_id)
    except KeyError:
        raise HTTPException(404, "job not found") from None


def _editable(job: Job) -> None:
    if job.status in ("planning", "rendering", "reviewing"):
        raise HTTPException(409, f"job is {job.status}")
    if job.plan is None:
        raise HTTPException(409, "job has no plan yet")


def view(job: Job) -> dict[str, Any]:
    out = job.model_dump()
    assets = []
    if job.plan:
        for a in job.plan.assets_needed:
            in_library = (config.LIBRARY / f"{a.slot}.png").exists()
            assets.append({**a.model_dump(), "in_library": in_library, "ready": bool(a.file) or in_library})
    out["assets"] = assets
    out["missing_assets"] = [a["slot"] for a in assets if not a["ready"]]
    out["templates"] = render.available_templates()
    return out


def _save_upload(upload: UploadFile, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f, 1 << 20)
    return dest


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "validator": config.VALIDATOR.exists(), "templates": render.available_templates()}


@app.post("/jobs")
def create_job(
    video: UploadFile = File(...),
    captions: UploadFile = File(...),
    script: str = Form(""),
    ratios: str = Form("9:16,16:9"),
    auto_plan: bool = Form(True),
    provider: str = Form(""),
    tighten: bool = Form(False),
    style: str = Form(""),
) -> dict[str, Any]:
    ext = Path(video.filename or "video.mp4").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"video must be one of {sorted(VIDEO_EXT)}")
    wanted = [r.strip() for r in ratios.split(",") if r.strip()]
    if not wanted or any(r not in ORIENT for r in wanted):
        raise HTTPException(400, "ratios must be 9:16 and/or 16:9")
    cues = parse_srt(captions.file.read().decode("utf-8", errors="replace"))
    if not cues:
        raise HTTPException(400, "captions file has no SRT cues")

    job_id = store.new_id()
    jdir = store.job_dir(job_id)
    path = _save_upload(video, jdir / f"source{ext}")
    try:
        info = probe(path)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(jdir, ignore_errors=True)
        raise HTTPException(400, f"could not read video: {e}") from e
    if not info.has_audio:
        shutil.rmtree(jdir, ignore_errors=True)
        raise HTTPException(400, "video has no audio track")
    provider = provider if provider in config.PROVIDERS else config.PLANNER_PROVIDER
    job = store.save(Job(id=job_id, ratios=wanted, video=info, captions=cues, script=script, provider=provider, tighten=tighten,
                         style=style if style in planner.styles() else ""))  # type: ignore[arg-type]
    if auto_plan:
        job = pipeline.set_status(job, "planning")
        pipeline.submit(pipeline.plan, job_id, "", True, provider)
    return view(job)


@app.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [{"id": j.id, "created": j.created, "status": j.status, "duration_s": j.video.duration_us / 1e6}
            for j in store.all_jobs()]


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return view(_job(job_id))


class PlanRequest(BaseModel):
    notes: str = ""
    fresh: bool = True  # "Re-plan" asks the planner again; False reuses a cached plan for identical inputs
    provider: str = ""  # "claude" | "codex"; empty = the job's last choice
    revise: bool = True  # update the current plan (keep what works) instead of planning from scratch
    remember: bool = False  # also save the notes as a standing preference for all future plans
    style: str | None = None  # switch the job's editing style ("" = house style); None keeps it


@app.post("/jobs/{job_id}/plan")
def replan(job_id: str, body: PlanRequest) -> dict[str, Any]:
    job = _job(job_id)
    if job.status in ("planning", "rendering"):
        raise HTTPException(409, f"job is {job.status}")
    if body.style is not None and (body.style == "" or body.style in planner.styles()):
        job.style = body.style
    if body.remember and body.notes.strip():
        planner.remember(body.notes)
    if body.provider in config.PROVIDERS:
        job.provider = body.provider
    job = pipeline.set_status(job, "planning")
    pipeline.submit(pipeline.plan, job_id, body.notes, body.fresh, job.provider, body.revise and job.plan is not None)
    return view(job)


class ScenePatch(BaseModel):
    start: float | None = None
    end: float | None = None
    mode: str | None = None
    template: str | None = None
    data: dict[str, Any] | None = None
    approved: bool | None = None


@app.patch("/jobs/{job_id}/scenes/{scene_id}")
@app.post("/jobs/{job_id}/scenes/{scene_id}")  # Android's HttpURLConnection cannot send PATCH
def edit_scene(job_id: str, scene_id: str, patch: ScenePatch) -> dict[str, Any]:
    job = _job(job_id)
    _editable(job)
    assert job.plan is not None
    scene = next((s for s in job.plan.scenes if s.id == scene_id), None)
    if scene is None:
        raise HTTPException(404, "scene not found")
    changes = patch.model_dump(exclude_none=True)
    if "data" in changes:
        changes["data"] = {**scene.data, **changes["data"]}
        changes["needs_input"] = [k for k in scene.needs_input if k not in patch.data]  # type: ignore[operator]
    updated = scene.model_copy(update=changes)
    scenes = [updated if s.id == scene_id else s for s in job.plan.scenes]
    try:
        job.plan = normalize(Plan(scenes=scenes, assets_needed=job.plan.assets_needed), job.video.duration_us / 1e6)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    job.status = "planned"
    return view(store.save(job))


@app.post("/jobs/{job_id}/assets/{slot}")
def upload_asset(job_id: str, slot: str, photo: UploadFile = File(...), save_to_library: bool = True) -> dict[str, Any]:
    job = _job(job_id)
    _editable(job)
    assert job.plan is not None
    slot = slug(slot)
    entry = next((a for a in job.plan.assets_needed if a.slot == slot), None)
    if entry is None:
        raise HTTPException(404, "no such asset slot in this plan")
    # Any image the phone hands over (jpg/png/webp/avif/heic, or a download with no extension):
    # to_png reads the content, not the name.
    ext = Path(photo.filename or "").suffix.lower()[:8] or ".img"
    jdir = store.job_dir(job_id)
    with tempfile.TemporaryDirectory() as tmp:
        raw = _save_upload(photo, Path(tmp) / f"upload{ext}")
        try:
            to_png(raw, jdir / "assets" / f"{slot}.png")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"could not read photo: {e}") from e
    entry.file = f"assets/{slot}.png"
    if save_to_library:
        config.LIBRARY.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(jdir / entry.file, config.LIBRARY / f"{slot}.png")
    return view(store.save(job))


@app.post("/jobs/{job_id}/render")
def start_render(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    _editable(job)
    job = pipeline.set_status(job, "rendering")
    pipeline.submit(pipeline.render_and_export, job_id)
    return view(job)


@app.get("/jobs/{job_id}/renders/{name}")
def get_render(job_id: str, name: str) -> FileResponse:
    _job(job_id)
    path = store.job_dir(job_id) / "renders" / Path(name).name
    if not path.exists():
        raise HTTPException(404, "render not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/jobs/{job_id}/export/{orient}")
def get_export(job_id: str, orient: str) -> FileResponse:
    job = _job(job_id)
    ratio = next((r for r, o in ORIENT.items() if o == orient), None)
    rel = job.exports.get(ratio or "")
    if not rel:
        raise HTTPException(404, "no export for that orientation; render first")
    return FileResponse(store.job_dir(job_id) / rel, media_type="application/zip", filename=Path(rel).name)


@app.get("/usage")
def get_usage(job_id: str | None = None) -> dict[str, Any]:
    return usage.summary(job_id)


class ReviewRequest(BaseModel):
    fix: bool = True  # also revise + re-render once for the defects found


@app.post("/jobs/{job_id}/review")
def start_review(job_id: str, body: ReviewRequest) -> dict[str, Any]:
    job = _job(job_id)
    if job.status in ("planning", "rendering", "reviewing"):
        raise HTTPException(409, f"job is {job.status}")
    if not job.exports:
        raise HTTPException(409, "render the graphics before reviewing")
    job = pipeline.set_status(job, "reviewing")
    pipeline.submit(pipeline.review_job, job_id, body.fix)
    return view(job)


@app.get("/styles")
def list_styles() -> list[str]:
    return planner.styles()


@app.get("/lessons")
def get_lessons() -> dict[str, str]:
    path = config.PLANNER_DIR / "lessons.md"
    return {"text": path.read_text(encoding="utf-8") if path.exists() else ""}


class LessonRequest(BaseModel):
    text: str


@app.post("/lessons")
def add_lesson(body: LessonRequest) -> dict[str, Any]:
    return {"added": planner.remember(body.text)}


@app.get("/usage/dashboard")
def get_dashboard_data() -> dict[str, Any]:
    data = usage.dashboard()
    jobs = {j.id: j for j in store.all_jobs()}
    for row in data["jobs"]:
        j = jobs.get(row["job_id"])
        if j:
            row.update(created=j.created, status=j.status, video=j.video.file, duration_s=round(j.video.duration_us / 1e6, 1),
                       ratios=j.ratios, exports=len(j.exports), provider_choice=j.provider,
                       scenes=len(j.plan.scenes) if j.plan else 0)
    data["edits"] = {"jobs": len(jobs), "exported": sum(1 for j in jobs.values() if j.exports)}
    data["providers"] = {"claude": config.PLANNER_MODEL, "codex": config.CODEX_MODEL}
    return data


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


@app.get("/library")
def list_library() -> list[str]:
    return sorted(pipeline.library_slots())


@app.get("/library/{slot}")
def get_library_photo(slot: str) -> FileResponse:
    path = config.LIBRARY / f"{slug(slot)}.png"
    if not path.exists():
        raise HTTPException(404, "not in library")
    return FileResponse(path, media_type="image/png")
