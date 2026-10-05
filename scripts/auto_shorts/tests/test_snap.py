from scripts.auto_shorts.snap import SNAP_MOVE_LIMIT_SEC, scene_cuts, snap_window

import pytest


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


def test_silence_band_largest_in_band_wins():
    words = [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 0.85, "end": 1.35},  # 0.35s gap, mid 0.675
        {"word": "c", "start": 1.8, "end": 2.3},  # 0.45s gap, mid 1.575
    ]
    ns, ne, _ = snap_window(0.6, 2.3, words)
    assert ns == pytest.approx(1.575)  # largest in-band gap, not the nearer 0.35s one


def test_silence_band_ignores_long_pause():
    words = [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 1.3, "end": 1.8},  # 0.8s pause, mid 0.9
        {"word": "c", "start": 2.2, "end": 2.7},  # 0.4s gap, mid 2.0
    ]
    ns, ne, _ = snap_window(1.3, 2.7, words)
    assert ns == 2.0


def test_unparseable_cuts_dropped_no_phantom_zero():
    words = [{"word": "hi", "start": 0.2, "end": 0.6}]
    ns, ne, _ = snap_window(0.0, 5.0, words, scene_cuts=["bad", None, True])
    assert ns == 0.2


def test_scene_cuts_extracts_and_filters(monkeypatch, tmp_path):
    import sys
    import types

    from scripts.auto_shorts import snap as snap_mod

    calls = {}

    class FakeTC:
        def __init__(self, s):
            self._s = s

        def get_seconds(self):
            return self._s

    class FakeVideo:
        def seek(self, target):
            calls["seek"] = target

    class FakeMgr:
        def add_detector(self, det):
            pass

        def detect_scenes(self, video, end_time=None):
            calls["video"] = video
            calls["end_time"] = end_time

        def get_scene_list(self):
            return [
                (FakeTC(1.0), FakeTC(2.0)),
                (FakeTC(5.0), FakeTC(6.0)),
                (FakeTC(50.0), FakeTC(51.0)),
                (FakeTC(-3.0), FakeTC(-2.0)),
            ]

    video = FakeVideo()
    pkg = types.ModuleType("scenedetect")
    pkg.SceneManager = FakeMgr
    pkg.open_video = lambda p: (calls.__setitem__("path", p), video)[1]
    det = types.ModuleType("scenedetect.detectors")
    det.ContentDetector = lambda: object()
    monkeypatch.setitem(sys.modules, "scenedetect", pkg)
    monkeypatch.setitem(sys.modules, "scenedetect.detectors", det)

    cuts = snap_mod.scene_cuts(tmp_path / "v.mp4", 0.0, 10.0)
    assert cuts == [1.0, 5.0]
    assert calls["seek"] == 0.0 and isinstance(calls["seek"], float)
    assert calls["end_time"] == 10.0 and isinstance(calls["end_time"], float)
    assert str(calls["path"]).endswith("v.mp4")
