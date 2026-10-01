def test_title_privacy_cmds():
    from scripts.auto_shorts.export_publish import truncate_youtube_title, build_publish_cmd, build_export_cmd
    assert len(truncate_youtube_title("x" * 150)) <= 100
    assert "privacyStatus=private" in " ".join(build_publish_cmd("p", "2", "private"))
    assert "shorts" in build_export_cmd("p", "2")


def test_publish_skipped_raises():
    import pytest
    from scripts.auto_shorts.export_publish import PublishSkipped, parse_publish_json
    with pytest.raises(PublishSkipped):
        parse_publish_json({"skipped": True})
    assert parse_publish_json({"skipped": False}) == {"skipped": False}
    assert parse_publish_json({}) == {}


def test_build_description_credit_line():
    from scripts.auto_shorts.export_publish import build_description
    assert "Source: https://example.com/v" in build_description("My clip", "https://example.com/v")
