# Cricket motion graphics for CutAgent (HyperFrames + Claude Code)

Your PC is the brain, your phone is the remote. In the CutAgent editor, **AI graphics** sends the talking
video and its captions to this server. Claude Code (your Pro plan) plans graphic scenes using only numbers
you said. You review them on the phone and add player photos. HyperFrames renders the scenes from
templates (no tokens). The phone imports a ready project for each ratio. Your voice runs underneath, graphics
cut in at the planned moments, and captions come across as an editable caption group.

```
Phone (CutAgent) ──HTTPS (tailscale serve)──► server/ (FastAPI, localhost:8787)
                                                ├─ planner/   claude -p (Sonnet, no tools) → plan JSON
                                                ├─ templates/ HyperFrames templates → MP4 per scene/ratio
                                                ├─ validator/ CutAgent's own Kotlin Validator on every export
                                                └─ data/      jobs, photo library, usage.db (token log)
```

## Setup (once)

Needs Node 22+, FFmpeg, uv, JDK 17, Claude Code logged in to your Pro account, and Tailscale on the PC and phone.

```powershell
.\scripts\start-server.ps1          # builds the validator on first run, creates data\api-token.txt
tailscale serve --bg 8787           # in another terminal, once; prints https://<pc>.<tailnet>.ts.net
```

On the phone: open a project → **AI graphics** → settings. Enter the `https://…ts.net` address and the token from
`data\api-token.txt`.

## Using it

1. Put your talking video on the main track and run **Captions** first, because the plan is built from them.
2. **AI graphics → Send to PC & plan**. An optional script helps Claude's planning.
3. Review the scenes. Switch any off, or tap one to fix its numbers. Saving costs no tokens.
4. Pick photos for the requested slots. They are saved to `data\library\` and reused automatically next time.
5. **Render graphics**, then **Import 9:16 / 16:9 project**. The new project opens in the editor.

Scenes that need a missing photo or template keep your face video. Nothing breaks.

## Planners: Claude or ChatGPT

The app's **Plan with Claude / ChatGPT** switch picks who plans. Both run on your own subscriptions through
their CLIs, with the same system prompt and plan schema:

- **Claude**: `claude -p` (Claude Code, Pro plan), model `HF_PLANNER_MODEL` (default `sonnet`).
- **ChatGPT**: `codex exec` (Codex CLI logged in with ChatGPT), model `HF_CODEX_MODEL` (default `gpt-6-sol`;
  any slug your login lists in `~\.codex\models_cache.json`). Codex adds ~16k tokens of its own instructions.

When one plan's session limit is used up, switch and re-plan. Settings live in `.env` (copy `.env.example`).

## Tokens and the dashboard

Only planning and re-planning call a model. Every call is logged in `data\usage.db` with provider, tokens
and (for Claude) API-equivalent cost. Open **`/dashboard`** on the server (`http://127.0.0.1:8787/dashboard`, or
the Tailscale address on the phone) and paste the API token once: it shows edits, tokens per edit, the
last 5 hours and 7 days per provider, a 30-day chart, and every edit. Anthropic does not publish Pro limits
in tokens, so enter one reading from Claude's usage screen and the dashboard estimates % per edit and edits
left from then on. Optional Firebase mirror: `uv sync --project server --extra firebase` and set
`HF_FIREBASE_CREDENTIALS` in `.env`.

## Templates

`templates/<name>/template.html` with variables declared in `data-composition-variables`. `__W__`, `__H__`,
`__ORIENT__` and `__DUR__` are filled per ratio. Shared fonts, CSS and helpers live in `templates/_shared/`
(`HF.backdrop` photo background with slow zoom, grain, vignette, light sweep/leak, flash, shake, `HF.cutout`).
The bottom 25% of 9:16 and 20% of 16:9 are kept clear for captions.

When a scene has a `photo`, the server also makes a **player cut-out** (local background removal, cached in
`data\cutouts\`) for templates that declare `cutout`. Missing photos render with a plain animated background.

| Template | Fields | Look |
| --- | --- | --- |
| innings-compare | player, tagline, left/right label, runs, balls, photo | cut-out + count-up cards |
| player-spotlight | player, headline, stat_lines[≤3], photo | big cut-out, name slam + shake |
| bowling-figures | player, overs, runs, wickets, extras_note, photo | cut-out, figure count-up |
| team-total | team, runs, wickets, overs, top_scorers[≤3], photo | total count-up, scorer bars |
| chase-meter | team, target, note, photo | target slam, meter |
| milestone | player, value, label, photo | poster cut-out behind the number, rays |
| kinetic-text | text, style (typewriter / slam / countup), photo | typing with key sounds, slams, counters |
| quote-card | text, attribution, photo | word-by-word reveal |
| photo-reveal | title, caption, photo | camera flash + shutter, cut-out parallax, name bar |
| photo-stack | title, labels[≤3], photos[≤3] | polaroids drop in with impacts |
| photo-behind | photo, caption | **in the app**: you shrink to the side (person cut-out + keyframes), photo fills behind |
| photo-overlay | photo, caption | **in the app**: photo card zooms in over your face with a shutter sound |

**Sound effects** are generated locally (`server/hfpipe/sfx.py`, royalty-free) into `data\sfx\`. Each template's
cues live in `server/hfpipe/cues.py` and are mixed into its render. App-built moments get them as audio items.

Preview a template without the server:

```powershell
uv run --project server python scripts/preview_template.py player-spotlight 9:16 vars.json --photo p.png --cutout p_cut.png --at 1,3
uv run --project server python scripts/preview_template.py kinetic-text 16:9 --render
```

Review a whole edit on the PC (approximate: no cut-out, captions or transitions):

```powershell
uv run --project server python scripts/rerun_job.py <job_id> [--stand-in photo.png]   # fresh plan + render
uv run --project server python scripts/preview_edit.py data\jobs\<job_id>\exports\<job_id>-portrait.zip
```

To add a template, write `template.html`, add its cues to `cues.py` and its fields to `planner/system.md`.

## Caching (no wasted tokens or renders)

- **Plans**: identical captions + script + notes + library reuse `data\plan-cache\` with no Claude call. *Re-plan* in
  the app always asks Claude again.
- **Renders**: same template + data + photos + sounds reuse `data
ender-cache\`.
- **Cut-outs**: one per photo, `data\cutouts\`.
- Downloading or re-importing a bundle never calls Claude.

## Checks

```powershell
cd server; uv run pytest -q                                   # 23 tests incl. the app validator
cd ..\..\cut-agent; .\gradlew.bat :app:testDebugUnitTest      # app tests incl. MotionBundleTest / MotionImportTest
```

## Notes

- Windows: `HYPERFRAMES_SKIP_SKILLS=1` is set by the server. Without it, `hyperframes init` hangs on a GitHub check.
- Bundles are zips holding `manifest.json`, `project.json` (cutagent-native v14), `sources.json` and `media/`. The
  phone copies the media into its managed sources, verifies SHA-256 and remaps source ids.
- Landscape exports from a portrait recording letterbox the face shots (`cover=false`).
- Uploads are converted once to an upright, 30 fps, 1 s-keyframe copy (`upright.mp4`) before planning: phones store
  portrait video sideways with a rotation flag, which shifted the first clip in the app; dense keyframes keep preview smooth.

## Voice videos (no camera)

For explainers where you record only your voice. **Home → Voice video** in the app:

1. Pick the recording. The app makes a project and generates captions with your caption service; tap **Add captions**.
2. The AI screen opens. Add a script if you have one (it fixes names and spellings), choose 16:9 or 9:16, send to the PC.
3. Claude plans the beats, picks colours for the script and lists the images it needs. Add them, or skip one: a
   screen without its image becomes an animated scene.
4. **Make the video**: Claude writes each scene, the PC renders it, Claude looks at frames of the result and fixes what
   it finds once. About a minute per scene. Then import: scenes, voice, captions, sounds and music are all editable.

Claude plans and writes through Claude Code on your plan; ChatGPT is not used for this mode (model: `HF_VOICE_MODEL`,
default `HF_PLANNER_MODEL`). Every call shows on the dashboard. Measured on Sonnet with a Pro plan: a 44 s video took 4% of the 5-hour limit (about $0.43 API-equivalent), a 1:49 video about $0.92.

- Screenshots and photos get a camera move with a ring and callouts. They can be messy: Claude crops tabs and taskbars.
- Colour themes are in `voice.THEMES`. Sounds are clicks, ticks and pops (`voice.SOUNDS`), no whooshes.
- Briefs: `planner/voice/` (`system.md` plan, `author.md` + `example.md` scenes, `screen.md` camera, `lessons.md` your
  standing preferences; **Remember this** adds to it). Scene kit: `templates/_voice/`. Scenes are saved in
  `data/jobs/<id>/voice/`, and only those whose brief, timing, words, theme or images changed are rewritten.
- The text limit of 200 captions fits about 7 minutes of speech.
- Test without the phone: `uv run --project server python scripts/voice_job.py plan voice.m4a captions.srt --script s.txt --id demo`,
  then `images demo slot=file ...`, then `produce demo`.

## Plan limits per edit

Each job step reads the real 5-hour and weekly usage from Claude Code before and after it runs (one tiny Haiku call each
way). The app shows what the edit took, and `/dashboard` shows the current levels and the figure per edit. Readings come in
whole percent, and anything else on the same Claude account during the job counts too.

## Phone preview and joins

- **One video stream in preview.** A second video stream freezes Media3's compositor at clip joins on a Galaxy A15: the sound and
  timecode run on over a stuck picture, and seeking does not recover it. So preview draws stills as overlays (`TimedPhoto`, which
  also plays keys, entrances, exits and rounded cards), and a silent moving cut-out shows as one still cut-out on the same keys
  (`CutoutStill`). Exports use the real layers, so the cut-out moves there. Video layers with sound still take a second stream.
- **Joins** (`export.py`): zoom punch into a graphic, flash zoom into a photo reveal, whips out of graphics and between them, and a
  glitch cut on every third graphic-to-graphic join. Jump zooms stay hard cuts, and so does the creator shrinking into a picture.
  The Punch pack is shader-only and runs half its length each side of the cut, so it adds no stream and moves no timing. New
  presets need the validator rebuilt: `cd validator; .\gradlew.bat installDist -q`.

## Getting edits right in fewer rounds

- **Lessons** (`planner/lessons.md`): the creator's standing preferences, read by every plan and review. Tick
  **Remember this for all future videos** on a re-plan note (or `POST /lessons`) to add one.
- **Update plan** revises the current plan (keeps what works) instead of planning from scratch.
- **Self-review** runs after every render on Claude plans, and **Review & fix on PC** runs it again on demand. The planner
  looks at one frame per scene of the PC preview (still frames, it does not watch the video) and at the uploaded photos, and
  lists concrete defects (overlaps, wrapped text, wrong-team photos, screenshots, empty frames). If it finds any it fixes
  exactly those in one revise + re-render pass and looks again. A look costs under 1% of the 5-hour limit, and a fix round
  about 2-3% more, because the plan is written out again.
- **Styles** (`planner/styles/*.md`): editing styles learned from reference videos, picked per job in the
  app. Make one with `uv run --project server python scripts/learn_style.py ref.mp4 --name my-style`.
- **Tighten pauses & retakes** (app checkbox): cuts long silences and restarted sentences before planning.
