"""Tracked 9:16 face-following crop for auto-shorts.

SPLIT-first: :func:`pick_layout` returns ``"split"`` for >=2 speakers
(stacked, zero tracking) and ``"track"`` otherwise. :func:`track_boxes`
samples every ``fps_sample``-th frame at 480p, keeps the largest MediaPipe
face, and smooths with EMA (alpha ``EMA_ALPHA``). Any failure returns []
and the caller falls back to the static center-crop layout
(``split_filter`` halves for two-speaker content).

:func:`track_filter` / :func:`split_filter` build the single-ffmpeg-pass
expressions the ``shorts`` export preset renders: dwell-quantized
(:data:`DWELL_SEC`) interpolated face-x for track (center fallback when
untracked), vstacked halves for split. Relative boxes are clamped to
[0, 1] in :func:`interpolate_boxes`.
"""

from __future__ import annotations

from pathlib import Path

EMA_ALPHA = 0.15
DETECT_HEIGHT = 480
# Face-x hold per position: re-quantized every DWELL_SEC (200-400ms band)
# so the crop glides in steps instead of jittering every frame.
DWELL_SEC = 0.3

# cv2 CAP_PROP numeric ids (avoid attribute refs so injected fakes work
# without cv2 installed).
_FPS_PROP = 5
_WIDTH_PROP = 3
_HEIGHT_PROP = 4
_POS_PROP = 1


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value) -> float:
    return max(0.0, min(1.0, _to_float(value)))


def pick_layout(speaker_count: int) -> str:
    """``"split"`` for >=2 speakers, else ``"track"``."""
    try:
        n = int(speaker_count)
    except (TypeError, ValueError):
        return "track"
    return "split" if n >= 2 else "track"


def track_boxes(
    video: Path, start: float, end: float, fps_sample: int = 3,
    capture_factory=None, detector_factory=None,
) -> list[dict]:
    """Largest-face boxes in ``[start, end]`` as ``[{t, x, y, w, h}]``.

    ``x, y, w, h`` are normalized 0-1 (resolution-independent; the ffmpeg
    crop expression is built at export time). ``t`` is seconds on the
    source clock. Returns [] on any failure (missing file, missing
    cv2/mediapipe, no face found) — the caller then uses blur.

    ``capture_factory(path)`` / ``detector_factory()`` inject the video
    probing seam (a capture with read/get/set/release/isOpened and a
    detector with process(rgb)/close) so the full sampling + EMA path is
    exercisable without cv2/mediapipe installed; a real-video VPS check
    still needs the real backends (see requirements.txt).
    """
    try:
        import cv2
    except ImportError:
        cv2 = None
    if capture_factory is None and cv2 is None:
        return []
    mp = None
    if detector_factory is None:
        try:
            import mediapipe as mp
        except ImportError:
            return []
    try:
        start, end = float(start), float(end)
        if end <= start:
            return []
        cap = capture_factory(str(video)) if capture_factory else cv2.VideoCapture(str(video))
        if not cap.isOpened():
            return []
        try:
            fps = float(cap.get(_FPS_PROP)) or 30.0
            width = int(cap.get(_WIDTH_PROP) or 0)
            height = int(cap.get(_HEIGHT_PROP) or 0)
            if width <= 0 or height <= 0:
                if cv2 is None:
                    width, height = 640, 480
                else:
                    return []
            scale = DETECT_HEIGHT / height
            dw = max(2, int(width * scale))
            step = max(1, int(fps_sample))
            cap.set(_POS_PROP, int(start * fps))
            if detector_factory is not None:
                face = detector_factory()
            else:
                face = mp.solutions.face_detection.FaceDetection(
                    model_selection=0, min_detection_confidence=0.5
                )
            try:
                boxes: list[dict] = []
                ema: list[float] | None = None
                idx = 0
                while True:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    t = start + idx / fps
                    idx += 1
                    if t > end:
                        break
                    if (idx - 1) % step:
                        continue
                    if cv2 is not None:
                        small = cv2.resize(frame, (dw, DETECT_HEIGHT))
                        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                    else:
                        rgb = frame
                    res = face.process(rgb)
                    dets = getattr(res, "detections", None) or []
                    if not dets:
                        continue
                    bb = _largest_box(dets)
                    if bb is None:
                        continue
                    cur = [bb[0], bb[1], bb[2], bb[3]]
                    if ema is None:
                        ema = cur
                    else:
                        ema = [EMA_ALPHA * c + (1 - EMA_ALPHA) * e for c, e in zip(cur, ema)]
                    boxes.append({"t": round(t, 3), "x": ema[0], "y": ema[1], "w": ema[2], "h": ema[3]})
                return boxes
            finally:
                face.close()
        finally:
            cap.release()
    except Exception:
        return []


def _largest_box(dets) -> tuple[float, float, float, float] | None:
    """Largest relative (xmin, ymin, w, h) from mediapipe detections; None if none."""
    best = None
    best_area = -1.0
    for d in dets:
        try:
            bb = d.location_data.relative_bounding_box
            area = float(bb.width) * float(bb.height)
        except (AttributeError, TypeError, ValueError):
            continue
        if area > best_area:
            best_area = area
            best = (bb.xmin, bb.ymin, bb.width, bb.height)
    return best


def interpolate_boxes(boxes: list[dict], t: float) -> tuple[float, float, float, float] | None:
    """Linearly interpolate clamped boxes at ``t`` as ``(cx, cy, w, h)``.

    ``cx``/``cy`` are box centers; every component is clamped to [0, 1].
    None when there are no boxes.
    """
    pts = sorted(
        ((_to_float(b.get("t")), _clamp01(b.get("x")), _clamp01(b.get("y")),
          _clamp01(b.get("w")), _clamp01(b.get("h")))
         for b in (boxes or [])),
        key=lambda p: p[0],
    )
    if not pts:
        return None

    def center(p) -> tuple[float, float, float, float]:
        _, x, y, w, h = p
        return (_clamp01(x + w / 2), _clamp01(y + h / 2), w, h)

    t = _to_float(t)
    if t <= pts[0][0]:
        return center(pts[0])
    if t >= pts[-1][0]:
        return center(pts[-1])
    for p0, p1 in zip(pts, pts[1:]):
        if p0[0] <= t <= p1[0]:
            f = (t - p0[0]) / (p1[0] - p0[0]) if p1[0] > p0[0] else 0.0
            c0, c1 = center(p0), center(p1)
            return tuple(ai + f * (bi - ai) for ai, bi in zip(c0, c1))
    return center(pts[-1])


def _dwell_values(boxes: list[dict], duration: float, t0: float = 0.0,
                  dwell: float = DWELL_SEC) -> list[float]:
    """Face-center-x per dwell step over ``[t0, t0+duration)`` (clip clock)."""
    import math
    n = max(1, math.ceil(max(duration, 0.0) / dwell))
    out = []
    for k in range(n):
        interp = interpolate_boxes(boxes, t0 + k * dwell)
        out.append(interp[0] if interp else 0.5)
    return out


def track_filter(boxes: list[dict], duration: float, w: int = 1080,
                 h: int = 1920, t0: float = 0.0) -> str:
    """Single-pass 9:16 face-following crop (``crop + scale`` in one chain).

    ``x`` follows the interpolated face center quantized to ``DWELL_SEC``
    steps (nested ``if(lt(t,…))``); empty ``boxes`` falls back to a static
    center crop so the output is always filtered, never silent.
    """
    cw, chh = "ih*9/16", "ih"
    if not boxes:
        return f"crop=w={cw}:h={chh}:x='(iw-ow)/2':y=0,scale={w}:{h}"
    vals = [f"({_c:.4f}*iw-{cw}/2)" for _c in _dwell_values(boxes, duration, t0)]
    bounds = [round((k + 1) * DWELL_SEC, 3) for k in range(len(vals) - 1)]
    expr = vals[-1]
    for bound, val in reversed(list(zip(bounds, vals))):
        expr = f"if(lt(t,{bound:.3f}),{val},{expr})"
    return f"crop=w={cw}:h={chh}:x='min(max({expr},0),iw-ow)':y=0,scale={w}:{h}"


def split_filter(w: int = 1080, h: int = 1920) -> list[str]:
    """Stacked SPLIT layout: top/bottom halves scaled and vstacked to 9:16."""
    half = h // 2
    return [
        "[0:v]split=2[sh_a][sh_b]",
        f"[sh_a]crop=iw:ih/2:0:0,scale={w}:{half}[sh_t]",
        f"[sh_b]crop=iw:ih/2:0:ih/2,scale={w}:{half}[sh_btm]",
        "[sh_t][sh_btm]vstack=inputs=2[base]",
    ]
