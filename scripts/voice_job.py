"""Make a voice job from files on the PC (the same path the app takes), for testing a new series without the phone.

uv run --project server python scripts/voice_job.py plan voice.m4a captions.srt [--script script.txt] [--ratio 16:9] [--id NAME]
uv run --project server python scripts/voice_job.py images NAME folder_or_slot=file ...
uv run --project server python scripts/voice_job.py produce NAME
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from hfpipe import pipeline, store  # noqa: E402
from hfpipe.media import probe, to_png  # noqa: E402
from hfpipe.models import Job  # noqa: E402
from hfpipe.srt import parse_srt  # noqa: E402


def option(args: list[str], name: str, default: str = "") -> str:
    if name in args:
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    command = args.pop(0)
    if command == "plan":
        ratio, job_id = option(args, "--ratio", "16:9"), option(args, "--id") or store.new_id()
        script = Path(option(args, "--script")).read_text(encoding="utf-8") if "--script" in args else ""
        audio, srt = Path(args[0]), Path(args[1])
        dest = store.job_dir(job_id) / f"source{audio.suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(audio, dest)
        store.save(Job(id=job_id, ratios=[ratio], mode="voice", video=probe(dest), script=script, captions=parse_srt(srt.read_text(encoding="utf-8"))))
        pipeline.plan(job_id, "", True)
        job = store.load(job_id)
        print(job.status, job.error)
        if job.plan:
            print("theme:", job.plan.theme)
            print(job.plan.story)
            for scene in job.plan.scenes:
                print(f"{scene.start:6.1f}-{scene.end:6.1f} {scene.template:6} {scene.id}: {scene.data['title']} {scene.data['images']}\n    {scene.data['brief']}")
            for asset in job.plan.assets_needed:
                print(f"ASK {asset.slot}: {asset.description}")
    elif command == "images":
        job = store.load(args.pop(0))
        assert job.plan is not None
        for pair in args:
            slot, _, file = pair.partition("=")
            entry = next(a for a in job.plan.assets_needed if a.slot == slot)
            to_png(Path(file), store.job_dir(job.id) / "assets" / f"{slot}.png", 2800)
            entry.file = f"assets/{slot}.png"
        store.save(job)
        print("waiting for:", [a.slot for a in job.plan.assets_needed if not a.file])
    else:
        job_id = args.pop(0)
        pipeline.set_status(store.load(job_id), "rendering")
        pipeline.render_and_export(job_id)
        job = store.load(job_id)
        print(job.status, job.error, job.exports, job.review)


if __name__ == "__main__":
    main()
