"""Small cricket graphics (ball, stumps, bat, brush stroke, burst, field) drawn locally as transparent PNGs.

They go into the edit as ordinary image layers with keyframes, so the creator can move, retime or delete them
in the app. Drawn at 4x and downsampled for clean edges; generated once into data/decor.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import config

SS = 4  # supersampling
VERSION = 6


def _canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _done(img: Image.Image, w: int, h: int) -> Image.Image:
    return img.resize((w, h), Image.LANCZOS)


def ball(size: int = 420) -> Image.Image:
    img, d = _canvas(size, size)
    s = size * SS
    r = s * 0.46
    cx = cy = s / 2
    for i in range(60, 0, -1):  # shaded red sphere, light from the top left
        t = i / 60
        rr = r * t
        ox, oy = -r * 0.25 * (1 - t), -r * 0.3 * (1 - t)
        shade = 0.45 + 0.55 * (1 - t) ** 0.6
        d.ellipse([cx + ox - rr, cy + oy - rr, cx + ox + rr, cy + oy + rr],
                  fill=(round(200 * shade + 30), round(22 * shade), round(28 * shade), 255))
    # Seam: two rows of stitches along a gently curved band.
    for side in (-1, 1):
        pts = []
        for k in range(41):
            a = -1.15 + 2.3 * k / 40
            x = cx + r * 0.93 * math.sin(a)
            y = cy + side * r * 0.11 + r * 0.06 * math.cos(a * 2)
            pts.append((x, y))
        d.line(pts, fill=(250, 236, 214, 255), width=round(s * 0.012))
        for x, y in pts[2:-2:2]:
            d.line([(x - s * 0.012, y - side * s * 0.03), (x + s * 0.012, y + side * s * 0.005)],
                   fill=(255, 244, 228, 255), width=round(s * 0.008))
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse([cx - r * 0.62, cy - r * 0.72, cx - r * 0.08, cy - r * 0.32], fill=(255, 255, 255, 110))
    img.alpha_composite(hl.filter(ImageFilter.GaussianBlur(s * 0.03)))
    return _done(img, size, size)


def stumps(w: int = 360, h: int = 520) -> Image.Image:
    img, d = _canvas(w, h)
    W, H = w * SS, h * SS
    sw = W * 0.12
    for i, x in enumerate((W * 0.2, W * 0.5, W * 0.8)):
        d.rounded_rectangle([x - sw / 2, H * 0.14, x + sw / 2, H * 0.98], radius=sw / 2, fill=(246, 226, 180, 255))
        d.rectangle([x - sw / 2, H * 0.14, x - sw / 6, H * 0.98], fill=(222, 196, 140, 255))
        d.rectangle([x - sw / 2, H * 0.3, x + sw / 2, H * 0.34], fill=(255, 122, 26, 255))
    for a, b in ((W * 0.17, W * 0.51), (W * 0.49, W * 0.83)):  # bails
        d.rounded_rectangle([a, H * 0.08, b, H * 0.13], radius=H * 0.02, fill=(255, 240, 205, 255))
    return _done(img, w, h)


def bat(w: int = 220, h: int = 640) -> Image.Image:
    img, d = _canvas(w, h)
    W, H = w * SS, h * SS
    d.rounded_rectangle([W * 0.4, 0, W * 0.6, H * 0.36], radius=W * 0.08, fill=(30, 30, 34, 255))  # grip
    for k in range(8):
        y = H * (0.03 + k * 0.042)
        d.line([(W * 0.4, y), (W * 0.6, y + H * 0.02)], fill=(70, 70, 80, 255), width=SS * 3)
    d.rounded_rectangle([W * 0.12, H * 0.34, W * 0.88, H * 0.99], radius=W * 0.2, fill=(236, 205, 150, 255))
    d.rounded_rectangle([W * 0.42, H * 0.38, W * 0.58, H * 0.95], radius=W * 0.06, fill=(214, 178, 118, 255))
    d.rectangle([W * 0.12, H * 0.52, W * 0.88, H * 0.6], fill=(255, 122, 26, 255))
    return _done(img, w, h)


def stroke(w: int = 1200, h: int = 190, color: tuple[int, int, int] = (255, 122, 26)) -> Image.Image:
    """A dry-brush marker stroke to sit behind a highlighted word."""
    img, d = _canvas(w, h)
    W, H = w * SS, h * SS
    rng = random.Random(7)
    for k in range(70):
        y = H * (0.2 + 0.6 * k / 69) + rng.uniform(-H * 0.02, H * 0.02)
        x0 = W * (0.02 + rng.uniform(0, 0.04)) + abs(k - 35) * W * 0.0007
        x1 = W * (0.98 - rng.uniform(0, 0.05)) - abs(k - 35) * W * 0.0006
        d.line([(x0, y), (x1, y + H * 0.04 * math.sin(k))], fill=(*color, rng.randint(200, 255)), width=round(H * 0.022))
    return _done(img.rotate(-2, resample=Image.BICUBIC, expand=False), w, h)


def burst(size: int = 900, color: tuple[int, int, int] = (255, 210, 63)) -> Image.Image:
    img, d = _canvas(size, size)
    s = size * SS
    c = s / 2
    for k in range(18):
        a = 2 * math.pi * k / 18
        spread = 0.07
        r0, r1 = s * 0.2, s * (0.44 if k % 2 else 0.36)
        pts = [(c + r0 * math.cos(a - spread), c + r0 * math.sin(a - spread)),
               (c + r1 * math.cos(a), c + r1 * math.sin(a)),
               (c + r0 * math.cos(a + spread), c + r0 * math.sin(a + spread))]
        d.polygon(pts, fill=(*color, 230))
    return _done(img, size, size)


def field(w: int, h: int) -> Image.Image:
    """Stylised night ground from above: mowing stripes, pitch strip, creases, floodlit vignette."""
    img = Image.new("RGBA", (w, h), (6, 28, 19, 255))
    d = ImageDraw.Draw(img)
    stripes = 9
    vertical = h > w
    for k in range(stripes):
        tone = (10, 42, 27, 255) if k % 2 else (8, 35, 23, 255)
        if vertical:
            d.rectangle([w * k / stripes, 0, w * (k + 1) / stripes, h], fill=tone)
        else:
            d.rectangle([0, h * k / stripes, w, h * (k + 1) / stripes], fill=tone)
    pw, ph = (w * 0.2, h * 0.62) if vertical else (w * 0.5, h * 0.18)
    box = [w / 2 - pw / 2, h / 2 - ph / 2, w / 2 + pw / 2, h / 2 + ph / 2]
    d.rectangle(box, fill=(78, 70, 48, 255))
    line = max(3, w // 300)
    for f in (0.1, 0.9):  # creases
        if vertical:
            y = box[1] + ph * f
            d.line([(box[0] - pw * 0.4, y), (box[2] + pw * 0.4, y)], fill=(170, 170, 160, 255), width=line)
        else:
            x = box[0] + pw * f
            d.line([(x, box[1] - ph * 0.4), (x, box[3] + ph * 0.4)], fill=(170, 170, 160, 255), width=line)
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([-w * 0.3, -h * 0.2, w * 1.3, h * 1.2], fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(min(w, h) * 0.3))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    return Image.composite(img, dark, glow)


def bar(w: int = 1200, h: int = 300) -> Image.Image:
    """Lower-third plate: slanted navy bar with a yellow accent edge and a thin underline."""
    img, d = _canvas(w, h)
    W, H = w * SS, h * SS
    k = H * 0.18  # slant
    d.polygon([(k, 0), (W, 0), (W - k, H * 0.86), (0, H * 0.86)], fill=(11, 16, 38, 238))
    d.polygon([(k, 0), (k + W * 0.05, 0), (W * 0.05, H * 0.86), (0, H * 0.86)], fill=(255, 210, 63, 255))
    d.polygon([(0, H * 0.9), (W - k, H * 0.9), (W - k - H * 0.02, H), (0, H)], fill=(255, 210, 63, 255))
    return _done(img, w, h)


def backdrop(w: int, h: int) -> Image.Image:
    """Studio wall for 'text behind me' when no photo was given: deep navy to maroon with a soft spotlight."""
    img = Image.new("RGBA", (w, h))
    top, bottom = (16, 22, 52), (70, 14, 30)
    d = ImageDraw.Draw(img)
    for y in range(h):
        u = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * u) for a, b in zip(top, bottom)) + (255,))
    spot = Image.new("L", (w, h), 0)
    ImageDraw.Draw(spot).ellipse([w * 0.1, h * 0.05, w * 0.9, h * 0.6], fill=90)
    spot = spot.filter(ImageFilter.GaussianBlur(min(w, h) * 0.15))
    return Image.composite(Image.new("RGBA", (w, h), (90, 110, 170, 255)), img, spot)


def grid(w: int = 1080, h: int = 960) -> Image.Image:
    """Analysis panel: near-black with a faint blueprint grid and a yellow hairline (as in data explainers)."""
    img = Image.new("RGBA", (w, h), (10, 11, 14, 255))
    d = ImageDraw.Draw(img)
    step = w // 12
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(30, 33, 40, 255), width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(30, 33, 40, 255), width=1)
    d.line([(0, h - 2), (w, h - 2)], fill=(255, 210, 63, 255), width=3)
    return img


def arrow(size: int = 160) -> Image.Image:
    """Green circular badge with a right arrow, leading a callout pill."""
    img, d = _canvas(size, size)
    s = size * SS
    d.ellipse([0, 0, s, s], fill=(34, 197, 94, 255))
    d.line([(s * 0.26, s / 2), (s * 0.62, s / 2)], fill=(255, 255, 255, 255), width=round(s * 0.09))
    d.polygon([(s * 0.78, s / 2), (s * 0.54, s * 0.3), (s * 0.54, s * 0.7)], fill=(255, 255, 255, 255))
    return _done(img, size, size)


def ring(w: int = 600, h: int = 360) -> Image.Image:
    """Hand-drawn red ellipse for circling something in a screenshot."""
    img, d = _canvas(w, h)
    W, H = w * SS, h * SS
    pts = []
    for k in range(400):
        a = 2 * math.pi * k / 360 - 0.3
        wobble = 1 + 0.03 * math.sin(a * 3)
        pts.append((W / 2 + W * 0.45 * wobble * math.cos(a), H / 2 + H * 0.4 * wobble * math.sin(a) + k * H * 0.0002))
    d.line(pts, fill=(225, 6, 0, 255), width=round(H * 0.035), joint="curve")
    return _done(img, w, h)


# Stage palettes: (top colour, bottom colour, glow colour)
PALETTES = {
    "blue": ((4, 12, 38), (10, 52, 140), (64, 140, 255)),
    "teal": ((2, 22, 26), (6, 92, 98), (40, 220, 200)),
    "crimson": ((24, 4, 10), (120, 14, 34), (255, 80, 90)),
    "purple": ((12, 6, 36), (70, 30, 130), (170, 110, 255)),
}


def stage(palette: str, w: int, h: int) -> Image.Image:
    """Designed backdrop for text moments: deep gradient, a soft glow behind the headline area, two diagonal
    light beams and a faint diagonal hatch, darkened at the edges. Calm enough for type, never flat."""
    top, bottom, glow = PALETTES[palette]
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        u = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * u) for a, b in zip(top, bottom)) + (255,))
    light = Image.new("L", (w, h), 0)
    ld = ImageDraw.Draw(light)
    ld.ellipse([-w * 0.35, -h * 0.3, w * 1.35, h * 0.5], fill=110)
    for k, (x0, width) in enumerate(((0.62, 0.16), (0.82, 0.07))):
        ld.polygon([(w * x0, 0), (w * (x0 + width), 0), (w * (x0 + width - 0.55), h), (w * (x0 - 0.55), h)],
                   fill=60 - 20 * k)
    light = light.filter(ImageFilter.GaussianBlur(min(w, h) * 0.14))
    img = Image.composite(Image.new("RGBA", (w, h), glow + (255,)), img, light.point(lambda v: v * 0.55))
    hatch = Image.new("L", (w, h), 0)
    hd = ImageDraw.Draw(hatch)
    step = max(12, w // 60)
    for x in range(-h, w, step):
        hd.line([(x, h), (x + h, 0)], fill=14, width=max(1, step // 6))
    img = Image.composite(Image.new("RGBA", (w, h), (255, 255, 255, 255)), img, hatch)
    edge = Image.new("L", (w, h), 255)
    ImageDraw.Draw(edge).ellipse([-w * 0.25, -h * 0.15, w * 1.25, h * 1.15], fill=0)
    edge = edge.filter(ImageFilter.GaussianBlur(min(w, h) * 0.15)).point(lambda v: v * 0.6)
    return Image.composite(Image.new("RGBA", (w, h), (0, 0, 0, 255)), img, edge)


DECOR = {"grid": grid, "arrow": arrow, "ring": ring, "bar": bar, "ball": ball, "stumps": stumps, "bat": bat, "stroke": stroke, "burst": burst,
         "stroke_yellow": lambda: stroke(color=(255, 210, 63))}


def file(name: str) -> Path:
    """Path of a decor PNG, drawing it on first use. Names: DECOR keys plus field_portrait/field_landscape."""
    dest = config.DATA / "decor" / f"v{VERSION}"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}.png"
    if not path.exists():
        if name == "field_portrait":
            img = field(1080, 1920)
        elif name == "field_landscape":
            img = field(1920, 1080)
        elif name.startswith("stage_"):
            _, palette, orient = name.split("_")
            img = stage(palette, *((1080, 1920) if orient == "portrait" else (1920, 1080)))
        elif name == "backdrop_portrait":
            img = backdrop(1080, 1920)
        elif name == "backdrop_landscape":
            img = backdrop(1920, 1080)
        else:
            img = DECOR[name]()
        tmp = path.with_suffix(".tmp.png")
        img.save(tmp)
        tmp.replace(path)
    return path


def text_png(lines: list[tuple[str, str]], width_px: int, dest: Path, max_line_px: int | None = None) -> Path:
    """Big poster type as a transparent PNG (for text that sits *behind* the creator's cut-out, which app
    titles cannot do: titles always paint above image layers). Each line is fitted to the width."""
    from PIL import ImageFont

    font_file = config.TEMPLATES / "_shared" / "fonts" / "ArchivoBlack-Regular.ttf"
    max_line_px = max_line_px or width_px // 3
    rendered = []
    for text, color in lines:
        size = max_line_px
        font = ImageFont.truetype(str(font_file), size)
        box = font.getbbox(text)
        if box[2] - box[0] > width_px:
            size = max(12, int(size * width_px / (box[2] - box[0])))
            font = ImageFont.truetype(str(font_file), size)
            box = font.getbbox(text)
        rendered.append((text, color, font, box))
    pad = max(8, width_px // 60)
    gap = pad
    height = sum(b[3] - b[1] for *_, b in rendered) + gap * (len(rendered) - 1) + 2 * pad
    img = Image.new("RGBA", (width_px + 2 * pad, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    y = pad
    for text, color, font, box in rendered:
        x = pad + (width_px - (box[2] - box[0])) // 2 - box[0]
        ImageDraw.Draw(shadow).text((x, y - box[1] + pad // 2), text, font=font, fill=(0, 0, 0, 150))
        ImageDraw.Draw(img).text((x, y - box[1]), text, font=font, fill=color)
        y += box[3] - box[1] + gap
    out = shadow.filter(ImageFilter.GaussianBlur(pad))
    out.alpha_composite(img)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest)
    return dest
