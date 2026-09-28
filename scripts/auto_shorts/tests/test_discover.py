def test_filter_and_iso8601():
    from scripts.auto_shorts.discover import parse_duration_iso8601, filter_candidates
    assert parse_duration_iso8601("PT16M30S") == 990
    vids = [{"video_id": "a", "duration_sec": 2000, "view_count": 5, "channel": "c"},
            {"video_id": "b", "duration_sec": 100, "view_count": 999, "channel": "c"}]
    out = filter_candidates(vids, seen={"a"}, allow=None, block=set())
    assert [v["video_id"] for v in out] == []


def test_quota_error_surfaces():
    from scripts.auto_shorts import discover
    def boom(*a, **k): raise discover.DiscoveryError("quotaExceeded")
    try:
        discover.discover_trending("k", http_get=boom)
        assert False
    except discover.DiscoveryError as e:
        assert "quota" in str(e).lower()
