"""File-backed job store: one directory per job, state in job.json."""

import threading
import time
import uuid
from pathlib import Path

from . import config
from .models import Job

_lock = threading.Lock()


def job_dir(job_id: str) -> Path:
    if not job_id.replace("-", "").isalnum():
        raise KeyError(job_id)
    return config.JOBS / job_id


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def save(job: Job) -> Job:
    path = job_dir(job.id) / "job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        for attempt in range(20):  # Windows refuses to replace a file another process is reading
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    return job


def load(job_id: str) -> Job:
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        raise KeyError(job_id)
    with _lock:
        return Job.model_validate_json(path.read_text(encoding="utf-8"))


def all_jobs() -> list[Job]:
    if not config.JOBS.exists():
        return []
    jobs = [load(p.parent.name) for p in config.JOBS.glob("*/job.json")]
    return sorted(jobs, key=lambda j: j.created, reverse=True)
