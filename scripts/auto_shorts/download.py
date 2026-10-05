"""yt-dlp download wrapper for the auto-shorts VPS pipeline.

Stdlib + subprocess only. ``runner`` is injectable for tests:
``runner(cmd)`` must return an object with ``returncode``, ``stdout`` and
``stderr`` (e.g. ``subprocess.CompletedProcess``).

Rate-limit / bot-check output (HTTP 429 or a "sign in to confirm you're
not a bot" style message) is retried with backoff x3; then one final try
with ``--extractor-args youtube:player_client=android`` is made when the
``AUTOCLIP_YT_CLIENT`` env var is unset, otherwise the video is skipped
(``DownloadSkipped``).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from scripts.auto_shorts._util import run as _default_runner

FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
ANDROID_EXTRACTOR_ARGS = "youtube:player_client=android"
_BACKOFF_SECS = (1.0, 2.0, 4.0)


class DownloadSkipped(RuntimeError):
    """Raised when a video is skipped after 429/bot-check retries."""


class DownloadError(RuntimeError):
    """Raised when yt-dlp fails for a non-rate-limit reason."""


def build_ytdlp_cmd(
    url: str,
    out_dir: Path,
    langs: str = "zh-Hans,zh,en",
    cookies_file: Path | None = None,
) -> list[str]:
    cmd = [
        "yt-dlp",
        "-f",
        FORMAT,
        "--write-sub",
        "--write-auto-sub",
        "--sub-langs",
        langs,
        "--convert-subs",
        "srt",
        "-o",
        str(Path(out_dir) / "%(id)s.%(ext)s"),
    ]
    if cookies_file is not None:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(url)
    return cmd


def _is_rate_limited(result) -> bool:
    text = f"{getattr(result, 'stdout', '') or ''}\n{getattr(result, 'stderr', '') or ''}"
    if "429" in text:
        return True
    low = text.lower()
    return (
        "sign in to confirm" in low
        or "not a bot" in low
        or "bot check" in low
        or "po token" in low
    )


def _find_outputs(work_dir: Path) -> dict:
    videos = sorted(
        Path(work_dir).glob("*.mp4"),
        key=lambda p: (p.stat().st_size, p.name),
        reverse=True,
    )
    if not videos:
        raise DownloadError(f"yt-dlp succeeded but no .mp4 found in {work_dir}")
    srts = sorted(Path(work_dir).glob("*.srt"), key=lambda p: p.name)
    return {
        "video_path": str(videos[0]),
        "srt_path": str(srts[0]) if srts else None,
    }


def download_video(url: str, work_dir: Path, runner=None,
                   cookies_file: Path | None = None) -> dict:
    """Download ``url`` into ``work_dir``; return ``{"video_path", "srt_path"}``."""
    run = runner or _default_runner
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    cmd = build_ytdlp_cmd(url, Path(work_dir), cookies_file=cookies_file)
    last = None
    for wait in _BACKOFF_SECS:
        last = run(cmd)
        if last.returncode == 0:
            return _find_outputs(Path(work_dir))
        if not _is_rate_limited(last):
            raise DownloadError(
                f"yt-dlp failed for {url}: {getattr(last, 'stderr', '') or ''}".strip()
            )
        time.sleep(wait)

    # Backoff exhausted on 429/bot-check: one android-client try, unless the
    # caller pinned a client via AUTOCLIP_YT_CLIENT (then skip directly).
    if os.environ.get("AUTOCLIP_YT_CLIENT"):
        raise DownloadSkipped(f"429/bot-check for {url} (custom YT client set, skipping)")
    last = run(cmd + ["--extractor-args", ANDROID_EXTRACTOR_ARGS])
    if last.returncode == 0:
        return _find_outputs(Path(work_dir))
    if _is_rate_limited(last):
        raise DownloadSkipped(f"429/bot-check for {url} after retries")
    raise DownloadError(
        f"yt-dlp android-client retry failed for {url}: "
        f"{getattr(last, 'stderr', '') or ''}".strip()
    )
