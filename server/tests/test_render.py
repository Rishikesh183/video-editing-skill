import json

from hfpipe import config, render
from hfpipe.models import Job, MediaInfo, Plan, Scene


def test_declared_variables_and_coercion():
    html = """<html data-composition-variables='[
      {"id":"player","type":"string","default":"X"},
      {"id":"runs","type":"number","default":0},
      {"id":"lines","type":"string","default":"[]"}]'>"""
    declared = render.declared_variables(html)
    assert set(declared) == {"player", "runs", "lines"}
    out = render.coerce_variables(declared, {"player": "Naman", "runs": "42", "lines": ["a", "b"], "unknown": 1})
    assert out == {"player": "Naman", "runs": 42, "lines": json.dumps(["a", "b"])}


def test_every_template_declares_valid_variables():
    for name in render.available_templates():
        html = render.template_path(name).read_text(encoding="utf-8")
        declared = render.declared_variables(html)
        assert declared, f"{name} declares no variables"
        for marker in ("__W__", "__H__", "__ORIENT__", "__DUR__"):
            assert marker in html, f"{name} lacks {marker}"


def _job(scene: Scene) -> Job:
    video = MediaInfo(file="source.mp4", mime="video/mp4", duration_us=10_000_000, has_audio=True)
    return Job(id="abc123", video=video, plan=Plan(scenes=[scene]))


def test_render_skips_without_spending_work():
    missing = Scene(id="s1", start=0, end=3, mode="fullframe", template="does-not-exist")
    assert "not built" in render.render_scene(_job(missing), missing, "9:16").detail
    needs = Scene(id="s2", start=0, end=3, mode="fullframe", template="innings-compare", needs_input=["left_runs"])
    assert render.render_scene(_job(needs), needs, "9:16").detail.startswith("needs input")
    native = Scene(id="s3", start=0, end=3, mode="overlay", template="photo-overlay")
    assert render.render_scene(_job(native), native, "9:16").status == "skipped"


def test_photo_prefers_job_upload_over_library(data_dir):
    (config.LIBRARY).mkdir(parents=True)
    (config.LIBRARY / "sanju_samson.png").write_bytes(b"lib")
    scene = Scene(id="s1", start=0, end=3, mode="overlay", template="photo-overlay")
    job = _job(scene)
    assert render.photo_file(job, "sanju_samson") == config.LIBRARY / "sanju_samson.png"
    assert render.photo_file(job, "nobody") is None


def test_claude_cmd_shim_resolves_to_real_binary(tmp_path, monkeypatch):
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off")
    exe = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(config, "tool", lambda name: str(shim))
    assert config.claude_executable() == str(exe)


def test_sound_cues_follow_template_timing():
    from hfpipe.cues import cues
    typed = cues("kinetic-text", {"text": "AUCTION ON 15 NOV", "style": "typewriter"}, 5.0)
    keys = [c for c in typed if c[1].startswith("key")]
    assert len(keys) == len("AUCTIONON15NOV") and keys[0][0] == 0.3 and typed[-1][1] == "ding"
    slam = cues("kinetic-text", {"text": "ALMOST 300 OVERS", "style": "slam"}, 4.0)
    assert sum(1 for c in slam if c[1] == "impact") == 1
    assert cues("photo-reveal", {}, 4.0)[0][1] == "shutter"


def test_sfx_pack_and_finish(data_dir, tmp_path):
    from conftest import make_video
    from hfpipe import sfx
    lib = sfx.library()
    assert {p.stem for p in lib.glob("*.wav")} == set(sfx.SOUNDS)
    raw = make_video(tmp_path / "raw.mp4", 2, 320, 568, audio=False)
    out = tmp_path / "out.mp4"
    sfx.finish(raw, [(0.0, "whoosh", 0.5), (1.0, "impact", 0.8)], 2.0, out)
    from hfpipe.media import probe
    info = probe(out)
    assert info.has_audio and abs(info.duration_us - 2_000_000) < 100_000


def test_explainer_scene_cues():
    from hfpipe import cues

    gm = cues.cues("ground-map", {"fielders": '["slip","gully","point"]', "shot": "long-on", "shot_label": "SIX"}, 5)
    names = [c[1] for c in gm]
    assert names.count("pop") == 3 and "bat_crack" in names and "crowd_cheer" in names
    cb = cues.cues("compare-bars", {"items": '[{"label":"A","value":1},{"label":"B","value":2}]'}, 5)
    assert [c[1] for c in cb].count("swoosh_up") == 2 and cb[-1][1] == "impact"
    tl = cues.cues("timeline", {"points": '["2006: a","2024: b","2025: c"]'}, 5)
    assert [c[1] for c in tl].count("pop") == 3


def test_explainer_templates_declare_variables():
    for name, keys in (("ground-map", {"fielders", "shot", "highlight"}), ("compare-bars", {"items", "highlight"}),
                       ("timeline", {"points", "highlight"}), ("stat-hero", {"value", "facts", "cutouts"}),
                       ("versus", {"rows", "cutouts", "left", "right"}), ("kinetic-statement", {"text", "highlight", "cutouts"})):
        html = render.template_path(name).read_text(encoding="utf-8")
        assert keys <= set(render.declared_variables(html))
        assert 'window.__timelines["main"]' in html


def test_hero_scene_cues():
    from hfpipe import cues

    sh = cues.cues("stat-hero", {"facts": '["a","b"]', "cutouts": '["assets/c0.png"]'}, 5)
    assert [c[1] for c in sh].count("pop") == 2 and ("impact" in [c[1] for c in sh])
    vs = cues.cues("versus", {"rows": '[{"label":"x","left":1,"right":2}]'}, 5)
    assert "boom" in [c[1] for c in vs]
    ks = cues.cues("kinetic-statement", {"text": "a b c", "highlight": "c"}, 5)
    assert [c[1] for c in ks].count("tick") == 3 and ks[-1][1] == "impact"
