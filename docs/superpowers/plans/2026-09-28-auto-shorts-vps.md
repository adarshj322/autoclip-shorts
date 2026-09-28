# Auto Shorts VPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/auto_shorts/` cron wrapper that turns latest AI long videos into private YouTube Shorts via existing AutoClip CLI.

**Architecture:** Single venv script called by cron with flock; YT Data API discovery, yt-dlp download, `autoclip run/export/publish` subprocess wrappers, JSON ledger dedupe. No core pipeline edits, no new runtime deps.

**Tech Stack:** Python 3.10+ stdlib + `requests==2.34.2` (already pinned), `yt-dlp==2026.8.19`, existing `backend/cli.py`, `backend/services/publish_export.py`, `backend/services/upload_post_publisher.py`.

**Spec:** `docs/superpowers/specs/2026-09-28-auto-shorts-vps-design.md`

## Global Constraints

- Long = duration >900s (15min).
- Shorts preset = `shorts`: 1080x1920 crop, max 60s truncate.
- Publish privacy default = `private` (`privacyStatus=private`); no public auto-publish in v1.
- Subtitle langs default = `zh-Hans,zh,en` via `AUTOCLIP_YT_SUBTITLE_LANGS`.
- Clip threshold = `--min-score 0.5`, one retry at `0.4`, then skip.
- Max per day = 3 (`AUTO_SH_MAX_PER_DAY=3`).
- Retain work dirs = 7 days; abort if free disk <5GB.
- Upload poll = 10s interval, 600s timeout.
- Exit codes: 0 ok/already_running, 1 partial failure, 2 config error.
- Stdout = single JSON summary; progress to stderr (CLI convention).
- No new dependencies; keys never committed; new code only under `scripts/auto_shorts/`.
- Follow `scripts/verify_live_publish.py` pattern: `ROOT` sys.path insert, `_die`/`_need` style, `requests` for HTTP.

## Review Focus

- YT Data API quota exhausted (403 quotaExceeded) mid-cron should abort cleanly with actionable message, not half-publish — pinned in Task 2.
- yt-dlp 429 / SABR bot-check on VPS datacenter IP should skip video, not kill whole run — pinned in Task 3.
- Video with 0 qualifying clips should be skipped with scores logged, not exported — pinned in Task 4.
- Upload-Post profile not connected to youtube (`skipped=true`) should surface as config error, not silent success — pinned in Task 5.
- Re-running same day must not re-download/re-publish same video_id (ledger atomicity) — pinned in Task 1 + Task 6.

---

### Task 1: Config + ledger foundation

**Files:**
- Create: `scripts/auto_shorts/__init__.py`
- Create: `scripts/auto_shorts/config.py`
- Create: `scripts/auto_shorts/ledger.py`
- Test: `scripts/auto_shorts/tests/test_config_ledger.py`

**Interfaces:**
- Consumes: os.environ (spec section 8 names).
- Produces:
  - `config.Config dataclass -> {yt_api_key, llm_provider, upload_post_key, upload_post_user, data_dir: Path, max_per_day: int=3, privacy: str="private", keep_days: int=7}`
  - `config.load_config(env: dict | None = None) -> Config`
  - `config.validate_config(cfg: Config) -> list[str]` (missing-key messages; empty = ok)
  - `ledger.load_ledger(path: Path) -> dict`
  - `ledger.save_ledger(path: Path, data: dict) -> None` (atomic tmp+rename)
  - `ledger.is_seen(data: dict, video_id: str) -> bool`
  - `ledger.mark_result(data: dict, video_id: str, entry: dict) -> dict`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_config_ledger.py -v`
Expected: FAIL with import / not defined.

- [ ] **Step 3: Implement `load_config/validate_config/load_ledger/save_ledger/is_seen/mark_result` in `scripts/auto_shorts/config.py` + `ledger.py`**

Env names verbatim from spec section 8; `privacy` must be `private|unlisted`, `max_per_day` int default 3. Ledger shape `{"seen": {video_id: entry}}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/auto_shorts/tests/test_config_ledger.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/__init__.py scripts/auto_shorts/config.py scripts/auto_shorts/ledger.py scripts/auto_shorts/tests/test_config_ledger.py
git commit -m "feat(auto-shorts): config and ledger foundation"
```

### Task 2: Trending discovery (YT Data API v3)

**Files:**
- Create: `scripts/auto_shorts/discover.py`
- Test: `scripts/auto_shorts/tests/test_discover.py`

**Interfaces:**
- Consumes: `config.Config.yt_api_key`, `ledger` seen set.
- Produces:
  - `discover.parse_duration_iso8601(s: str) -> int` (seconds)
  - `discover.filter_candidates(videos: list[dict], seen: set[str], allow: set[str] | None, block: set[str], min_sec: int = 900) -> list[dict]`
  - `discover.discover_trending(api_key: str, query: str = "AI artificial intelligence", days: int = 7, max_results: int = 20, http_get=None) -> list[dict]` returns `[{video_id, title, channel, duration_sec, view_count, url}]` sorted by view_count desc.

- [ ] **Step 1: Write the failing test**

```python
def test_filter_and_iso8601():
    from scripts.auto_shorts.discover import parse_duration_iso8601, filter_candidates
    assert parse_duration_iso8601("PT16M30S") == 990
    vids = [{"video_id": "a", "duration_sec": 2000, "view_count": 5, "channel": "c"},
            {"video_id": "b", "duration_sec": 100, "view_count": 999, "channel": "c"}]
    out = filter_candidates(vids, seen={"a"}, allow=None, block=set())
    assert [v["video_id"] for v in out] == []

def test_quota_error_surfaces():
    from scripts.auto_shorts import discover
    def boom(*a, **k): raise discover.DiscoveryError("quotaExceeded")
    try:
        discover.discover_trending("k", http_get=boom)
        assert False
    except discover.DiscoveryError as e:
        assert "quota" in str(e).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_discover.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `discover.py` with injectable `http_get`**

Use `search.list(type=video, videoDuration=long, publishedAfter, order=viewCount)` then `videos.list(part=contentDetails,statistics,snippet)`; raise `DiscoveryError` on 403/quota with actionable text. No `google-api-client` dep — plain `requests`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/auto_shorts/tests/test_discover.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/discover.py scripts/auto_shorts/tests/test_discover.py
git commit -m "feat(auto-shorts): trending discovery with filter"
```

### Task 3: Downloader (yt-dlp wrapper)

**Files:**
- Create: `scripts/auto_shorts/download.py`
- Test: `scripts/auto_shorts/tests/test_download.py`

**Interfaces:**
- Consumes: video url, work dir.
- Produces:
  - `download.build_ytdlp_cmd(url: str, out_dir: Path, langs: str = "zh-Hans,zh,en", cookies_file: Path | None = None) -> list[str]`
  - `download.download_video(url: str, work_dir: Path, runner=None) -> dict` returns `{"video_path": str, "srt_path": str | None}`; raises `DownloadSkipped` on 429/bot-check after retries.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_download.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `download.py`**

Format verbatim: `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best` + `--write-sub --write-auto-sub --sub-langs <langs> --convert-subs srt`. Backoff x3, then one try with `--extractor-args youtube:player_client=android` if `AUTOCLIP_YT_CLIENT` unset, else skip.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/auto_shorts/tests/test_download.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/download.py scripts/auto_shorts/tests/test_download.py
git commit -m "feat(auto-shorts): yt-dlp download wrapper"
```

### Task 4: Clip runner + top-clip pick

**Files:**
- Create: `scripts/auto_shorts/clip.py`
- Test: `scripts/auto_shorts/tests/test_clip.py`

**Interfaces:**
- Consumes: `download` outputs.
- Produces:
  - `clip.build_run_cmd(video: Path, srt: Path | None, min_score: float = 0.5) -> list[str]`
  - `clip.parse_run_json(payload: dict) -> dict` returns `{project_id, clips: [{id, title, score_100}]}`
  - `clip.pick_top_clip(clips: list[dict]) -> dict | None` (max score_100)

- [ ] **Step 1: Write the failing test**

```python
def test_pick_top_and_parse():
    from scripts.auto_shorts.clip import pick_top_clip, parse_run_json
    assert pick_top_clip([]) is None
    assert pick_top_clip([{"id": "1", "score_100": 60}, {"id": "2", "score_100": 91}])["id"] == "2"
    d = parse_run_json({"project_id": "p", "clips": [{"id": "2", "score_100": 91, "title": "t"}]})
    assert d["project_id"] == "p"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_clip.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `clip.py`**

Cmd: `autoclip run <video> [--srt] --min-score <v> --json`. `parse` tolerates missing keys; `pick` returns None on empty (caller retries 0.4 once then skips).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/auto_shorts/tests/test_clip.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/clip.py scripts/auto_shorts/tests/test_clip.py
git commit -m "feat(auto-shorts): clip pick wrapper"
```

### Task 5: Export + publish wrappers

**Files:**
- Create: `scripts/auto_shorts/export_publish.py`
- Test: `scripts/auto_shorts/tests/test_export_publish.py`

**Interfaces:**
- Consumes: `clip.pick_top_clip` output.
- Produces:
  - `export_publish.truncate_youtube_title(t: str, limit: int = 100) -> str`
  - `export_publish.build_description(clip_title: str, source_url: str) -> str` (credit line)
  - `export_publish.build_export_cmd(project_id: str, clip_id: str, preset: str = "shorts") -> list[str]`
  - `export_publish.build_publish_cmd(project_id: str, clip_id: str, privacy: str = "private") -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
def test_title_privacy_cmds():
    from scripts.auto_shorts.export_publish import truncate_youtube_title, build_publish_cmd, build_export_cmd
    assert len(truncate_youtube_title("x" * 150)) <= 100
    assert "privacyStatus=private" in " ".join(build_publish_cmd("p", "2", "private"))
    assert "shorts" in build_export_cmd("p", "2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_export_publish.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `export_publish.py`**

Export: `autoclip export <pid> --clip <id> --preset shorts`. Publish: `autoclip publish <pid> --clip <id> --platform youtube --extra privacyStatus=<privacy> --wait --json`. Description appends `Source: <url>`. Detect `skipped=true` in publish JSON and raise `PublishSkipped`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/auto_shorts/tests/test_export_publish.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/export_publish.py scripts/auto_shorts/tests/test_export_publish.py
git commit -m "feat(auto-shorts): export publish wrappers"
```

### Task 6: Orchestrator + cron docs

**Files:**
- Create: `scripts/auto_shorts/run.py`
- Create: `scripts/auto_shorts/README.md`
- Create: `scripts/auto_shorts/cron.example`
- Create: `scripts/auto_shorts/env.example`
- Test: `scripts/auto_shorts/tests/test_run.py`

**Interfaces:**
- Consumes: all Tasks 1-5.
- Produces: `run.main(argv: list[str] | None = None) -> int`; flags `--dry-run --max-per-day --keep-days --workdir`. Stdout JSON summary `{ok, processed: [{video_id, project_id, request_id, status}], skipped: [...]}`.

- [ ] **Step 1: Write the failing test**

```python
def test_dry_run_and_idempotent(tmp_path, monkeypatch):
    from scripts.auto_shorts import run
    argv = ["--dry-run", "--max-per-day", "1", "--workdir", str(tmp_path)]
    assert run.main(argv) in (0, 1, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/auto_shorts/tests/test_run.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `run.py`**

Order: load/validate config (exit 2 on missing) -> `autoclip doctor` preflight -> disk check 5GB -> discover -> ledger dedupe -> per-video download/run(pick, retry 0.4)/export/publish -> ledger save -> cleanup >keep-days -> JSON summary. `--dry-run` stops before publish. README documents cron `flock -n`, systemd alternative, private→public manual step in YouTube Studio, reused-content warning.

- [ ] **Step 4: Run all tests**

Run: `pytest scripts/auto_shorts/tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_shorts/run.py scripts/auto_shorts/README.md scripts/auto_shorts/cron.example scripts/auto_shorts/env.example scripts/auto_shorts/tests/test_run.py
git commit -m "feat(auto-shorts): orchestrator and cron docs"
```
