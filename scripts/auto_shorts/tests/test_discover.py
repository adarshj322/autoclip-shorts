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


def test_quota_word_in_video_content_is_not_an_error():
    # A video titled "... quota ..." in a 200 body must not abort discovery.
    from scripts.auto_shorts import discover

    def fake(url, params=None, timeout=30):
        if "search" in url:
            return {"items": [{"id": {"videoId": "v1"}}]}
        return {"items": [{
            "id": "v1",
            "snippet": {"title": "How quotas work in cloud APIs", "channelTitle": "C"},
            "contentDetails": {"duration": "PT20M"},
            "statistics": {"viewCount": "10"},
        }]}

    res = discover.discover_trending("k", http_get=fake)
    assert [r["video_id"] for r in res] == ["v1"]


def test_quota_reason_in_error_object_raises():
    from scripts.auto_shorts import discover

    class R:
        status_code = 200

        def json(self):
            return {"error": {"errors": [{"reason": "quotaExceeded",
                                          "message": "Quota exceeded."}]}}

    try:
        discover.discover_trending("k", http_get=lambda *a, **k: R())
        assert False
    except discover.DiscoveryError as e:
        assert "quota" in str(e).lower()


def test_dict_error_shape_checked():
    from scripts.auto_shorts import discover

    try:
        discover.discover_trending(
            "k", http_get=lambda *a, **k: {"error": {"errors": [
                {"reason": "dailyLimitExceeded"}]}})
        assert False
    except discover.DiscoveryError as e:
        assert "quota" in str(e).lower()

    try:
        discover.discover_trending(
            "k", http_get=lambda *a, **k: {"error": {"code": 400,
                                                    "message": "Bad request"}})
        assert False
    except discover.DiscoveryError:
        pass


def test_filter_non_numeric_duration_and_bare_p():
    from scripts.auto_shorts.discover import (
        parse_duration_iso8601, filter_candidates)
    import pytest
    for bad in ("P", "PT"):
        with pytest.raises(ValueError):
            parse_duration_iso8601(bad)
    vids = [{"video_id": "x", "duration_sec": "abc",
             "view_count": 1, "channel": "c"}]
    assert filter_candidates(vids, seen=set(), allow=None,
                             block=set()) == []
