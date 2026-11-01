# Agent handoff: AI video editor (Rust backend + Anthropic API)

You are the engineering and editing agent for a web-based AI video editor. Users upload talking-head or
commentary footage. The backend plans motion graphics and edits with Claude, renders animated scenes,
assembles an editable project (layers + keyframes, never a flattened video only), and reviews its own
output before the user sees it.

Everything here was learned building a working version of this pipeline (Python/FastAPI server, Kotlin
Android editor, Claude Code CLI as planner). This document translates it to: **Rust backend, direct
Anthropic API keys, generic (any niche) content.** Read it fully before writing code. Sections 1-3 are the
editorial brain, 4-7 the engineering, 8-10 the hard-won pitfalls.

---

## 0. North star and working rules

**Goal:** an edit that looks produced on the first or second try, stays fully editable, and costs as few
tokens and revision rounds as possible. The previous build took 9-10 rounds to land one edit; every
design choice below exists to cut that number.

Working rules for you, the agent:

1. **Never trust the plan, trust the frames.** Render, look at the frames (vision), fix, then ship.
2. **Update, don't rewrite.** A re-plan revises the current plan; it keeps what works.
3. **Save corrections as lessons, not one-off fixes.** If the user says "never dark text on orange", that
   goes into the user's preference file and is in every future prompt.
4. **Measure, don't guess.** Text width with real font metrics, durations from the video stream, face
   position from detection, cost from `usage` in the API response.
5. **Determinism outside the model.** The model decides *what* and *when*; code decides *exact pixels,
   timing snaps, colours, fonts, durations*. Never let the model output raw coordinates for layout that
   code can compute.
6. **Never invent facts.** Numbers, names and claims on screen must come from the transcript/script.
7. **One phase at a time.** Build, run tests (`cargo test`, `cargo clippy -- -D warnings`), stop and summarise.
8. Secrets (`ANTHROPIC_API_KEY`, storage keys) live only on the server, in env / `.env` never committed.
   The browser never calls Anthropic directly.

---

## 1. The pipeline (end to end)

```
upload ─► ingest ─► transcribe ─► (tighten) ─► PLAN ─► align ─► build layers ─► render scenes
                                                 ▲                                     │
                                                 │                                     ▼
                       lessons + style ──────────┤                         preview frames / contact sheet
                                                 │                                     │
                                                 └──── REVISE (fix list) ◄── SELF-REVIEW (vision)
                                                                                       │
                                                                          export editable project
```

### 1.1 Ingest (ffmpeg, no tokens)
- Probe with `ffprobe -show_streams -of json`. **Duration = the video stream's duration**, not the
  container's (container duration includes trailing audio and breaks timelines).
- Normalise once to an upright, constant-frame-rate working copy: apply the rotation flag (phones store
  portrait video sideways + rotation metadata), 30 fps CFR, keyframe every 1 s (`-g 30 -keyint_min 30`)
  so scrubbing/preview is smooth and cuts are exact.
- Audio: loudness-normalise voice to `loudnorm=I=-14:TP=-1.5:LRA=11` (YouTube/Shorts target).
- **Make audio cover the full video length** (`apad` + `-shortest` against the video duration). Many
  muxers/encoders stall or truncate when audio ends before video.
- Images: convert every upload to PNG (handle HEIC/HEIF, WebP, EXIF orientation). Accept any format.

### 1.2 Transcribe
- Timed captions (word-level if possible). Use a local Whisper (`whisper.cpp` via `whisper-rs`) or a
  hosted STT. Captions drive *everything*: plan timing, mention alignment, tighten cuts.
- Store as a list of `{start_us, end_us, text, words?}`. Feed the model SRT text (compact, it knows it).

### 1.3 Tighten (optional, no tokens)
- `ffmpeg -af silencedetect=noise=-35dB:d=0.45` → silences. Cut silences longer than ~0.45 s but
  **keep 0.25 s of breath** on each side so speech does not feel chopped.
- Detect filler words and retakes from the transcript (a sentence restarted with the same first 3-5
  words → keep the last take).
- After cutting, **remap every caption timestamp** through the cut list. Plan only after tightening.

### 1.4 Plan (Claude, the only expensive step)
See §3 for prompts and §5 for the API call. The model returns a JSON plan: a think-first `story` plus a
list of `scenes` (template + timing + data + asset slots). Cache by hash of (system prompt, schema,
model, effort, input). Identical input = zero tokens.

### 1.5 Align to mentions (code, no tokens)
The model is bad at exact timing. After planning, snap each scene:
- For each scene about a named subject (person, place, product), fuzzy-match the subject name
  (token overlap / normalised Levenshtein, lowercase, strip punctuation) against caption text.
- **Start the scene on the caption where the subject is first named** (never before). Keep it while the
  subject is still being discussed (typically 4-6 s), end on a caption boundary.
- Resolve conflicts: full-frame scenes may not overlap; an overlay that collides with a full-frame
  scene gets moved or dropped; minimum gap and duration rules (§2.4) re-applied.
- Then `normalize()`: clamp to video length, drop unknown templates, auto-add any asset slots that scenes
  reference but the plan forgot to request.

### 1.6 Build editable layers (code)
Translate each scene into project layers with keyframes (§6). Things the editor app can animate natively
(titles, photo cards, cut-outs, zooms, transitions) should be **native layers**, so the user can tweak
them. Only things the editor can't express (charts, maps, kinetic typography, data graphics) are
**rendered** to video clips (§7).

### 1.7 Render scenes (no tokens)
HTML/CSS + GSAP compositions rendered headless to MP4 (HyperFrames, Remotion, or your own
Chromium-frame-capture pipeline). One render per scene per aspect ratio. Cache by hash of
(template, data, assets, sounds, ratio).

### 1.8 Self-review (Claude with vision, cheap)
Render a preview, sample frames (one or two per scene), tile them into labelled contact sheets, plus one
sheet of all uploaded photos with their slot names. Send to Claude with the rules + user lessons. Get a
structured defect list. Run **one** revise pass that fixes only those defects, re-render, look again.
This replaced most human revision rounds. Cost ≈ a few cents per look.

### 1.9 Export
Editable project JSON + media + manifest with SHA-256 per file. Validate the project with the editor's
own validator before handing it to the user (run the same schema/limit checks the client runs).

---

## 2. Editorial rules (the "taste" — put these in the system prompt)

These are generic; the niche (sports, tech, finance, cooking…) changes the examples only.

### 2.1 Think first
Before choosing any scene, the model writes a `story`:
1. The **hook** (what makes a scrolling viewer stop), the 3-5 **beats** of the argument, the **payoff**.
2. For each beat, the *picture* a top editor in this niche would show. **Show the idea, not the words**:
   translate figures of speech into the concrete situation.
   - "neck and neck / too close to call" → both sides side by side with the real numbers that make it
     close, never a lone "100%".
   - "comeback / years later" → a timeline with the years + the person.
   - "best ever / record" → bars against previous record holders.
   - "the plan / how it works" → a diagram that builds one part per beat.
   - "who is better" → versus with 2-3 real numbers each.
   - big emotional moment → full-frame photo of that moment.
3. Only then pick templates.

### 2.2 Structure (retention)
- **Hook (0-3 s):** stay on the speaker's face with a headline overlay. Never open on a graphic.
  Hook text is concrete: a name, number or date plus stakes or a question ("8 YEARS LATER",
  "WHY $430M WASN'T ENOUGH"). Never vague hype words (amazing, insane, crazy, huge).
- **Body:** ~55-65% of runtime is visuals; something new every 3-6 s (1.5-3 s during dense analysis).
- Pattern face → visual → face → visual. Never > 8 s of plain face without a visual or headline.
- 3-4 big animated scenes per 40-60 s video, never two in a row. Never the same template twice in a row.
- **Ending:** last 2-3 s on the face (sign-off/CTA).
- Gaps between scenes ARE the face. The model never plans "face" scenes. Face stretches get automatic
  jump-zooms at sentence starts (scale 1.0 ↔ ~1.1), done in code.

### 2.3 Content
- Use ONLY numbers and facts present in the transcript/script. If a template needs a value that wasn't
  said, choose another template or mark `needs_input`.
- A number becomes a count-up only if it is a real metric, not an expression ("100% sure").
- On-screen text is short: 2-7 words per line; payoff 1-3 words. `text` = lead-in, `highlight` = payoff;
  never repeat highlight words inside text.
- Every text or number moment gets a **related picture** (moment photo, or cut-outs of the people
  involved). A bare coloured box with text is a failure.
- Two-sided comparisons are a **duel**: one side enters from the top, the other from the bottom
  (left/right in 16:9), contrasting frames (red vs blue), "VS" between. Never two photos piled up.
- Each person slot is a solo photo of exactly that person; the two sides always use different people.

### 2.4 Timing
- A visual about a subject starts **when it is named** and stays while it is discussed.
- Scenes 2.5-6 s, aligned to caption boundaries, full-frame scenes never overlap.
- No sound effects in the first 3.5 s (the hook is the voice).

### 2.5 Asset requests (the model asks the user for pictures)
Each requested slot has a snake_case name and a "shopping spec" the user can search for:
`"<who/what>, <moment/context>; <shot: close-up|waist-up|wide>; vertical preferred; <space needed>; no text,
logos or watermarks"`. Background-only slots end in `_bg`. Reuse existing library slot names exactly.
Never scrape copyrighted images; assets come from the user (or licensed/stock APIs the user configured).

---

## 3. Visual design rules (enforced in code, also stated in the prompt)

### 3.1 Typography
- Bold modern families only; never thin or plain system fonts. Working set:
  - Display / slams: **Archivo Black**
  - Headings: **Montserrat Bold/ExtraBold**
  - Labels, UI, numbers: **Inter** (with `font-variant-numeric: tabular-nums` for count-ups)
  - Straps / lower thirds: **Barlow Condensed SemiBold**
- **Measure text with the real font file** (Rust: `ab_glyph` or `fontdue`/`swash` + the TTF) and fit to
  90% of the available width. Pick the largest size that fits within the allowed line count; break lines
  at word boundaries yourself. EM-width heuristics caused unexpected wraps — don't use them.
- Title size scales with output height (`size * H / 720`), clamp to the editor's min/max.

### 3.2 Colour
- On footage: **white text with a black stroke** (or strong shadow).
- On coloured plates: dark text **only** on yellow or white plates, and **no stroke** on plate text.
  Never dark text on orange.
- Payoff word in the accent (yellow by default).
- Designed colour "stages" for text/stat moments rotate through a palette (blue, crimson, teal, purple),
  each a radial/vignetted ground with subtle grain — **not** a full-screen linear gradient on dark (it bands
  in H.264). Allow the plan to pick a palette that matches a brand/team.
- Never plain dark-blue boxes; don't blur the speaker's own shot behind text (at most once per video).

### 3.3 Layout
- Keep caption-safe zones clear: bottom 25% in 9:16, bottom 20% in 16:9.
- **No text over the speaker's face.** Detect the face per scene (OpenCV Haar/DNN via the `opencv` crate,
  or a small ONNX face model via `ort`) and place text in the other half.
- Nothing stacked on top of something else; labels sit on the parts they name, with leader lines.
- When the speaker is cut out and shrunk aside, they sit **flush in the bottom-left corner** (no gap
  below or left). Compute the anchor from the cut-out's actual alpha bounding box, not the image size.
- Photo cards: border + bottom caption band + soft shadow ("photo frame").

### 3.4 Motion (applies to rendered scenes and native keyframes)
- Arrivals: `expo.out` / `power4.out` (native: `ease_out` or `back_out` for pops). Exits 40-50% shorter
  than arrivals.
- Word-by-word reveals: 60-120 ms stagger.
- Titles land, then **hold still** (no creeping scale).
- Diagrams grow one part per beat; count-ups run 1.2-1.6 s.
- Transitions: zoom-fade ~400 ms between face and full-frame scenes; flash-white ~320 ms into photo
  reveals; whip for back-to-back visuals. Nothing snaps.
- Rendered compositions must be deterministic: transform/opacity tweens only, no `Date.now()`,
  `Math.random()`, timers — seeded values only.

### 3.5 Sound
- Every entrance gets a matching sound (whoosh, whip, pop, impact, riser, shutter, ding, typing, boom).
- Generate SFX procedurally (CC0, you own them) or from a licensed pack; cache to disk.
- No SFX in the first 3.5 s. If more than ~7 cues, mix them into one "SFX bed" track instead of many
  audio items (editor track limits).
- SFX ~ -18 to -12 dB under the voice; duck music under speech.

---

## 4. Prompts (copy these, generalise the examples)

Prompts are files on disk (`prompts/system.md`, `prompts/review.md`, `prompts/styles/*.md`,
`data/users/<id>/lessons.md`), loaded at request time. Assemble in a fixed, cache-friendly order:

```
system = [ editor_brief (static, largest) | template_catalog (static) ]  ← cache_control here
       + [ style file (per job, optional) ]
       + [ user lessons (per user) ]                                     ← second cache breakpoint
user   = [ duration, captions SRT, script, asset library slot names, notes, (current plan if revising) ]
```

### 4.1 Editor brief (system.md) — skeleton
```
You are the senior editor for <creator>'s talking-head videos (Shorts and long form).
Input: timed captions (SRT), optional script and notes. The creator's voice plays for the whole video.
You decide what the viewer SEES at each moment. Every visual must earn its place and move.

Think first, in "story" (before any scene): hook, 3-5 beats, payoff; for each beat the PICTURE a top
editor in this niche would show. Show the idea, not the words. <niche examples>

Structure: <§2.2 rules>
Choosing the visual: <map of phrase types → templates>
Content rules: <§2.3>
Photos: <§2.5 shopping-spec format>
Templates: <catalogue: name, mode (overlay|fullframe), duration range, fields with limits, one-line look>
Output JSON only, matching the schema. Keep "reason" under 12 words.
```
The template catalogue must list every field with hard limits (e.g. `title ≤ 6 words`, `items[2-5]`,
`text ≤ 9 words`). The model respects limits it can see.

### 4.2 Lessons file (per user, grows over time)
```
# <User>'s standing preferences
Learned from feedback on earlier edits. They override general editing habits.
## Look / ## Pictures / ## Words and numbers / ## Timing and motion / ## Re-plans
- ...
## Added from re-plan notes
- <appended automatically when the user ticks "Remember this">
```
`remember(note)`: whitespace-normalise, skip if already present (case-insensitive), append as a bullet.
Lessons go into planner **and** reviewer prompts.

### 4.3 Revise prompt (append to user message when revising)
```
This is a REVISION of the current plan (JSON below). Keep its scenes, templates, timings, photos and wording
unless something is wrong or the notes ask for a change. Fix: wrong or repeated photos (each side/person
gets their own photo), visuals that start before the subject is named or end while it is still being
discussed, text that is too long, and anything the notes mention. Output the FULL revised plan, including
unchanged scenes with their same ids, and update "story" with what you changed and why.
Current plan: <json>
```

### 4.4 Review prompt (vision)
```
You are reviewing an edit before the creator sees it. Images: edit sheets (each tile labelled with scene id,
time and template) and a photos sheet (every uploaded photo with its slot name and what it was requested as).
The frames come from an APPROXIMATE preview: <list what the preview doesn't draw>. Don't report those.
Report only real, specific defects a viewer would notice, against the rules and preferences below:
overlapping/stacked elements; text over the face; text wrapping unexpectedly or off-screen; a picture
unrelated to its moment; empty or unbalanced frame; same person/photo on both sides of a comparison;
a photo contradicting its slot (wrong team/brand/kit); screenshots, social posts, collages or watermarks used
as a person photo. Do not identify people from faces; judge context, captions, text and layout.
Each fix must be a concrete change to the plan (scene id, field, new value or layout).
If the edit is clean, say so and return empty lists.
```
Review output schema: `{summary, edit_issues[{scene_id, problem, fix}], photo_issues[{slot, problem}]}`.
Then `fix_notes = issues → bullet list` → revise call (§4.3) → re-render → (optional) one more look.
**Cap at one automatic fix round**; then hand to the user with the remaining issues listed.

### 4.5 Style learning from a reference video
User uploads a reference edit. Code samples ~1 frame/s, builds contact sheets with timestamps, sends to
Claude (vision) asking for a style file: layout mix, panel content, typography, colour use, motion,
pacing (element every N s), building blocks (which of *our* templates map to it), do/don't. Save as
`styles/<name>.md`; user picks a style per job. Example output:
```
# Style: split-explainer
- Layout mix: ~half the video is a 50/50 split (analysis panel top, speaker bottom) alternating with full face.
- Panel content: real screenshots on a near-black grid, slow push-in; key number circled in red; callout
  pills appear one by one as each point is spoken (white pill = fact, red pill = key fact).
- Typography: clean sans, sentence case; colour only on the key value.
- Motion: pills pop, screenshots ease and drift, no flashy transitions; a sound on every pill.
- Pacing: a new element every 1-1.5 s inside the panel.
- Building blocks: split, compare-bars, stat-hero, headline. Don't: decorative backdrops.
```

---

## 5. Anthropic API from Rust (raw HTTP)

There is no official Rust SDK — call the REST API with `reqwest` + `serde_json`. Never use
OpenAI-compatible shims. All calls happen server-side.

### 5.1 Request basics
```
POST https://api.anthropic.com/v1/messages
x-api-key: $ANTHROPIC_API_KEY
anthropic-version: 2026-10-21
content-type: application/json
```
- **Models (Oct 2026):** `claude-opus-5-5` ($4 in / $20 out per MTok, cache reads $0.20) — default for
  planning and review. `claude-sonnet-5-5` ($2 / $10) and `claude-haiku-5-5` ($0.10 / $0.50 up to 100K
  input) are cheaper options if the user chooses to trade quality for cost (style learning, title
  suggestions, simple classification). Use exact IDs, no date suffixes. Make the model a config value.
- **Thinking:** on Opus 5.5 thinking is always on; omit `thinking` or send `{"type":"adaptive"}`.
  `{"type":"disabled"}` and `budget_tokens` return 400. Control depth with
  `"output_config": {"effort": "low"|"medium"|"high"|"xhigh"|"max"}` — Opus 5.5 defaults to `medium`;
  use `high` for planning, `medium` for review, `low` for trivial calls.
- **No sampling params:** `temperature`/`top_p`/`top_k` return 400 on this API version. No assistant
  prefill (400).
- **Forced tool use is rejected** on Opus 5.5 / Sonnet 5.5 (`tool_choice` `any`/`tool` → 400). Don't use
  the old "force a tool call to get JSON" trick; use structured outputs (below).
- **Stream** when `max_tokens` is large (> ~16K) to avoid HTTP timeouts (`"stream": true`, parse SSE,
  accumulate `content_block_delta`). A plan fits in 16K non-streaming; use streaming anyway for UI progress.
- **Always check `stop_reason`** before reading content: `end_turn` OK; `max_tokens` → truncated, retry with
  more; `refusal` → read `stop_details.category`, surface to the user. Optionally enable server fallback:
  header `anthropic-beta: server-side-fallback-2026-07-01` + body `"fallbacks": "default"`.
- Retry 429/5xx/529 with exponential backoff + `retry-after`; don't retry 400s.

### 5.2 Structured output (the plan, the review)
```json
{
  "model": "claude-opus-5-5",
  "max_tokens": 16000,
  "output_config": {
    "effort": "high",
    "format": { "type": "json_schema", "schema": { ...plan schema... } }
  },
  "system": [...],
  "messages": [{ "role": "user", "content": "..." }]
}
```
The answer is one `text` block containing JSON matching the schema; still `serde_json::from_str` it into
your Rust types and validate business rules (limits, known templates) yourself.

Schema limitations: every object needs `"additionalProperties": false` and `required`; no recursive
schemas; no `minimum`/`maximum`/`minLength`/`maxLength` (enforce in code); `enum`, `anyOf`, `$ref` are fine.
**Open-ended per-template data**: either a `anyOf` of one object schema per template (best — validates
every field), or pass `data` as a JSON *string* and parse it after (the trick used for strict-mode
providers). Generate the schema from the template catalogue so they never drift.

### 5.3 Prompt caching
Put `"cache_control": {"type": "ephemeral"}` on the last block of the stable prefix (`"ttl": "1h"` for
longer reuse). Up to 4 breakpoints. Order: tools → system → messages. Rules:
- Static brief + template catalogue first (one breakpoint), then per-user lessons (second breakpoint),
  then the per-job user message (not cached).
- Any byte change invalidates everything after it: no timestamps, request IDs, unsorted JSON
  (`serde_json` with `preserve_order` or BTreeMap) in the cached prefix.
- Prefixes shorter than the model minimum (512-4096 tokens) silently don't cache.
- Verify via `usage.cache_read_input_tokens` > 0 on repeat calls.

### 5.4 Images (review, style learning)
```json
{ "role": "user", "content": [
  { "type": "image", "source": { "type": "base64", "media_type": "image/jpeg", "data": "<b64, no newlines>" } },
  { "type": "text", "text": "Sheet 1: scenes s1-s8 ..." },
  { "type": "text", "text": "<review brief>" }
]}
```
Images before the text that refers to them. Downscale contact sheets (~1200-1600 px long side, JPEG q80):
tiles of ~300 px with a label strip (scene id, time, template) — one sheet holds 8-16 frames. A few
sheets per review, not dozens of frames. For many reviews of the same photos, upload once via the Files
API (`POST /v1/files`) and reference `{"type":"file","file_id":...}`.

### 5.5 Token counting and cost tracking
- Pre-flight estimate: `POST /v1/messages/count_tokens` with the same body (minus `max_tokens`).
- Every response has `usage`: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`. Log each call to a `usage` table (sqlx + SQLite/Postgres):
  `(id, user_id, job_id, kind[plan|revise|review|style], model, effort, input, output, cache_write,
  cache_read, cost_usd, duration_ms, cached_plan bool, created_at)`.
- Cost = Σ tokens × model price (keep a price table in config; cache writes ≈ 1.25× input for 5-min TTL,
  2× for 1h; cache reads per the price table). Show per-edit cost, per-user totals, 30-day chart.
- Enforce per-user budgets (daily $ or tokens) before calling the API.

### 5.6 Rust skeleton
```rust
// Cargo: axum, tokio, reqwest (json, stream, rustls-tls), serde, serde_json, sqlx, thiserror, tracing,
//        sha2, base64, image, ab_glyph, tokio-util; schemars (optional, schema from types)

pub struct Claude { http: reqwest::Client, key: String, model: String }

impl Claude {
    pub async fn structured<T: DeserializeOwned>(
        &self, system: Vec<Value>, content: Vec<Value>, schema: &Value, effort: &str, max_tokens: u32,
    ) -> Result<(T, Usage), AiError> {
        let body = json!({
            "model": self.model, "max_tokens": max_tokens,
            "output_config": { "effort": effort, "format": { "type": "json_schema", "schema": schema } },
            "system": system,
            "messages": [{ "role": "user", "content": content }],
        });
        let resp = self.post_with_retry("/v1/messages", &body).await?;      // backoff on 429/5xx/529
        let msg: ApiMessage = resp.json().await?;
        match msg.stop_reason.as_deref() {
            Some("end_turn") => {}
            Some("refusal") => return Err(AiError::Refused(msg.stop_details)),
            Some("max_tokens") => return Err(AiError::Truncated),
            other => return Err(AiError::Unexpected(other.map(str::to_owned))),
        }
        let text = msg.content.iter().find_map(|b| b.text.as_deref()).ok_or(AiError::Empty)?;
        Ok((serde_json::from_str(text)?, msg.usage))
    }
}
```

---

## 6. Data model (editable project)

Keep one versioned project schema shared by backend and editor (`serde` structs in Rust, generate
TypeScript types with `ts-rs`/`specta` for the web editor). Shape that worked:

- **clips** (main track, contiguous, no gaps): `{source_id, in_us, out_us, transition?}`.
- **images** (overlay layers): photo/cut-out/rendered-still/video overlay
  `{source_id, start_us, end_us, keys: [MotionKey], frame?: PhotoFrame, cutout?: {mode:"people"},
  stretch_x?, muted}`.
- **titles**: `{text, font, size, color, stroke, plate?, start_us, end_us, entrance:
  typewriter|word_reveal|slam|count_up, counter?: {from,to,prefix,suffix}, keys: [MotionKey]}`.
- **music/audio**: voice, SFX bed, music (with ducking), max N items.
- **MotionKey**: `{time_us, scale, x, y, rotation, opacity, blur, mask_x, mask_y, easing:
  linear|ease_in|ease_out|ease_in_out|back_in|back_out|back_in_out|hold}`.
- **point effects**: `{time_us, kind: flash|shake, strength}`.
- **transitions**: zoom_fade, flash_white, whip, push — with durations in ms.
- Limits (enforce in the builder, validate on export): concurrent image+text layers ≤ 8, total images
  ≤ 32, titles ≤ 200, rotation within ±360°, title size within the editor's range.

Plan schema (model output) is separate from the project schema:
```
Plan { story: String, assets_needed: [{slot, description}], scenes: [Scene] }
Scene { id, template, mode: overlay|fullframe, start_s, end_s, reason, data: {...}, needs_input?: [field] }
```
Code turns `Plan` → `Project`. Templates the editor can build natively ("moments": headline,
text-punch, stat-count, photo-full, photo-behind, photo-frames/duel, photo-overlay, person-cutout,
split-screen, news-cards) become layers + keyframes; rendered templates (compare-bars, timeline,
stat-hero, versus, kinetic-statement, diagram/map) become video overlay clips.

---

## 7. Rendering animated scenes

- Templates: `templates/<name>/template.html` + declared variables + shared CSS/fonts/helpers.
  Variables filled per ratio (`W`, `H`, orientation, duration).
- **Unset template variables must render empty**, never the template's demo defaults (demo text leaked
  into real renders once).
- Each template has an SFX cue list (time offsets per entrance) mixed into its render or exported as
  audio items.
- Lint templates before render; snapshot frames at given times for tests and for the review contact sheet.
- Background removal (person cut-outs): local model (e.g. rembg/U²-Net/BiRefNet via ONNX `ort`), cached
  per photo hash. Static decor (props, backdrops, text plates) → pre-rendered PNG stills, cached.
- Run renders in a worker pool (tokio + bounded semaphore; Chromium is memory-hungry). Job queue with
  status (`queued → planning → rendering → reviewing → ready | failed`) persisted in the DB; push progress
  to the browser via SSE/WebSocket.

---

## 8. Export / encoder pitfalls (cost us days — read twice)

These came from Media3 on Android but the root causes apply to any encoder/muxer pipeline (ffmpeg,
WebCodecs, GStreamer):

1. **Still images must get their real used duration** (min ~5 s), not a huge default. A still given 600 s
   made the encoder generate 600 s of silent audio; export stalled at ~83% with "no output sample written
   in the last 10000 ms". Use the clip's out-point as the still's duration.
2. **Audio must cover the full video length.** Pad voice/mix with silence to the video stream duration.
3. **Muted overlay videos: strip their audio track** (or use a silent copy). Extra audio streams for every
   overlay overload the mixer.
4. **Overlay count limits.** A compositor may cap simultaneous overlays (Media3: 15). Pack titles into
   lanes and pack non-overlapping overlay layers into shared tracks (interval-graph lane packing), and
   turn static decor into pre-rendered stills.
5. Duration = video stream duration; container duration lies.
6. Portrait recordings exported to 16:9: letterbox the face shots (cover=false) rather than crop the face.
7. Avoid full-frame dark linear gradients (banding); add grain.
8. Atomic file writes: write temp → rename; on Windows retry rename on PermissionError (AV/indexer locks).

---

## 9. Lessons from real user feedback (generalised)

- Users dislike: dark text on orange, outlined text on coloured plates, blurred self-shot backgrounds,
  plain dark boxes with text, thin fonts, text wrapping onto surprise lines, gaps under cut-outs, two
  photos piled on each other, the same person on both sides of a comparison, a visual appearing 2 s before
  the subject is named, demo text leaking, snapping motion.
- Users love: related pictures under every text moment, cut-outs rising from the bottom, duel layouts,
  smooth eased transitions with sound, bold fonts, a concrete hook.
- Wrong photos happen (a screenshot of a different person uploaded into a slot). The photo sheet in
  self-review catches it: judge context (kit, captions, text in image), never identify faces.
- The biggest cost is **revision rounds**, not tokens. Lessons file + revise mode + self-review +
  style files cut rounds more than any prompt tweak.

---

## 10. Suggested Rust backend layout

```
crates/
  api/        axum routes, auth (bearer token / session), SSE progress, multipart upload
  core/       Plan + Project types (serde), schema generation, validation, limits
  media/      ffprobe/ffmpeg wrappers (tokio::process), normalize, loudnorm, pad audio, to_png, tighten
  transcribe/ whisper bindings or STT client, SRT read/write, caption remap
  ai/         Claude client (reqwest), prompts loader, cache key, structured(), vision(), usage logging
  planner/    build_input, plan_job (cache → call → parse → filter templates → align → normalize), revise
  align/      fuzzy mention matching, conflict resolution, snapping to captions
  layout/     font measurement (ab_glyph), fit_size/lines, face detection, cut-out anchoring, palettes
  builder/    Plan → Project layers/keyframes, transitions, jump-zooms, lane packing, SFX placement
  render/     template filling, headless render workers, render cache, background removal
  review/     preview frames, contact sheets (image crate), review call, fix_notes, one fix round
  store/      sqlx (jobs, assets library, usage, lessons per user), object storage for media
prompts/      system.md, review.md, styles/*.md          (per-user lessons in DB or data/users/<id>/)
templates/    <name>/template.html, _shared/ (fonts, css, helpers), cues per template
```

Routes (mirroring what worked):
```
POST /jobs                       upload video + captions? + script + ratios + style + tighten
POST /jobs/:id/plan              {notes, fresh, revise=true, remember, style}
PATCH /jobs/:id/scenes/:sid      user edits a scene (no tokens)
POST /jobs/:id/assets/:slot      upload photo for a slot (any format) → library
POST /jobs/:id/render            render + build project
POST /jobs/:id/review            {fix: bool} → review, optional one fix round
GET  /jobs/:id/export?ratio=     project bundle
GET/POST /lessons                 user's standing preferences
GET  /styles                     available styles
GET  /usage                      per-user usage + cost dashboard data
```

Caching layers (all keyed by SHA-256 of their inputs): plan cache, render cache, cut-out cache,
SFX cache, rendered-stills cache. Re-download/re-import never calls the model.

Tests: schema round-trip, plan normalisation (overlaps, clamps, unknown templates), mention alignment
on fixture captions, text fitting at boundary widths, lane packing, still-duration rule, audio-length rule,
export validator on a golden project, and a mocked Claude client (fixture JSON) for planner/review flows.

---

## 11. Checklist before you call an edit "done"

- [ ] Plan has a `story`; every scene's subject is named in the captions at or before its start.
- [ ] No invented numbers; all on-screen text within limits; no unexpected wraps (measured).
- [ ] Every text/number moment has a related picture; comparisons are duels with different people.
- [ ] No text over the face; caption zone clear; cut-out flush bottom-left.
- [ ] No SFX in first 3.5 s; every entrance has a sound; audio covers full video length.
- [ ] Stills have real durations; overlay counts within compositor limits; export validates.
- [ ] Self-review ran on rendered frames + photos sheet; listed defects fixed in one revise round.
- [ ] Usage logged with cost; plan/render caches hit on repeat.
- [ ] User corrections from this round saved to lessons if they said "remember".
---

## 12. Additions from the SlideOn editor (built after this doc; adopt them)

These came from shipping the same kind of edits (the Telugu cricket creator, plus a short-form explainer
reference the owner chose as "how I would edit it"). Everything above still holds; this section adds what
the pipeline above does not have yet. Numbers are the values that worked.

### 12.1 Pick an editing style before planning
The model first returns the genre and the *purpose* (inform, teach, persuade, entertain, celebrate). Code maps
them to a style, and only that style's rules go into the prompt:
- **informative**: sports, history, and any talking head whose purpose is to inform, teach or persuade.
  It gets everything in 12.2 to 12.7.
- **conversational** (podcast, interview): mostly faces, the occasional reference picture, few sounds.
- **vlog**: action B-roll, quicker cuts, playful text, transitions only at real changes of place or time.
- **tutorial**: zoom on the control being used, numbered steps, still and readable screenshots, no
  decorative sound.
- **story** (photo stories, celebrations, montages): their own playbook.

Restraint ("do less") is the rule for the calmer styles only. Telling an informative edit to be restrained
produced plain edits the owner rejected.

### 12.2 The split screen is the default layout for informative edits
- Most pictures and graphics show as a **split screen**: the picture or panel in one half (the top in 9:16,
  one side in 16:9) and the speaker in the other half. Consecutive panels keep the speaker in place while the
  panel changes on the next word. Go back to the full speaker between ideas and for opinions.
- The speaker half is a **copy of the main clip at the same media time** (in-point = the media time at the
  split's start, same speed, muted). It is cropped around the detected face (centre plus 6% downward, so the
  chin and shoulders fit), and back-to-back copies merge into one layer. If the footage's shape differs from
  the half by more than 1.25x, show the whole frame contained in the half rather than a tight crop.
- No titles over a split; the frame is full.
- Two pictures in the same half at the same time is a defect: send the plan back.

### 12.3 Captions: one word at a time, on the split line
- Add captions to informative edits by default: **one word at a time**, boxed preset, built from the
  transcript's word times.
- **Height**: during a top-and-bottom split, a caption sits **on the line between the halves (y = 0.5)**.
  Otherwise it sits low. Decide this per caption, by whether a split is on screen when it starts.
- **Grouping** (when showing more than one word): break at a pause over 0.6 s, at a sentence end, and at
  **every edge of a range the plan's own cut removes**. Each caption then lies inside one kept piece, so the
  cut keeps or drops it whole and its word times stay right. A caption runs on to the next one across gaps
  under 0.5 s, and otherwise ends 0.25 s after its last word.
- Titles treat the caption band as busy: never place a title over it while captions show.
- The model only asks for captions (`{"op":"captions","words":1,"preset":"boxed"}`); code fills in the items.
  Do not send the filled items back to the model in repair or revision prompts: thousands of tokens, and the
  model starts copying them.

### 12.4 Generated motion graphics, kept as reusable clips
- An **explainer kit** of data-only specs, never code from the model: `stat` (count-up), `bars`, `checklist`,
  `cards` (icon cards), `chapter`, `headline` (with a pill), `note` (a caveat).
- Each spec says its frame (`panel` = half the screen, shown as a split with the speaker; `full` = the whole
  screen) and its theme (studio, the default; night; paper). Copy only numbers and words that were spoken.
- Every element carries `at` = the **word cue** it lands on, so items appear as they are named. The cue becomes
  an offset inside the clip.
- Rendered once with HyperFrames and GSAP (5 to 7 s per graphic on a laptop CPU), **cached by a hash of the spec**, and saved
  to the project's media library as a normal video clip ("Graphic stat ..."). The user can reuse, move or
  delete it like any upload.
- Graphics are rendered **before planning**. The planner gets each one as a media id with its window and
  placement, and must use it on its window with its rendered length intact.
- Budget per style: informative allows about one graphic per 4 s of video (at most 14, 80% of the runtime);
  other styles allow 0 to 2. Never the same kind twice in a row. A calculation said aloud becomes a checklist
  of its steps as spoken.
- If a graphic fails to render, the edit continues without it and says so.

### 12.5 A deterministic finishing pass after planning (code, no tokens)
Run these after the model's plan passes the checks, in this order:
1. **Stills move**: a still photo with no motion gets one calm slow push (0.35); a document or scorecard
   gets a gentler 0.3. No automatic punch-ins or zooms on the speaker: the owner called them unnecessary.
2. **Entrance accents**: a quiet `click` (volume 0.3) on each picture or title entrance, at least 1.2 s apart.
   A typewriter title gets `typing` instead, at most 3 per video. No whoosh; nothing in the first 3.5 s.
   A sound tied to a photo the user has not supplied goes away if the photo is skipped.
3. **Close gaps**: when two pictures in the finished edit are less than 0.5 s apart, the earlier one holds
   (a video holds its last frame) until the next begins. A few frames of the plain shot between two panels
   read as a flicker.
4. **Panel dissolves**: a graphic or split picture that follows another picture fades in over it in 0.18 s,
   with the earlier one held under it for the dissolve. One that starts from the plain speaker fades in
   over 0.15 s. Leaving a split is a hard cut. Full photos and entrances the model chose itself stay as they
   are. (This matched the reference edit frame by frame; slides and wipes across the split line break the
   layout.)
5. **Pairs**: when two examples fill complementary halves, bring the second in 0.3 s after the first, so the
   speaker never shows in the empty half.

### 12.6 Checks that send the plan back (one repair round each, then salvage)
- **Rhythm**: in an informative edit, any stretch of the finished edit over 6.5 s with no picture (8 s for
  the closing line) goes back once, with what is said in that stretch. Titles do not count as pictures.
- **Coverage**: every picture moment the director planned must appear on its word cue, within 0.25 s.
- **Do not let your own passes fight your checks.** Our pair alignment moved a photo off its word, the
  coverage check then demanded it back, and the planner looped until the job failed. Whenever code moves
  something, the checks must accept that move.
- **Salvage instead of failing**: after the last repair, drop the ops the remaining issues name. Take the later
  of two pictures sharing a place, and the last graphic over the budget. Then check again. Never drop more
  than a third of the plan, and never all of it. A plan that fails only quality asks (rhythm, director
  moments) ships as it is, with a note.
- Log each rejected issue with any quoted user text replaced by `"..."`.

### 12.7 Layout details that mattered
- **Title search**: try every 1% of the height, nearest the chest band (0.72) first. Shrink to a floor of 4%
  of the width, try the original, one-line and balanced two-line forms, and measure Indic vowel signs as
  zero width.
- A contained (full_fit) photo protects only its picture area. The blurred surround is free for titles.
  Over a photo that has not been supplied yet, a title may use the top band (to y = 0.215) only on a backing
  plate.
- An overlay can crop to one panel of a multi-panel image, and carry a border `{width 6, white or accent}`
  so it looks placed rather than pasted.

### 12.8 Photo requests the user can skip
- A request (a slot) names exactly what was said and is shown to the user in the review. The user fills it,
  picks a library photo or skips it.
- Ops that only make sense with that photo carry `needs: <slot>` and disappear when it is skipped, and the
  edit still works without it. Related ops share a group, so the user ticks or unticks a whole moment, and
  the applied edit is one undo.

### 12.9 Transcription for Indian languages
- Whisper loses long Telugu stretches. ElevenLabs Scribe v2 (through OpenRouter) returned full clips with
  word times. Plan from the **original-language word cues** (`w` ids); a translation is only for text, never
  for timing.
- Find audible gaps (sound but no words) and transcribe them again. Mark low-confidence words with `?` in the
  planner input, and never put a claim-specific picture or a number over speech the transcript missed.

### 12.10 Cost, pausing and resuming
- Record the provider's reported charge for every call (OpenRouter `usage.cost`) and show the rupee cost beside
  the video name. Saved AI edits can be reopened, applied later or revised.
- **Out of provider credits (HTTP 402)**: do not fail the job. Hand it back to the queue with a not-before time
  (60 s, doubling to 16 min, giving up 3 days after it was queued), without spending an attempt. Show
  "Paused: the AI provider is out of credits...".
- **Journal every paid answer** with the job (step plus a SHA-256 of the whole request), and read it back on
  resume. A paused or restarted job then pays only for calls it has not made. Delete the journal when the job
  ends.

### 12.11 Frame-accurate export (ffmpeg)
Measured with a source whose every frame encodes its own number in its brightness:
- **Clips on one frame grid**: clip i produces output frames `ceil(start*fps)` up to `ceil(end*fps)`
  (`trim=end_frame=N`). Trimming each clip to its own fractional length lost or doubled a frame at every cut,
  and the picture drifted from the sound. Put transition offsets and overlaps on the same grid.
- **Overlays on whole frames**: start each overlay on the first frame at or after its time, placed with
  `setpts=PTS-STARTPTS+K/(fps*TB)`. A time like `8.133333/TB` rounded onto the frame before. Move a video
  overlay on by the remaining fraction of a frame.
- **Exact reads**: read each picture from 2 frames before its in-point to 2 after its out-point, set its clock
  to 0 at the in-point itself (not at its first decoded frame: `PTS-STARTPTS` after a trim shifts by up to a
  frame), and use `fps=fps=N:start_time=0`. Trim the sound from the in-point exactly. Only then does a speaker
  copy show the same frame as the main clip.
- Keep clips on `settb=AVTB`: `concat` outputs that clock, and `xfade` needs both inputs on the same one.
- A video overlay longer than its file holds its last frame (`tpad=stop_mode=clone`); with
  `eof_action=pass` it would vanish early.
- Test it: render cuts of fractional length, splits that start between frames and two transitions, then
  read every output frame back. Expect no drops or doubles inside a piece, and every frame within one frame of
  where the timeline says it comes from.

### 12.12 Preview player
- Mount overlays hidden and paused on their first frame **2.5 s before they start**. A graphic mounted only
  at its start shows nothing while it loads, and the layer under it shows through: for a split, the
  speaker's face twice.
- When a video finishes loading while paused, move it to the current frame; nothing else will.

### 12.13 Checklist additions
- [ ] Style chosen from genre and purpose; only that style's rules were in the prompt.
- [ ] Informative: split screens with a synced speaker copy, one-word captions on the split line.
- [ ] Graphics are data-only specs on word cues, cached, saved as library clips, used with their full length.
- [ ] Finishing pass ran (still motion, click accents, gaps under 0.5 s closed, panel dissolves).
- [ ] No check can reject something your own code moved; salvage ran before any failure.
- [ ] Export frame test passes: every frame accounted for, speaker copy in sync.
