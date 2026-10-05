"""Final fix-wave regressions: duration-normalized density (triage) and
fail-closed hook veto (run). Appended, not edited, per wave instructions."""

from scripts.auto_shorts.run import _hook_pass
from scripts.auto_shorts.triage import _clippability, pick_winner


def _win(score, start, end, hook="", text="filler content here"):
    return {"score": score, "start": start, "end": end,
            "hook": hook, "text": text}


def test_density_longer_video_does_not_outrank_shorter():
    same = [80, 80, 10]
    short = {"video_id": "short",
             "windows": [_win(s, i * 100.0, (i + 1) * 100.0) for i, s in enumerate(same)]}
    long = {"video_id": "long",
            "windows": [_win(s, i * 1200.0, (i + 1) * 1200.0) for i, s in enumerate(same)]}
    assert _clippability(short["windows"]) > _clippability(long["windows"])
    winner, _ = pick_winner([long, short])  # order-biased: tie keeps long
    assert winner["video_id"] == "short"


def test_density_uses_explicit_duration_sec():
    mk = lambda dur: {"video_id": f"v{dur}", "duration_sec": dur,
                      "windows": [_win(80, 0.0, 45.0), _win(80, 45.0, 90.0),
                                  _win(10, 90.0, 135.0)]}
    winner, _ = pick_winner([mk(3600), mk(300)])  # tie keeps 3600 pre-fix
    assert winner["video_id"] == "v300"


def test_hook_no_overlap_fails_closed_despite_hookful_best():
    # Snapped start lands in a gap: no window covers [100, 103), while the
    # best-scoring window elsewhere carries a hook. Pre-fix fallback passed
    # the veto via that non-overlapping window; fail closed instead.
    windows = [
        _win(60, 0.0, 45.0, hook="", text="um uh so like you know well"),
        _win(95, 300.0, 345.0, hook="wait, watch this", text="why this works"),
    ]
    assert _hook_pass(windows, 100.0) is False
