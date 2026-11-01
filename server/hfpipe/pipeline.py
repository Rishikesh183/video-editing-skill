"""Long-running steps, run one at a time on a single background worker."""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import config, export, media, planner, render, review, store, tighten, usage, voice
from .plan import align_to_mentions, normalize
from .models import Job

log = logging.getLogger("hfpipe")
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hfpipe")


def library_slots() -> list[str]:
    return [p.stem for p in config.LIBRARY.glob("*.png")] if config.LIBRARY.exists() else []


def _fail(job_id: str, exc: BaseException) -> None:
    log.error("job %s failed: %s", job_id, traceback.format_exc())
    job = store.load(job_id)
    job.status, job.error = "error", str(exc)[-1000:]
    store.save(job)


def prepare(job_id: str) -> None:
    """Swap the upload for an upright, edit-friendly copy (once per job)."""
    job = store.load(job_id)
    if job.mode == "voice":
        voice.prepare(job)
        return
    jdir = store.job_dir(job_id)
    if job.video.file.startswith("upright"):
        before = (jdir / job.video.file).stat().st_mtime
        media.match_audio_length(jdir / job.video.file)  # jobs normalised before audio padding existed
        if (jdir / job.video.file).stat().st_mtime != before:
            job.video = media.probe(jdir / job.video.file)
            store.save(job)
        return
    upright = media.normalize(jdir / job.video.file, jdir / "upright.mp4")
    job = store.load(job_id)
    job.video = media.probe(upright)
    if job.tighten:
        cuts = tighten.plan_cuts(tighten.silences(upright), job.captions)
        if cuts:
            tight = tighten.render(upright, cuts, job.video.duration_us / 1e6, jdir / "upright-tight.mp4")
            media.match_audio_length(tight)
            removed = sum(c.end - c.start for c in cuts)
            job.captions = tighten.remap_captions(job.captions, cuts)
            job.video = media.probe(tight)
            kinds = {k: sum(1 for c in cuts if c.why.startswith(k)) for k in ("retake", "filler")}
            job.tightened = (f"Removed {removed:.1f}s: {len(cuts) - sum(kinds.values())} long pauses, "
                             f"{kinds['retake']} retakes, {kinds['filler']} filler lines")
    store.save(job)


def plan(job_id: str, notes: str = "", fresh: bool = False, provider: str = "", revise: bool = False) -> None:
    try:
        prepare(job_id)
        job = store.load(job_id)
        if job.mode == "voice":
            with usage.metered(job_id, "plan"):
                new_plan, captions = voice.plan_job(job, notes, revise)
            job = store.load(job_id)
            job.plan, job.captions, job.status, job.error = new_plan, captions, "planned", ""
            job.renders, job.exports = [], {}
            job.tightened = voice.gap_note(job)
            store.save(job)
            return
        with usage.metered(job_id, "plan"):
            new_plan = planner.plan_job(job, library_slots(), notes, fresh, provider or job.provider, revise)
        new_plan = normalize(align_to_mentions(new_plan, job.captions), job.video.duration_us / 1e6)
        job = store.load(job_id)
        job.plan, job.status, job.error = new_plan, "planned", ""
        job.renders, job.exports = [], {}
        store.save(job)
    except Exception as e:  # noqa: BLE001 - surfaced to the client via job.error
        _fail(job_id, e)


def render_and_export(job_id: str) -> None:
    try:
        prepare(job_id)
        job = store.load(job_id)
        assert job.plan is not None
        if job.mode == "voice":
            with usage.metered(job_id, "make"):
                voice.produce(job_id)
            return
        results = []
        for ratio in job.ratios:
            for scene in job.plan.scenes:
                if scene.approved:
                    results.append(render.render_scene(job, scene, ratio))
                    job.renders = results
                    store.save(job)
        exports, warnings = {}, []
        for ratio in job.ratios:
            rel, warn = export.export_bundle(job, ratio)
            exports[ratio] = rel
            warnings += [f"[{ratio}] {w}" for w in warn]
        job = store.load(job_id)
        job.renders, job.exports, job.status = results, exports, "rendered"
        job.error = "; ".join(warnings)[-1000:]
        store.save(job)
    except Exception as e:  # noqa: BLE001
        _fail(job_id, e)


def render_and_review(job_id: str) -> None:
    """Render, then look at the result before the creator does. A failed look never costs them the finished edit."""
    render_and_export(job_id)
    job = store.load(job_id)
    if job.mode == "voice" or job.status != "rendered" or job.provider != "claude":  # voice videos review inside produce
        return
    review_job(job_id, True)
    job = store.load(job_id)
    if job.status == "error" and job.exports:
        job.status, job.error = "rendered", f"Self-review did not finish: {job.error[-200:]}"
        store.save(job)


def review_job(job_id: str, fix: bool = True) -> None:
    """Look at the rendered edit and the photos; with `fix`, revise the plan for exactly the listed defects,
    re-render, and look once more. One round only: anything still listed is left for the creator."""
    try:
        job = store.load(job_id)
        if job.mode == "voice":  # scenes are re-checked and fixed, and the project exported again
            set_status(job, "reviewing")
            with usage.metered(job_id, "review"):
                voice.produce(job_id)
            return
        job = set_status(job, "reviewing")
        with usage.metered(job_id, "review"):
            found = review.review(job, review.bundle_for(job))
            found["round"] = 1
            notes = review.fix_notes(found)
            if fix and found.get("edit_issues"):
                plan(job_id, notes, True, "", True)
                if store.load(job_id).status == "error":
                    return
                render_and_export(job_id)
                job = set_status(store.load(job_id), "reviewing")
                found = review.review(job, review.bundle_for(job))
                found["round"] = 2
        job = store.load(job_id)
        job.review, job.status = found, "rendered"
        store.save(job)
    except Exception as e:  # noqa: BLE001
        _fail(job_id, e)


def submit(fn, *args) -> None:
    _worker.submit(fn, *args)


def set_status(job: Job, status: str) -> Job:
    job.status, job.error = status, ""  # type: ignore[assignment]
    return store.save(job)
