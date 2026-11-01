from pathlib import Path

import pytest

from hfpipe import native
from hfpipe.models import Scene


def media(ratio: float = 1.4) -> native.Media:
    """Sources `ratio` times as tall as they are wide."""
    return native.Media(photo=lambda s: f"photo-{s}", decor=lambda n: f"decor-{n}", still=lambda i, p: i, face=lambda a, b: None,
                        canvas=(1080, 1920), workdir=Path("."), cutout=lambda s: f"cut-{s}", size=lambda s: (1000, round(1000 * ratio)))


def scene(**data) -> Scene:
    return Scene(id="s", start=0, end=4, mode="fullframe", template="stat-count", data=data)


@pytest.mark.parametrize("portrait", [True, False])
def test_strap_never_lands_on_the_shrunk_creators_face(portrait):
    face = (0.3, 0.2, 0.7, 0.42)
    pose = native.aside_pose(portrait, 9 / 16)
    top = pose["y"] + (face[1] - 0.5) * pose["scale"]
    bottom = pose["y"] + (face[3] - 0.5) * pose["scale"]
    y = native.strap_height(face, portrait, 9 / 16)
    sub_line = y + 0.05
    assert sub_line < top or y > bottom or not portrait


def test_strap_goes_above_the_head_in_portrait():
    face = (0.3, 0.2, 0.7, 0.42)
    pose = native.aside_pose(True, 9 / 16)
    assert native.strap_height(face, True, 9 / 16) < pose["y"] + (face[1] - 0.5) * pose["scale"] - 0.05


def test_strap_stays_a_lower_third_when_it_clears_the_creator():
    assert native.strap_height((0.02, 0.2, 0.2, 0.5), False, 16 / 9) == 0.72


def test_strap_without_a_detected_face_stays_clear_of_where_heads_usually_are():
    assert native.strap_height(None, True, 9 / 16) == 0.42


def test_a_single_player_on_a_stage_fills_the_free_space():
    m = native._player_cutouts(scene(players=["sanju"]), 0, 4_000_000, True, media(), max_h=0.6, fill=True)
    image = m.images[0]
    assert image["x"] == 0.5, "centred, not pushed into a corner"
    assert native._height(media(), "cut-sanju", image["width"]) == pytest.approx(0.6, abs=0.01)
    assert image["y"] == pytest.approx(1 - 0.3 + 0.02, abs=0.01), "standing on the bottom edge"


def test_a_very_wide_cut_out_stays_on_screen():
    m = native._player_cutouts(scene(players=["sanju"]), 0, 4_000_000, True, media(0.5), max_h=0.6, fill=True)
    assert m.images[0]["width"] <= 0.94


def test_beside_the_creator_a_player_keeps_to_the_side():
    assert native._player_cutouts(scene(players=["sanju"]), 0, 4_000_000, True, media()).images[0]["x"] == 0.72


def test_kicker_plate_hugs_its_word():
    short = native._kicker(scene(kicker="OPENER"), 0, 1_000_000, 0.1)[0]
    long = native._kicker(scene(kicker="MIDDLE ORDER BATTERS"), 0, 1_000_000, 0.1)[0]
    assert short["wrapWidth"] < 0.4 < long["wrapWidth"] <= 0.8


def test_stages_and_photos_carry_no_per_frame_shader():
    clip = native._photo_clip("c", "p", 3_000_000, "right")
    assert "effect" not in clip["effects"] and all("blur" not in k for k in clip["keys"])
