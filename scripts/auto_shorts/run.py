"""Daily orchestrator for the auto-shorts VPS wrapper.

Cron entry point: load/validate config -> ``autoclip doctor`` preflight ->
5GB disk check -> discover -> ledger dedupe -> per-video
download/run(retry 0.4)/export/publish -> ledger save -> cleanup ->
single JSON summary on stdout.

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

    for video in todo:
        vid = video.get("video_id", "")
        url = video.get("url") or f"https://www.youtube.com/watch?v={vid}"
        work_dir = work_root / vid
        _log(f"processing {vid}: {video.get('title', '')}")

        try:
            dl = download_mod.download_video(url, work_dir,
                                             cookies_file=cookies_file)
        except (download_mod.DownloadSkipped, download_mod.DownloadError) as e:
            skipped.append({"video_id": vid, "reason": str(e)})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": str(e), "timestamp": _now_iso()})
            continue

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
            skipped.append({"video_id": vid, "reason": f"run_failed: {e}"})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": f"run_failed: {e}",
                                              "timestamp": _now_iso()})
            continue
        if top is None:
            reason = "no_clips"
            skipped.append({"video_id": vid, "reason": reason})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": reason,
                                              "timestamp": _now_iso()})
            continue

        project_id = parsed.get("project_id")
        clip_id = top.get("id")
        if not project_id or not clip_id:
            reason = (f"export_failed: missing project_id/clip_id "
                      f"(project_id={project_id!r}, clip_id={clip_id!r})")
            skipped.append({"video_id": vid, "reason": reason})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": reason,
                                              "timestamp": _now_iso()})
            continue
        try:
            export_mod.run_and_parse(
                export_mod.build_export_cmd(project_id, clip_id))
        except (RuntimeError, export_mod.PublishSkipped) as e:
            skipped.append({"video_id": vid, "reason": f"export_failed: {e}"})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": f"export_failed: {e}",
                                              "project_id": project_id,
                                              "timestamp": _now_iso()})
            continue

        if args.dry_run:
            processed.append({"video_id": vid, "project_id": project_id,
                              "request_id": None, "status": "dry_run"})
            # NB: dry-run must not touch the ledger, otherwise a later
            # real run would treat this video as already seen and skip it.
            continue

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
            skipped.append({"video_id": vid, "reason": f"publish_skipped: {e}"})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": f"publish_skipped: {e}",
                                              "project_id": project_id,
                                              "timestamp": _now_iso()})
            continue
        except RuntimeError as e:
            skipped.append({"video_id": vid, "reason": f"publish_failed: {e}"})
            ledger_mod.mark_result(data, vid, {"status": "skipped",
                                              "reason": f"publish_failed: {e}",
                                              "project_id": project_id,
                                              "timestamp": _now_iso()})
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
