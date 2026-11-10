"""Shared stdlib-only helpers for the auto-shorts VPS wrapper.

One home for the small functions every module re-implements, so the
pipeline has exactly one copy of each.
"""

from __future__ import annotations

import os
import re
import subprocess


def to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def yt_proxy_args() -> list[str]:
    """--proxy args for yt-dlp when AUTO_SH_YT_PROXY is set.

    Datacenter IPs get bot-walled by YouTube; a residential proxy URL
    (http://user:pass@host:port or socks5://...) rides that out. Applies to
    yt-dlp traffic only, never to API calls.
    """
    proxy = (os.environ.get("AUTO_SH_YT_PROXY", "") or "").strip()
    return ["--proxy", proxy] if proxy else []


def run(cmd: list[str]):
    """Run ``cmd``, capturing output; never raises on exit status."""
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# Sentence-final punctuation (CJK-aware); shared by triage chunking and snap.
SENT_END_RE = re.compile(r"[.!?\u3002\uff01\uff1f]['\"\u201d)]?\s*$")
