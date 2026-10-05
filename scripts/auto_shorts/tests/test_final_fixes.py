"""Regression tests for the final fix wave (dry-run ledger, source credit,
corrupt-ledger/malformed-stdout JSON contract, cron CWD docs)."""

import json
from pathlib import Path


def _setup_ok_pipeline(monkeypatch, tmp_path, clip_title="My Clip Title"):
    """Mock doctor/discover/download/clip/export+publish all-healthy."""
    import subprocess
    from scripts.auto_shorts import clip as clip_mod
    from scripts.auto_shorts import discover as discover_mod
    from scripts.auto_shorts import download as download_mod
    from scripts.auto_shorts import export_publish as export_mod
    from scripts.auto_shorts import run as run_mod
    from scripts.auto_shorts import triage as triage_mod

    monkeypatch.setenv("YT_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "x")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")

    url = "https://www.youtube.com/watch?v=v1"
    monkeypatch.setattr(
        discover_mod, "discover_trending",
        lambda *a, **k: [{"video_id": "v1", "title": "Source Video Title",
                           "channel": "C", "duration_sec": 1200,
                           "view_count": 1, "url": url}])

    def fake_download(video_url, work_dir, runner=None, cookies_file=None):
        d = Path(work_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "v1.mp4").write_bytes(b"x")
        return {"video_path": str(d / "v1.mp4"), "srt_path": None}

    monkeypatch.setattr(download_mod, "download_video", fake_download)
    payload = {"project_id": "p1", "clips": [
        {"id": "c1", "title": clip_title, "score_100": 90}]}

    def fake_fetch(video_id, work_dir, langs="zh-Hans,zh,en", runner=None):
        d = Path(work_dir)
        d.mkdir(parents=True, exist_ok=True)
        srt = d / f"{video_id}.en.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:45,000\n"
            + ("wait watch this hook content here now " * 60) + "\n",
            encoding="utf-8")
        return srt

    monkeypatch.setattr(triage_mod, "fetch_subtitles", fake_fetch)
    monkeypatch.setattr(
        run_mod, "_llm_fn",
        lambda prompt: {"score": 90, "hook": "wait, watch this",
                        "reason": "t"})
    monkeypatch.setattr(clip_mod, "run_and_parse",
                        lambda *a, **k: payload)

    seen_cmds = []

    def fake_run_and_parse(cmd, runner=None):
        seen_cmds.append(cmd)
        return {"request_id": "r1"}

    monkeypatch.setattr(export_mod, "run_and_parse", fake_run_and_parse)

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    return seen_cmds, url


def test_dry_run_does_not_poison_ledger(tmp_path, monkeypatch, capsys):
    from scripts.auto_shorts import run
    _setup_ok_pipeline(monkeypatch, tmp_path)
    rc1 = run.main(["--dry-run", "--max-per-day", "1",
                    "--workdir", str(tmp_path)])
    first = json.loads(capsys.readouterr().out)
    assert rc1 == 0
    assert first["processed"][0]["status"] == "dry_run"
    ledger_path = tmp_path / run.LEDGER_NAME
    if ledger_path.exists():
        seen = json.loads(ledger_path.read_text()).get("seen", {})
        assert "v1" not in seen
    rc2 = run.main(["--max-per-day", "1", "--workdir", str(tmp_path)])
    second = json.loads(capsys.readouterr().out)
    assert rc2 == 0
    assert second["processed"][0]["video_id"] == "v1"
    assert second["processed"][0]["status"] == "published"


def test_publish_cmd_carries_source_credit(tmp_path, monkeypatch, capsys):
    from scripts.auto_shorts import run
    from scripts.auto_shorts.export_publish import truncate_youtube_title
    long_title = "C" * 150
    seen_cmds, url = _setup_ok_pipeline(
        monkeypatch, tmp_path, clip_title=long_title)
    rc = run.main(["--max-per-day", "1", "--workdir", str(tmp_path)])
    capsys.readouterr()
    assert rc == 0
    pub_cmds = [c for c in seen_cmds if "publish" in c]
    assert pub_cmds, f"no publish invocation captured: {seen_cmds}"
    cmd = pub_cmds[-1]
    expected_title = truncate_youtube_title(long_title)
    assert len(expected_title) == 100
    assert "--title" in cmd and "--description" in cmd
    assert cmd[cmd.index("--title") + 1] == expected_title
    desc = cmd[cmd.index("--description") + 1]
    assert expected_title in desc
    assert f"Source: {url}" in desc


def test_corrupt_ledger_is_json_error_not_traceback(
        tmp_path, monkeypatch, capsys):
    from scripts.auto_shorts import run
    monkeypatch.setenv("YT_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "x")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")
    import subprocess

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    (tmp_path / run.LEDGER_NAME).write_text("{not valid json!!!")
    rc = run.main(["--max-per-day", "1", "--workdir", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)  # raises if no JSON
    assert rc == 1
    assert summary["ok"] is False
    assert "ledger" in summary["error"].lower()


def test_malformed_cli_stdout_raises_runtime_error():
    import pytest
    from scripts.auto_shorts import clip as clip_mod
    from scripts.auto_shorts import export_publish as export_mod

    class _R:
        returncode = 0
        stdout = "this is not json{{{"
        stderr = ""

    with pytest.raises(RuntimeError) as clip_exc:
        clip_mod.run_and_parse(Path("v.mp4"), None,
                               runner=lambda cmd: _R())
    assert not isinstance(clip_exc.value, json.JSONDecodeError)
    with pytest.raises(RuntimeError) as pub_exc:
        export_mod.run_and_parse(["autoclip", "publish", "--json"],
                                 runner=lambda cmd: _R())
    assert not isinstance(pub_exc.value, json.JSONDecodeError)


def test_cron_docs_pin_repo_root():
    base = Path(__file__).resolve().parent.parent
    cron = (base / "cron.example").read_text(encoding="utf-8")
    readme = (base / "README.md").read_text(encoding="utf-8")
    for text in (cron, readme):
        assert "cd /path/to/autoclip &&" in text
        assert "python -m scripts.auto_shorts.run" in text
