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
