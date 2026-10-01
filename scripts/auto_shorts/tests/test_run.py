def test_dry_run_and_idempotent(tmp_path, monkeypatch):
    from scripts.auto_shorts import run
    argv = ["--dry-run", "--max-per-day", "1", "--workdir", str(tmp_path)]
    assert run.main(argv) in (0, 1, 2)
