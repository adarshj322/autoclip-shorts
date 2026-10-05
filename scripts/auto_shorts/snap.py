"""Deterministic boundary snap for auto-shorts candidate windows.

Snaps a coarse (LLM-proposed) ``(start, end)`` window to exact cut points:

sentence end -> VAD-silence edge -> PySceneDetect cut -> sub-60s tail clamp.

Silence edges are inter-word gaps >= ``SILENCE_GAP_SEC`` (300ms) from the
word-timestamped list — no separate VAD call. Scene-cut anchoring uses the
``scene_cuts`` timestamps passed in (Task 4 wires the producer via
:func:`scene_cuts`); a cut within +/- ``SCENE_SNAP_SEC`` of an edge wins.
The hook (start) is never moved earlier by the tail clamp — over-long
windows clamp the tail to ``ns + MAX_DUR_SEC``.

``snap_window`` is pure (no network, no ffmpeg); only :func:`scene_cuts`
touches the video file, and it returns [] on any failure so snapping
proceeds without scene anchoring. Moves larger than ``SNAP_MOVE_LIMIT_SEC``
flag the window for skip by the caller (Task 4).
"""

from __future__ import annotations

import re
from pathlib import Path

SNAP_MOVE_LIMIT_SEC = 2.0
SILENCE_GAP_SEC = 0.3
SCENE_SNAP_SEC = 1.0
MAX_DUR_SEC = 60.0
_EDGE_SEARCH_SEC = 2.0

_SENT_END_RE = re.compile(r"[.!?\u3002\uff01\uff1f]['\"\u201d)]?\s*$")


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _words_sorted(words: list[dict]) -> list[dict]:
    clean = [
        w for w in (words or [])
        if _to_float(w.get("start"), -1) >= 0 and _to_float(w.get("end"), -1) >= 0
    ]
    return sorted(clean, key=lambda w: (_to_float(w.get("start")), _to_float(w.get("end"))))


def _nearest(value: float, options: list[float]) -> float:
    return min(options, key=lambda o: abs(o - value))


def _word_snap(start: float, end: float, words: list[dict]) -> tuple[float, float]:
    ns = _nearest(start, [_to_float(w["start"]) for w in words])
    ne = _nearest(end, [_to_float(w["end"]) for w in words])
    return (ns, ne)


def _sentence_snap(start: float, end: float, words: list[dict]) -> tuple[float, float]:
    ns, ne = start, end
    for w in words:
        if _SENT_END_RE.search(str(w.get("word", ""))):
            wend = _to_float(w["end"])
            if start <= wend <= start + _EDGE_SEARCH_SEC and abs(wend - start) < abs(ns - start):
                ns = wend
            if end - _EDGE_SEARCH_SEC <= wend <= end and abs(wend - end) < abs(ne - end):
                ne = wend
    return (ns, ne)


def _silence_gaps(words: list[dict]) -> list[tuple[float, float]]:
    gaps = []
    for prev, nxt in zip(words, words[1:]):
        gap = _to_float(nxt["start"]) - _to_float(prev["end"])
        if gap >= SILENCE_GAP_SEC:
            gaps.append((_to_float(prev["end"]) + gap / 2, gap))
    return gaps


def _silence_snap(start: float, end: float, words: list[dict]) -> tuple[float, float]:
    gaps = _silence_gaps(words)
    if not gaps:
        return (start, end)
    near = lambda edge: [m for m, _ in gaps if abs(m - edge) <= _EDGE_SEARCH_SEC]
    ns = _nearest(start, near(start)) if near(start) else start
    ne = _nearest(end, near(end)) if near(end) else end
    return (ns, ne)


def _scene_snap(start: float, end: float, cuts: list[float]) -> tuple[float, float]:
    ns = _nearest(start, [c for c in cuts if abs(c - start) <= SCENE_SNAP_SEC]) \
        if any(abs(c - start) <= SCENE_SNAP_SEC for c in cuts) else start
    ne = _nearest(end, [c for c in cuts if abs(c - end) <= SCENE_SNAP_SEC]) \
        if any(abs(c - end) <= SCENE_SNAP_SEC for c in cuts) else end
    return (ns, ne)


def snap_window(
    start: float,
    end: float,
    words: list[dict],
    scene_cuts: list[float] | None = None,
) -> tuple[float, float, float]:
    """Snap ``(start, end)`` to cut points; return ``(new_start, new_end, max_move)``."""
    start, end = _to_float(start), _to_float(end)
    if end < start:
        start, end = end, start
    words = _words_sorted(words)
    cuts = sorted(_to_float(c) for c in (scene_cuts or []))
    if not words:
        ne = min(end, start + MAX_DUR_SEC)
        return (start, ne, max(0.0, end - ne))
    ns, ne = _word_snap(start, end, words)
    ns, ne = _sentence_snap(ns, ne, words)
    ns, ne = _silence_snap(ns, ne, words)
    ns, ne = _scene_snap(ns, ne, cuts)
    if ne < ns:
        ne = ns
    if ne - ns > MAX_DUR_SEC:
        ne = ns + MAX_DUR_SEC
    return (ns, ne, max(abs(ns - start), abs(ne - end)))


def scene_cuts(video: Path, start: float, end: float) -> list[float]:
    """PySceneDetect cut timestamps within ``[start, end]``; [] on any failure."""
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector

        mgr = SceneManager()
        mgr.add_detector(ContentDetector())
        vid = open_video(str(video))
        start_t = vid.time_to_frame(_to_float(start)) if hasattr(vid, "time_to_frame") else None
        end_t = vid.time_to_frame(_to_float(end)) if hasattr(vid, "time_to_frame") else None
        if start_t is not None and end_t is not None and end_t > start_t:
            vid.seek(start_t)
            mgr.detect_scenes(vid, end_time=end_t)
        else:
            mgr.detect_scenes(vid)
        cuts = []
        for s in mgr.get_scene_list():
            try:
                t = s[0].get_seconds()
            except (AttributeError, IndexError):
                continue
            if _to_float(start) <= t <= _to_float(end):
                cuts.append(float(t))
        return sorted(cuts)
    except Exception:
        return []
