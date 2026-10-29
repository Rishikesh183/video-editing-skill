import subprocess

from hfpipe import media, tighten
from hfpipe.models import Cue


def _take(path):
    # 2 s tone, 2 s silence, 2 s tone (a pause in the middle of a take)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x284:r=30:d=6",
                    "-f", "lavfi", "-i", "sine=f=300:d=6",
                    "-af", "volume='if(between(t,2,4),0,1)':eval=frame",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_long_pause_is_shortened_and_captions_follow(tmp_path):
    take = _take(tmp_path / "take.mp4")
    gaps = tighten.silences(take)
    assert gaps and 1.7 < gaps[0][1] - gaps[0][0] < 2.3
    captions = [Cue(start=0.2, end=1.8, text="first line"), Cue(start=4.2, end=5.8, text="second line")]
    cuts = tighten.plan_cuts(gaps, captions)
    assert len(cuts) == 1 and cuts[0].start > gaps[0][0] and cuts[0].end < gaps[0][1]  # a breath kept on each side
    out = tighten.render(take, cuts, 6.0, tmp_path / "tight.mp4")
    removed = cuts[0].end - cuts[0].start
    assert abs(media.probe(out).duration_us / 1e6 - (6.0 - removed)) < 0.15
    moved = tighten.remap_captions(captions, cuts)
    assert moved[0].start == 0.2 and abs(moved[1].start - (4.2 - removed)) < 0.01


def test_restarted_sentence_drops_the_first_attempt():
    captions = [Cue(start=0, end=2, text="South Africa have the best batting"),
                Cue(start=2.2, end=2.6, text="um"),
                Cue(start=3, end=5, text="South Africa have the best batting line up")]
    cuts = tighten.plan_cuts([], captions)
    assert any(c.why.startswith("retake") and c.start == 0 and c.end == 3 for c in cuts)
