// Shared helpers for cricket templates. Deterministic only: no time, randomness or network.
window.HF = (function () {
  const vars = window.__hyperframes.getVariables();
  if (vars.accent) document.documentElement.style.setProperty("--accent", vars.accent);
  const root = document.getElementById("root");
  const portrait = root ? root.classList.contains("portrait") : true;

  function $(sel) { return document.querySelector(sel); }

  function text(sel, value) {
    const el = $(sel);
    if (el) el.textContent = value == null ? "" : String(value);
    return el;
  }

  // Lists arrive as JSON strings; tolerate plain strings and " | " separated text.
  function list(value) {
    if (Array.isArray(value)) return value;
    if (value == null || value === "") return [];
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch (e) {
      return String(value).split("|").map((s) => s.trim()).filter(Boolean);
    }
  }

  // Shrink a single-line element's font until it fits its box.
  function fit(el, maxWidth, maxHeight) {
    if (!el) return;
    let size = parseFloat(getComputedStyle(el).fontSize);
    const w = maxWidth || el.parentElement.clientWidth;
    for (let i = 0; i < 40 && size > 12; i++) {
      const tooWide = el.scrollWidth > w;
      const tooTall = maxHeight && el.scrollHeight > maxHeight;
      if (!tooWide && !tooTall) break;
      size *= 0.94;
      el.style.fontSize = size + "px";
    }
  }

  // Font size for a wrapped text block, from character counts (no layout measurement).
  function blockSize(str, boxW, boxH, maxPx, charW = 0.5, lineH = 1.12) {
    const words = String(str).split(/\s+/).filter(Boolean);
    const longest = Math.max(1, ...words.map((w) => w.length));
    const chars = Math.max(1, String(str).length);
    const byWord = (boxW * 0.95) / (longest * charW);
    const byArea = Math.sqrt((boxW * boxH * 0.5) / (chars * charW * lineH));
    return Math.floor(Math.min(maxPx, byWord, byArea));
  }

  // Photo slot: shows the image when supplied, else the player's initials.
  function photo(sel, src, name) {
    const frame = $(sel);
    if (!frame) return;
    frame.innerHTML = "";
    if (src) {
      const img = document.createElement("img");
      img.src = src;
      img.alt = name || "";
      frame.appendChild(img);
    } else {
      const mono = document.createElement("div");
      mono.className = "monogram display";
      mono.style.fontSize = Math.round(frame.clientHeight * 0.4) + "px";
      mono.textContent = String(name || "?").split(/\s+/).map((w) => w[0] || "").join("").slice(0, 3).toUpperCase();
      frame.appendChild(mono);
    }
  }

  function el(tag, cls, parent) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    (parent || $("#scene")).appendChild(node);
    return node;
  }

  /**
   * Background stack under everything in #scene: blurred tinted photo (or gradient + orb),
   * grain, vignette, light-sweep, light-leak and flash layers. Ken Burns drift for the whole duration.
   * opts: { photo, sharp, kenBurns: 'in'|'out', dur }
   */
  function backdrop(tl, opts) {
    const scene = $("#scene");
    const bg = document.createElement("div");
    bg.className = "bg " + (opts.photo ? "photo" + (opts.sharp ? " sharp" : "") : "plain");
    if (opts.photo) {
      const img = document.createElement("img");
      img.src = opts.photo;
      bg.appendChild(img);
      const tint = document.createElement("div");
      tint.className = "tint";
      bg.appendChild(tint);
    } else {
      const orb = document.createElement("div");
      orb.className = "orb";
      orb.style.left = "-15%";
      orb.style.top = "-10%";
      bg.appendChild(orb);
      tl.to(orb, { x: portrait ? 260 : 600, y: portrait ? 500 : 200, duration: opts.dur, ease: "sine.inOut" }, 0);
    }
    scene.insertBefore(bg, scene.firstChild);
    const zoomIn = opts.kenBurns !== "out";
    tl.fromTo(bg, { scale: zoomIn ? 1.0 : 1.14, xPercent: zoomIn ? 0 : 2 },
      { scale: zoomIn ? 1.14 : 1.0, xPercent: zoomIn ? -2 : 0, duration: opts.dur, ease: "none" }, 0);
    const grain = el("div", "grain");
    // Stepped offsets read as living film grain while staying seek-safe.
    tl.to(grain, { x: 37, y: -23, duration: opts.dur, ease: `steps(${Math.max(4, Math.round(opts.dur * 12))})` }, 0);
    el("div", "vignette");
    return { leak: el("div", "leak"), sweep: el("div", "sweep"), flash: el("div", "flash") };
  }

  function flash(tl, layers, at, strength = 0.85) {
    tl.fromTo(layers.flash, { opacity: strength }, { opacity: 0, duration: 0.35, ease: "power2.out", immediateRender: false }, at);
  }

  function leak(tl, layers, at, dur = 1.2) {
    tl.fromTo(layers.leak, { opacity: 0, x: -200 }, { opacity: 0.75, x: 0, duration: dur / 2, ease: "sine.out", immediateRender: false }, at)
      .to(layers.leak, { opacity: 0, x: 200, duration: dur / 2, ease: "sine.in" }, at + dur / 2);
  }

  function sweep(tl, layers, at, dur = 0.9) {
    const w = root ? root.clientWidth : 1080;
    tl.fromTo(layers.sweep, { x: -0.45 * w, opacity: 1 }, { x: 1.1 * w, duration: dur, ease: "power2.inOut", immediateRender: false }, at)
      .set(layers.sweep, { opacity: 0 }, at + dur);
  }

  // Short decaying shake on a target, deterministic.
  function shake(tl, target, at, power = 14, frames = 6) {
    for (let i = 0; i < frames; i++) {
      const k = (1 - i / frames) * power;
      tl.to(target, { x: (i % 2 ? -1 : 1) * k, y: (i % 3 - 1) * k * 0.6, duration: 1 / 30, ease: "none" }, at + i / 30);
    }
    tl.to(target, { x: 0, y: 0, duration: 1 / 30, ease: "none" }, at + frames / 30);
  }

  /** Player cut-out (transparent PNG) as a big hero layer. Returns the element or null. */
  function cutout(src, box) {
    if (!src) return null;
    const wrap = el("div", "cutout");
    Object.assign(wrap.style, box);
    const img = document.createElement("img");
    img.src = src;
    wrap.appendChild(img);
    return wrap;
  }

  // Text fitting must measure with the real fonts, so run it once they have loaded.
  function afterFonts(fn) {
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fn);
    else fn();
  }

  return { vars, portrait, $, text, list, fit, blockSize, photo, el, backdrop, flash, leak, sweep, shake, cutout, afterFonts };
})();
