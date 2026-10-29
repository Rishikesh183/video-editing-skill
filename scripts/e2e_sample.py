"""End-to-end run on the sample recording through the real API (in-process).

uv run --project server python scripts/e2e_sample.py [--replan]

Without --replan it reuses spike/plan.json, so no Claude tokens are spent.
Every requested photo slot gets a still from the recording as a stand-in.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from fastapi.testclient import TestClient  # noqa: E402

from hfpipe import config, store  # noqa: E402
from hfpipe.api import app  # noqa: E402
from hfpipe.models import Plan  # noqa: E402
from hfpipe.plan import normalize  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--replan", action="store_true")
args = parser.parse_args()

video = next(ROOT.glob("WhatsApp Video*.mp4"))
srt = next(ROOT.glob("*.srt"))
client = TestClient(app)


def wait(job_id: str, busy: str) -> dict:
    while True:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] != busy:
            return job
        done = sum(1 for r in job["renders"])
        print(f"  {busy}... {done} scene renders", flush=True)
        time.sleep(10)


with video.open("rb") as v, srt.open("rb") as s:
    r = client.post("/jobs", files={"video": (video.name, v, "video/mp4"), "captions": (srt.name, s)},
                    data={"auto_plan": str(args.replan).lower()})
r.raise_for_status()
job = r.json()
print("job", job["id"], f"{job['video']['duration_us'] / 1e6:.1f}s", job["video"]["width"], "x", job["video"]["height"])

if args.replan:
    job = wait(job["id"], "planning")
else:
    saved = json.loads((ROOT / "spike" / "plan.json").read_text(encoding="utf-8"))
    stored = store.load(job["id"])
    stored.plan = normalize(Plan.model_validate(saved), stored.video.duration_us / 1e6)
    stored.status = "planned"
    store.save(stored)
    job = client.get(f"/jobs/{job['id']}").json()
if job["status"] != "planned":
    sys.exit(f"planning failed: {job['error']}")
for sc in job["plan"]["scenes"]:
    print(f"  {sc['start']:6.2f}-{sc['end']:6.2f} {sc['mode']:9} {sc['template']}")

still = config.DATA / "e2e-still.png"
subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-ss", "3", "-i", str(video), "-frames:v", "1", str(still)], check=True)
for slot in job["missing_assets"]:
    with still.open("rb") as f:
        client.post(f"/jobs/{job['id']}/assets/{slot}?save_to_library=false", files={"photo": ("still.png", f, "image/png")}).raise_for_status()
print("photos supplied for", job["missing_assets"])

t0 = time.time()
client.post(f"/jobs/{job['id']}/render").raise_for_status()
job = wait(job["id"], "rendering")
print(f"render+export took {time.time() - t0:.0f}s, status={job['status']}")
for rr in job["renders"]:
    print(f"  {rr['scene_id']:14} {rr['ratio']:5} {rr['status']:7} {rr['detail'][:90]}")
print("exports:", job["exports"])
print("warnings/error:", job["error"] or "-")
print("usage:", client.get("/usage").json()["total"])
