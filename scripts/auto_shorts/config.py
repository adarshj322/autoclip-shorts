"""Config loading/validation for the auto-shorts VPS wrapper.

Env names are verbatim from the design spec, section 8.
Secrets are read from the environment only and never committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    yt_api_key: str = ""
    llm_provider: str = ""
    upload_post_key: str = ""
    upload_post_user: str = ""
    data_dir: Path = Path("~/.local/share/AutoClip").expanduser()
    max_per_day: int = 3
    privacy: str = "private"
    keep_days: int = 7
    cookies_file: Path | None = None


def _parse_int(raw: str | None, default: int) -> int:
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def load_config(env: dict | None = None) -> Config:
    src = env if env is not None else os.environ
    data_dir_raw = src.get("AUTOCLIP_DATA_DIR", "~/.local/share/AutoClip")
    cookies_raw = (src.get("AUTO_SH_COOKIES_FILE", "") or "").strip()
    return Config(
        yt_api_key=src.get("YT_API_KEY", ""),
        llm_provider=src.get("LLM_PROVIDER", ""),
        upload_post_key=src.get("UPLOAD_POST_API_KEY", ""),
        upload_post_user=src.get("UPLOAD_POST_USER", ""),
        data_dir=Path(data_dir_raw).expanduser(),
        max_per_day=_parse_int(src.get("AUTO_SH_MAX_PER_DAY"), 3),
        privacy=src.get("AUTO_SH_PRIVACY", "private"),
        keep_days=_parse_int(src.get("AUTO_SH_KEEP_DAYS"), 7),
        cookies_file=Path(cookies_raw).expanduser() if cookies_raw else None,
    )


def validate_config(cfg: Config) -> list[str]:
    errors: list[str] = []
    if not cfg.yt_api_key:
        errors.append("YT_API_KEY")
    if not cfg.llm_provider:
        errors.append("LLM_PROVIDER")
    if not cfg.upload_post_key:
        errors.append("UPLOAD_POST_API_KEY")
    if not cfg.upload_post_user:
        errors.append("UPLOAD_POST_USER")
    if cfg.privacy not in ("private", "unlisted"):
        errors.append("AUTO_SH_PRIVACY must be private|unlisted")
    return errors
