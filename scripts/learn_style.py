"""Learn an editing style from a reference video and save it as planner/styles/<name>.md.

uv run --project server python scripts/learn_style.py <reference.mp4> --name edit2 [--fps 1]

Frames are sampled (default 1 per second) into labelled contact sheets; Claude reads them and writes concrete,
reusable rules (layouts and how often each is used, typography, colours, motion, transitions, pacing) in the
vocabulary of our templates. A job planned with that style gets the file appended to its brief.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from PIL import Image  # noqa: E402

from hfpipe import config, usage  # noqa: E402
from hfpipe.review import _sheet  # noqa: E402

BRIEF = """Read every sheet-*.png in this folder: frames sampled from a reference video an editor likes, each tile
labelled with its time in seconds. Work out the editing STYLE so it can be reproduced on other videos of a
talking-head cricket creator, and write it as concise markdown rules (no intro, no examples of this video's
topic). Cover, with numbers wherever you can measure them from the frames:
1. Layout mix: share of time on the full-screen presenter, split screen (which half), full-screen visuals,
   presenter + overlay; how long each layout holds before changing.
2. How visuals relate to speech (what kind of visual appears: screenshots, photos, cut-outs, cards, charts).
3. Typography: weight, case, size relative to frame, colours, plates/boxes, where text sits.
4. Colour palette and backgrounds.
5. Motion and transitions you can infer (zooms, slides, flashes, circles/highlights, callouts).
6. Pacing: how often something new appears.
Then map each rule onto these available building blocks where it fits: headline, text-punch, stat-count,
stat-hero, versus, kinetic-statement, photo-full, photo-behind, photo-frames (2 photos = duel), photo-overlay,
player-cutout, split (creator in one half + analysis panel with callout pills), news-cards, compare-bars,
timeline, ground-map. End with a short "Do / Don't" list."""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--name", required=True)
    parser.add_argument("--fps", type=float, default=1.0)
    args = parser.parse_args()
    video = Path(args.video).resolve()
    dest = config.PLANNER_DIR / "styles" / f"{args.name}.md"
    with tempfile.TemporaryDirectory(prefix="hf-style-") as tmp:
        work = Path(tmp)
        subprocess.run([config.tool("ffmpeg"), "-v", "error", "-i", str(video), "-vf", f"fps={args.fps},scale=360:-2",
                        str(work / "f%04d.png")], check=True)
        shots = sorted(work.glob("f*.png"))
        tiles = [(Image.open(p), f"{(i + 0.5) / args.fps:.1f}s") for i, p in enumerate(shots)]
        for n in range(0, len(tiles), 12):
            _sheet(tiles[n:n + 12], work / f"sheet-{n // 12 + 1:02d}.png", cols=6, tile_w=220)
        for p in shots:
            p.unlink()
        cmd = [config.claude_executable(), "-p", BRIEF, "--model", config.PLANNER_MODEL, "--tools", "Read",
               "--allowedTools", "Read", "--output-format", "json", "--strict-mcp-config", "--disable-slash-commands",
               "--no-session-persistence", "--setting-sources", ""]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=str(work), timeout=900)
    result = json.loads(proc.stdout)
    usage.record(None, f"learn style {args.name}", result)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# Style: {args.name} (learned from {video.name})\n\n{result.get('result', '').strip()}\n", encoding="utf-8")
    print(dest, "cost", result.get("total_cost_usd"))


if __name__ == "__main__":
    main()
