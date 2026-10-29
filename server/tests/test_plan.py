from hfpipe.models import AssetSlot, Plan, Scene
from hfpipe.plan import normalize, slug
from hfpipe.srt import parse_srt, to_srt


def scene(id, start, end, mode="fullframe", **kw):
    return Scene(id=id, start=start, end=end, mode=mode, template="innings-compare", **kw)


def test_parse_srt_round_trip():
    text = "1\n00:00:00,000 --> 00:00:01,269\nNAMAN DHIR WHAT A MAN\n\n2\n00:01:00,000 --> 00:01:00,442\nHE HAS\nFULL PRESSURE\n"
    cues = parse_srt(text)
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 1.269, "NAMAN DHIR WHAT A MAN"), (60.0, 60.442, "HE HAS FULL PRESSURE")]
    assert parse_srt(to_srt(cues)) == cues


def test_overlaps_trimmed_and_short_scenes_dropped():
    plan = Plan(scenes=[scene("a", 2, 6), scene("b", 5, 9), scene("c", 8.5, 9.2), scene("d", 20, 20.5)])
    out = normalize(plan, 30)
    assert [(s.id, s.start, s.end) for s in out.scenes] == [("a", 2, 6), ("b", 6, 9)]


def test_face_slivers_absorbed_and_edges_snapped():
    plan = Plan(scenes=[scene("a", 0.3, 5), scene("b", 5.4, 9), scene("c", 9.9, 29.7)])
    out = normalize(plan, 30)
    assert [(s.start, s.end) for s in out.scenes] == [(0.0, 5.4), (5.4, 9.0), (9.9, 30.0)]


def test_edges_land_on_30fps_frames():
    out = normalize(Plan(scenes=[scene("a", 1.2689, 5.0123)]), 30)
    s = out.scenes[0]
    assert abs(s.start * 30 - round(s.start * 30)) < 1e-3
    assert abs(s.end * 30 - round(s.end * 30)) < 1e-3


def test_overlays_may_overlap_fullframe_and_slots_are_slugged():
    plan = Plan(
        scenes=[scene("a", 2, 6), scene("o", 4, 8, mode="overlay", data={"photo": "Sanju Samson"})],
        assets_needed=[AssetSlot(slot="Sanju Samson", description="photo")],
    )
    out = normalize(plan, 30)
    assert [s.id for s in out.scenes] == ["a", "o"]
    assert out.scenes[1].data["photo"] == "sanju_samson"
    assert out.assets_needed[0].slot == "sanju_samson"
    assert slug("Bhuvneshwar  Kumar!") == "bhuvneshwar_kumar"


def test_visuals_move_onto_the_spoken_mention():
    from hfpipe.models import Cue
    from hfpipe.plan import align_to_mentions

    plan = Plan(scenes=[
        Scene(id="a", start=12.2, end=17.0, mode="fullframe", template="photo-frames", data={"photos": ["matt_renshaw"]}),
        Scene(id="b", start=17.6, end=19.8, mode="overlay", template="player-cutout", data={"players": ["temba_bavuma"]}),
    ])
    captions = [Cue(start=14.0, end=15.9, text="AUSTRALIA SAVED MATT RENSHAW"),
                Cue(start=17.0, end=18.6, text="SOUTH AFRICA SAVED TEMBA BAHOMA")]  # misspelt by translation
    out = {s.id: s for s in align_to_mentions(plan, captions).scenes}
    assert out["a"].start == 14.0 and out["b"].start == 17.0
    assert out["a"].end <= out["b"].start  # the overlay is not swallowed
