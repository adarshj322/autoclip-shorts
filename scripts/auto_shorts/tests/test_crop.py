from pathlib import Path

from scripts.auto_shorts.crop import pick_layout, track_boxes


def test_two_speaker_picks_split():
    assert pick_layout(2) == "split"
    assert pick_layout(1) == "track"


def test_empty_tracking_falls_back():
    assert track_boxes(Path("nonexistent.mp4"), 0.0, 1.0) == []
