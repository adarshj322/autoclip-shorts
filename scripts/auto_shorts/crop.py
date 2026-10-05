"""Tracked 9:16 face-following crop for auto-shorts.

SPLIT-first: :func:`pick_layout` returns ``"split"`` for >=2 speakers
(stacked, zero tracking) and ``"track"`` otherwise. :func:`track_boxes`
samples every ``fps_sample``-th frame at 480p, keeps the largest MediaPipe
face, and smooths with EMA (alpha ``EMA_ALPHA``). Any failure returns []
and the caller falls back to the blur-pillarbox layout.
"""

from __future__ import annotations

from pathlib import Path

EMA_ALPHA = 0.15
DETECT_HEIGHT = 480


def pick_layout(speaker_count: int) -> str:
    """``"split"`` for >=2 speakers, else ``"track"``."""
    try:
        n = int(speaker_count)
    except (TypeError, ValueError):
        return "track"
    return "split" if n >= 2 else "track"


def track_boxes(
    video: Path, start: float, end: float, fps_sample: int = 3
) -> list[dict]:
    """Largest-face boxes in ``[start, end]`` as ``[{t, x, y, w, h}]``.

    ``x, y, w, h`` are normalized 0-1 (resolution-independent; the ffmpeg
    crop expression is built at export time). ``t`` is seconds on the
    source clock. Returns [] on any failure (missing file, missing
    cv2/mediapipe, no face found) — the caller then uses blur.
    """
    try:
        import cv2
    except ImportError:
        return []
    try:
        import mediapipe as mp
    except ImportError:
        return []
    try:
        start, end = float(start), float(end)
        if end <= start:
            return []
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            return []
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if width <= 0 or height <= 0:
                return []
            scale = DETECT_HEIGHT / height
            dw = max(2, int(width * scale))
            step = max(1, int(fps_sample))
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(start * fps))
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
                    small = cv2.resize(frame, (dw, DETECT_HEIGHT))
                    res = face.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                    dets = getattr(res, "detections", None) or []
                    if not dets:
                        continue
                    bb = max(
                        (d.location_data.relative_bounding_box for d in dets),
                        key=lambda b: b.width * b.height,
                    )
                    cur = [bb.xmin, bb.ymin, bb.width, bb.height]
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
