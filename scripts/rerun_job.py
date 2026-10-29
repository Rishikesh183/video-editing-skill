"""Re-plan (fresh Claude call) and re-render an existing job on the PC, without the phone.

uv run --project server python scripts/rerun_job.py <job_id> [--stand-in data/library/x.png] [--no-plan] [--provider claude|codex] [--plan-only]
--stand-in fills every photo slot that has no upload or library photo (for previewing the look only).
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from hfpipe import config, pipeline, store, usage  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("job_id")
parser.add_argument("--stand-in")
parser.add_argument("--no-plan", action="store_true")
parser.add_argument("--provider", choices=["claude", "codex"], default="")
parser.add_argument("--plan-only", action="store_true")
parser.add_argument("--revise", action="store_true", help="update the current plan instead of planning from scratch")
parser.add_argument("--notes", default="")
args = parser.parse_args()

if not args.no_plan:
    pipeline.plan(args.job_id, args.notes, True, args.provider, args.revise)
job = store.load(args.job_id)
if job.status == "error" and not args.no_plan:
    sys.exit(job.error)
for s in job.plan.scenes:
    print(f"  {s.start:6.2f}-{s.end:6.2f} {s.mode:9} {s.template:16} {str(s.data)[:90]}")
if args.stand_in:
    for a in job.plan.assets_needed:
        if not a.file and not (config.LIBRARY / f"{a.slot}.png").exists():
            dest = store.job_dir(job.id) / "assets" / f"{a.slot}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.stand_in, dest)
            a.file = f"assets/{a.slot}.png"
    store.save(job)
print("photos:", [(a.slot, a.file or "library") for a in job.plan.assets_needed])
if args.plan_only:
    print("usage:", usage.summary(job.id)["by_step"])
    sys.exit(0)
pipeline.render_and_export(args.job_id)
job = store.load(args.job_id)
for r in job.renders:
    print(f"  {r.scene_id:8} {r.ratio:5} {r.status:7} {r.detail[:80]}")
print("status:", job.status, "| notes:", job.error[:300])
print("exports:", job.exports)
print("usage:", usage.summary(job.id)["by_step"])
