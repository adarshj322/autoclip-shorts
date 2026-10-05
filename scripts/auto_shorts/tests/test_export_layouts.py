"""Track/split layout arms filter (never silently unfiltered) + ffmpeg smoke.

`backend.services.__init__` needs sqlalchemy (absent here), so stub the
parent package and load publish_export.py standalone — same trick as the
Task 3 preset-flip check.
"""

import importlib.util
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent


def _load_publish_export():
    if "backend.services" not in sys.modules:
        pkg = types.ModuleType("backend.services")
        pkg.__path__ = [str(REPO / "backend" / "services")]
        sys.modules["backend.services"] = pkg
    path = REPO / "backend" / "services" / "publish_export.py"
    spec = importlib.util.spec_from_file_location(
        "publish_export_standalone", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pe():
    return _load_publish_export()


def test_track_layout_filters(pe):
    parts = pe._layout_filters("track", 1080, 1920)
    assert parts, "track must filter, never fall through to []"
    assert any("crop=" in p for p in parts)


def test_track_layout_uses_boxes_when_given(pe):
    from scripts.auto_shorts.crop import track_boxes  # noqa: F401 (ensures module exists)
    boxes = [{"t": 0.0, "x": 0.2, "y": 0.4, "w": 0.2, "h": 0.2},
             {"t": 1.0, "x": 0.7, "y": 0.4, "w": 0.2, "h": 0.2}]
    parts = pe._layout_filters("track", 1080, 1920, boxes=boxes,
                               clip_start=0.0, clip_duration=1.0)
    assert any("crop=" in p and "lt(t," in p for p in parts)


def test_split_layout_is_stacked(pe):
    parts = pe._layout_filters("split", 1080, 1920)
    assert parts and any("vstack" in p for p in parts)


def _ffmpeg_out_size(filter_graph, last, out_path, dur=1.0):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
           "-t", str(dur), "-filter_complex", filter_graph,
           "-map", f"[{last}]", "-c:v", "libx264", "-preset", "veryfast",
           "-y", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    probe = ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             str(out_path)]
    return subprocess.check_output(probe, text=True).strip()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_track_filter_renders_1080x1920(pe, tmp_path):
    from scripts.auto_shorts import crop as crop_mod
    boxes = [{"t": 0.0, "x": 0.3, "y": 0.4, "w": 0.2, "h": 0.25},
             {"t": 0.9, "x": 0.6, "y": 0.4, "w": 0.2, "h": 0.25}]
    graph = f"[0:v]{crop_mod.track_filter(boxes, 1.0)}[base]"
    assert _ffmpeg_out_size(graph, "base", tmp_path / "track.mp4") == "1080,1920"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_split_filter_renders_1080x1920(pe, tmp_path):
    from scripts.auto_shorts import crop as crop_mod
    graph = ";".join(crop_mod.split_filter())
    assert _ffmpeg_out_size(graph, "base", tmp_path / "split.mp4") == "1080,1920"
