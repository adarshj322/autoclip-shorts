def test_pick_top_and_parse():
    from scripts.auto_shorts.clip import pick_top_clip, parse_run_json
    assert pick_top_clip([]) is None
    assert pick_top_clip([{"id": "1", "score_100": 60}, {"id": "2", "score_100": 91}])["id"] == "2"
    d = parse_run_json({"project_id": "p", "clips": [{"id": "2", "score_100": 91, "title": "t"}]})
    assert d["project_id"] == "p"
