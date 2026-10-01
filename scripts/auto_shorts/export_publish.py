"""Export + publish wrappers for the auto-shorts VPS pipeline.

Stdlib + subprocess only. Wraps ``autoclip export`` / ``autoclip publish``
for the top clip picked by :func:`clip.pick_top_clip`. The orchestrator
(Task 6) exports with the ``shorts`` preset, then publishes to YouTube as
private and detects ``skipped=true`` in the publish JSON.
"""

from __future__ import annotations

import subprocess

EXPORT_PRESET = "shorts"
YOUTUBE_TITLE_LIMIT = 100


class PublishSkipped(Exception):
    """Raised when ``autoclip publish --json`` reports ``skipped=true``."""


def truncate_youtube_title(t: str, limit: int = YOUTUBE_TITLE_LIMIT) -> str:
    """Truncate ``t`` to at most ``limit`` chars (YouTube title cap)."""
    return t[:limit]


def build_description(clip_title: str, source_url: str) -> str:
    """Build the YouTube description with a ``Source: <url>`` credit line."""
    return f"{clip_title}\n\nSource: {source_url}"


def build_export_cmd(
    project_id: str,
    clip_id: str,
    preset: str = EXPORT_PRESET,
) -> list[str]:
    """Build ``autoclip export <pid> --clip <id> --preset shorts``."""
    return ["autoclip", "export", project_id, "--clip", clip_id, "--preset", preset]


def build_publish_cmd(
    project_id: str,
    clip_id: str,
    privacy: str = "private",
) -> list[str]:
    """Build ``autoclip publish <pid> --clip <id> --platform youtube ...``."""
    return [
        "autoclip",
        "publish",
        project_id,
        "--clip",
        clip_id,
        "--platform",
        "youtube",
        "--extra",
        f"privacyStatus={privacy}",
        "--wait",
        "--json",
    ]


def parse_publish_json(payload: dict) -> dict:
    """Normalize ``autoclip publish --json`` output.

    Raises :class:`PublishSkipped` when the payload reports
    ``skipped=true``.
    """
    payload = payload or {}
    if payload.get("skipped") is True:
        raise PublishSkipped(f"publish skipped: {payload.get('reason', '')}".strip())
    return payload


def run_and_parse(cmd: list[str], runner=None) -> dict:
    """Run an export/publish ``cmd`` via ``runner`` and parse stdout JSON.

    ``runner(cmd)`` must return an object with ``returncode``/``stdout``/
    ``stderr`` (defaults to :func:`subprocess.run`). Raises
    ``RuntimeError`` on non-zero exit and :class:`PublishSkipped` when the
    JSON payload reports ``skipped=true``.
    """
    import json

    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, check=False))
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"autoclip command failed {' '.join(cmd)}: {(getattr(result, 'stderr', '') or '').strip()}")
    return parse_publish_json(json.loads(result.stdout or "{}"))
