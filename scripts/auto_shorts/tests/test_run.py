def test_dry_run_and_idempotent(tmp_path, monkeypatch):
    from scripts.auto_shorts import run
    argv = ["--dry-run", "--max-per-day", "1", "--workdir", str(tmp_path)]
    assert run.main(argv) in (0, 1, 2)


def _mock_triage_ok(monkeypatch, tmp_path, hook="wait, this changes everything",
                    score=90):
    """Mock triage fetch (writes a small EN srt) + run._llm_fn scoring."""
    from pathlib import Path

    from scripts.auto_shorts import run as run_mod
    from scripts.auto_shorts import triage as triage_mod

    def fake_fetch(video_id, work_dir, langs="zh-Hans,zh,en", runner=None):
        d = Path(work_dir)
        d.mkdir(parents=True, exist_ok=True)
        srt = d / f"{video_id}.en.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:45,000\n"
            + ("hello world voice talking content here now " * 60) + "\n",
            encoding="utf-8")
        return srt

    monkeypatch.setattr(triage_mod, "fetch_subtitles", fake_fetch)
    monkeypatch.setattr(
        run_mod, "_llm_fn",
        lambda prompt: {"score": score, "hook": hook, "reason": "t"})


def _mock_pipeline(monkeypatch, tmp_path, run_payload):
    """Mock doctor/discover/download/clip/export; export must never run."""
    import subprocess
    from scripts.auto_shorts import clip as clip_mod
    from scripts.auto_shorts import discover as discover_mod
    from scripts.auto_shorts import download as download_mod
    from scripts.auto_shorts import export_publish as export_mod

    _mock_triage_ok(monkeypatch, tmp_path)

    monkeypatch.setenv("YT_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "x")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")

    monkeypatch.setattr(
        discover_mod, "discover_trending",
        lambda *a, **k: [{"video_id": "vbad", "title": "T", "channel": "C",
                           "duration_sec": 1200, "view_count": 1,
                           "url": "https://www.youtube.com/watch?v=vbad"}])

    def fake_download(url, work_dir, runner=None, cookies_file=None):
        d = tmp_path / "work" / "vbad"
        d.mkdir(parents=True, exist_ok=True)
        (d / "vbad.mp4").write_bytes(b"x")
        return {"video_path": str(d / "vbad.mp4"), "srt_path": None}

    monkeypatch.setattr(download_mod, "download_video", fake_download)
    monkeypatch.setattr(clip_mod, "run_and_parse",
                        lambda *a, **k: run_payload)

    def boom(cmd, runner=None):
        raise AssertionError("export must not run with missing ids")

    monkeypatch.setattr(export_mod, "run_and_parse", boom)

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())


def test_missing_project_id_skips_cleanly(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    _mock_pipeline(monkeypatch, tmp_path,
                   {"project_id": None, "clips": [
                       {"id": "c1", "title": "Clip", "score_100": 80}]})
    rc = run.main(["--dry-run", "--max-per-day", "1",
                   "--workdir", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)  # raises if no JSON
    assert rc == 1
    assert summary["ok"] is False
    assert summary["processed"] == []
    assert len(summary["skipped"]) == 1
    assert summary["skipped"][0]["video_id"] == "vbad"
    assert summary["skipped"][0]["reason"].startswith(
        "export_failed: missing project_id/clip_id")


def test_missing_clip_id_skips_cleanly(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    _mock_pipeline(monkeypatch, tmp_path,
                   {"project_id": "p1", "clips": [
                       {"id": None, "title": "Clip", "score_100": 80}]})
    rc = run.main(["--dry-run", "--max-per-day", "1",
                   "--workdir", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)  # raises if no JSON
    assert rc == 1
    assert summary["ok"] is False
    assert summary["processed"] == []
    assert len(summary["skipped"]) == 1
    assert summary["skipped"][0]["video_id"] == "vbad"
    assert summary["skipped"][0]["reason"].startswith(
        "export_failed: missing project_id/clip_id")


def test_missing_cookies_file_is_config_error(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    monkeypatch.setenv("YT_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "x")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")
    rc = run.main(["--dry-run", "--max-per-day", "1",
                   "--workdir", str(tmp_path),
                   "--cookies-file", str(tmp_path / "nope.txt")])
    summary = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert summary["ok"] is False
    assert "cookies" in summary["error"]


def test_hook_veto_skips_hookless_clip(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    # _mock_pipeline with a top clip whose first 3s are throat-clearing
    # (hook score 0 / hook text empty) → skipped with reason hook_failed
    _mock_triage_ok(monkeypatch, tmp_path, hook="", score=85)
    import subprocess
    from scripts.auto_shorts import clip as clip_mod
    from scripts.auto_shorts import discover as discover_mod
    from scripts.auto_shorts import download as download_mod
    from scripts.auto_shorts import export_publish as export_mod
    monkeypatch.setenv("YT_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "x")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")
    monkeypatch.setattr(
        discover_mod, "discover_trending",
        lambda *a, **k: [{"video_id": "vhook", "title": "T", "channel": "C",
                           "duration_sec": 1200, "view_count": 1,
                           "url": "https://www.youtube.com/watch?v=vhook"}])

    def fake_download(url, work_dir, runner=None, cookies_file=None):
        from pathlib import Path
        d = Path(work_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "vhook.mp4").write_bytes(b"x")
        return {"video_path": str(d / "vhook.mp4"), "srt_path": None}

    monkeypatch.setattr(download_mod, "download_video", fake_download)
    monkeypatch.setattr(
        clip_mod, "run_and_parse",
        lambda *a, **k: {"project_id": "p1", "clips": [
            {"id": "c1", "title": "um uh so like you know", "score_100": 95}]})

    def boom(cmd, runner=None):
        raise AssertionError("export must not run on hook veto")

    monkeypatch.setattr(export_mod, "run_and_parse", boom)

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    rc = run.main(["--dry-run", "--max-per-day", "1",
                   "--workdir", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any(s["reason"].startswith("hook_") for s in summary["skipped"])


def test_unfetchable_subtitles_skips_video(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    from scripts.auto_shorts import triage as triage_mod
    # _mock_pipeline with triage returning a video whose subtitles cannot
    # be fetched → skipped with reason no_subtitles, never downloaded
    import subprocess
    from scripts.auto_shorts import discover as discover_mod
    from scripts.auto_shorts import download as download_mod
    monkeypatch.setenv("YT_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "x")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")
    monkeypatch.setattr(
        discover_mod, "discover_trending",
        lambda *a, **k: [{"video_id": "vnosub", "title": "T", "channel": "C",
                           "duration_sec": 1200, "view_count": 1,
                           "url": "https://www.youtube.com/watch?v=vnosub"}])
    monkeypatch.setattr(triage_mod, "fetch_subtitles", lambda *a, **k: None)
    downloaded = []

    def fake_download(url, work_dir, runner=None, cookies_file=None):
        downloaded.append(url)
        raise AssertionError("download must not run without subtitles")

    monkeypatch.setattr(download_mod, "download_video", fake_download)

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    rc = run.main(["--dry-run", "--max-per-day", "1",
                   "--workdir", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert downloaded == []
    assert any(s["reason"] == "no_subtitles" for s in summary["skipped"])


def test_hook_pass_hook_field_keyword_and_empty():
    from scripts.auto_shorts.run import _hook_pass
    wins = [{"start": 0.0, "end": 45.0, "text": "hello world",
             "score": 85, "hook": "wait for it", "reason": "t"}]
    assert _hook_pass(wins, 0.0) is True
    wins_kw = [{"start": 0.0, "end": 45.0, "text": "why this works",
                "score": 85, "hook": "", "reason": "t"}]
    assert _hook_pass(wins_kw, 0.0) is True
    wins_throat = [{"start": 0.0, "end": 45.0,
                    "text": "um uh so like you know well",
                    "score": 85, "hook": "", "reason": "t"}]
    assert _hook_pass(wins_throat, 0.0) is False


def test_words_from_srt_even_split(tmp_path):
    from scripts.auto_shorts.run import _words_from_srt
    srt = tmp_path / "v.en.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:04,000\nhello brave world\n",
                   encoding="utf-8")
    words = _words_from_srt(srt, 0.0, 4.0)
    assert [w["word"] for w in words] == ["hello", "brave", "world"]
    assert words[0]["start"] == 0.0
    assert words[-1]["end"] == 4.0
    assert _words_from_srt(tmp_path / "missing.srt", 0.0, 4.0) == []


def test_hook_keywords_need_word_boundaries():
    from scripts.auto_shorts.run import _hook_pass

    def wins(text):
        return [{"start": 0.0, "end": 45.0, "text": text,
                 "score": 85, "hook": "", "reason": "t"}]

    for trap in ("look out the window today",
                 "the news tonight at nine",
                 "come see the show with us"):
        assert _hook_pass(wins(trap), 0.0) is False
    for hit in ("you can win a prize",
                "a brand new day awaits",
                "how does this work"):
        assert _hook_pass(wins(hit), 0.0) is True


def test_llm_fn_openai_compatible_endpoint(monkeypatch):
    import json as _json
    import urllib.request
    from scripts.auto_shorts import run as run_mod
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("API_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("API_MODEL_NAME", "m")
    monkeypatch.delenv("API_DASHSCOPE_API_KEY", raising=False)
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"choices": [{"message": {"content": _json.dumps({"score": 80})}}]}).encode()

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert run_mod._llm_fn("hello") == {"score": 80}
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"


def test_llm_fn_no_key_scores_zero(monkeypatch):
    from scripts.auto_shorts import run as run_mod
    monkeypatch.delenv("API_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_DASHSCOPE_API_KEY", raising=False)
    assert run_mod._llm_fn("hello") == {}
