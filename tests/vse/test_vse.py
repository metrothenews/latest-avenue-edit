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

import json
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


def _lyrics_tool():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "vse_lyrics",
                        "align_lyrics.py")
    spec = importlib.util.spec_from_file_location("align_lyrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _heard(text, step=0.4):
    return [{"text": w, "start": i * step, "end": i * step + 0.3, "probability": 0.9}
            for i, w in enumerate(text.split())]


@test
def test_lyrics_align_corrects_to_sheet():
    al = _lyrics_tool()
    sheet = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    # "perf\u0435ct" holds a Cyrillic letter, as lyric sheets copied from the web do.
    sheet.write("[Verse]\nToo soon? I'm ready, babe\nWho knew I'd settle, babe\n\n[Chorus]\n"
                "Your body perf\u0435ct, I like\nTake off, hit that runway\n")
    sheet.close()
    words, lines, homoglyphs = al.read_sheet(sheet.name)
    assert len(lines) == 4 and len(homoglyphs) == 1
    assert "perfect," in [w["text"] for w in words]
    # Misheard words, an invented phrase in the silence at the end, a line starting mid-way.
    heard = _heard("hold on i settle babe your body perfect i like")
    heard += [{"text": w, "start": 9.0, "end": 9.01, "probability": 0.1} for w in ("thanks", "for", "watching")]
    result = al.align(heard, words, lines, 10.0)
    assert [l["text"] for l in result["lines"]] == ["Who knew I'd settle, babe", "Your body perfect, I like"], result["lines"]
    assert all(l["complete"] for l in result["lines"])
    starts = [w["start"] for w in result["words"]]
    assert starts == sorted(starts)
    assert any(c["heard"] == "i" and c["sheet"] == "I'd" for c in result["corrections"])


@test
def test_lyric_layout():
    from vse_standalone import lyric_video
    assert lyric_video.display_word("Soon?") == "soon"
    assert lyric_video.display_word("I\u2019m") == "i'm"
    assert lyric_video.display_word("my\u2014") == "my"
    # Rows hold at most 14 characters, spaces included, as in the reference videos.
    rows = lyric_video.wrap_rows(["too", "soon", "i'm", "ready", "babe"], 14)
    assert rows == [[0, 1, 2], [3, 4]], rows
    assert lyric_video.wrap_rows(["screw", "you", "over", "x"], 14) == [[0, 1, 2], [3]]
    placed = lyric_video.layout_block([100, 60, 50, 120, 90], rows, 300)
    assert [row for row, _x in placed] == [0, 0, 0, 1, 1], placed
    assert placed[0][1] == 0 and placed[2][1] == 250 and placed[4][1] == 210  # justified to both edges
    cuts = lyric_video.plan_cuts({"lines": [
        {"start": 0.3, "end": 2.0, "words": [0, 1]}, {"start": 2.5, "end": 9.0, "words": [2, 3, 4]}],
        "words": [{"start": 0.3}, {"start": 1.0}, {"start": 2.5}, {"start": 5.5}, {"start": 8.0}]}, 10.0)
    assert cuts[0][0] == 0.0 and cuts[-1][1] == 10.0
    assert all(abs(a[1] - b[0]) < 1e-9 for a, b in zip(cuts, cuts[1:]))
    assert (2.5, 5.5) in cuts, cuts


@test
def test_make_lyric_video():
    from vse_standalone import lyric_video
    reset()
    fps = 30.0
    lyrics = {"duration": 1.5, "words": [
        {"text": "Too", "start": 0.1, "end": 0.3}, {"text": "soon?", "start": 0.4, "end": 0.6},
        {"text": "I'm", "start": 0.8, "end": 0.9}, {"text": "ready,", "start": 1.0, "end": 1.3}],
        "lines": [{"start": 0.1, "end": 0.6, "words": [0, 1]}, {"start": 0.8, "end": 1.3, "words": [2, 3]}]}
    path = os.path.join(TMP, "song.lyrics.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lyrics, fh)
    import wave
    audio = os.path.join(TMP, "song.wav")
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\0\0" * int(48000 * 1.5))
    out = os.path.join(TMP, "lyric_video.mp4")
    r = api.call("make_lyric_video", {"audio": audio, "lyrics": path, "clips": [VIDEO, VIDEO, VIDEO],
                                      "output": out, "fps": fps})
    assert r["frames"] >= 45 and r["lyrics"]["words"] == 4, r
    assert r["render"]["exists"] and r["render"]["bytes"] > 1000
    scene = bpy.context.scene
    assert (scene.render.resolution_x, scene.render.resolution_y) == (1080, 1920)
    texts = sorted((s.frame_final_start, s.text) for s in scene.sequence_editor.strips if s.type == 'TEXT')
    assert [t for _f, t in texts] == ["too", "soon", "i'm", "ready"], texts
    first = [s for s in scene.sequence_editor.strips if s.type == 'TEXT' and s.text == "too"][0]
    second = [s for s in scene.sequence_editor.strips if s.type == 'TEXT' and s.text == "i'm"][0]
    assert first.frame_final_end <= second.frame_final_start  # a line leaves when the next arrives
    movie = [s for s in scene.sequence_editor.strips if s.type == 'MOVIE'][0]
    shown_h = (movie.elements[0].orig_height - movie.crop.min_y - movie.crop.max_y) * movie.transform.scale_y
    assert abs(shown_h - 1440) <= 2, shown_h  # 3:4 picture, 240 px bars above and below


def _tool_module(name):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "vse_lyrics",
                        name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@test
def test_forced_align_path():
    import numpy
    fa = _tool_module("forced_align")
    assert fa.spell("somethin'") == "SOMETHING" and fa.spell("I\u2019m") == "I'M" and fa.spell("my\u2014") == "MY"
    # Letters 1 and 2, blank 0. Frames: blank, 1, 1, blank, 2, 2, blank.
    frames = [0, 1, 1, 0, 2, 2, 0]
    probs = numpy.full((len(frames), 3), 0.05)
    for t, k in enumerate(frames):
        probs[t, k] = 0.9
    spans = fa.viterbi(numpy.log(probs), [1, 2], blank=0)
    assert spans == [(1, 2), (4, 5)], spans
    # The same letter twice needs a blank between: 1, blank, 1.
    frames = [1, 1, 0, 1, 0]
    probs = numpy.full((len(frames), 3), 0.05)
    for t, k in enumerate(frames):
        probs[t, k] = 0.9
    assert fa.viterbi(numpy.log(probs), [1, 1], blank=0) == [(0, 1), (3, 3)]
    assert fa.viterbi(numpy.log(probs[:1]), [1, 1], blank=0) is None  # audio shorter than the words


@test
def test_clip_planning():
    mk = _tool_module("make_lyric_video")
    library = os.path.join(TMP, "library")
    subjects = ["cars", "fireworks", "misc luxury", "people", "transit", "urban"]
    lengths = {}
    for collection in ("collection-1", "collection-2"):
        for subject in subjects:
            folder = os.path.join(library, collection, subject)
            os.makedirs(folder)
            for n in range(6):
                path = os.path.join(folder, "{:s}{:d}.mp4".format(subject[0], n))
                open(path, "w").close()
                # In "fireworks" only short clips: it cannot fill a long slot.
                lengths[path] = 1.0 if subject == "fireworks" else 2.0 + n
    cache = os.path.join(TMP, "clip_lengths.json")
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(lengths, fh)

    quota = mk.subject_quotas(subjects, 8, {"people": 2.0})
    assert sum(quota.values()) == 8 and min(quota.values()) >= 1 and quota["people"] == 3, quota
    # A subject behind its share over the earlier videos gets the leftover slot.
    behind = mk.subject_quotas(subjects, 7, {}, {"cars": 2, "fireworks": 2, "misc luxury": 2,
                                                  "people": 2, "transit": 2, "urban": 0})
    assert behind["urban"] == 2, behind
    order = mk.subject_order(quota, "people")
    assert order[0] == "people" and len(order) == 8
    assert all(a != b for a, b in zip(order, order[1:])), order

    slots = [(i * 2.5, i * 2.5 + 2.5) for i in range(8)]
    clips, rows, _quota = mk.plan_clips(library, slots, 1, (), {"people": 2.0}, "people", cache)
    chosen = [row["clip"] for row in rows]
    assert len(set(chosen)) == 8 and all(lengths[c] >= 2.5 for c in chosen), rows
    assert clips[:8] == chosen
    assert rows[0]["subject"] == "people"
    assert all(a["subject"] != b["subject"] for a, b in zip(rows, rows[1:])), rows
    assert "fireworks" not in {row["subject"] for row in rows}  # too short: slot handed on, with a note
    assert any("note" in row for row in rows)
    used_collections = [row["collection"] for row in rows]
    assert abs(used_collections.count("collection-1") - used_collections.count("collection-2")) <= 2
    # Same seed, same plan; the next video never repeats a clip.
    assert [r["clip"] for r in mk.plan_clips(library, slots, 1, (), {"people": 2.0}, "people", cache)[1]] == chosen
    again = [r["clip"] for r in mk.plan_clips(library, slots, 2, chosen, {"people": 2.0}, "people", cache)[1]]
    assert not set(again) & set(chosen)


@test
def test_timing_refinement_rules():
    import numpy
    import types
    al = _tool_module("align_lyrics")
    rate = 16000
    # A voice: silent to 0.5 s, sounding to 2.0 s, silent to 2.4 s, sounding to 3.5 s.
    rng = numpy.random.default_rng(0)
    samples = rng.normal(0.0, 0.1, int(3.5 * rate)).astype(numpy.float32)
    samples[:int(0.5 * rate)] = 0.0
    samples[int(2.0 * rate):int(2.4 * rate)] = 0.0
    placed = [{"start": 0.8, "end": 0.9, "confidence": 0.1},    # "I": late, the voice enters at 0.5
              {"start": 0.95, "end": 1.2, "confidence": 0.5},   # agrees with the recognizer
              {"start": 2.4, "end": 2.7, "confidence": 0.4},    # right after the silence
              {"start": 1.0, "end": 1.1, "confidence": 0.05}]   # far off and unsure
    fake = types.ModuleType("forced_align")
    fake.SAMPLE_RATE = rate
    fake.load_audio = lambda path: samples
    fake.load_model = lambda models_dir=None: (None, None)
    fake.align_words = lambda samples, words, session, vocab: placed
    saved = sys.modules.get("forced_align")
    sys.modules["forced_align"] = fake
    try:
        result = {"words": [
            {"text": "I", "start": 0.0, "end": 0.2, "matched": True},        # recognizer: in silence
            {"text": "don't", "start": 0.9, "end": 1.2, "matched": True},
            {"text": "Back", "start": 1.6, "end": 1.9, "matched": True},      # recognizer: an echo
            {"text": "then", "start": 2.9, "end": 3.2, "matched": True}],
            "lines": [{"words": [0, 1], "start": 0.0, "end": 1.2}, {"words": [2, 3], "start": 1.6, "end": 3.2}]}
        al.refine_timing(result, "voice.wav", log=lambda text: None)
    finally:
        if saved is None:
            del sys.modules["forced_align"]
        else:
            sys.modules["forced_align"] = saved
    words = result["words"]
    assert abs(words[0]["start"] - 0.5) < 0.05 and words[0]["source"] == "voice-onset", words[0]
    assert words[1]["start"] == 0.95 and words[1]["source"] == "forced", words[1]
    assert words[2]["start"] == 2.4 and words[2]["source"] == "forced", words[2]
    assert words[3]["start"] == 2.9 and words[3]["source"] == "recognizer", words[3]
    assert [w["start"] for w in words] == sorted(w["start"] for w in words)
    assert result["lines"][1]["start"] == 2.4
    assert result["timing"]["sources"] == {"forced": 2, "recognizer": 1, "voice-onset": 1}


@test
def test_sync_ticks():
    from vse_standalone import lyric_video
    reset()
    api.set_project(fps=30.0)
    lyrics = {"words": [{"text": "a", "start": 0.5}, {"text": "b", "start": 1.0}, {"text": "c", "start": 1.0}],
              "lines": []}
    r = lyric_video.add_sync_ticks(lyrics)
    ticks = sorted(s.frame_final_start for s in bpy.context.scene.sequence_editor.strips
                   if s.type == 'SOUND' and s.name.startswith("tick"))
    assert r["ticks"] == 2 and ticks == [16, 31], ticks  # one tick per distinct start


def main():
    failed = [name for name, err in RESULTS if err]
    print("\n{:d} passed, {:d} failed".format(len(RESULTS) - len(failed), len(failed)))
    for name in failed:
        print("  FAILED:", name)
    sys.exit(1 if failed else 0)


main()
