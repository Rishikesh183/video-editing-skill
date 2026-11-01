// Helpers for voice-video scenes. Everything runs off the paused GSAP timeline `tl` (no timers, no callbacks).
window.KIT = (() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);
  const el = (s) => (typeof s === "string" ? $(s) : s);

  function timeline() {
    const tl = gsap.timeline({ paused: true });
    window.__timelines = window.__timelines || {};
    window.__timelines["main"] = tl;
    return tl;
  }
  // Characters as spans for a report-style type-on. Words stay whole, so text never breaks mid-word.
  function chars(sel, text) {
    const box = el(sel);
    box.textContent = "";
    text.split(" ").forEach((word, i, all) => {
      const w = document.createElement("span");
      w.style.display = "inline-block"; w.style.whiteSpace = "nowrap";
      for (const c of word + (i < all.length - 1 ? " " : "")) {
        const s = document.createElement("span"); s.className = "ch"; s.textContent = c; w.appendChild(s);
      }
      box.appendChild(w);
    });
    return box.querySelectorAll(".ch");
  }
  // Plain text as word spans; words from index `hot` on (negative counts from the end) take the accent colour.
  function words(sel, text, hot = null) {
    const box = el(sel);
    const list = text.split(/\s+/).filter(Boolean);
    const from = hot === null ? list.length : (hot < 0 ? list.length + hot : hot);
    box.textContent = "";
    list.forEach((w, i) => {
      const s = document.createElement("span"); s.className = "w" + (i >= from ? " accent" : ""); s.textContent = w; box.appendChild(s);
    });
    return box.querySelectorAll(".w");
  }
  function type(tl, sel, text, at, dur) {
    const nodes = chars(sel, text);
    tl.from(nodes, { opacity: 0, duration: 0.01, stagger: dur / Math.max(1, nodes.length) }, at);
    return nodes;
  }
  function reveal(tl, sel, text, at, hot = null, stagger = 0.08) {
    tl.from(words(sel, text, hot), { opacity: 0, y: 30, duration: 0.45, ease: "expo.out", stagger }, at);
  }
  function pop(tl, sel, at) {
    tl.from(sel, { opacity: 0, scale: 0.85, duration: 0.45, ease: "back.out(1.7)" }, at);
  }
  function slide(tl, sel, at, x = 0, y = 40) {
    tl.from(sel, { opacity: 0, x, y, duration: 0.5, ease: "expo.out" }, at);
  }
  // Draw an SVG path on.
  function wire(tl, sel, at, dur = 0.4) {
    const p = el(sel), len = p.getTotalLength();
    gsap.set(p, { strokeDasharray: len, strokeDashoffset: len });
    tl.to(p, { strokeDashoffset: 0, duration: dur, ease: "power2.out" }, at);
  }
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function lit(tl, sel, at) {
    tl.to(sel, { borderColor: css("--accent"), boxShadow: `0 0 0 3px ${css("--glow")}, 0 18px 50px ${css("--glow")}`, duration: 0.3 }, at);
  }
  function dim(tl, sel, at) {
    tl.to(sel, { borderColor: css("--line"), boxShadow: "0 18px 40px rgba(0,0,0,.35)", duration: 0.4 }, at);
  }
  // A number or timecode driven by the timeline.
  function count(tl, sel, from, to, at, dur, fmt = (v) => Math.round(v).toLocaleString("en-US")) {
    const box = el(sel), o = { v: from };
    box.textContent = fmt(from);
    tl.to(o, { v: to, duration: dur, ease: "power2.out", onUpdate: () => { box.textContent = fmt(o.v); } }, at);
  }
  return { $, $$, timeline, chars, words, type, reveal, pop, slide, wire, lit, dim, count };
})();
