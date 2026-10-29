import json
import zipfile

import pytest

from conftest import make_png, make_video
from hfpipe import config, export, native, store
from hfpipe.media import probe
from hfpipe.models import AssetSlot, Cue, Job, Plan, RenderResult, Scene

needs_validator = pytest.mark.skipif(not config.VALIDATOR.exists(), reason="validator not built")


def _job_with_renders() -> Job:
    job_id = "exp1"
    jdir = store.job_dir(job_id)
    make_video(jdir / "source.mp4", 12, 478, 850)
    renders = []
    for ratio, (w, h), orient in (("9:16", (1080, 1920), "portrait"), ("16:9", (1920, 1080), "landscape")):
        for sid, secs in (("intro", 3), ("stats", 4)):
            rel = f"renders/{sid}-{orient}.mp4"
            make_video(jdir / rel, secs, w // 4, h // 4, audio=False)
            renders.append(RenderResult(scene_id=sid, ratio=ratio, file=rel, status="ok"))
    make_png(jdir / "assets" / "sanju_samson.png")
    plan = Plan(
        scenes=[
            Scene(id="intro", start=0, end=3, mode="fullframe", template="innings-compare"),
            Scene(id="stats", start=5, end=9, mode="fullframe", template="innings-compare"),
            Scene(id="potm", start=9.5, end=11.5, mode="overlay", template="photo-overlay",
                  data={"photo": "sanju_samson", "caption": "Sanju Samson"}),
            Scene(id="off", start=10, end=11, mode="fullframe", template="innings-compare", approved=False),
        ],
        assets_needed=[AssetSlot(slot="sanju_samson", description="photo", file="assets/sanju_samson.png")],
    )
    captions = [Cue(start=0, end=2, text="NAMAN DHIR"), Cue(start=2, end=4.5, text="15 BALLS 42 RUNS"), Cue(start=30, end=31, text="past the end")]
    job = Job(id=job_id, video=probe(jdir / "source.mp4"), plan=plan, renders=renders, captions=captions)
    return store.save(job)


def test_timeline_keeps_voice_in_sync():
    job = _job_with_renders()
    project, sources, files, warnings = export.build(job, "9:16")
    clips = project["clips"]
    assert [c["id"] for c in clips] == ["gfx-intro", "face-1", "gfx-stats", "face-3"]
    # Face clips read the source at the same time they occupy on the timeline.
    t = 0
    for c in clips:
        if c["sourceId"] == export.VIDEO_ID:
            assert c["inUs"] == t
        t += c["outUs"] - c["inUs"]
    assert t == job.video.duration_us
    assert project["music"][0] == {"id": "voice", "sourceId": "video-src", "inUs": 0, "startUs": 0, "endUs": t, "volume": 1.0}
    assert [m["sourceId"] for m in project["music"][1:]] == ["sfx-whoosh", "sfx-shutter"]  # photo slide-in sounds
    assert all(c["muted"] for c in clips if c["sourceId"] == "video-src")
    assert clips[0]["transition"]["rightId"] == "face-1" and "transition" not in clips[1]
    assert project["images"][0]["sourceId"] == "photo-sanju_samson"
    assert project["titles"][0]["text"] == "Sanju Samson"
    captions = [t for t in project["titles"] if t.get("captionGroupId") == "ai-captions"]
    assert [c["text"] for c in captions] == ["NAMAN DHIR", "15 BALLS 42 RUNS"]
    assert captions[1]["startUs"] == 2_000_000 and captions[1]["endUs"] == 4_500_000
    assert project["output"]["cover"] is True
    assert {s["id"] for s in sources} == {"video-src", "gfx-intro", "gfx-stats", "photo-sanju_samson", "sfx-whoosh", "sfx-shutter"}
    assert set(files) == {s["path"] for s in sources}
    assert warnings == []


def test_landscape_from_portrait_source_letterboxes():
    project, *_ = export.build(_job_with_renders(), "16:9")
    assert project["output"] == {"width": 1920, "height": 1080, "fps": 30, "cover": False, "ratio": "16:9", "background": "#000000"}


@needs_validator
@pytest.mark.parametrize("ratio", ["9:16", "16:9"])
def test_bundle_passes_app_validator(ratio):
    job = _job_with_renders()
    rel, _ = export.export_bundle(job, ratio)
    with zipfile.ZipFile(store.job_dir(job.id) / rel) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert {"manifest.json", "project.json", "sources.json"} <= names
    assert {m["path"] for m in manifest["media"]} <= names


@needs_validator
def test_validator_rejects_bad_project(tmp_path):
    (tmp_path / "p.json").write_text('{"format":"cutagent-native","version":14,"clips":[],"extra":1}')
    (tmp_path / "s.json").write_text("[]")
    verdict = export.validate(tmp_path / "p.json", tmp_path / "s.json")
    assert verdict["ok"] is False and "extra" in verdict["error"]


def test_photo_behind_shrinks_face_over_photo():
    job = _job_with_renders()
    job.plan.scenes.append(Scene(id="pb", start=9.5, end=12, mode="fullframe", template="photo-behind",
                                 data={"photo": "sanju_samson", "caption": "Sanju"}))
    job.plan.scenes = [s for s in job.plan.scenes if s.id != "potm"]
    project, sources, _, _ = export.build(store.save(job), "9:16")
    assert [c["id"] for c in project["clips"]] == ["gfx-intro", "face-1", "gfx-stats", "face-3", "bg-pb"]
    bg = project["clips"][-1]
    assert bg["sourceId"] == "photo-sanju_samson" and bg["outUs"] == 2_500_000
    me = next(i for i in project["images"] if i["id"] == "me-pb")
    assert me["sourceId"] == export.SILENT_ID and me["inUs"] == 9_500_000 and me["sourceOutUs"] == 12_000_000
    assert me["cutout"] == {"mode": "people"}
    scales = [k["scale"] for k in me["keys"]]
    assert scales[0] == scales[1] == 1.0 and scales[2] < 1 and scales[-1] == 1.0  # full size, shrink, back
    assert me["keys"][2]["timeUs"] - me["keys"][1]["timeUs"] >= 600_000  # smooth, not a snap
    assert bg["keys"][0]["x"] != bg["keys"][1]["x"] and bg["keys"][1]["scale"] > bg["keys"][0]["scale"]  # pan + zoom
    assert any(m["sourceId"] == "sfx-whoosh" for m in project["music"])


@needs_validator
def test_photo_behind_bundle_passes_app_validator():
    job = _job_with_renders()
    job.plan.scenes.append(Scene(id="pb", start=9.5, end=12, mode="fullframe", template="photo-behind",
                                 data={"photo": "sanju_samson"}))
    job.plan.scenes = [s for s in job.plan.scenes if s.id != "potm"]
    rel, _ = export.export_bundle(store.save(job), "9:16")
    assert rel.endswith(".zip")


def _all_native_job() -> Job:
    job = _job_with_renders()
    jdir = store.job_dir(job.id)
    for slot in ("axar_patel", "nithish_reddy"):
        make_png(jdir / "assets" / f"{slot}.png", 500, 400)
        job.plan.assets_needed.append(AssetSlot(slot=slot, description="p", file=f"assets/{slot}.png"))
    job.plan.scenes = [s for s in job.plan.scenes if s.id == "intro"] + [
        Scene(id="pf", start=3.0, end=5.0, mode="fullframe", template="photo-full", data={"photo": "sanju_samson", "caption": "Sanju"}),
        Scene(id="fr", start=9.0, end=10.5, mode="fullframe", template="photo-frames",
              data={"title": "Depth", "photos": ["sanju_samson", "axar_patel", "nithish_reddy"], "labels": ["S", "A", "N"]}),
        Scene(id="tp", start=10.5, end=12.0, mode="fullframe", template="text-punch", data={"text": "Auction on 15 Nov", "style": "typewriter", "bg": "face"}),
        Scene(id="ov", start=5.5, end=8.0, mode="overlay", template="photo-overlay", data={"photo": "axar_patel", "caption": "Axar"}),
    ]
    return store.save(job)


def test_native_moments_are_layers_not_renders():
    project, sources, _, warnings = export.build(_all_native_job(), "9:16")
    ids = [c["id"] for c in project["clips"]]
    assert ids == ["gfx-intro", "bg-pf", "face-2", "bg-fr", "bg-tp"], (ids, warnings)
    tp = next(c for c in project["clips"] if c["id"] == "bg-tp")
    assert "background" not in tp  # never a flat colour: own shot defocusing, a photo or the ground
    assert tp["sourceId"] == "video-src" and tp["inUs"] == 10_500_000 and tp["keys"][-1]["blur"] > 0
    typed = next(t for t in project["titles"] if t["id"] == "tp-tp")
    assert typed["entrance"]["preset"] == "typewriter" and typed["text"] == "Auction on 15 Nov"
    frames = [i for i in project["images"] if i["id"].startswith("fr-")]
    assert len(frames) == 3 and len({f["rotation"] for f in frames}) == 3
    assert all(f["frame"]["border"] > 0 and f["keys"][0]["opacity"] == 0 for f in frames)  # polaroids fade in
    ov = next(i for i in project["images"] if i["id"] == "ov-ov")
    assert ov["keys"][0]["x"] > 0.5 and ov["keys"][1]["x"] == 0.5  # slides in from the right
    pf = next(c for c in project["clips"] if c["id"] == "bg-pf")
    assert pf["keys"][0]["blur"] > 0 and pf["pointEffects"][0]["preset"] == "flash"  # focus pull + flash
    assert project["version"] == 15


def _text_job() -> Job:
    job = _job_with_renders()
    job.plan.scenes = [
        Scene(id="hook", start=0.0, end=2.0, mode="overlay", template="headline",
              data={"text": "Not only flat pitches", "highlight": "Depth", "style": "slam"}),
        Scene(id="st", start=4.0, end=7.0, mode="fullframe", template="stat-count",
              data={"value": "8", "prefix": "No. ", "label": "Batting depth", "bg": "field"}),
        Scene(id="sl", start=9.0, end=12.0, mode="fullframe", template="text-punch",
              data={"text": "2024", "highlight": "SRH effect", "style": "slam", "kicker": "Fact", "bg": "field"}),
        Scene(id="tb", start=7.2, end=9.0, mode="fullframe", template="text-punch",
              data={"text": "Auction", "highlight": "15 Nov"}),
    ]
    return store.save(job)


def test_text_moments_count_slam_and_punch_in():
    project, sources, _, warnings = export.build(_text_job(), "9:16")
    count = next(t for t in project["titles"] if t["id"] == "st-st")
    assert count["entrance"]["preset"] == "count_up" and count["counter"]["to"] == 8 and count["text"] == "No. 8"
    st = next(c for c in project["clips"] if c["id"] == "bg-st")
    assert st["sourceId"] == "decor-field_portrait"
    tb = [i["id"] for i in project["images"] if i["id"].endswith("-tb")]
    assert tb == ["tb-tb", "me-tb"], tb  # words behind, the creator's cut-out above them
    me = next(i for i in project["images"] if i["id"] == "me-tb")
    assert me["cutout"] == {"mode": "people"} and me["keys"][1]["scale"] < 1
    sl = next(c for c in project["clips"] if c["id"] == "bg-sl")
    assert {e["preset"] for e in sl["pointEffects"]} == {"flash", "shake"}
    assert any(i["id"] == "dc-mk-sl" for i in project["images"])  # marker stroke behind the highlight
    first = project["clips"][0]
    assert first["id"].startswith("face-") and first["outUs"] == 2_000_000 and first["keys"][0]["scale"] >= export.TIGHT_SCALE > 1.0
    # captions step aside under big text
    for t in project["titles"]:
        if t.get("captionGroupId"):
            assert not (t["startUs"] < 7_000_000 and 4_000_000 < t["endUs"]), t


def test_long_face_stretch_gets_jump_zooms():
    pieces = export._face_pieces(0, 12_000_000, [], [3_000_000, 6_000_000, 9_000_000, 11_000_000])
    assert [(a, b) for a, b, _ in pieces] == [(0, 3_000_000), (3_000_000, 6_000_000), (6_000_000, 9_000_000), (9_000_000, 12_000_000)]
    pieces = export._face_pieces(0, 5_000_000, [(1_000_000, 2_500_000)], [])
    assert [p[2] for p in pieces] == [None, True, None]


@needs_validator
def test_text_bundle_passes_app_validator():
    export.export_bundle(_text_job(), "9:16")
    export.export_bundle(_text_job(), "16:9")


@needs_validator
def test_native_bundle_passes_app_validator():
    rel, warnings = export.export_bundle(_all_native_job(), "9:16")
    with zipfile.ZipFile(store.job_dir("exp1") / rel) as z:
        project = json.loads(z.read("project.json"))
    # More sounds than the app's 8 audio items: they share one track instead of being dropped.
    assert len(project["music"]) <= 8
    assert any(m["id"] == "sfx-bed" for m in project["music"])


def _reference_job() -> Job:
    job = _job_with_renders()
    job.plan.scenes = [
        Scene(id="pc", start=0.5, end=3.0, mode="overlay", template="player-cutout", data={"players": ["sanju_samson"]}),
        Scene(id="sp", start=3.5, end=7.0, mode="fullframe", template="split",
              data={"photo": "sanju_samson", "callouts": ["Look at this screenshot", "22.3 overs left"]}),
        Scene(id="nc", start=7.5, end=11.5, mode="fullframe", template="news-cards",
              data={"cards": [{"photo": "sanju_samson", "tag": "Reports", "text": "Sanju could open in NZ"}]}),
    ]
    return store.save(job)


def test_reference_moments(monkeypatch):
    monkeypatch.setattr(export, "cutout_file", lambda photo: photo)  # skip background removal in tests
    project, sources, _, warnings = export.build(_reference_job(), "9:16")
    pc = next(i for i in project["images"] if i["id"] == "pc-pc-0")
    assert pc["sourceId"] == "cut-sanju_samson" and pc["keys"][0]["y"] > 0.5  # rises from below
    sp = next(c for c in project["clips"] if c["id"] == "bg-sp")
    assert sp["sourceId"] == "video-src" and sp["keys"][-1]["y"] > 0.5  # creator in the lower half
    assert {"dc-grid-sp", "sp-sp"} <= {i["id"] for i in project["images"]}
    pills = [t for t in project["titles"] if t["id"].startswith("pl-sp")]
    assert len(pills) == 2 and pills[-1]["decoration"]["background"] == "#E10600"
    assert any(t["id"] == "ng-nc-0" and t["font"] == "archivo_black" for t in project["titles"])
    face = next(c for c in project["clips"] if c.get("transition", {}).get("rightId") == "bg-nc")
    assert face["transition"]["preset"] == "flash_white"


@needs_validator
def test_reference_bundle_passes_app_validator(monkeypatch):
    monkeypatch.setattr(export, "cutout_file", lambda photo: photo)
    export.export_bundle(_reference_job(), "9:16")
    export.export_bundle(_reference_job(), "16:9")


def _app_batches(titles):
    """CutAgent's old TitleTracks.batches: a new batch whenever a title overlaps the current one."""
    out, batch = [], []
    for t in titles:
        if any(b["startUs"] < t["endUs"] and t["startUs"] < b["endUs"] for b in batch):
            out.append(batch)
            batch = []
        batch.append(t)
    return out + ([batch] if batch else [])


def test_text_stays_under_the_apps_overlay_limit(monkeypatch):
    monkeypatch.setattr(export, "cutout_file", lambda photo: photo)
    for job in (_text_job(), _reference_job(), _all_native_job()):
        project, *_ = export.build(job, "9:16")
        assert len(_app_batches(project["titles"])) <= 8, len(_app_batches(project["titles"]))


def test_decorations_become_cached_stills_on_top(monkeypatch):
    monkeypatch.setattr(export, "cutout_file", lambda photo: photo)
    for job in (_text_job(), _reference_job(), _all_native_job()):
        project, *_ = export.build(job, "9:16")
        ids = [i["id"] for i in project["images"]]
        decor = [i for i in project["images"] if i["id"].startswith(export.STATIC_DECOR)]
        assert all(not set(i) & set(export._ANIMATION_KEYS) for i in decor)  # cache-eligible in the app
        assert ids[len(ids) - len(decor):] == [i["id"] for i in decor]  # a contiguous run on top
        assert len(decor) + export._title_groups(project["titles"]) <= export.OVERLAY_SLOTS


def test_cutout_overlays_use_video_without_audio(monkeypatch):
    job = _job_with_renders()
    job.plan.scenes = [s for s in job.plan.scenes if s.id != "potm"] + [
        Scene(id="pb", start=9.5, end=12, mode="fullframe", template="photo-behind", data={"photo": "sanju_samson"})]
    project, sources, files, _ = export.build(store.save(job), "9:16")
    me = next(i for i in project["images"] if i["id"] == "me-pb")
    src = next(s for s in sources if s["id"] == me["sourceId"])
    assert me["sourceId"] == export.SILENT_ID and src["hasAudio"] is False and src["mime"].startswith("video/")
    assert all(i["sourceId"] != export.VIDEO_ID for i in project["images"])


def test_short_audio_is_padded_to_video_length(tmp_path):
    import subprocess

    from hfpipe import media

    clip = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=d=1.9", "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
    media.match_audio_length(clip)
    d = media._durations(clip)
    assert d["audio"] >= d["video"] - 0.0005


def test_still_images_get_their_used_length_not_minutes(monkeypatch):
    monkeypatch.setattr(export, "cutout_file", lambda photo: photo)
    for job in (_text_job(), _reference_job(), _all_native_job()):
        project, sources, *_ = export.build(job, "9:16")
        used = {}
        for c in project["clips"]:
            used[c.get("sourceId")] = max(used.get(c.get("sourceId"), 0), c["outUs"])
        for s in sources:
            if s["mime"].startswith("image/"):
                assert s["durationUs"] == max(export.STILL_MIN_US, used.get(s["id"], 0)), s


def test_two_photo_frames_become_a_duel(monkeypatch):
    job = _all_native_job()
    for s in job.plan.scenes:
        if s.id == "fr":
            s.data = {"title": "2018 vs Now", "photos": ["sanju_samson", "axar_patel"], "labels": ["ABD · 2018", "Head · Now"]}
    store.save(job)
    for ratio in ("9:16", "16:9"):
        project, *_ = export.build(job, ratio)
        cards = [i for i in project["images"] if i["id"].startswith("du-fr-")]
        assert len(cards) == 2 and {c["frame"]["color"] for c in cards} == set(native.DUEL_COLORS)
        a, b = cards
        if ratio == "9:16":  # one above the other, entering from opposite edges
            assert a["y"] < b["y"] and a["keys"][0]["y"] < 0.5 < b["keys"][0]["y"]
        else:
            assert a["x"] < b["x"] and a["keys"][0]["x"] < 0.5 < b["keys"][0]["x"]
        assert any(t["id"] == "vs-fr" for t in project["titles"])
