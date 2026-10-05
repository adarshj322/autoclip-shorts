from pathlib import Path

from scripts.auto_shorts.crop import (
    DWELL_SEC,
    interpolate_boxes,
    pick_layout,
    split_filter,
    track_boxes,
    track_filter,
)


def test_two_speaker_picks_split():
    assert pick_layout(2) == "split"
    assert pick_layout(1) == "track"


def test_empty_tracking_falls_back():
    assert track_boxes(Path("nonexistent.mp4"), 0.0, 1.0) == []


def test_dwell_within_200_400ms():
    assert 0.2 <= DWELL_SEC <= 0.4


def test_interpolate_clamps_to_unit_range():
    boxes = [{"t": 0.0, "x": -0.5, "y": 1.4, "w": 2.0, "h": -0.2}]
    cx, cy, w, h = interpolate_boxes(boxes, 0.0)
    assert 0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0
    assert 0.0 <= w <= 1.0 and 0.0 <= h <= 1.0
    assert interpolate_boxes([], 1.0) is None


def test_interpolate_midpoint():
    boxes = [{"t": 0.0, "x": 0.0, "y": 0.0, "w": 0.2, "h": 0.2},
             {"t": 1.0, "x": 0.8, "y": 0.0, "w": 0.2, "h": 0.2}]
    cx, _, _, _ = interpolate_boxes(boxes, 0.5)
    assert cx == 0.5


def test_track_filter_single_pass_with_dwell():
    boxes = [{"t": 0.0, "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2},
             {"t": 1.0, "x": 0.6, "y": 0.4, "w": 0.2, "h": 0.2}]
    f = track_filter(boxes, 1.0)
    assert f.count("crop=") == 1 and f.count("scale=") == 1
    assert "lt(t,0.300)" in f  # dwell-quantized piecewise x
    assert "vstack" not in f


def test_track_filter_empty_boxes_still_filters():
    f = track_filter([], 5.0)
    assert "crop=" in f  # center fallback, never silently unfiltered


def test_split_filter_is_stacked_single_pass():
    f = ";".join(split_filter())
    assert "vstack" in f and f.count("crop=") == 2
    assert "1080:960" in f  # two 1080x960 halves stack to 1080x1920


def test_track_boxes_injected_probing_path():
    import numpy as np

    seen = {}

    class FakeCap:
        def __init__(self):
            self.n = 0

        def isOpened(self):
            return True

        def get(self, _):
            return 30.0 if _ == 5 else 640.0

        def set(self, *_):
            return True

        def read(self):
            self.n += 1
            if self.n > 6:
                return False, None
            return True, np.zeros((480, 640, 3), dtype=np.uint8)

        def release(self):
            seen["released"] = True

    class FakeBox:
        def __init__(self, xmin):
            self.xmin, self.ymin, self.width, self.height = xmin, 0.3, 0.2, 0.2

    class FakeDet:
        def __init__(self, xmin):
            self.location_data = type(
                "L", (), {"relative_bounding_box": FakeBox(xmin)})()

    class FakeFace:
        def process(self, _):
            return type("R", (), {"detections": [FakeDet(0.5)]})()

        def close(self):
            seen["closed"] = True

    boxes = track_boxes(Path("v.mp4"), 0.0, 1.0,
                        capture_factory=lambda p: FakeCap(),
                        detector_factory=lambda: FakeFace())
    assert len(boxes) >= 1
    assert set(boxes[0]) == {"t", "x", "y", "w", "h"}
    assert all(0.0 <= boxes[0][k] <= 1.0 for k in ("x", "y", "w", "h"))
    assert seen.get("released") and seen.get("closed")
