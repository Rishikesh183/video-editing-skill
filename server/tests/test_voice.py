import json
import subprocess
from pathlib import Path

import pytest
from conftest import make_png, make_video
from fastapi.testclient import TestClient

from hfpipe import api, config, export, media, pipeline, sfx, store, tighten, usage, voice
from hfpipe.models import AssetSlot, Cue, Job, MediaInfo, Plan, Scene


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def make_voice(path: Path, seconds: float = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([config.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
                    "-c:a", "aac", str(path)], check=True)
    return path


CUES = [Cue(start=0.5, end=2.5, text="Every claim has a story"), Cue(start=4.2, end=6.0, text="The NBS token factory"),
        Cue(start=8.1, end=11.0, text="Try it yourself")]


def beat(sid: str, start: float, end: float, kind: str = "story", images: list[str] | None = None) -> Scene:
    return Scene(id=sid, start=start, end=end, mode="fullframe", template=kind, data={"title": sid, "brief": f"brief {sid}", "images": images or []})


@pytest.mark.parametrize("name", list(voice.THEMES))
def test_themes_are_legible(name):
    t = voice.THEMES[name]
    assert contrast(t["ink"], t["bg"]) >= 7, "body text"
    assert contrast(t["muted"], t["bg"]) >= 4, "labels"
    assert contrast(t["accent"], t["bg"]) >= 3, "accent on the ground"
    assert contrast(t["accent-ink"], t["accent"]) >= 3.5, "text on an accent plate"


def test_no_rushing_air_sounds_can_be_chosen():
    assert not {"whoosh", "whip", "swoosh_up", "riser"} & set(voice.SOUNDS)
    assert set(voice.SOUNDS) <= set(sfx.SOUNDS)
    assert not {"whoosh", "whip", "swoosh_up"} & set(voice.STORY_SCHEMA["properties"]["sounds"]["items"]["properties"]["sound"]["enum"])


def test_click_sounds_generate():
    library = sfx.library()
    for name in ("click", "soft_click", "tap"):
        assert (library / f"{name}.wav").stat().st_size > 1000


def test_beats_fill_the_timeline():
    scenes = voice.tidy([beat("a", 0.3, 4), beat("b", 4.4, 8), beat("c", 8.3, 12)], CUES, 12.0)
    assert [s.start for s in scenes] == [0.0, pytest.approx(4.2, abs=0.04), pytest.approx(8.1, abs=0.04)]
    assert all(left.end == right.start for left, right in zip(scenes, scenes[1:]))
    assert scenes[-1].end == pytest.approx(12.8, abs=0.04)


def test_slivers_fold_into_the_previous_beat():
    scenes = voice.tidy([beat("a", 0, 5), beat("b", 5.2, 6), beat("c", 6.3, 12)], [], 12.0)
    assert [s.id for s in scenes] == ["a", "c"] and scenes[0].end == scenes[1].start


def test_beats_past_the_end_are_dropped():
    assert len(voice.tidy([beat("a", 0, 5), beat("b", 40, 45)], [], 10.0)) == 1


def test_switched_off_scene_leaves_no_gap():
    job = Job(id="j", video=MediaInfo(file="v", mime="audio/mp4", duration_us=12_000_000), mode="voice",
              plan=Plan(scenes=[beat("a", 0, 4), beat("b", 4, 8).model_copy(update={"approved": False}), beat("c", 8, 12)]))
    a, c = voice.timeline(job)
    assert a.end == 8 and c.start == 8 and c.end == 12


def test_transcription_fixes_are_whole_words():
    fixed = voice.apply_corrections(CUES, [{"wrong": "NBS", "right": "Nebius"}, {"wrong": "tor", "right": "x"}])
    assert fixed[1].text == "The Nebius token factory"
    assert voice.apply_corrections([Cue(start=0, end=1, text="the actor story")], [{"wrong": "tor", "right": "x"}])[0].text == "the actor story"


@pytest.mark.parametrize("bad", ["Math.random()", "setTimeout(f, 1)", "tl.call(() => {})", "gsap.to(a, {className: 'x'})", "new Date()"])
def test_lint_rejects_what_breaks_a_render(bad):
    assert voice._lint({"js": bad})


def test_lint_rejects_the_reserved_class():
    assert voice._lint({"html": '<div class="abs clip">', "js": "tl;"})


def test_lint_accepts_a_normal_scene():
    assert voice._lint(voice.fallback_doc(beat("a", 0, 4))) == ""


def test_camera_centres_the_point_and_hides_edges():
    place = {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0, "crop": [0.0, 0.0, 1.0, 1.0]}
    key = voice.camera(place, 1920, 1080, 0.7, 0.6, 2.0)
    assert voice._point(place, 1920, 1080, 0.7, 0.6, key) == pytest.approx((0.5, 0.5))
    corner = voice.camera(place, 1920, 1080, 0.0, 0.0, 2.0)
    assert (corner["x"], corner["y"]) == (1.0, 1.0)  # stops at the edge rather than showing past it


def test_camera_works_in_original_image_fractions_after_a_crop():
    place = {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0, "crop": [0.0, 0.1, 1.0, 0.9]}
    assert voice._point(place, 1920, 1080, 0.5, 0.5, {"scale": 1.0, "x": 0.5, "y": 0.5})[1] == pytest.approx(0.5)


def test_keys_always_cover_the_clip():
    place = {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0, "crop": [0.0, 0.0, 1.0, 1.0]}
    keys = voice.screen_keys({"moves": [{"at": 2, "x": 0.5, "y": 0.5, "zoom": 9}]}, place, 1920, 1080, 5_000_000)
    assert keys[0]["timeUs"] == 0 and keys[-1]["timeUs"] == 5_000_000
    assert keys[1]["scale"] == voice.MAX_ZOOM


def test_callouts_lose_symbols_the_font_cannot_draw():
    assert voice.clean_callout("ONE CLICK → THE FRAME") == "ONE CLICK THE FRAME"


def fake_claude(answers):
    calls = []

    def ask(job_id, step, system, prompt, schema, cwd=None, read_files=False):
        calls.append({"step": step, "prompt": prompt, "read_files": read_files, "files": sorted(p.name for p in cwd.iterdir()) if cwd else []})
        return json.loads(json.dumps(answers[step]))

    ask.calls = calls
    return ask


PLAN = {"story": "s", "theme": "ocean", "corrections": [{"wrong": "NBS", "right": "Nebius"}],
        "assets": [{"slot": "App Home", "description": "Screenshot of the home page"}, {"slot": "unused", "description": "not used"}],
        "beats": [{"id": "hook", "start": 0, "end": 4, "kind": "story", "title": "Hook", "brief": "b", "images": [], "reason": "r"},
                  {"id": "demo", "start": 4, "end": 8, "kind": "screen", "title": "Demo", "brief": "b", "images": ["app_home"], "reason": "r"},
                  {"id": "orphan", "start": 8, "end": 12, "kind": "screen", "title": "No image", "brief": "b", "images": ["nothing"], "reason": "r"}]}
STORY = {"css": ".t { left: 100px; top: 100px; font-size: 80px; }", "html": '<div class="abs t head" id="t"></div>',
         "js": 'KIT.reveal(tl, "#t", "Hello there", 0.2, -1);', "sounds": [{"at": 0.2, "sound": "click"}]}
SCREEN = {"crop": [0, 0.1, 1, 0.9], "moves": [{"at": 0, "x": 0.5, "y": 0.5, "zoom": 1}, {"at": 1.5, "x": 0.7, "y": 0.6, "zoom": 2}],
          "ring": {"show": True, "at": 2, "end": 3.5, "x": 0.7, "y": 0.6}, "callouts": [{"at": 2, "end": 3.5, "text": "LOOK → HERE", "y": 0.15}],
          "sounds": [{"at": 1.5, "sound": "click"}], "note": ""}
REVIEW = {"summary": "clean", "edit_issues": [], "photo_issues": []}


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def voice_job(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "submit", lambda fn, *a: None)
    jid = "voicejob"
    voice_path = make_voice(store.job_dir(jid) / "source.m4a")
    job = store.save(Job(id=jid, ratios=["16:9"], mode="voice", video=media.probe(voice_path), captions=CUES))
    return store.load(job.id)


def test_upload_makes_a_voice_job_on_claude(client, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "submit", lambda fn, *a: None)
    audio = make_voice(tmp_path / "up.m4a")
    srt = b"1\n00:00:00,500 --> 00:00:02,000\nHello\n"
    r = client.post("/jobs", files={"video": ("voice.m4a", audio.read_bytes(), "audio/mp4"), "captions": ("c.srt", srt, "text/plain")},
                    data={"ratios": "16:9", "mode": "voice", "provider": "codex"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "voice" and r.json()["provider"] == "claude" and r.json()["templates"] == ["story", "screen"]


def test_voice_job_takes_one_ratio(client, tmp_path):
    audio = make_voice(tmp_path / "up.m4a")
    r = client.post("/jobs", files={"video": ("voice.m4a", audio.read_bytes(), "audio/mp4"), "captions": ("c.srt", b"x", "text/plain")},
                    data={"ratios": "9:16,16:9", "mode": "voice"})
    assert r.status_code == 400


def test_a_talking_job_still_rejects_audio(client, tmp_path):
    audio = make_voice(tmp_path / "up.m4a")
    r = client.post("/jobs", files={"video": ("voice.m4a", audio.read_bytes(), "audio/mp4"), "captions": ("c.srt", b"x", "text/plain")},
                    data={"ratios": "16:9"})
    assert r.status_code == 400


def test_prepare_levels_the_voice_once(voice_job):
    job = voice.prepare(voice_job)
    assert job.video.file == "voice.m4a" and job.video.duration_us > 0 and job.video.mime.startswith("audio/")
    assert voice.prepare(job).video.file == "voice.m4a"


def test_plan_keeps_only_images_it_uses_and_downgrades_a_screen_without_one(voice_job, monkeypatch):
    monkeypatch.setattr(voice, "ask_claude", fake_claude({"voice plan": PLAN}))
    plan, captions = voice.plan_job(voice_job)
    assert plan.theme == "ocean" and [a.slot for a in plan.assets_needed] == ["app_home"]
    assert [s.template for s in plan.scenes] == ["story", "screen", "story"]
    assert captions[1].text == "The Nebius token factory"


def test_revision_keeps_uploaded_images(voice_job, monkeypatch):
    voice_job.plan = Plan(scenes=[beat("hook", 0, 4)], assets_needed=[AssetSlot(slot="app_home", description="d", file="assets/app_home.png")])
    monkeypatch.setattr(voice, "ask_claude", fake_claude({"voice plan": PLAN}))
    plan, _ = voice.plan_job(voice_job, "notes", True)
    assert plan.assets_needed[0].file == "assets/app_home.png"


def planned(job, monkeypatch, with_image=True):
    monkeypatch.setattr(voice, "ask_claude", fake_claude({"voice plan": PLAN}))
    job.plan, job.captions = voice.plan_job(job)
    if with_image:
        make_png(store.job_dir(job.id) / "assets" / "app_home.png", 1920, 1200)
        job.plan.assets_needed[0].file = "assets/app_home.png"
    return store.save(voice.prepare(store.save(job)))


def fake_renders(job):
    for scene in job.plan.scenes:
        if voice.kind_of(job, scene) == "story":
            make_video(store.job_dir(job.id) / "renders" / f"{scene.id}-landscape.mp4", scene.end - scene.start + 0.1, 1920, 1080, audio=False)


def test_scene_is_reused_until_its_brief_changes(voice_job, monkeypatch):
    job = planned(voice_job, monkeypatch)
    ask = fake_claude({"voice scene": STORY, "voice screen": SCREEN})
    monkeypatch.setattr(voice, "ask_claude", ask)
    scene = job.plan.scenes[0]
    voice.author(job, scene)
    voice.author(job, scene)
    assert len(ask.calls) == 1
    scene.data["brief"] = "something else"
    voice.author(job, scene)
    assert len(ask.calls) == 2


def test_screen_author_gets_the_image_to_read(voice_job, monkeypatch):
    job = planned(voice_job, monkeypatch)
    ask = fake_claude({"voice scene": STORY, "voice screen": SCREEN})
    monkeypatch.setattr(voice, "ask_claude", ask)
    voice.author(job, job.plan.scenes[1])
    assert ask.calls[0]["read_files"] and ask.calls[0]["files"] == ["app_home.png"]


def test_a_scene_that_keeps_failing_lint_becomes_a_plain_card(voice_job, monkeypatch):
    job = planned(voice_job, monkeypatch)
    monkeypatch.setattr(voice, "ask_claude", fake_claude({"voice scene": {**STORY, "js": "setTimeout(f)"}}))
    assert voice.author(job, job.plan.scenes[0]).get("fallback")


def test_fix_rewrites_only_on_feedback_and_includes_the_current_scene(voice_job, monkeypatch):
    job = planned(voice_job, monkeypatch)
    ask = fake_claude({"voice scene": STORY})
    monkeypatch.setattr(voice, "ask_claude", ask)
    voice.author(job, job.plan.scenes[0])
    voice.author(job, job.plan.scenes[0], feedback="text overlaps")
    assert "Fix these: text overlaps" in ask.calls[1]["prompt"] and "Current scene:" in ask.calls[1]["prompt"]


def test_exported_voice_project_passes_the_app_validator(voice_job, monkeypatch):
    job = planned(voice_job, monkeypatch)
    monkeypatch.setattr(voice, "ask_claude", fake_claude({"voice scene": STORY, "voice screen": SCREEN}))
    for scene in job.plan.scenes:
        voice.author(job, scene)
    fake_renders(job)
    rel, _ = export.export_bundle(job, "16:9")
    assert (store.job_dir(job.id) / rel).exists()
    project = json.loads((store.job_dir(job.id) / "exports" / "landscape" / "project.json").read_text(encoding="utf-8"))
    total = sum(c["outUs"] - c["inUs"] for c in project["clips"])
    assert total == round(job.plan.scenes[-1].end * 1e6)
    assert [m["id"] for m in project["music"]] == ["voice", "sfx", "music"]
    assert any(t.get("captionGroupId") for t in project["titles"]), "captions come across"
    say = next(t for t in project["titles"] if t["id"].startswith("say-"))
    assert say["text"] == "LOOK HERE" and say["color"] == voice.THEMES["ocean"]["accent-ink"]
    assert any(i["id"].startswith("ring-") for i in project["images"])
    assert next(c for c in project["clips"] if c["id"] == "v-demo")["transition"]["preset"].startswith("whip_")
    assert next(t for t in project["titles"] if t.get("captionGroupId"))["wordStyle"]["secondaryColor"] == voice.THEMES["ocean"]["accent"].upper()


def test_full_production_with_a_rendered_scene(voice_job, monkeypatch):
    """The real renderer on a real scene from the kit, to catch a broken wrapper, then review and export."""
    job = planned(voice_job, monkeypatch, with_image=True)
    monkeypatch.setattr(voice, "ask_claude", fake_claude({"voice scene": STORY, "voice screen": SCREEN, "voice review": REVIEW}))
    keep = job.plan.scenes[0]
    render_real = voice.render_story

    def render_first_only(job, scene, doc, last=False):
        if scene.id == keep.id:
            return render_real(job, scene, doc, last)
        fake_renders(job)
        return None

    monkeypatch.setattr(voice, "render_story", render_first_only)
    voice.produce(job.id)
    done = store.load(job.id)
    assert done.status == "rendered" and done.exports["16:9"] and done.review["summary"] == "clean"
    info = media.probe(store.job_dir(job.id) / "renders" / f"{keep.id}-landscape.mp4")
    assert info.duration_us >= (keep.end - keep.start) * 1e6 and info.width == 1920


def test_limit_reading_comes_from_claude_codes_own_report():
    stream = "\n".join([json.dumps({"type": "system"}), json.dumps({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
        "five_hour": {"utilization": 0.3, "resetsAt": 100}, "seven_day": {"utilization": 0.16, "resetsAt": 200}}}})])
    assert usage.parse_reading(stream) == {"session": 30.0, "week": 16.0, "session_resets": 100, "week_resets": 200}
    assert usage.parse_reading("not json\n") is None


def test_edit_reports_what_it_took_from_the_limits(monkeypatch):
    seq = iter([{"session": 30.0, "week": 16.0, "session_resets": 1, "week_resets": 2}, {"session": 34.5, "week": 17.0, "session_resets": 1, "week_resets": 2},
                {"session": 95.0, "week": 18.0, "session_resets": 1, "week_resets": 2}, {"session": 3.0, "week": 18.5, "session_resets": 9, "week_resets": 2}])
    monkeypatch.setattr(usage, "reading", lambda: next(seq))
    with usage.metered("j", "plan"):
        with usage.metered("j", "nested step is part of the outer one"):
            pass
    with usage.metered("j", "make"):
        pass
    limits = usage.job_limits("j")
    assert limits["session_used"] == 7.5  # 4.5, then 3 after the window reset
    assert limits["week_used"] == 1.5 and limits["session_now"] == 3.0 and limits["session_resets"] == 9
    assert usage.dashboard()["limits_now"]["session"] == 3.0
    assert usage.job_limits("other") is None


def test_limits_failing_to_read_never_break_a_job(monkeypatch):
    with usage.metered("j", "plan"):
        pass
    assert usage.job_limits("j") is None


def test_voice_calls_count_toward_the_dashboard():
    usage.record("j", "voice scene", {"usage": {"output_tokens": 10}, "total_cost_usd": 0.1})
    assert usage.dashboard()["session_5h"]["claude"]["calls"] == 1


def test_speech_with_no_captions_is_flagged(voice_job, monkeypatch):
    voice_job.video = voice_job.video.model_copy(update={"duration_us": 20_000_000})
    voice_job.captions = [Cue(start=0, end=5, text="said"), Cue(start=14, end=20, text="said")]
    monkeypatch.setattr(tighten, "silences", lambda path: [])
    assert voice.speech_gaps(voice_job) == [(5, 14)]
    assert "0:05-0:14" in voice.gap_note(voice_job)


def test_silence_without_captions_is_not_flagged(voice_job, monkeypatch):
    voice_job.video = voice_job.video.model_copy(update={"duration_us": 20_000_000})
    voice_job.captions = [Cue(start=0, end=5, text="said"), Cue(start=14, end=20, text="said")]
    monkeypatch.setattr(tighten, "silences", lambda path: [(5.2, 13.9)])
    assert voice.speech_gaps(voice_job) == []


def test_fewer_sounds_and_the_strongest_one_wins():
    cues = [(1.0, "tick", 0.5), (1.2, "impact", 0.8), (1.4, "pop", 0.4), (4.0, "pop", 0.4), (4.5, "tick", 0.5), (9.0, "whoosh", 0.6)]
    assert sfx.calm(cues, 1.8) == [(1.2, "impact", 0.8), (4.0, "pop", 0.4), (9.0, "soft_click", 0.42)]
