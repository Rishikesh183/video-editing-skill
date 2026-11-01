## Worked example (1920x1080, 14 s): quality and density to aim for

Brief: a pipeline. The video goes through a frame sampler to a vision model; the story goes to a splitter; both
feed the judge, which has one rule. Timed words: "frames are sampled" 1.2, "stamped with their true time" 3.3,
"a vision model reports what happened" 6.4, "the story becomes statements" 8.6, "the judge cross-examines" 10.2.

css:
.title { left: 80px; top: 60px; font-size: 96px; }
.node { background: var(--panel); border: 3px solid var(--line); border-radius: 18px; padding: 20px 26px; height: 150px; width: 340px; }
.node .n { font-family: var(--display); font-size: 56px; text-transform: uppercase; line-height: 1; white-space: nowrap; }
.node .d { font-size: 34px; color: var(--muted); margin-top: 8px; white-space: nowrap; }
.strip { left: 480px; top: 215px; width: 500px; display: flex; gap: 10px; }
.fr { position: relative; width: 74px; height: 150px; border-radius: 6px; border: 3px solid var(--line); background: var(--panel-hi); overflow: hidden; }
.fr .ts { position: absolute; left: 0; right: 0; bottom: 0; background: var(--bg); color: var(--accent); font-size: 22px; text-align: center; padding: 4px 0; }
.stmt { left: 470px; width: 640px; font-size: 42px; padding: 8px 16px; border-left: 6px solid var(--accent); background: var(--panel-hi); white-space: nowrap; }
.rule { left: 1190px; top: 630px; width: 650px; padding: 22px 26px; font-size: 42px; font-weight: 700; line-height: 1.25; }

html:
<div class="grain"></div>
<div class="abs title head" id="title">Under the hood</div>
<svg class="wires" viewBox="0 0 1920 1080">
  <path id="w1" d="M420 290 H480"/><path id="w2" d="M985 290 H1100"/><path id="w3" d="M420 610 H470"/>
  <path id="w4" d="M1360 365 V430"/><path id="w5" d="M1110 590 C1150 590, 1150 500, 1190 500"/>
</svg>
<div class="abs node" id="video" style="left:80px; top:215px"><div class="n">Video</div><div class="d">the clip</div></div>
<div class="abs strip" id="strip"></div>
<div class="abs node" id="vision" style="left:1100px; top:215px"><div class="n">Vision model</div><div class="d">what happened</div></div>
<div class="abs node" id="story" style="left:80px; top:535px"><div class="n">Story</div><div class="d">their words</div></div>
<div class="abs stmt" id="s1" style="top:545px">1  Stopped at the crossing</div>
<div class="abs stmt" id="s2" style="top:615px">2  A train came from the left</div>
<div class="abs node" id="judge" style="left:1190px; top:430px; width:650px"><div class="n">The judge</div><div class="d">checks each one</div></div>
<div class="abs panel rule" id="rule"></div>

js:
const strip = KIT.$("#strip");
const frames = ["0.50s", "1.50s", "3.08s", "4.00s", "5.60s", "7.20s"].map((t) => {
  const f = document.createElement("div");
  f.className = "fr";
  f.innerHTML = `<div class="ts mono">${t}</div>`;
  strip.appendChild(f);
  return f;
});
KIT.slide(tl, "#title", 0.1, 0, -24);
KIT.pop(tl, "#video", 0.5); KIT.lit(tl, "#video", 0.5); KIT.wire(tl, "#w1", 1.0);
tl.from(frames, { opacity: 0, y: 30, duration: 0.35, ease: "expo.out", stagger: 0.1 }, 1.2);
tl.from(".fr .ts", { yPercent: 100, duration: 0.3, ease: "expo.out", stagger: 0.25 }, 3.3);
KIT.dim(tl, "#video", 3.3);
KIT.wire(tl, "#w2", 6.0); KIT.pop(tl, "#vision", 6.3); KIT.lit(tl, "#vision", 6.4); KIT.dim(tl, "#vision", 8.4);
KIT.pop(tl, "#story", 8.5); KIT.lit(tl, "#story", 8.5); KIT.wire(tl, "#w3", 8.8);
KIT.slide(tl, "#s1", 9.0, -30, 0); KIT.slide(tl, "#s2", 9.4, -30, 0); KIT.dim(tl, "#story", 10.0);
KIT.wire(tl, "#w4", 10.0); KIT.wire(tl, "#w5", 10.0); KIT.pop(tl, "#judge", 10.2); KIT.lit(tl, "#judge", 10.3);
KIT.pop(tl, "#rule", 11.2); KIT.type(tl, "#rule", "No verdict without positive evidence.", 11.4, 1.4);

sounds: tap 0.5, tick 1.2, tick 1.5, soft_click 3.3, click 6.3, click 8.5, tick 9.0, tick 9.4, click 10.2, typing 11.4
