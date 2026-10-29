"""Diagnostics: rewrite a job's export bundle with parts removed, to bisect device export failures.

uv run --project server python scripts/variant_bundle.py <job> <portrait|landscape> <no-images|no-titles|no-music|images-only:N> ...
The original zip is kept as <name>.orig.zip; pass "restore" to put it back.
"""
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from hfpipe import store  # noqa: E402

job, orient, *ops = sys.argv[1:]
zpath = store.job_dir(job) / "exports" / f"{job}-{orient}.zip"
orig = zpath.with_suffix(".orig.zip")
if not orig.exists():
    shutil.copyfile(zpath, orig)
if ops == ["restore"]:
    shutil.copyfile(orig, zpath)
    print("restored")
    sys.exit()
with zipfile.ZipFile(orig) as z:
    names = z.namelist()
    project = json.loads(z.read("project.json"))
    payload = {n: z.read(n) for n in names}
for op in ops:
    if op == "no-images":
        project["images"] = []
    elif op == "no-titles":
        project["titles"] = []
    elif op == "no-music":
        project["music"] = [m for m in project["music"] if m["id"] == "voice"]
    elif op == "no-voice":
        project["music"] = [m for m in project["music"] if m["id"] != "voice"]
    elif op == "no-transitions":
        for c in project["clips"]:
            c.pop("transition", None)
    elif op.startswith("cut:"):  # keep only the first N seconds of the timeline
        limit = float(op[4:]) * 1e6
        clips, t = [], 0
        for c in project["clips"]:
            if t >= limit:
                break
            clips.append(c)
            t += c["outUs"] - c["inUs"]
        project["clips"] = clips
        for key in ("images", "titles", "music"):
            project[key] = [x for x in project[key] if x["startUs"] < t]
            for x in project[key]:
                x["endUs"] = min(x["endUs"], t)
payload["project.json"] = json.dumps(project).encode()
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
    for n in names:
        z.writestr(n, payload[n])
print("variant", ops, "images", len(project["images"]), "titles", len(project["titles"]), "music", len(project["music"]))
