def test_title_privacy_cmds():
    from scripts.auto_shorts.export_publish import truncate_youtube_title, build_publish_cmd, build_export_cmd
    assert len(truncate_youtube_title("x" * 150)) <= 100
    assert "privacyStatus=private" in " ".join(build_publish_cmd("p", "2", "private"))
    assert "shorts" in build_export_cmd("p", "2")
