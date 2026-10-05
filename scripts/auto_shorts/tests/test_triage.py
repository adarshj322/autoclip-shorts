def test_bunched_scores_pick_dense_video_not_peak():
    from scripts.auto_shorts.triage import pick_winner
    a = {"video_id": "a", "windows": [{"score": 95}, {"score": 40}, {"score": 30}]}
    b = {"video_id": "b", "windows": [{"score": 85}, {"score": 84}, {"score": 83}]}
    winner, backup = pick_winner([a, b])
    assert winner["video_id"] == "b"


def test_gate_rejects_single_peak():
    from scripts.auto_shorts.triage import pick_winner
    a = {"video_id": "a", "windows": [{"score": 95}]}
    assert pick_winner([a]) == (None, None)


def test_unfetchable_subtitles_returns_none(tmp_path):
    from scripts.auto_shorts.triage import fetch_subtitles
    assert fetch_subtitles("novideo", tmp_path) is None


def test_gate_rejects_enough_windows_but_not_enough_qualifying():
    from scripts.auto_shorts.triage import pick_winner
    a = {"video_id": "a", "windows": [{"score": 95}, {"score": 40}, {"score": 30}]}
    assert pick_winner([a]) == (None, None)


def test_gate_scoreless_windows_are_non_qualifying():
    from scripts.auto_shorts.triage import pick_winner
    a = {"video_id": "a", "windows": [{}, {"hook": "x"}]}
    assert pick_winner([a]) == (None, None)


def test_fetch_subtitles_ignores_foreign_srt(tmp_path):
    from scripts.auto_shorts.triage import fetch_subtitles
    def ok(cmd, *a, **k):
        (tmp_path / "other.en.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    assert fetch_subtitles("myvideo", tmp_path, runner=ok) is None


def test_score_video_caps_llm_calls_at_15(monkeypatch):
    from scripts.auto_shorts import triage as triage_mod
    windows = [{"start": float(i), "end": float(i + 5),
                "text": f"filler words number {i} here today"}
               for i in range(20)]
    monkeypatch.setattr(triage_mod, "chunk_windows",
                        lambda *a, **k: windows)
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return {"score": 80, "hook": "h", "reason": "r"}

    out = triage_mod.score_video({"video_id": "v"}, None, llm)
    assert len(calls) <= 15
    assert len(out["windows"]) == 20


def test_lexical_prerank_prefers_question_window(monkeypatch):
    from scripts.auto_shorts import triage as triage_mod
    windows = [{"start": float(i), "end": float(i + 5),
                "text": f"filler words number {i} here today"}
               for i in range(19)]
    windows.append({"start": 100.0, "end": 105.0,
                    "text": "what is this? why does it happen? how?"})
    monkeypatch.setattr(triage_mod, "chunk_windows",
                        lambda *a, **k: windows)
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return {"score": 80, "hook": "h", "reason": "r"}

    out = triage_mod.score_video({"video_id": "v"}, None, llm)
    assert len(calls) == 15
    q = [w for w in out["windows"] if w["start"] == 100.0][0]
    assert q["score"] == 80  # ?-heavy window beat the filler for a slot
