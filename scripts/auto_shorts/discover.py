"""Trending discovery via YouTube Data API v3 for the auto-shorts VPS wrapper.

Two-step lookup with plain ``requests`` (no google-api-client dependency):

1. ``search.list`` (type=video, videoDuration=long, publishedAfter, order=viewCount)
2. ``videos.list`` (part=contentDetails,statistics,snippet)

``http_get`` is injectable for tests: ``http_get(url, params=params, timeout=30)``
and may return either a ``requests.Response`` or an already-parsed JSON dict.
"""

from __future__ import annotations

import datetime as _dt
import re

try:
    import requests as _requests
except ImportError:  # pragma: no cover - requests is a hard runtime dep
    _requests = None

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

_DEFAULT_QUERY = "AI artificial intelligence"
_TIMEOUT = 30


class DiscoveryError(RuntimeError):
    """Raised when trending discovery fails (quota/403, HTTP or network error)."""


_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<weeks>\d+)W)?"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
    r"$"
)


def parse_duration_iso8601(s: str) -> int:
    """Parse an ISO 8601 duration (e.g. ``PT16M30S``) into seconds."""
    if not s:
        return 0
    m = _DURATION_RE.match(s.strip())
    if not m or not any(v is not None for v in m.groupdict().values()):
        raise ValueError(f"Invalid ISO 8601 duration: {s!r}")
    parts = {k: int(v) if v is not None else 0 for k, v in m.groupdict().items()}
    return (
        parts["weeks"] * 604800
        + parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def filter_candidates(
    videos: list[dict],
    seen: set[str],
    allow: set[str] | None,
    block: set[str],
    min_sec: int = 900,
) -> list[dict]:
    """Drop seen/short/blocked videos; when ``allow`` is given keep only it."""
    out: list[dict] = []
    for v in videos:
        vid = v.get("video_id", "")
        if vid in seen:
            continue
        if _to_int(v.get("duration_sec"), 0) < min_sec:
            continue
        channel = v.get("channel", "")
        if allow is not None and channel not in allow:
            continue
        if channel in (block or set()):
            continue
        out.append(v)
    return out


def _default_http_get(url, params=None, timeout=_TIMEOUT):
    if _requests is None:  # pragma: no cover
        raise DiscoveryError("requests library is required for discovery")
    return _requests.get(url, params=params, timeout=timeout)


def _get_json(http_get, url: str, params: dict) -> dict:
    try:
        resp = http_get(url, params=params, timeout=_TIMEOUT)
    except DiscoveryError:
        raise
    except Exception as e:
        raise DiscoveryError(f"YouTube API request failed: {e}") from e
    if isinstance(resp, dict):
        if _body_says_quota(resp):
            raise DiscoveryError(_QUOTA_MESSAGE)
        if isinstance(resp.get("error"), dict):
            raise DiscoveryError(f"YouTube API error: {resp['error']}")
        return resp
    status = getattr(resp, "status_code", 200)
    try:
        body = resp.json()
    except Exception:
        body = {}
    if status == 403 or _body_says_quota(body):
        raise DiscoveryError(_QUOTA_MESSAGE)
    if status >= 400:
        raise DiscoveryError(f"YouTube API error HTTP {status}: {body}")
    return body if isinstance(body, dict) else {}


_QUOTA_REASONS = frozenset({"quotaexceeded", "dailylimitexceeded", "ratelimitexceeded"})

_QUOTA_MESSAGE = (
    "YouTube API quota exceeded (403/quotaExceeded). "
    "Check YT_API_KEY quota in Google Cloud Console and retry tomorrow."
)


def _body_says_quota(body) -> bool:
    """True only when the API *error object* signals quota exhaustion.

    Inspects ``body["error"]`` (``errors[].reason``/``message`` and the
    top-level error ``message``/``status``) — never video content such as
    titles/descriptions, so a video mentioning "quota" cannot trip this.
    """
    if not isinstance(body, dict):
        return False
    err = body.get("error")
    if not isinstance(err, dict):
        return False
    errors = err.get("errors")
    if isinstance(errors, list):
        for entry in errors:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("reason", "")).lower() in _QUOTA_REASONS:
                return True
            if "quota" in str(entry.get("message", "")).lower():
                return True
    if "quota" in str(err.get("message", "")).lower():
        return True
    return str(err.get("status", "")).lower() in ("quota_exceeded", "resource_exhausted")


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def discover_trending(
    api_key: str,
    query: str = _DEFAULT_QUERY,
    days: int = 7,
    max_results: int = 20,
    http_get=None,
) -> list[dict]:
    """Return trending long-form videos sorted by view_count desc.

    Each item: {video_id, title, channel, duration_sec, view_count, url}.
    Raises :class:`DiscoveryError` on 403/quota or other API failures.
    """
    if not api_key:
        raise DiscoveryError("Missing YouTube API key (YT_API_KEY is empty).")
    get = http_get or _default_http_get
    published_after = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    search_body = _get_json(
        get,
        SEARCH_URL,
        {
            "part": "snippet",
            "q": query,
            "type": "video",
            "videoDuration": "long",
            "order": "viewCount",
            "publishedAfter": published_after,
            "maxResults": min(max(max_results, 1), 50),
            "key": api_key,
        },
    )
    video_ids: list[str] = []
    for item in search_body.get("items", []) or []:
        vid = (item.get("id") or {}).get("videoId")
        if vid and vid not in video_ids:
            video_ids.append(vid)
    if not video_ids:
        return []

    videos_body = _get_json(
        get,
        VIDEOS_URL,
        {
            "part": "contentDetails,statistics,snippet",
            "id": ",".join(video_ids[:50]),
            "maxResults": min(len(video_ids), 50),
            "key": api_key,
        },
    )
    results: list[dict] = []
    for item in videos_body.get("items", []) or []:
        vid = item.get("id", "")
        snippet = item.get("snippet") or {}
        details = item.get("contentDetails") or {}
        stats = item.get("statistics") or {}
        try:
            duration_sec = parse_duration_iso8601(details.get("duration", ""))
        except ValueError:
            duration_sec = 0
        results.append(
            {
                "video_id": vid,
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "duration_sec": duration_sec,
                "view_count": _to_int(stats.get("viewCount"), 0),
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
        )
    results.sort(key=lambda v: v["view_count"], reverse=True)
    return results
