"""Clip runner wrapper for the auto-shorts VPS pipeline.

Stdlib + subprocess only. Wraps ``autoclip run <video> [--srt] --min-score <v>
--json`` and picks the top-scoring clip. The orchestrator (Task 6) runs the
command, feeds stdout JSON through :func:`parse_run_json`, then
:func:`pick_top_clip`; on ``None`` it retries once with ``min_score=0.4``
and skips the video if still empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_MIN_SCORE = 0.5
RETRY_MIN_SCORE = 0.4


def build_run_cmd(
    video: Path,
    srt: Path | None,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[str]:
    """Build ``autoclip run <video> [--srt <srt>] --min-score <v> --json``."""
    cmd = ["autoclip", "run", str(video)]
    if srt is not None:
        cmd += ["--srt", str(srt)]
    cmd += ["--min-score", str(min_score), "--json"]
    return cmd


def parse_run_json(payload: dict) -> dict:
    """Normalize ``autoclip run --json`` output.

    Tolerates missing keys: ``project_id`` defaults to ``None`` and
    ``clips`` defaults to ``[]``; per-clip ``title``/``score_100`` fall
    back to ``""``/``0``.
    """
    payload = payload or {}
    clips = []
    for c in payload.get("clips") or []:
        if not isinstance(c, dict):
            continue
        clips.append(
            {
                "id": c.get("id"),
                "title": c.get("title", ""),
                "score_100": c.get("score_100", 0),
            }
        )
    return {"project_id": payload.get("project_id"), "clips": clips}


def pick_top_clip(clips: list[dict]) -> dict | None:
    """Return the clip with the highest ``score_100``, or ``None`` if empty."""
    if not clips:
        return None
    return max(clips, key=lambda c: (c.get("score_100") or 0))


def run_and_parse(video: Path, srt: Path | None, min_score: float = DEFAULT_MIN_SCORE, runner=None) -> dict:
    """Run ``autoclip run --json`` via ``runner`` and parse stdout JSON.

    ``runner(cmd)`` must return an object with ``returncode``/``stdout``/
    ``stderr`` (defaults to :func:`subprocess.run`). Raises
    ``RuntimeError`` on non-zero exit. JSON decoding needs ``json`` --
    imported lazily to keep module import side-effect free.
    """
    import json

    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, check=False))
    cmd = build_run_cmd(video, srt, min_score)
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"autoclip run failed for {video}: {(getattr(result, 'stderr', '') or '').strip()}"
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"autoclip run produced invalid JSON for {video}: {e}"
        ) from e
    return parse_run_json(payload)
