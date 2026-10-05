from scripts.auto_shorts.snap import SNAP_MOVE_LIMIT_SEC, scene_cuts, snap_window


def test_snap_to_sentence_and_clamp():
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate("the cat sat on the mat".split())]
    ns, ne, moved = snap_window(0.1, 75.0, words, scene_cuts=[2.0])
    assert ne - ns <= 60.0
    assert moved >= 0

def test_big_move_flagged():
    words = [{"word": "hi", "start": 50.0, "end": 50.4}]
    ns, ne, moved = snap_window(0.0, 1.0, words)
    assert moved > 2.0


def test_scene_cuts_failures_return_empty(tmp_path):
    assert scene_cuts(tmp_path / "missing.mp4", 0.0, 10.0) == []
    assert SNAP_MOVE_LIMIT_SEC == 2.0
