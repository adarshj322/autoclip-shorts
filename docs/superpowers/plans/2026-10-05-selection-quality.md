# Selection-Quality Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transcript-density triage, deterministic boundary snapping, and tracked 9:16 reframing to the auto-shorts pipeline.

**Architecture:** Three new modules under `scripts/auto_shorts/` (triage, snap, crop) wired through `run.py`; new deps isolated in `scripts/auto_shorts/requirements.txt` so upstream `requirements.txt` is untouched. Each task ends with tested, committable code.

**Tech Stack:** Python 3.10+ stdlib + requests; faster-whisper word timestamps (already installed); new: `scenedetect`, `opencv-python-headless`, `mediapipe` (CPU).

**Spec:** `docs/superpowers/specs/2026-10-05-selection-quality-design.md`

## Global Constraints

- Triage sees subtitles + metadata only; zero video bytes before the winner.
- Aggregation is mean(top-3) × density with a ≥2-qualifying-clips gate; scores order, never threshold-gate publishing.
- Snap order is sentence → VAD silence (300–500ms) → PySceneDetect cut (±1s) → sub-60s tail clamp; boundary moves >2s → skip + log.
- Tracked crop runs at 480p, every 3rd frame + interpolate, largest-face + EMA (α 0.1–0.2), 200–400ms speaker dwell, single ffmpeg pass; two-speaker content uses SPLIT stacked layout first.
- Hook-in-first-3s is a binary publish veto; private-only flow unchanged.
- New runtime deps live in `scripts/auto_shorts/requirements.txt`, never in upstream `requirements.txt`.
- Keys never committed; code only under `scripts/auto_shorts/`.

## Review Focus

- A trending video with no caption track must be skipped-and-logged, never crash the batch — pinned in Task 1.
- An LLM window score above 90 must not auto-select anything by itself — pinned in Task 1 (aggregation test with bunched scores).
- A snap that moves a boundary >2s must skip, not silently ship a re-anchored clip — pinned in Task 2.
- A two-speaker video must get SPLIT layout before any tracking is attempted — pinned in Task 3.
- A clip failing the hook-in-3s check must not publish even with a top score — pinned in Task 4.

---

### Task 1: Triage ranking module

**Files:**
- Create: `scripts/auto_shorts/triage.py`
- Test: `scripts/auto_shorts/tests/test_triage.py`

**Interfaces:**
- Consumes: `discover.filter_candidates` output (video dicts with video_id/title/url); `config.Config` (max_per_day).
- Produces:
  - `triage.fetch_subtitles(video_id: str, work_dir: Path, langs: str = "zh-Hans,zh,en") -> Path | None` (yt-dlp `--write-auto-subs --skip-download`; None on unfetchable)
  - `triage.chunk_windows(srt_path: Path, win_sec: int = 45, overlap_sec: int = 10) -> list[dict]` ({start, end, text})
  - `triage.score_video(video: dict, srt_path: Path, llm_fn) -> dict` ({video_id, windows: [{score, hook, reason}], clippability: float})
  - `triage.pick_winner(scored: list[dict]) -> tuple[dict | None, dict | None]` (winner + backup by clippability with ≥2-clips gate; Nones when gate unmet)

- [ ] **Step 1: Write the failing test**

```python
def test_bunched_scores_pick_dense_video_not_peak():
    a = {"video_id": "a", "windows": [{"score": 95}, {"score": 40}, {"score": 30}]}
    b = {"video_id": "b", "windows": [{"score": 85}, {"score": 84}, {"score": 83}]}
    winner, backup = pick_winner([a, b])
    assert winner["video_id"] == "b"

def test_gate_rejects_single_peak():
    a = {"video_id": "a", "windows": [{"score": 95}]}
    assert pick_winner([a]) == (None, None)

def test_unfetchable_subtitles_returns_none(tmp_path):
    assert fetch_subtitles("novideo", tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_triage.py -v`
Expected: FAIL with import / not defined.

- [ ] **Step 3: Implement `triage.py` with the four signatures above**

`llm_fn` is injected `(prompt: str) -> dict` so tests never call a model; clippability is mean(top-3) × density (qualifying/10min). `fetch_subtitles` wraps yt-dlp via subprocess with an injectable runner like `download.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest scripts/auto_shorts/tests/ -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/triage.py scripts/auto_shorts/tests/test_triage.py
git commit -m "feat(auto-shorts): transcript-density triage ranking"
```

### Task 2: Boundary snap module

**Files:**
- Create: `scripts/auto_shorts/snap.py`
- Test: `scripts/auto_shorts/tests/test_snap.py`

**Interfaces:**
- Consumes: winner word-timestamped entries `[{word, start, end}]` (from faster-whisper JSON/honcho — Task 4 wires the producer; here accept plain lists).
- Produces:
  - `snap.snap_window(start: float, end: float, words: list[dict], scene_cuts: list[float] = []) -> tuple[float, float, float]` (new_start, new_end, max_move_sec)
  - `snap.scene_cuts(video: Path, start: float, end: float) -> list[float]` (PySceneDetect cut timestamps; [] on any failure — snap proceeds without scene anchoring)
  - `SNAP_MOVE_LIMIT_SEC = 2.0`
- Silence edges are inter-word gaps ≥300ms from the word list (no separate VAD call).

- [ ] **Step 1: Write the failing test**

```python
def test_snap_to_sentence_and_clamp():
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate("the cat sat on the mat".split())]
    ns, ne, moved = snap_window(0.1, 75.0, words, scene_cuts=[2.0])
    assert ne - ns <= 60.0
    assert moved >= 0

def test_big_move_flagged():
    words = [{"word": "hi", "start": 50.0, "end": 50.4}]
    ns, ne, moved = snap_window(0.0, 1.0, words)
    assert moved > 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_snap.py -v`
Expected: FAIL with import / not defined.

- [ ] **Step 3: Implement `snap.snap_window` in `scripts/auto_shorts/snap.py`**

Sentence boundary from word gaps, VAD edge approximated by largest inter-word gap in 300–500ms band, scene-cut snap within ±1s, tail clamp to 60s (never move the hook/start earlier... start stays, tail clamps). Pure functions, no ffmpeg calls (ffmpeg stays in export).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest scripts/auto_shorts/tests/ -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/snap.py scripts/auto_shorts/tests/test_snap.py
git commit -m "feat(auto-shorts): deterministic boundary snap chain"
```

### Task 3: Tracked crop + preset default

**Files:**
- Create: `scripts/auto_shorts/crop.py`
- Create: `scripts/auto_shorts/requirements.txt`
- Modify: `backend/services/publish_export.py` (`shorts` preset layout only)
- Test: `scripts/auto_shorts/tests/test_crop.py`

**Interfaces:**
- Consumes: source video path + clip (start, end) + speaker_count hint.
- Produces:
  - `crop.pick_layout(speaker_count: int) -> str` ("split" if ≥2 else "track")
  - `crop.track_boxes(video: Path, start: float, end: float, fps_sample: int = 3) -> list[dict]` ([{t, x, y, w, h}] smoothed boxes; empty list = fall back to blur)
  - `shorts` preset layout becomes tracked (ffmpeg crop expression built at export time from boxes)

- [ ] **Step 1: Write the failing test**

```python
def test_two_speaker_picks_split():
    assert pick_layout(2) == "split"
    assert pick_layout(1) == "track"

def test_empty_tracking_falls_back():
    assert track_boxes(Path("nonexistent.mp4"), 0.0, 1.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_crop.py -v`
Expected: FAIL with import / not defined.

- [ ] **Step 3: Implement `crop.py` (opencv/mediapipe, 480p, every-3rd-frame, EMA α 0.15) and flip the `shorts` preset**

Detection failures return [] (caller uses blur layout). Guard imports so unit tests run without cv2 installed (skip tracking tests if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest scripts/auto_shorts/tests/ -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/crop.py scripts/auto_shorts/requirements.txt scripts/auto_shorts/tests/test_crop.py backend/services/publish_export.py
git commit -m "feat(auto-shorts): tracked 9:16 crop with SPLIT-first layout"
```

### Task 4: Orchestration wiring + hook veto + docs

**Files:**
- Modify: `scripts/auto_shorts/run.py`
- Modify: `scripts/auto_shorts/setup_vps.sh` (install new requirements file)
- Modify: `scripts/auto_shorts/README.md`, `scripts/auto_shorts/env.example` (only if new knobs are added)
- Test: `scripts/auto_shorts/tests/test_run.py` (extend)

**Interfaces:**
- Consumes: Tasks 1–3 interfaces exactly as specified above.
- Produces: run order discover → triage (winner+backup) → download winner → snap → hook veto → export (tracked) → publish private; exit codes and JSON shape unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_hook_veto_skips_hookless_clip(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    # _mock_pipeline with a top clip whose first 3s are throat-clearing
    # (hook score 0 / hook text empty) → skipped with reason hook_failed
    ...
    assert rc == 1
    assert any(s["reason"].startswith("hook_") for s in summary["skipped"])
```

```python
def test_unfetchable_subtitles_skips_video(tmp_path, monkeypatch, capsys):
    import json
    from scripts.auto_shorts import run
    # _mock_pipeline with triage returning a video whose subtitles cannot
    # be fetched → skipped with reason no_subtitles, never downloaded
    ...
    assert rc == 1
    assert any(s["reason"] == "no_subtitles" for s in summary["skipped"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_run.py::test_hook_veto_skips_hookless_clip -v`
Expected: FAIL (no veto logic yet).

- [ ] **Step 3: Wire triage → download → snap → hook veto → export → publish in `run.py`**

Triage replaces first-come processing; snap runs after clip pick with >2s-move skip; hook check is binary on the first 3s (LLM hook field or hook-keyword presence); `setup_vps.sh` pip-installs `scripts/auto_shorts/requirements.txt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest scripts/auto_shorts/tests/ -q`
Expected: PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/run.py scripts/auto_shorts/setup_vps.sh scripts/auto_shorts/tests/test_run.py
git commit -m "feat(auto-shorts): wire triage, snap, hook veto into orchestrator"
```
