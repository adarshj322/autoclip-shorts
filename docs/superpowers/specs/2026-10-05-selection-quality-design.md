# Selection-Quality Upgrade — Spec (auto-shorts fork)

Date: 2026-10-05 | Status: pending user review
Basis: research/notes/final_report_shorts-selection-triage-a663f8.md (ship-gate passed)

## Intent

Fix the three validated complaints (wrong video, weak moment, bad crop) with
the research-backed architecture. Constraints: single VPS, subtitles-only
triage, existing LLM key, +~$0.07/day max added spend. Non-goals: multimodal
selection models, local Ollama ranking, score-threshold gating, public
auto-publish.

## Component 1 — Triage stage (best-video selection)

New `scripts/auto_shorts/triage.py`, called by run.py between discover and
per-video processing:

1. Gate 0 (free): duration ≥8–10 min, caption track exists, language match;
   soft ordering by velocity/outlier-vs-baseline + comment density (small
   weights). Emit top ~10.
2. Fetch subtitles only (`--write-auto-subs --skip-download`, KBs each).
   Unfetchable → skip-and-log candidate (PO-token/throttle fallback), never
   crash the batch.
3. Gate 1 (one cheap LLM pass per file, existing `LLM_PROVIDER` key):
   sentence-aware 30–60s overlapping windows, heuristic pre-filter to ~15,
   rubric score (hook, self-containment, single point + payoff,
   conflict/opinion peak, value) with JSON out.
4. Aggregate per video: mean(top-3) × density (qualifying/10min), require
   ≥2 qualifying clips. Emit 1 winner + 1 backup. Scores order only.

## Component 2 — Snap chain (best-moment boundaries)

New `scripts/auto_shorts/snap.py`, applied to the winner's clip pick before
export, using the winner's faster-whisper word timestamps (already produced):

1. Nearest sentence boundary → 2. VAD silence edge 300–500ms →
3. PySceneDetect cut within ±1s → 4. sub-60s tail clamp (never the hook).
Flag (skip + log reason) any clip whose boundary moved >2s.
New dep: `scenedetect` (pip, CPU-only). VAD via faster-whisper vad_filter
(no new dep).

## Component 3 — Tracked crop (framing)

Change `shorts` preset default layout `crop` → tracked reframing:
- 480p detection, every 3rd frame + interpolate, largest-face + EMA
  (α 0.1–0.2), 200–400ms speaker dwell, boundary clamp, single ffmpeg pass
  with caption burn-in.
- Two-speaker content: SPLIT stacked layout first (zero tracking).
- Keep `crop` available as preset option, not default.
New deps: opencv-python-headless + mediapipe (CPU). If install proves heavy
on the target box, fallback recorded in code: blur-pillarbox layout.

Hook-in-first-3s is enforced as a binary publish veto alongside the existing
private-only flow (no public auto-publish; unchanged).

## Testing

Unit tests per module (window chunking, aggregation math, snap order,
layout pick) + one recorded-fixture end-to-end (no network) + full suite
green. Live validation stays manual: one real private upload, judged in
Studio.

## Self-review

No TBDs. Scope is three additive modules + preset default; no core-pipeline
or upstream-file edits. Ambiguity resolved: winner+backup (not top-K);
>2s snap = skip; SPLIT before tracking for two-speaker; blur only as
fallback. Consistent with the private-first, no-threshold-gating rules.
