def test_cmd_shape_and_429_skip(tmp_path):
    from scripts.auto_shorts.download import build_ytdlp_cmd, download_video, DownloadSkipped
    cmd = build_ytdlp_cmd("https://youtube.com/watch?v=x", tmp_path)
    assert "--write-sub" in cmd and "zh-Hans,zh,en" in " ".join(cmd)
    def fail429(*a, **k):
        class R: returncode = 1; stderr = "HTTP Error 429"; stdout = ""
        return R()
    try:
        download_video("u", tmp_path, runner=fail429)
        assert False
    except DownloadSkipped:
        pass


def test_success_returns_paths(tmp_path):
    from scripts.auto_shorts.download import download_video
    work = tmp_path / "work"
    def ok(*a, **k):
        work.mkdir(parents=True, exist_ok=True)
        (work / "abc123.mp4").write_bytes(b"\x00" * 16)
        (work / "abc123.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    out = download_video("https://youtube.com/watch?v=abc123", work, runner=ok)
    assert out["video_path"].endswith(".mp4")
    assert out["srt_path"] is not None and out["srt_path"].endswith(".srt")
