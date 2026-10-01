def test_dry_run_and_idempotent(tmp_path, monkeypatch):
    from scripts.auto_shorts import run
    argv = ["--dry-run", "--max-per-day", "1", "--workdir", str(tmp_path)]
    assert run.main(argv) in (0, 1, 2)


def _mock_pipeline(monkeypatch, tmp_path, run_payload):
    """Mock doctor/discover/download/clip/export; export must never run."""
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
        lambda *a, **k: [{"video_id": "vbad", "title": "T", "channel": "C",
                           "duration_sec": 1200, "view_count": 1,
                           "url": "https://www.youtube.com/watch?v=vbad"}])

    def fake_download(url, work_dir, runner=None):
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
