def test_bunched_scores_pick_dense_video_not_peak():
    from scripts.auto_shorts.triage import pick_winner
    a = {"video_id": "a", "windows": [{"score": 95}, {"score": 40}, {"score": 30}]}
    b = {"video_id": "b", "windows": [{"score": 85}, {"score": 84}, {"score": 83}]}
    winner, backup = pick_winner([a, b])
    assert winner["video_id"] == "b"


def test_gate_rejects_single_peak():
    from scripts.auto_shorts.triage import pick_winner
    a = {"video_id": "a", "windows": [{"score": 95}]}
    assert pick_winner([a]) == (None, None)


def test_unfetchable_subtitles_returns_none(tmp_path):
    from scripts.auto_shorts.triage import fetch_subtitles
    assert fetch_subtitles("novideo", tmp_path) is None
