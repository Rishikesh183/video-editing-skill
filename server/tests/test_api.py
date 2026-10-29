import io

from fastapi.testclient import TestClient

from conftest import make_png, make_video
from hfpipe import config, planner, store, usage
from hfpipe.api import app
from hfpipe.models import AssetSlot, Plan, Scene

SRT = "1\n00:00:00,000 --> 00:00:02,000\nNAMAN DHIR 15 BALLS 42 RUNS\n\n2\n00:00:02,000 --> 00:00:05,000\nSO HAPPY FOR HIM\n"


def _create(client, tmp_path):
    video = make_video(tmp_path / "in.mp4", 6, 320, 568)
    with video.open("rb") as v:
        r = client.post("/jobs", files={"video": ("in.mp4", v, "video/mp4"), "captions": ("c.srt", io.BytesIO(SRT.encode()))},
                        data={"auto_plan": "false", "ratios": "9:16"})
    assert r.status_code == 200, r.text
    return r.json()


def _inject_plan(job_id):
    job = store.load(job_id)
    job.plan = Plan(
        scenes=[Scene(id="s1", start=0, end=3, mode="fullframe", template="innings-compare", needs_input=["left_runs"])],
        assets_needed=[AssetSlot(slot="naman_dhir", description="Naman Dhir batting")],
    )
    job.status = "planned"
    store.save(job)


def test_create_edit_upload_flow(tmp_path):
    client = TestClient(app)
    job = _create(client, tmp_path)
    assert job["status"] == "uploaded" and len(job["captions"]) == 2

    _inject_plan(job["id"])
    r = client.patch(f"/jobs/{job['id']}/scenes/s1", json={"data": {"left_runs": 24}, "end": 4})
    scene = r.json()["plan"]["scenes"][0]
    assert scene["data"]["left_runs"] == 24 and scene["needs_input"] == [] and scene["end"] == 4

    assert client.get(f"/jobs/{job['id']}").json()["missing_assets"] == ["naman_dhir"]
    png = make_png(tmp_path / "p.png")
    with png.open("rb") as f:
        r = client.post(f"/jobs/{job['id']}/assets/naman_dhir", files={"photo": ("p.png", f, "image/png")})
    assert r.status_code == 200 and r.json()["missing_assets"] == []
    assert (config.LIBRARY / "naman_dhir.png").exists()
    assert client.get("/library").json() == ["naman_dhir"]


def test_rejects_bad_input(tmp_path):
    client = TestClient(app)
    r = client.post("/jobs", files={"video": ("x.avi", b"x"), "captions": ("c.srt", SRT.encode())})
    assert r.status_code == 400
    silent = make_video(tmp_path / "silent.mp4", 2, 320, 568, audio=False)
    with silent.open("rb") as v:
        r = client.post("/jobs", files={"video": ("s.mp4", v), "captions": ("c.srt", SRT.encode())}, data={"auto_plan": "false"})
    assert r.status_code == 400 and "audio" in r.text


def test_token_required_when_configured(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "secret-token")
    client = TestClient(app)
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_planner_parses_and_logs_usage(tmp_path, monkeypatch):
    client = TestClient(app)
    job = _create(client, tmp_path)
    fake = {
        "structured_output": {"scenes": [{"id": "a", "start": 0.2, "end": 3.1, "mode": "fullframe",
                                          "template": "innings-compare", "data": {}, "reason": "stat"}],
                              "assets_needed": [{"slot": "Naman Dhir", "description": "photo"}]},
        "usage": {"input_tokens": 10, "output_tokens": 500, "cache_creation_input_tokens": 4000},
        "modelUsage": {"claude-sonnet-5-5": {}}, "total_cost_usd": 0.04, "duration_ms": 1200,
    }
    seen = {}
    monkeypatch.setattr(planner, "run_claude", lambda text, style="": seen.setdefault("input", text) and fake)
    plan = planner.plan_job(store.load(job["id"]), ["sanju_samson"], notes="more stats")
    assert plan.scenes[0].start == 0.0 and plan.assets_needed[0].slot == "naman_dhir"
    assert "NAMAN DHIR" in seen["input"] and "sanju_samson" in seen["input"] and "more stats" in seen["input"]
    summary = usage.summary(job["id"])
    assert summary["total"]["calls"] == 1 and summary["total"]["output_tokens"] == 500
    assert client.get("/usage").json()["last_7_days"]["calls"] == 1
    # Same inputs again: served from the plan cache, no second Claude call.
    monkeypatch.setattr(planner, "run_claude", lambda text, style="": (_ for _ in ()).throw(AssertionError("Claude called")))
    again = planner.plan_job(store.load(job["id"]), ["sanju_samson"], notes="more stats")
    assert again == plan
    steps = {r["step"] for r in usage.summary(job["id"])["by_step"]}
    assert steps == {"plan", "plan (cached)"}


def test_codex_provider_and_dashboard(tmp_path, monkeypatch):
    client = TestClient(app)
    job = _create(client, tmp_path)
    fake = {"provider": "codex", "structured_output": {
        "scenes": [{"id": "a", "start": 0.2, "end": 3.1, "mode": "fullframe", "template": "text-punch",
                    "data": {"text": "Auction"}, "needs_input": [], "reason": "date"}],
        "assets_needed": []},
        "usage": {"input_tokens": 9000, "output_tokens": 700, "cache_read_input_tokens": 16000},
        "modelUsage": {"gpt-6-sol": {}}, "total_cost_usd": 0.0, "duration_ms": 900}
    monkeypatch.setattr(planner, "run_codex", lambda text, style="": fake)
    monkeypatch.setattr(planner, "run_claude", lambda text, style="": (_ for _ in ()).throw(AssertionError("wrong provider")))
    plan = planner.plan_job(store.load(job["id"]), [], provider="codex")
    assert plan.scenes[0].data["text"] == "Auction"
    data = client.get("/usage/dashboard").json()
    assert data["week"]["codex"]["calls"] == 1 and data["edits"]["jobs"] == 1
    row = next(j for j in data["jobs"] if j["job_id"] == job["id"])
    assert row["providers"] == "codex" and row["video"]
    assert "Edit usage" in client.get("/dashboard").text


def test_codex_schema_is_strict_and_data_round_trips():
    import json

    schema = planner._codex_schema(json.loads((config.PLANNER_DIR / "plan.schema.json").read_text(encoding="utf-8")))
    scene = schema["properties"]["scenes"]["items"]
    assert set(scene["required"]) == set(scene["properties"]) and scene["additionalProperties"] is False
    assert scene["properties"]["data"]["type"] == "string"
    out = planner._from_codex({"scenes": [{"data": '{"value": 430}'}], "assets_needed": []})
    assert out["scenes"][0]["data"] == {"value": 430}


def test_dashboard_page_is_public_but_data_needs_token(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "secret-token")
    client = TestClient(app)
    assert client.get("/dashboard").status_code == 200
    assert client.get("/usage/dashboard").status_code == 401


def test_photo_upload_accepts_webp_and_extensionless(tmp_path):
    from PIL import Image

    client = TestClient(app)
    job = _create(client, tmp_path)
    _inject_plan(job["id"])
    webp = tmp_path / "p.webp"
    Image.new("RGB", (64, 48), (200, 30, 30)).save(webp, "WEBP")
    for name in ("player.webp", "download"):  # a browser download often has no extension
        with webp.open("rb") as f:
            r = client.post(f"/jobs/{job['id']}/assets/naman_dhir", files={"photo": (name, f, "application/octet-stream")})
        assert r.status_code == 200, r.text


def test_lessons_are_remembered_and_reach_the_planner(tmp_path, monkeypatch):
    import shutil

    planner_dir = tmp_path / "planner"
    shutil.copytree(config.PLANNER_DIR, planner_dir)
    monkeypatch.setattr(config, "PLANNER_DIR", planner_dir)
    client = TestClient(app)
    assert client.post("/lessons", json={"text": "Never show the scoreboard upside down"}).json()["added"] is True
    assert client.post("/lessons", json={"text": "Never show the scoreboard upside down"}).json()["added"] is False
    assert "scoreboard upside down" in planner.system_prompt()
    assert "Creator's standing preferences" in client.get("/lessons").json()["text"]


def test_review_findings_become_fix_notes():
    from hfpipe import review

    notes = review.fix_notes({"edit_issues": [{"scene_id": "s9", "problem": "photos stacked", "fix": "use a duel"}],
                              "photo_issues": [{"slot": "travis_head", "problem": "South Africa kit in an Australia slot"}]})
    assert "s9: photos stacked -> use a duel" in notes and "travis_head" in notes
    assert review.fix_notes({"edit_issues": [], "photo_issues": []}) == ""
