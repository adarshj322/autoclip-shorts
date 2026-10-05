"""Daily orchestrator for the auto-shorts VPS wrapper.

Cron entry point: load/validate config -> ``autoclip doctor`` preflight ->
5GB disk check -> discover -> triage (subtitle-only winner+backup pick) ->
per-candidate download/clip/snap/hook-veto/export/publish -> ledger save ->
cleanup -> single JSON summary on stdout.

Triage replaces first-come processing: subtitles are fetched for every
fresh candidate (unfetchable -> ``no_subtitles`` skip, never downloaded),
windows are scored, and ``pick_winner`` ranks by clippability. Only the
winner is downloaded; the backup is tried when the winner fails any later
stage. After ``autoclip run`` picks the top clip, the winner's best triage
window is boundary-snapped (``snap.snap_window``; move > 2s -> ``snap_moved``
skip, gate only) and hook-vetoed on its first 3s (LLM ``hook`` field or
hook-keyword presence; fail -> ``hook_failed`` skip). Export uses the
``shorts`` preset (tracked 9:16 via box-driven crop expression, static
fallback when untracked), publish stays private.

Exit codes: 0 ok / already_running, 1 partial (or preflight/runtime
failure), 2 config error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scripts.auto_shorts import clip as clip_mod
from scripts.auto_shorts import config as config_mod
from scripts.auto_shorts import discover as discover_mod
from scripts.auto_shorts import download as download_mod
from scripts.auto_shorts import export_publish as export_mod
from scripts.auto_shorts import ledger as ledger_mod
from scripts.auto_shorts import snap as snap_mod
from scripts.auto_shorts import triage as triage_mod

HOOK_WINDOW_SEC = 3.0
HOOK_KEYWORDS = frozenset({
    "why", "how", "secret", "shocking", "stop", "wait", "watch", "never",
    "free", "new", "win", "warning", "breaking", "truth", "mistake",
    "千万", "竟然", "震惊", "注意", "揭秘", "为什么", "如何", "免费",
    "警告", "真相", "千万别",
})

DISK_MIN_BYTES = 5 * 1024**3
DISCOVER_MAX_RESULTS = 20
LEDGER_NAME = "auto_shorts_ledger.json"
WORK_SUBDIR = "work"
LOCK_NAME = "auto_shorts.lock"


class _AlreadyRunning(RuntimeError):
    """Raised when another orchestrator run holds the lock."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-Shorts daily orchestrator")
    p.add_argument("--dry-run", action="store_true",
                   help="Run download/clip/export but stop before publish.")
    p.add_argument("--max-per-day", type=int, default=None,
                   help="Override AUTO_SH_MAX_PER_DAY.")
    p.add_argument("--keep-days", type=int, default=None,
                   help="Override AUTO_SH_KEEP_DAYS for raw-download cleanup.")
    p.add_argument("--workdir", default=None,
                   help="Base dir for ledger/work dirs (default: data_dir).")
    p.add_argument("--cookies-file", default=None,
                   help="Netscape cookies.txt for yt-dlp (overrides AUTO_SH_COOKIES_FILE).")
    return p.parse_args(argv)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _emit(summary: dict) -> None:
    print(json.dumps(summary))


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _acquire_lock(base: Path):
    """Non-blocking exclusive lock; raise _AlreadyRunning if held."""
    try:
        import fcntl
    except ImportError:
        return None
    fh = open(base / LOCK_NAME, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise _AlreadyRunning(f"lock held: {base / LOCK_NAME}")
    return fh


def _read_channel_list(path: Path) -> set[str] | None:
    """Channel names, one per line; None when the file does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line)
    return names


def _cleanup_old_dirs(work_root: Path, keep_days: int) -> None:
    """Remove per-video work dirs with mtime older than keep_days."""
    if not work_root.is_dir():
        return
    cutoff = time.time() - max(keep_days, 0) * 86400
    for child in work_root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _doctor_ok() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["autoclip", "doctor"],
            capture_output=True, text=True, check=False,
        )
    except OSError as e:
        return False, f"autoclip doctor preflight failed: {e}"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return False, f"autoclip doctor preflight failed: {err}"
    return True, ""


def _llm_fn(prompt: str) -> dict:
    """Score a transcript window via the DashScope OpenAI-compatible API.

    Stdlib urllib only; keys come from the existing ``API_DASHSCOPE_API_KEY``
    / ``API_MODEL_NAME`` passthrough env. Any failure returns {} so the
    window scores 0 and the triage gate skips the video instead of crashing
    the run. Non-dashscope ``LLM_PROVIDER`` values are not scored here.
    """
    import os
    import urllib.request

    if os.environ.get("LLM_PROVIDER", "") != "dashscope":
        return {}
    api_key = os.environ.get("API_DASHSCOPE_API_KEY", "")
    model = os.environ.get("API_MODEL_NAME", "qwen-plus")
    if not api_key:
        return {}
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        content = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content) if content else {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _words_from_srt(srt_path: Path | None, start: float, end: float) -> list[dict]:
    """Word-timestamped entries for ``[start, end)`` from SRT cues.

    Cue text is even-split across the cue span (coarse but deterministic;
    a faster-whisper word-timestamp upgrade can replace this producer).
    [] when there is no readable SRT — snapping then proceeds unanchored.
    """
    if srt_path is None:
        return []
    cues = [c for c in triage_mod._parse_cues(Path(srt_path))
            if c["end"] > start and c["start"] < end]
    words: list[dict] = []
    for cue in cues:
        tokens = str(cue.get("text", "")).split()
        if not tokens:
            continue
        span = max(cue["end"] - cue["start"], 0.0)
        for i, tok in enumerate(tokens):
            ws = cue["start"] + span * i / len(tokens)
            we = cue["start"] + span * (i + 1) / len(tokens)
            if we > start and ws < end:
                words.append({"word": tok, "start": ws, "end": we})
    return words


def _hook_pass(windows: list[dict], start: float) -> bool:
    """Binary hook veto on the first ``HOOK_WINDOW_SEC`` seconds.

    Pass when any overlapping window carries a non-empty LLM ``hook`` field
    or hook-keyword/question signal in its text. Falls back to the best
    window when none overlap; empty input fails closed.
    """
    if not windows:
        return False
    cands = [w for w in windows
             if float(w.get("end", 0)) > start
             and float(w.get("start", 0)) < start + HOOK_WINDOW_SEC]
    if not cands:
        cands = [max(windows, key=lambda w: float(w.get("score", 0) or 0))]
    for w in cands:
        if str(w.get("hook", "") or "").strip():
            return True
        text = str(w.get("text", "") or "")
        low = text.lower()
        if any(k in low for k in HOOK_KEYWORDS):
            return True
        if text.rstrip().endswith(("?", "!", "？", "！")):
            return True
    return False


def _best_window(winner: dict) -> tuple[float, float]:
    """Highest-scoring triage window ``(start, end)``; (0, 0) when none."""
    wins = winner.get("windows") or []
    if not wins:
        return (0.0, 0.0)
    top = max(wins, key=lambda w: float(w.get("score", 0) or 0))
    return (float(top.get("start", 0) or 0), float(top.get("end", 0) or 0))


def _skip(skipped: list[dict], data: dict, vid: str, reason: str,
          extra: dict | None = None) -> None:
    skipped.append({"video_id": vid, "reason": reason})
    entry = {"status": "skipped", "reason": reason,
             "timestamp": _now_iso()}
    if extra:
        entry.update(extra)
    ledger_mod.mark_result(data, vid, entry)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = config_mod.load_config()
    if args.max_per_day is not None:
        cfg.max_per_day = args.max_per_day
    if args.keep_days is not None:
        cfg.keep_days = args.keep_days
    base = Path(args.workdir).expanduser() if args.workdir else cfg.data_dir

    errors = config_mod.validate_config(cfg)
    if errors:
        _emit({"ok": False, "error": f"missing/invalid config: {', '.join(errors)}",
               "processed": [], "skipped": []})
        return 2

    cookies_file = (Path(args.cookies_file).expanduser()
                    if args.cookies_file else cfg.cookies_file)
    if cookies_file is not None and not cookies_file.is_file():
        _emit({"ok": False,
               "error": f"cookies file not readable: {cookies_file}",
               "processed": [], "skipped": []})
        return 2

    base.mkdir(parents=True, exist_ok=True)
    try:
        _lock = _acquire_lock(base)
    except _AlreadyRunning:
        _emit({"ok": True, "status": "already_running",
               "processed": [], "skipped": []})
        return 0

    ok, msg = _doctor_ok()
    if not ok:
        _emit({"ok": False, "error": msg, "processed": [], "skipped": []})
        return 1

    try:
        free = shutil.disk_usage(base).free
    except OSError as e:
        _emit({"ok": False, "error": f"disk check failed: {e}",
               "processed": [], "skipped": []})
        return 1
    if free < DISK_MIN_BYTES:
        _emit({"ok": False,
               "error": f"disk full: {free} bytes free, need {DISK_MIN_BYTES}",
               "processed": [], "skipped": []})
        return 1

    ledger_path = base / LEDGER_NAME
    try:
        data = ledger_mod.load_ledger(ledger_path)
    except (OSError, ValueError) as e:
        _emit({"ok": False, "error": f"ledger load failed: {e}",
               "processed": [], "skipped": []})
        return 1
    seen = set((data.get("seen") or {}).keys())

    try:
        candidates = discover_mod.discover_trending(
            cfg.yt_api_key, max_results=DISCOVER_MAX_RESULTS)
    except discover_mod.DiscoveryError as e:
        _emit({"ok": False, "error": str(e), "processed": [], "skipped": []})
        return 1

    allow = _read_channel_list(base / "allowlist_channels.txt")
    block = _read_channel_list(base / "blocklist.txt") or set()
    fresh = discover_mod.filter_candidates(candidates, seen, allow, block)
    todo = fresh[: max(cfg.max_per_day, 0)]

    processed: list[dict] = []
    skipped: list[dict] = []
    work_root = base / WORK_SUBDIR
    by_id = {v.get("video_id", ""): v for v in todo}

    # Phase 1: subtitle-only triage over every candidate. Unfetchable
    # subtitles skip the video before anything is downloaded.
    scored: list[dict] = []
    triage_srts: dict[str, Path] = {}
    for video in todo:
        vid = video.get("video_id", "")
        work_dir = work_root / vid
        _log(f"triaging {vid}: {video.get('title', '')}")
        srt = triage_mod.fetch_subtitles(vid, work_dir)
        if srt is None:
            _skip(skipped, data, vid, "no_subtitles")
            continue
        triage_srts[vid] = Path(srt)
        scored.append(triage_mod.score_video(video, Path(srt), _llm_fn))
    winner, backup = triage_mod.pick_winner(scored)
    if winner is None:
        for video in todo:
            vid = video.get("video_id", "")
            if vid not in triage_srts:
                continue  # already recorded as no_subtitles above
            _skip(skipped, data, vid, "no_qualifying_clips")
    candidates = [c for c in (winner, backup) if c is not None]

    for cand in candidates:
        vid = cand.get("video_id", "")
        video = by_id.get(vid, {"video_id": vid})
        url = video.get("url") or f"https://www.youtube.com/watch?v={vid}"
        work_dir = work_root / vid
        _log(f"processing {vid}: {video.get('title', '')}")

        try:
            dl = download_mod.download_video(url, work_dir,
                                             cookies_file=cookies_file)
        except (download_mod.DownloadSkipped, download_mod.DownloadError) as e:
            _skip(skipped, data, vid, str(e))
            continue  # backup (if any) is tried next

        video_path = Path(dl["video_path"])
        srt_path = Path(dl["srt_path"]) if dl.get("srt_path") else None
        try:
            parsed = clip_mod.run_and_parse(video_path, srt_path)
            top = clip_mod.pick_top_clip(parsed.get("clips") or [])
            if top is None:
                parsed = clip_mod.run_and_parse(
                    video_path, srt_path, clip_mod.RETRY_MIN_SCORE)
                top = clip_mod.pick_top_clip(parsed.get("clips") or [])
        except RuntimeError as e:
            _skip(skipped, data, vid, f"run_failed: {e}")
            continue
        if top is None:
            _skip(skipped, data, vid, "no_clips")
            continue

        # Boundary snap on the winner's best triage window (gate only:
        # moves beyond SNAP_MOVE_LIMIT_SEC skip the video).
        ws, we = _best_window(cand)
        snap_srt = srt_path or triage_srts.get(vid)
        words = _words_from_srt(snap_srt, ws, we)
        try:
            cuts = snap_mod.scene_cuts(video_path, ws, we)
        except Exception:
            cuts = []
        ns, ne, moved = snap_mod.snap_window(ws, we, words, cuts)
        if moved > snap_mod.SNAP_MOVE_LIMIT_SEC:
            _skip(skipped, data, vid,
                  f"snap_moved: {moved:.1f}s > {snap_mod.SNAP_MOVE_LIMIT_SEC:.1f}s")
            continue

        # Hook veto on the first 3s of the snapped window.
        if not _hook_pass(cand.get("windows") or [], ns):
            _skip(skipped, data, vid, "hook_failed: no hook in first 3s")
            continue
        _log(f"snapped {vid}: [{ws:.1f}, {we:.1f}] -> [{ns:.1f}, {ne:.1f}] hook ok")

        project_id = parsed.get("project_id")
        clip_id = top.get("id")
        if not project_id or not clip_id:
            reason = (f"export_failed: missing project_id/clip_id "
                      f"(project_id={project_id!r}, clip_id={clip_id!r})")
            _skip(skipped, data, vid, reason)
            continue
        try:
            export_mod.run_and_parse(
                export_mod.build_export_cmd(project_id, clip_id))
        except (RuntimeError, export_mod.PublishSkipped) as e:
            _skip(skipped, data, vid, f"export_failed: {e}",
                  {"project_id": project_id})
            continue

        if args.dry_run:
            processed.append({"video_id": vid, "project_id": project_id,
                              "request_id": None, "status": "dry_run"})
            # NB: dry-run must not touch the ledger, otherwise a later
            # real run would treat this video as already seen and skip it.
            break  # winner done; backup stays fresh for the next run

        clip_title = export_mod.truncate_youtube_title(
            top.get("title") or video.get("title", ""))
        description = export_mod.build_description(
            top.get("title") or video.get("title", ""), url)
        try:
            pub = export_mod.run_and_parse(
                export_mod.build_publish_cmd(project_id, clip_id,
                                             privacy=cfg.privacy,
                                             title=clip_title,
                                             description=description))
        except export_mod.PublishSkipped as e:
            _skip(skipped, data, vid, f"publish_skipped: {e}",
                  {"project_id": project_id})
            continue
        except RuntimeError as e:
            _skip(skipped, data, vid, f"publish_failed: {e}",
                  {"project_id": project_id})
            continue

        request_id = pub.get("request_id")
        processed.append({"video_id": vid, "project_id": project_id,
                          "request_id": request_id, "status": "published"})
        ledger_mod.mark_result(data, vid, {"status": "published",
                                          "project_id": project_id,
                                          "request_id": request_id,
                                          "title": video.get("title", ""),
                                          "url": url,
                                          "timestamp": _now_iso()})
        break  # winner published; backup is fallback only, not extra output

    try:
        ledger_mod.save_ledger(ledger_path, data)
    except OSError as e:
        _emit({"ok": False, "error": f"ledger save failed: {e}",
               "processed": processed, "skipped": skipped})
        return 1

    _cleanup_old_dirs(work_root, cfg.keep_days)

    exit_code = 0 if not skipped else 1
    _emit({"ok": exit_code == 0, "dry_run": bool(args.dry_run),
           "processed": processed, "skipped": skipped})
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
