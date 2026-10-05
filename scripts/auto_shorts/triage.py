"""Transcript-density triage ranking for the auto-shorts VPS pipeline.

Subtitles-only selection: fetch auto-subs (KBs, no video download), chunk
into overlapping windows, score each window with an injected LLM function,
and rank videos by ``mean(top-3) x density`` (qualifying clips per 10 min).
Videos with fewer than 2 scored windows fail the clips gate.

``runner`` (fetch) and ``llm_fn`` (score) are injectable so tests never hit
the network or a model — same pattern as ``download.py``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

QUALIFYING_SCORE = 70
MIN_CLIPS = 2

_TS_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
)
_SENT_END_RE = re.compile(r"[.!?\u3002\uff01\uff1f]['\"\u201d)]?\s*$")


def _default_runner(cmd: list[str]):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_subtitles(
    video_id: str,
    work_dir: Path,
    langs: str = "zh-Hans,zh,en",
    runner=None,
) -> Path | None:
    """Fetch auto-subs for ``video_id`` into ``work_dir``; None if unfetchable."""
    run = runner or _default_runner
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--skip-download",
        "--sub-langs",
        langs,
        "--convert-subs",
        "srt",
        "-o",
        str(work / "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        result = run(cmd)
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    tagged = sorted(work.glob(f"{video_id}*.srt"), key=lambda p: p.name)
    if tagged:
        return tagged[0]
    srts = sorted(work.glob("*.srt"), key=lambda p: p.name)
    return srts[0] if srts else None


def _parse_ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_cues(srt_path: Path) -> list[dict]:
    try:
        text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    cues: list[dict] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        ts_line = next((l for l in lines if "-->" in l), "")
        m = _TS_RE.search(ts_line)
        if not m:
            continue
        words = " ".join(lines[lines.index(ts_line) + 1:])
        cues.append(
            {
                "start": _parse_ts(*m.groups()[:4]),
                "end": _parse_ts(*m.groups()[4:]),
                "text": re.sub(r"\s+", " ", words).strip(),
            }
        )
    return [c for c in cues if c["text"]]


def chunk_windows(
    srt_path: Path, win_sec: int = 45, overlap_sec: int = 10
) -> list[dict]:
    """Slide ``win_sec`` windows over SRT cues; snap ends to sentence bounds."""
    cues = _parse_cues(srt_path)
    if not cues:
        return []
    step = max(win_sec - overlap_sec, 1)
    windows: list[dict] = []
    t = cues[0]["start"]
    last_end = cues[-1]["end"]
    while t < last_end:
        in_win = [c for c in cues if c["end"] > t and c["start"] < t + win_sec]
        if not in_win:
            t += step
            continue
        end = in_win[-1]["end"]
        if not _SENT_END_RE.search(in_win[-1]["text"]):
            for nxt in cues[cues.index(in_win[-1]) + 1:]:
                if nxt["start"] - end > 5 or nxt["end"] - t > win_sec + 10:
                    break
                in_win.append(nxt)
                end = nxt["end"]
                if _SENT_END_RE.search(nxt["text"]):
                    break
        windows.append(
            {
                "start": round(t, 3),
                "end": round(end, 3),
                "text": " ".join(c["text"] for c in in_win),
            }
        )
        t += step
    return windows


def _clippability(windows: list[dict]) -> float:
    if not windows:
        return 0.0
    top3 = sorted((_to_float(w.get("score")) for w in windows), reverse=True)[:3]
    mean_top3 = sum(top3) / len(top3)
    density = sum(1 for w in windows if _to_float(w.get("score")) >= QUALIFYING_SCORE) / 10.0
    return mean_top3 * density


def score_video(video: dict, srt_path: Path, llm_fn) -> dict:
    """Score each window via ``llm_fn(prompt) -> dict``; attach clippability."""
    scored: list[dict] = []
    for w in chunk_windows(srt_path):
        try:
            res = llm_fn(f"Score this clip transcript 0-100 as JSON "
                         f"{{score, hook, reason}}:\n{w['text']}") or {}
        except Exception:
            res = {}
        scored.append(
            {
                "start": w["start"],
                "end": w["end"],
                "text": w["text"],
                "score": _to_float(res.get("score")),
                "hook": str(res.get("hook", "")),
                "reason": str(res.get("reason", "")),
            }
        )
    return {
        "video_id": video.get("video_id", ""),
        "windows": scored,
        "clippability": _clippability(scored),
    }


def pick_winner(scored: list[dict]) -> tuple[dict | None, dict | None]:
    """Rank by clippability; videos with <2 scored windows fail the gate."""
    eligible = [s for s in scored if len(s.get("windows", [])) >= MIN_CLIPS]
    if not eligible:
        return (None, None)
    ranked = sorted(
        eligible,
        key=lambda s: s.get("clippability", _clippability(s.get("windows", []))),
        reverse=True,
    )
    winner = ranked[0]
    backup = ranked[1] if len(ranked) > 1 else None
    return (winner, backup)
