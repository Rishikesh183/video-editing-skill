"""Approximate an exported CutAgent bundle as an MP4 on the PC, for reviewing edits without the phone.

uv run --project server python scripts/preview_edit.py <bundle.zip> [-o out.mp4]

Shows: the main track (face, graphics, photo backgrounds with a slow zoom), photo/video overlays at their
side position, the voice, graphic sounds and sound effects. Not shown: person cut-out (the face overlay is a
plain rectangle here), captions/titles, transitions and entrance animations — those render in the app.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from hfpipe import config, render  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("bundle")
parser.add_argument("-o", "--output")
parser.add_argument("--captions", action="store_true", help="also draw the caption group (for a deliverable)")
parser.add_argument("--crf", default="22")
parser.add_argument("--skip", default="", help="comma-separated layer id prefixes to leave out, e.g. me-")
args = parser.parse_args()

bundle = Path(args.bundle)
out = Path(args.output or bundle.with_suffix(".preview.mp4"))
work = Path(tempfile.mkdtemp(prefix="hf-preview-"))
with zipfile.ZipFile(bundle) as z:
    z.extractall(work)
project = json.loads((work / "project.json").read_text(encoding="utf-8"))
sources = {s["id"]: s for s in json.loads((work / "sources.json").read_text(encoding="utf-8"))}
W, H, FPS = project["output"]["width"], project["output"]["height"], 30
cover = project["output"]["cover"]
sec = lambda us: us / 1_000_000  # noqa: E731

inputs: list[list[str]] = []
def add_input(path: Path, still_seconds: float | None = None) -> int:
    opts = ["-loop", "1", "-t", f"{still_seconds:.3f}", "-framerate", str(FPS)] if still_seconds else []
    inputs.append(opts + ["-i", str(path)])
    return len(inputs) - 1

fit = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}" if cover
       else f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2")
graph, segs, audio = [], [], []
t = 0
for i, clip in enumerate(project["clips"]):
    length = clip["outUs"] - clip["inUs"]
    if clip.get("background"):  # app-generated background clip (solid/gradient): first colour here
        color = clip["background"].get("color", "#000000").lstrip("#")
        inputs.append(["-f", "lavfi", "-t", f"{sec(length):.3f}", "-i", f"color=c=0x{color}:s={W}x{H}:r={FPS}"])
        graph.append(f"[{len(inputs) - 1}:v]setsar=1,format=yuv420p[v{i}]")
        segs.append(f"[v{i}]")
        t += length
        continue
    src = sources[clip["sourceId"]]
    path = work / src["path"]
    fx = clip.get("effects", {})
    keys = clip.get("keys") or []
    blurry = fx.get("effect") == "blur" or (keys and keys[-1].get("blur", 0) > 0.5)  # settled state, not a focus pull
    blur = ",boxblur=24:2,eq=brightness=-0.25" if blurry else ""
    zoom = keys[0].get("scale", 1.0) if keys else 1.0
    if src["mime"].startswith("image/"):
        n = add_input(path, sec(length))
        frames = max(1, round(sec(length) * FPS))
        graph.append(f"[{n}:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                     f"zoompan=z='1+0.14*on/{frames}':d=1:s={W}x{H}:fps={FPS},setsar=1{blur},trim=0:{sec(length):.3f},setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
    else:
        n = add_input(path)
        punch = f",scale={round(W * zoom / 2) * 2}:-2,crop={W}:{H}" if zoom > 1.1 else ""  # jump zoom
        dx, dy = (keys[-1].get("x", 0.5) - 0.5, keys[-1].get("y", 0.5) - 0.5) if keys else (0, 0)
        if dx or dy:  # moved shot (split screen): shift inside a black frame
            punch += f",pad={2 * W}:{2 * H}:{W // 2}:{H // 2},crop={W}:{H}:{round(W / 2 - dx * W)}:{round(H / 2 - dy * H)}"
        graph.append(f"[{n}:v]trim={sec(clip['inUs']):.3f}:{sec(clip['outUs']):.3f},setpts=PTS-STARTPTS,fps={FPS},{fit}{punch},setsar=1{blur},format=yuv420p[v{i}]")
        if src["hasAudio"] and not clip.get("muted"):
            graph.append(f"[{n}:a]atrim={sec(clip['inUs']):.3f}:{sec(clip['outUs']):.3f},asetpts=PTS-STARTPTS,"
                         f"volume={clip.get('volume', 1.0)},adelay={round(sec(t) * 1000)}:all=1[ca{i}]")
            audio.append(f"[ca{i}]")
    segs.append(f"[v{i}]")
    t += length
graph.append(f"{''.join(segs)}concat=n={len(segs)}:v=1:a=0[base]")

last = "base"
skip = tuple(x for x in args.skip.split(",") if x)
for j, layer in enumerate(l for l in project["images"] if not (skip and l["id"].startswith(skip))):
    src = sources[layer["sourceId"]]
    start, end = sec(layer["startUs"]), sec(layer["endUs"])
    keys = layer.get("keys") or []
    pose = keys[-2] if len(keys) > 2 else {}  # the settled pose (after the entrance, before the exit)
    scale = pose.get("scale", 1.0)
    width = round(W * layer["width"] * scale / 2) * 2
    stretched = round(width * layer.get("stretchX", 1.0) / 2) * 2
    cx = layer["x"] + (pose.get("x", 0.5) - 0.5)
    cy = layer["y"] + (pose.get("y", 0.5) - 0.5)
    if layer.get("cutout", {}).get("mode") == "people" and not src["mime"].startswith("image/"):
        # Person cut-out: one still frame of the creator, background removed (lips won't move here).
        still = work / f"cut{j}.png"
        mid = sec(layer.get("inUs", 0)) + (end - start) / 2
        subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{mid:.3f}", "-i", str(work / src["path"]),
                        "-frames:v", "1", str(still)], check=True)
        n = add_input(render.cutout_file(still), end - start)
        graph.append(f"[{n}:v]scale={width}:-2,format=rgba,setpts=PTS-STARTPTS+{start:.3f}/TB[o{j}]")
    elif src["mime"].startswith("image/"):
        n = add_input(work / src["path"], end - start)
        rot = layer.get("rotation", 0.0)
        turn = f",format=rgba,rotate={rot}*PI/180:ow=rotw({rot}*PI/180):oh=roth({rot}*PI/180):c=none" if rot else ""
        graph.append(f"[{n}:v]scale={stretched}:'trunc(ih*{width}/iw/2)*2'{turn},setpts=PTS-STARTPTS+{start:.3f}/TB[o{j}]")
    else:
        n = add_input(work / src["path"])
        a, b = sec(layer.get("inUs", 0)), sec(layer.get("inUs", 0)) + (end - start)
        graph.append(f"[{n}:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS+{start:.3f}/TB,scale={width}:-2[o{j}]")
    graph.append(f"[{last}][o{j}]overlay=x={cx * W:.0f}-w/2:y={cy * H:.0f}-h/2:enable='between(t,{start:.3f},{end:.3f})'[ov{j}]")
    last = f"ov{j}"

# Titles other than captions (name tags, punch text, labels), drawn plainly in their own font (bold).
FONTS = {"poppins": ("Montserrat.ttf", 0.62, -6), "anton": ("Anton-Regular.ttf", 0.45, -3), "archivo_black": ("ArchivoBlack-Regular.ttf", 0.7, -6),
         "montserrat": ("Montserrat.ttf", 0.66, -6), "inter": ("Inter.ttf", 0.62, -6),
         "barlow_condensed": ("BarlowCondensed-Bold.ttf", 0.42, -6)}


def font_file(font_id: str) -> tuple[str, float, int]:
    name, em, spacing = FONTS.get(font_id, FONTS["anton"])
    dest = work / f"{font_id if font_id in FONTS else 'anton'}.ttf"
    if not dest.exists():
        src = config.TEMPLATES / "_shared" / "fonts" / name
        try:  # variable fonts: pin the bold instance, as the app draws titles bold
            from fontTools.ttLib import TTFont
            from fontTools.varLib import instancer

            tt = TTFont(src)
            if "fvar" in tt:
                tt = instancer.instantiateVariableFont(tt, {"wght": 700})
            tt.save(dest)
        except ImportError:
            dest.write_bytes(src.read_bytes())
    return dest.name, em, spacing


def wrap(text: str, per_line: int) -> str:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > per_line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return "\n".join([*lines, line])


# Entrances, approximated like the app plays them: slides/fades ease in, typewriter and word reveal
# show the text growing, count-up steps the number. (Pop/zoom/slam read as a fast fade here.)
SLIDES = {"slide_left": (-0.25, 0), "slide_right": (0.25, 0), "slide_up": (0, 0.06), "slide_down": (0, -0.06)}
n_text = 0
for title in (x for x in project["titles"] if args.captions or not x.get("captionGroupId")):
    size = round(title.get("size", 42) * H / 720)
    text = title["text"].upper() if title.get("capitalize") else title["text"]
    fname, em, spacing = font_file(title.get("font", "anton"))
    per_line = max(4, int(W * (title.get("wrapWidth") or 0.85) / (size * em)))
    deco = title.get("decoration", {})
    box = deco.get("background")
    style = f":box=1:boxcolor=0x{box.lstrip('#')}@{deco.get('backgroundOpacity', 0.85)}:boxborderw=14" if box else ""
    stroke = deco.get("strokeWidth", 0)
    style += f":borderw={max(1, round(stroke * H / 720))}:bordercolor=0x{deco.get('strokeColor', '#000000').lstrip('#')}" if stroke else ""
    ts, te = sec(title["startUs"]), sec(title["endUs"])
    entrance = title.get("entrance", {})
    preset, d = entrance.get("preset", "none"), max(0.05, sec(entrance.get("durationUs", 400_000)))
    d = min(d, (te - ts) / 2)
    ease = f"(1-pow(1-clip((t-{ts:.3f})/{d:.3f},0,1),3))"
    dx, dy = SLIDES.get(preset, (0, 0))
    x = f"{title['x'] * W:.0f}-tw/2" + (f"+(1-{ease})*{dx * W:.0f}" if dx else "")
    y = f"{title['y'] * H:.0f}-th/2" + (f"+(1-{ease})*{dy * H:.0f}" if dy else "")
    alpha = f":alpha='{ease}'" if preset not in ("none", "typewriter", "word_reveal", "count_up") else ""

    def draw(body: str, a: float, b: float, fade: bool = True) -> None:
        global last, n_text
        (work / f"t{n_text}.txt").write_text(wrap(body, per_line), encoding="utf-8")
        graph.append(f"[{last}]drawtext=fontfile={fname}:textfile=t{n_text}.txt:expansion=none:fontsize={size}:fontcolor={title.get('color', '#FFFFFF')}"
                     f":line_spacing={size // spacing}:text_align=C{style}:x='{x}':y='{y}'{alpha if fade else ''}"
                     f":enable='between(t,{a:.3f},{b:.3f})'[tx{n_text}]")
        last = f"tx{n_text}"
        n_text += 1

    if preset in ("typewriter", "word_reveal", "count_up"):
        if preset == "count_up":
            c = title.get("counter", {})
            frm, to, dec = float(c.get("from", 0)), float(c.get("to", 0)), int(c.get("decimals", 0))
            steps = [f"{c.get('prefix', '')}{frm + (to - frm) * (1 - (1 - k / 12) ** 2):.{dec}f}{c.get('suffix', '')}" for k in range(12)]
        elif preset == "word_reveal":
            words = text.split()
            steps = [" ".join(words[: k + 1]) for k in range(len(words) - 1)]
        else:
            stride = max(1, len(text) // 24)
            steps = [text[: k] for k in range(stride, len(text), stride)]
        for k, body in enumerate(steps):
            if body.strip():
                draw(body, ts + d * k / len(steps), ts + d * (k + 1) / len(steps), fade=False)
        draw(text, ts + d, te, fade=False)
    else:
        draw(text, ts, te)

for k, m in enumerate(project["music"]):
    n = add_input(work / sources[m["sourceId"]]["path"])
    span = sec(m["endUs"] - m["startUs"])
    graph.append(f"[{n}:a]atrim={sec(m.get('inUs', 0)):.3f}:{sec(m.get('inUs', 0)) + span:.3f},asetpts=PTS-STARTPTS,"
                 f"volume={m.get('volume', 0.5)},adelay={round(sec(m['startUs']) * 1000)}:all=1[ma{k}]")
    audio.append(f"[ma{k}]")
graph.append(f"{''.join(audio)}amix=inputs={len(audio)}:normalize=0,atrim=0:{sec(t):.3f}[aout]")

cmd = [config.tool("ffmpeg"), "-v", "error", "-y"]
for opts in inputs:
    cmd += opts
cmd += ["-filter_complex", ";".join(graph), "-map", f"[{last}]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", args.crf, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", "-disposition:a:0", "default", "-movflags", "+faststart", "-t", f"{sec(t):.3f}", str(out.resolve())]
subprocess.run(cmd, check=True, cwd=work)  # relative font/text paths avoid drive-colon escaping
print(out)
