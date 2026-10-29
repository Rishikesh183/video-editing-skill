"""Build a template for one ratio and snapshot frames (no Claude, no export).

uv run --project server python scripts/preview_template.py <template> <9:16|16:9> [vars.json] [--dur 6] [--at 1,2.5,4] [--render]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from hfpipe import config, render  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("template")
parser.add_argument("ratio", choices=["9:16", "16:9"])
parser.add_argument("vars", nargs="?")
parser.add_argument("--dur", type=float, default=6)
parser.add_argument("--at", default="1,2.5,4")
parser.add_argument("--render", action="store_true")
parser.add_argument("--photo", help="png to use as assets/photo.png")
parser.add_argument("--cutout", help="transparent png to use as assets/cutout.png")
args = parser.parse_args()

path = render.template_path(args.template)
html = path.read_text(encoding="utf-8")
out = config.ROOT / "builds" / f"{args.template}-{render.ORIENT[args.ratio]}"
render.materialise(html, path.parent / "assets", out, args.ratio, args.dur)
variables = json.loads(Path(args.vars).read_text(encoding="utf-8-sig")) if args.vars else {}
variables = render.coerce_variables(render.declared_variables(html), variables)
if args.photo:
    (out / "assets" / "photo.png").write_bytes(Path(args.photo).read_bytes())
    variables["photo"] = "assets/photo.png"
if args.cutout:
    (out / "assets" / "cutout.png").write_bytes(Path(args.cutout).read_bytes())
    variables["cutout"] = "assets/cutout.png"
(out / "variables.json").write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")

env = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
hf = [config.tool("npx"), "-y", f"hyperframes@{config.HYPERFRAMES_VERSION}"]
subprocess.run(hf + ["lint", str(out)], env=env)
if args.render:
    dest = config.ROOT / "out" / f"{out.name}.mp4"
    subprocess.run(hf + ["render", str(out), "-o", str(dest), "--quiet", "--variables-file", str(out / "variables.json")], env=env)
    print(dest)
else:
    # snapshot has no variables flag; bake the values in as declared defaults for the preview build.
    decl = json.loads(json.dumps(list(render.declared_variables(html).values())))
    for d in decl:
        if d["id"] in variables:
            d["default"] = variables[d["id"]]
    index = out / "index.html"
    baked = render._VARS_ATTR.sub(lambda m: "data-composition-variables='" + json.dumps(decl, ensure_ascii=False).replace("'", "&#39;") + "'",
                                  index.read_text(encoding="utf-8"), count=1)
    index.write_text(baked, encoding="utf-8")
    subprocess.run(hf + ["snapshot", str(out), "--at", args.at], env=env)
