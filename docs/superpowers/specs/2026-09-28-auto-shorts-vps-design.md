# Auto Shorts VPS Automation — Design

Date: 2026-09-28
Path: Architectural (new `scripts/auto_shorts/` wrapper, no changes to core pipeline)
Status: pending user review

## 1. Intent

Automated daily job on Linux VPS:
find latest AI trending long videos -> cut 1 Short via AutoClip -> private/unlisted upload to user's YouTube.

Constraints agreed:
- Discovery: YouTube Data API v3 primary + allowlist, yt-dlp search fallback only.
- Upload: Upload-Post (`autoclip publish --platform youtube`), private/unlisted only.
- Runtime: venv + CLI + cron/systemd timer (no Docker/Redis).
- Safety: auto private/unlisted, manual flip to public. Source credit required.

Success: cron runs unattended, dedupes, logs per step, 1 video -> 1 private Short end-to-end.

Non-goals: direct YouTube API upload, public auto-publish, license laundering, web UI.

## 2. Architecture

Single wrapper `scripts/auto_shorts/` invoked by cron daily with `flock -n`:

```
cron -> auto_shorts.py --config env
  -> discover (YT Data API v3)
  -> for each new video_id (max 3/day):
       yt-dlp download mp4+srt -> autoclip run --json
       -> pick top clip -> autoclip export --preset shorts
       -> autoclip publish --platform youtube --extra privacyStatus=private --wait
  -> ledger update + cleanup + log
```

Reuses: `backend/cli.py` run/export/publish, `publish_export.py:PRESETS["shorts"]` (1080x1920 crop, 60s cap), `upload_post_publisher.py:publish_clip`.

No core pipeline changes. New code isolated to `scripts/auto_shorts/`.

## 3. Components

- `discover.py`: `search.list(q="AI artificial intelligence", type=video, videoDuration=long, publishedAfter=7d, order=viewCount, maxResults=20)` then `videos.list` for duration/contentDetails/statistics + `captions.list` existence. Filter: duration >15min, language en (or zh per config), viewCount threshold, not in ledger/allowlist-blocklist. Output ranked list.
- `download.py`: `yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" --write-sub --write-auto-sub --sub-langs zh-Hans,zh,en --convert-subs srt`. Respects `AUTOCLIP_YT_SUBTITLE_LANGS`, `AUTOCLIP_YT_CLIENT`, optional `--cookies-from-file`.
- `clip.py`: thin wrapper around `autoclip run <mp4> [--srt] --min-score 0.5 --json --provider <configured>`. Parses `project_id`, `clips[]`. Picks max `score_100`. If 0 clips, retry once at 0.4 then skip.
- `export_publish.py`: `autoclip export <pid> --clip <id> --preset shorts` then `autoclip publish <pid> --clip <id> --platform youtube --extra privacyStatus=private --wait --json`. Title defaults to clip title (truncated to 100 for youtube_title), description appends `Source: <url>`.
- `ledger.py`: `data/auto_shorts_ledger.json` (or sqlite): `seen_video_ids`, `project_ids`, `request_ids`, timestamps. Atomic write.
- `run.py / auto_shorts.py`: orchestrator, `--dry-run` (stop before publish), `--max-per-day 3`, `--keep-days 7` cleanup of raw downloads.

## 4. Data flow

1. Load env: `YT_API_KEY`, `LLM_PROVIDER` + `API_*`, `UPLOAD_POST_API_KEY/USER`, `AUTOCLIP_DATA_DIR`.
2. `autoclip doctor` preflight (ffmpeg + llm ok else abort).
3. Discover -> dedupe -> top N.
4. Per video: download to `work/<video_id>/`, run, export, publish, record.
5. Exit 0 all private-uploaded, 1 partial (log which step failed), 2 config error.

Stdout: single JSON summary for cron logs. Progress/stderr per CLI convention.

## 5. Error handling

- YT API quota/403: abort with message, keep ledger, next cron retries. Cache discovery 24h.
- yt-dlp 429/SABR: exponential backoff x3, then try `AUTOCLIP_YT_CLIENT=android`, then skip video (do not block queue).
- No SRT + Whisper fail: skip (log `subtitle_error`), do not publish silent clip.
- 0 clips: log scores, retry min-score 0.4 once, else skip.
- Export ffmpeg fail / missing CJK font: publish with `--no-title` fallback once, else fail clip.
- Upload-Post fail/skipped/rate-limit: `wait_for_status` 10s poll, 600s timeout; record `request_id`, leave private retry for next run.
- Lock contention (`flock` held): exit 0 with `already_running` (not failure).
- Disk full: pre-check `shutil.disk_usage`, abort if <5GB.

## 6. Testing + ops

- `pytest scripts/auto_shorts/tests/` (no network): ledger dedupe, score pick, title truncation, form building, dry-run orchestration with mocked CLI.
- Live: `--dry-run` on 1 known video (e.g. Stanford Energy sample in README), then 1 real private upload via `verify_live_publish.py` pattern.
- Cron: `0 9 * * * flock -n /tmp/auto_shorts.lock /path/venv/bin/python -m scripts.auto_shorts.run >> logs/auto_shorts.log 2>&1`. Systemd timer alternative. Log rotation. `tar` backup of ledger + `.env` (never commit keys).
- Monitoring: exit codes + JSON summary; optional health ping.

## 7. Guardrails (legal/ToS)

- Default `privacyStatus=private` (or unlisted). No public auto-publish in v1.
- `allowlist_channels.txt` recommended; `blocklist.txt` for takedowns. Only process videos with captions / permissive intent; always credit source URL in description.
- User owns copyright risk. Document warns about reused-content policy. Provide `--allow-public` behind explicit flag only (not default).

## 8. Env example

```dotenv
YT_API_KEY=
LLM_PROVIDER=dashscope
API_DASHSCOPE_API_KEY=
API_MODEL_NAME=qwen-plus
UPLOAD_POST_API_KEY=
UPLOAD_POST_USER=main
AUTOCLIP_DATA_DIR=~/.local/share/AutoClip
AUTOCLIP_YT_SUBTITLE_LANGS=zh-Hans,zh,en
AUTO_SH_MAX_PER_DAY=3
AUTO_SH_PRIVACY=private
```

## 9. Self-review

- No TBDs. Scope is single wrapper + cron, no core edits — fits one plan.
- Consistent: private-only matches safety choice; venv+cron matches runtime choice; Data API primary matches VPS stability.
- Ambiguity resolved: long = >15min; Shorts = `shorts` preset (crop, 60s truncate); publish waits for terminal status.
