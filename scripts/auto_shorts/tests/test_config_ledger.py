def test_validate_missing_keys_and_ledger_roundtrip(tmp_path):
    from scripts.auto_shorts.config import load_config, validate_config
    cfg = load_config(env={})
    assert "YT_API_KEY" in validate_config(cfg)
    from scripts.auto_shorts.ledger import load_ledger, save_ledger, is_seen, mark_result
    p = tmp_path / "ledger.json"
    d = load_ledger(p)
    assert is_seen(d, "abc") is False
    mark_result(d, "abc", {"status": "published"})
    save_ledger(p, d)
    assert is_seen(load_ledger(p), "abc") is True
