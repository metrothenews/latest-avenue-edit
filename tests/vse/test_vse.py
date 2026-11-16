# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
End-to-end tests for the video editor features, run headless:

    blender -b --factory-startup --python tests/vse/test_vse.py

Creates its own media (so it needs no test files), then checks: video encode/decode,
the LUT modifier (pixel values), ripple delete / ripple cut / ripple delete range,
the color pass engine and crop zoom. Exits non-zero on failure.
"""

import os
import sys
import tempfile
import traceback

import bpy

from vse_standalone import api, color_pass

TMP = tempfile.mkdtemp(prefix="vse_test_")
RESULTS = []


def test(fn):
    try:
        fn()
        RESULTS.append((fn.__name__, None))
        print("PASS", fn.__name__)
    except Exception:
        RESULTS.append((fn.__name__, traceback.format_exc()))
        print("FAIL", fn.__name__)
        traceback.print_exc()
    return fn


def close(a, b, tol=0.03):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def write_cube(path, fn, size=17, title="test"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('TITLE "{:s}"\nLUT_3D_SIZE {:d}\n'.format(title, size))
        for b in range(size):
            for g in range(size):
                for r in range(size):
                    rgb = fn(r / (size - 1), g / (size - 1), b / (size - 1))
                    fh.write("{:.6f} {:.6f} {:.6f}\n".format(*rgb))


def pixel(path, x=0.5, y=0.5):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        i = (int(y * (h - 1)) * w + int(x * (w - 1))) * 4
        return tuple(img.pixels[i:i + 3])
    finally:
        bpy.data.images.remove(img)


def reset():
    scene = api._scene()
    for strip in list(scene.sequence_editor.strips):
        scene.sequence_editor.strips.remove(strip)
    for m in list(scene.timeline_markers):
        scene.timeline_markers.remove(m)
    scene.frame_current = 1
    scene.use_preview_range = False
    api.set_project(fps=24, width=320, height=180, frame_start=1, frame_end=96)
    scene.view_settings.view_transform = 'Standard'


def strip_span(name):
    s = api.get_strip(name)
    return (s["frame_start"], s["frame_end"])


# -----------------------------------------------------------------------------

VIDEO = os.path.join(TMP, "source.mp4")


@test
def test_tools_described():
    names = {t["name"] for t in api.describe()}
    for required in ("ripple_delete", "ripple_cut", "ripple_delete_range", "apply_lut",
                     "render_video", "color_pass_apply", "zoom_all_clips"):
        assert required in names, required


@test
def test_render_video():
    reset()
    api.add_color([1, 0, 0], channel=1, frame_start=1, length=48, name="red")
    api.add_color([0, 0, 1], channel=1, frame_start=49, length=48, name="blue")
    out = api.render_video(VIDEO, format="H264_MP4", frame_start=1, frame_end=96)
    assert out["exists"] and out["bytes"] > 1000, out


@test
def test_add_movie():
    reset()
    strips = api.add_movie(VIDEO, channel=1, frame_start=1, with_audio=False, name="clip")
    assert strips[0]["duration"] == 96, strips


@test
def test_lut_modifier_pixels():
    reset()
    api.add_color([0.8, 0.2, 0.1], channel=1, frame_start=1, length=48, name="c")
    before = pixel(api.render_frame(10, os.path.join(TMP, "before.png"))["filepath"])
    assert close(before, (0.8, 0.2, 0.1)), before

    invert = os.path.join(TMP, "invert.cube")
    write_cube(invert, lambda r, g, b: (1 - r, 1 - g, 1 - b))
    api.apply_lut("c", invert)
    after = pixel(api.render_frame(10, os.path.join(TMP, "after.png"))["filepath"])
    assert close(after, (0.2, 0.8, 0.9)), after

    api.apply_lut("c", invert, intensity=0.5)
    half = pixel(api.render_frame(10, os.path.join(TMP, "half.png"))["filepath"])
    assert close(half, (0.5, 0.5, 0.5)), half

    # Non-linear LUT: tetrahedral interpolation must reproduce a smooth curve closely.
    gamma = os.path.join(TMP, "gamma.cube")
    write_cube(gamma, lambda r, g, b: (r ** 2, g ** 2, b ** 2), size=33)
    api.apply_lut("c", gamma, intensity=1.0)
    sq = pixel(api.render_frame(10, os.path.join(TMP, "gamma.png"))["filepath"])
    assert close(sq, (0.64, 0.04, 0.01), 0.02), sq

    # Stacked LUTs (look over color) apply in order: invert then square.
    api.apply_lut("c", invert, modifier_name="first")
    strip = api._strip("c")
    # "LUT" (gamma) was added first, so the stack is gamma -> invert.
    stacked = pixel(api.render_frame(10, os.path.join(TMP, "stack.png"))["filepath"])
    assert close(stacked, (1 - 0.64, 1 - 0.04, 1 - 0.01), 0.02), stacked
    assert [m.name for m in strip.modifiers] == ["LUT", "first"]


@test
def test_lut_bad_file_is_harmless():
    reset()
    api.add_color([0.5, 0.5, 0.5], channel=1, frame_start=1, length=10, name="c")
    bad = os.path.join(TMP, "bad.cube")
    with open(bad, "w") as fh:
        fh.write("LUT_3D_SIZE 4\n0 0 0\n")
    api.apply_lut("c", bad)
    px = pixel(api.render_frame(5, os.path.join(TMP, "bad.png"))["filepath"])
    assert close(px, (0.5, 0.5, 0.5)), px


def _three_strips():
    reset()
    api.add_color([1, 0, 0], channel=1, frame_start=1, length=20, name="a")
    api.add_color([0, 1, 0], channel=1, frame_start=21, length=20, name="b")
    api.add_color([0, 0, 1], channel=1, frame_start=41, length=20, name="c")
    api.add_color([1, 1, 1], channel=2, frame_start=45, length=10, name="above")
    api.add_marker(50, "m")


@test
def test_ripple_delete():
    _three_strips()
    api.ripple_delete(["b"])
    assert strip_span("c") == (21, 41), strip_span("c")
    assert strip_span("above") == (25, 35), strip_span("above")
    assert api.get_project()["markers"][0]["frame"] == 30


@test
def test_ripple_delete_same_channel_only():
    _three_strips()
    api.ripple_delete(["b"], all_channels=False)
    assert strip_span("c") == (21, 41)
    assert strip_span("above") == (45, 55), strip_span("above")


@test
def test_ripple_cut_and_paste():
    _three_strips()
    api.ripple_cut(["a"])
    assert "a" not in {s["name"] for s in api.list_strips()}
    assert strip_span("b") == (1, 21), strip_span("b")
    pasted = api.paste_strips(frame=100)
    assert len(pasted) == 1 and pasted[0]["duration"] == 20, pasted


@test
def test_ripple_delete_range():
    _three_strips()
    # Remove 15..25: trims the end of a, the start of b, closes the 10-frame gap everywhere.
    api.ripple_delete_range(15, 25)
    assert strip_span("a") == (1, 15), strip_span("a")
    assert strip_span("b") == (15, 31), strip_span("b")
    assert strip_span("c") == (31, 51), strip_span("c")
    assert strip_span("above") == (35, 45), strip_span("above")


@test
def test_ripple_delete_range_inside_strip():
    _three_strips()
    api.ripple_delete_range(25, 30, channel=1)
    spans = sorted((s["frame_start"], s["frame_end"]) for s in api.list_strips(channel=1))
    assert spans == [(1, 21), (21, 25), (25, 36), (36, 56)], spans
    assert strip_span("above") == (45, 55)  # Other channel untouched.


@test
def test_split_and_trim():
    _three_strips()
    left, right = api.split_strip("b", 30)
    assert (left["frame_start"], left["frame_end"]) == (21, 30)
    assert (right["frame_start"], right["frame_end"]) == (30, 41)
    api.trim_strip("c", frame_start=45)
    assert strip_span("c") == (45, 61)


@test
def test_color_pass():
    reset()
    api.add_movie(VIDEO, channel=1, frame_start=1, with_audio=False, name="clip1")
    api.add_movie(VIDEO, channel=1, frame_start=97, with_audio=False, name="clip2")
    state = color_pass.color_pass_load("night")
    assert [c["name"] for c in state["clips"]] == ["clip1", "clip2"], state["clips"]
    gen = state["generation"]
    color_pass.color_pass_apply(0, "color", 2, gen)
    color_pass.color_pass_apply(0, "look", 5, gen)
    clip = color_pass.color_pass_state()["clips"][0]
    assert (clip["look"], clip["color"]) == (5, 2), clip
    mods = [m.name for m in api._strip("clip1").modifiers]
    assert mods == ["Color LUT", "Look LUT"], mods
    # Changing the look keeps the color, and the order.
    color_pass.color_pass_apply(0, "look", 3, gen)
    assert [m.name for m in api._strip("clip1").modifiers] == ["Color LUT", "Look LUT"]
    prev = color_pass.color_pass_preview(1, color=0, look=1)
    assert prev["exists"], prev
    # Preview must not leave changes behind.
    assert len(api._strip("clip2").modifiers) == 0
    color_pass.color_pass_apply(0, "look", 0, gen)
    assert [m.name for m in api._strip("clip1").modifiers] == ["Color LUT"]


@test
def test_zoom_all_clips():
    reset()
    api.add_movie(VIDEO, channel=1, frame_start=1, with_audio=False, name="clip1")
    r = color_pass.zoom_all_clips(1.34)
    assert r["changed"] == 1
    assert color_pass.zoom_all_clips(1.34)["unchanged"] == 1
    assert abs(api._strip("clip1").transform.scale_x - 1.34) < 1e-5


def main():
    failed = [name for name, err in RESULTS if err]
    print("\n{:d} passed, {:d} failed".format(len(RESULTS) - len(failed), len(failed)))
    for name in failed:
        print("  FAILED:", name)
    sys.exit(1 if failed else 0)


main()
