"""Tests for lecture_video.py. Run from the project folder:

    python tests/test_lecture_video.py              # no network needed
    LV_REAL_VOICE=k:af_heart python tests/test_lecture_video.py   # also a real voice (Kokoro downloads ~350 MB)

Uses only temporary folders; LOCALTTS_DIR is pointed at one so your real settings are untouched
(unless LOCALTTS_DIR is already set, e.g. to reuse downloaded models).
"""
import os
import sys
import json
import wave
import shutil
import tempfile
import unittest
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="lv_test_")
os.environ.setdefault("LOCALTTS_DIR", os.path.join(TMP, "home"))

import numpy as np                      # noqa: E402
from PIL import Image                   # noqa: E402
import localtts_core as core            # noqa: E402
import lecture_video as lv              # noqa: E402

COLORS = [(220, 30, 30), (30, 160, 40), (30, 60, 220), (230, 200, 20), (150, 40, 160)]


def make_pdf(path, n, size=(800, 450)):
    imgs = [Image.new("RGB", size, COLORS[i % len(COLORS)]) for i in range(n)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=72)


def make_script(path, narrations, pdf_name="deck.pdf", lang="en-US"):
    pages = [dict(pdf_page=i + 1, slide_label=str(i + 1), title=f"Page {i + 1}", narration=t,
                  comment="never spoken", prompt_note="ignored", est_duration_sec=3)
             for i, t in enumerate(narrations)]
    d = dict(format="lecture-script", schema_version=3,
             meta=dict(title="Test", language=lang, words_per_minute=125,
                       source_pdf=dict(filename=pdf_name, page_count=len(pages))), pages=pages)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)
    return d


def write_tone(path, seconds, rate=24000, freq=440):
    t = np.arange(int(seconds * rate)) / rate
    a = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(a.tobytes())


def frame_color(video, t, size):
    raw = subprocess.run([lv.ffmpeg_exe(), "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3)
    return tuple(int(x) for x in a[size[1] // 2, size[0] // 2])


def close(c1, c2, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(c1, c2))


class Validation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=TMP)
        self.pdf = os.path.join(self.d, "deck.pdf")
        make_pdf(self.pdf, 3)
        self.js = os.path.join(self.d, "deck.script.json")

    def test_ok(self):
        make_script(self.js, ["a", "", "c"])
        pages, warn = lv.validate(lv.load_script(self.js), self.pdf)
        self.assertEqual([p["pdf_page"] for p in pages], [1, 2, 3])
        self.assertEqual(warn, [])

    def test_wrong_format_and_version(self):
        d = make_script(self.js, ["a", "b", "c"])
        for key, val in (("format", "something"), ("schema_version", 2)):
            bad = dict(d, **{key: val})
            with open(self.js, "w") as f:
                json.dump(bad, f)
            with self.assertRaises(lv.ScriptError):
                lv.load_script(self.js)

    def test_page_count_mismatch(self):
        make_script(self.js, ["a", "b"])
        with self.assertRaises(lv.ScriptError) as e:
            lv.validate(lv.load_script(self.js), self.pdf)
        self.assertIn("3 pages", str(e.exception))

    def test_duplicates_and_gaps(self):
        d = make_script(self.js, ["a", "b", "c"])
        d["pages"][2]["pdf_page"] = 2
        with open(self.js, "w") as f:
            json.dump(d, f)
        with self.assertRaises(lv.ScriptError) as e:
            lv.validate(lv.load_script(self.js), self.pdf)
        self.assertIn("more than once: 2", str(e.exception))
        self.assertIn("without a script entry: 3", str(e.exception))

    def test_unsorted_is_fine_and_name_warning(self):
        d = make_script(self.js, ["a", "b", "c"], pdf_name="other.pdf")
        d["pages"].reverse()
        with open(self.js, "w") as f:
            json.dump(d, f)
        pages, warn = lv.validate(lv.load_script(self.js), self.pdf)
        self.assertEqual([p["narration"] for p in pages], ["a", "b", "c"])
        self.assertEqual(len(warn), 1)

    def test_pairing(self):
        make_script(self.js, ["a", "b", "c"])
        self.assertEqual(lv.find_script_for_pdf(self.pdf), self.js)
        self.assertEqual(lv.find_pdf_for_script(self.js), self.pdf)
        renamed = os.path.join(self.d, "my download.json")
        os.rename(self.js, renamed)
        self.assertEqual(lv.find_script_for_pdf(self.pdf), renamed)
        self.assertEqual(lv.find_pdf_for_script(renamed), self.pdf)


class Helpers(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(lv.clean_text("Hallo\x0bWelt\u00ad!\u200b  x\x00y"), "Hallo Welt! x y")
        self.assertEqual(lv.clean_text("a\u2028b\r\nc"), "a\nb\nc")
        self.assertEqual(lv.clean_text("   \x0c "), "")

    def test_language(self):
        self.assertEqual(lv.language_code("de-DE"), "de")
        self.assertEqual(lv.language_code("de_AT"), "de")
        self.assertEqual(lv.language_code("pt-BR"), "pt-BR")
        self.assertEqual(lv.language_code("pt"), "pt-PT")
        self.assertIsNone(lv.language_code("fr-FR"))
        self.assertIsNone(lv.language_code(""))
        self.assertEqual(lv.default_voice("en-GB")["id"], "e:en-GB-SoniaNeural")
        self.assertEqual(lv.default_voice("de-DE")["engine"], "edge")
        self.assertEqual(lv.default_voice("en-GB", "offline")["lang"], "en-gb")
        self.assertEqual(lv.default_voice("de-DE", "offline")["engine"], "piper")
        for code, _ in core.LANGUAGES:
            self.assertIsNotNone(lv.default_voice(code))
            self.assertIsNotNone(lv.default_voice(code, "offline"))

    def test_page_times_do_not_drift(self):
        rate = 24000
        counts = [int(rate * (1.013 + 0.007 * i)) for i in range(200)]   # awkward lengths
        times = lv.page_times(counts, rate)
        cum_audio = np.cumsum(counts) / rate
        cum_video = np.cumsum(times)
        self.assertLessEqual(float(np.max(np.abs(cum_audio - cum_video))), 0.5 / lv.FPS + 1e-9)
        for t in times:
            self.assertAlmostEqual(t * lv.FPS, round(t * lv.FPS), places=6)


class Encode(unittest.TestCase):
    """Real ffmpeg: pages of different lengths change exactly where their audio ends."""

    def test_timing_and_colors(self):
        d = tempfile.mkdtemp(dir=TMP)
        pdf = os.path.join(d, "deck.pdf")
        make_pdf(pdf, 5)
        size = lv.frame_size(pdf, 360)
        self.assertEqual(size, (640, 360))
        imgs = lv.render_pages(pdf, size, os.path.join(d, "img"))
        lens = [1.37, 2.0, 0.53, 3.11, 1.0]
        wavs = []
        for i, s in enumerate(lens):
            p = os.path.join(d, f"a{i}.wav")
            write_tone(p, s, freq=300 + 100 * i)
            wavs.append(p)
        entries = [(wavs[0], 0), (wavs[1], 0), (None, 2.5), (wavs[3], 0), (wavs[4], 0)]
        audio = os.path.join(d, "sound.wav")
        counts, rate = lv.build_soundtrack(entries, audio)
        self.assertEqual(counts[2], int(2.5 * rate))
        durs = lv.page_times(counts, rate)
        total = sum(counts) / rate
        out = os.path.join(d, "out.mp4")
        lv.encode(imgs, durs, audio, out, total)
        self.assertAlmostEqual(lv.media_duration(out), total, delta=0.1)
        start = 0.0
        for i, dur in enumerate(durs):
            for t in (start + 0.08, start + dur - 0.08):
                self.assertTrue(close(frame_color(out, t, size), COLORS[i]),
                                f"page {i + 1} at {t:.2f}s: {frame_color(out, t, size)}")
            start += dur

    def test_mixed_page_shapes(self):
        d = tempfile.mkdtemp(dir=TMP)
        pdf = os.path.join(d, "mixed.pdf")
        a = Image.new("RGB", (800, 450), COLORS[0])
        b = Image.new("RGB", (600, 800), COLORS[1])           # portrait page in a wide deck
        a.save(pdf, save_all=True, append_images=[b], resolution=72)
        imgs = lv.render_pages(pdf, lv.frame_size(pdf, 480), os.path.join(d, "img"))
        self.assertEqual({Image.open(p).size for p in imgs}, {(854, 480)})


def edge_mock_mp3():
    import soundfile as sf
    p = os.path.join(TMP, "edge_mock.mp3")
    if not os.path.isfile(p):
        t = np.arange(int(1.2 * 24000)) / 24000
        sf.write(p, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), 24000, format="MP3")
    return p


class EndToEnd(unittest.TestCase):
    """Whole make_video() run through the real worker process (Edge mocked, no network)."""

    def run_job(self, voice, narrations, **kw):
        d = tempfile.mkdtemp(dir=TMP)
        pdf = os.path.join(d, "deck.pdf")
        make_pdf(pdf, len(narrations))
        js = os.path.join(d, "deck.script.json")
        make_script(js, narrations)
        synth = core.Synth()
        log = []
        try:
            res = lv.make_video(pdf, js, os.path.join(d, "deck.mp4"), voice, synth, height=360,
                                on_progress=lambda f, l: log.append((f, l)), **kw)
        finally:
            synth.stop()
        fr = [f for f, _ in log]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(fr, fr[1:])), f"progress went backwards: {fr}")
        return res, d, pdf, js

    def test_edge_mock(self):
        os.environ["LOCALTTS_EDGE_MOCK"] = edge_mock_mp3()
        old = lv.EDGE_PAUSE
        lv.EDGE_PAUSE = 0
        try:
            res, d, pdf, js = self.run_job("e:en-US-AriaNeural",
                                           ["First page.", "", "Third\x0b page.", "Fourth."], silent_hold=1.5)
            self.assertEqual(res["silent"], [2])
            self.assertEqual(res["spoken"], 3)
            self.assertAlmostEqual(res["page_seconds"][1], 1.5, places=3)
            self.assertAlmostEqual(res["seconds"], sum(res["page_seconds"]), delta=0.1)
            self.assertTrue(os.path.isfile(os.path.join(d, "deck.mp4")))
            # Second run: everything comes from the cache.
            synth = core.Synth()
            try:
                res2 = lv.make_video(pdf, js, os.path.join(d, "deck2.mp4"), "e:en-US-AriaNeural", synth,
                                     height=360, silent_hold=1.5)
            finally:
                synth.stop()
            self.assertEqual((res2["spoken"], res2["cached"]), (0, 3))
        finally:
            lv.EDGE_PAUSE = old
            os.environ.pop("LOCALTTS_EDGE_MOCK", None)

    def test_silent_pages_left_out(self):
        os.environ["LOCALTTS_EDGE_MOCK"] = edge_mock_mp3()
        old = lv.EDGE_PAUSE
        lv.EDGE_PAUSE = 0
        try:
            res, d, pdf, js = self.run_job("e:en-US-AriaNeural", ["One.", "", "Three.", ""], silent_hold=0)
            self.assertEqual(res["skipped"], [2, 4])
            self.assertEqual(len(res["page_seconds"]), 2)
            self.assertAlmostEqual(res["seconds"], sum(res["page_seconds"]), delta=0.1)
            out = os.path.join(d, "deck.mp4")
            size = lv.frame_size(pdf, 360)
            t1 = res["page_seconds"][0]
            self.assertTrue(close(frame_color(out, t1 - 0.1, size), COLORS[0]))
            self.assertTrue(close(frame_color(out, t1 + 0.1, size), COLORS[2]))     # page 2 is gone
            self.assertEqual(sorted(os.listdir(res["audio_dir"])), ["page_001_Page_1.wav", "page_003_Page_3.wav"])
            make_script(js, ["", ""])
            make_pdf(pdf, 2)
            synth = core.Synth()
            try:
                with self.assertRaises(lv.ScriptError):
                    lv.make_video(pdf, js, out, "e:en-US-AriaNeural", synth, height=360, silent_hold=0)
            finally:
                synth.stop()
        finally:
            lv.EDGE_PAUSE = old
            os.environ.pop("LOCALTTS_EDGE_MOCK", None)

    def test_cancel(self):
        os.environ["LOCALTTS_EDGE_MOCK"] = edge_mock_mp3()
        d = tempfile.mkdtemp(dir=TMP)
        pdf = os.path.join(d, "deck.pdf")
        make_pdf(pdf, 3)
        js = os.path.join(d, "deck.script.json")
        make_script(js, ["Unique cancel text one.", "two", "three"])
        synth = core.Synth()
        try:
            with self.assertRaises(lv.Cancelled):
                lv.make_video(pdf, js, os.path.join(d, "x.mp4"), "e:en-US-AriaNeural", synth, height=360,
                              should_stop=lambda: True)
        finally:
            synth.stop()
            os.environ.pop("LOCALTTS_EDGE_MOCK", None)
        self.assertFalse(os.path.exists(os.path.join(d, "x.mp4")))

    @unittest.skipUnless(os.environ.get("LV_REAL_VOICE"), "set LV_REAL_VOICE=<voice id> to use a real voice")
    def test_real_voice(self):
        res, d, *_ = self.run_job(os.environ["LV_REAL_VOICE"],
                                  ["Welcome to this short test lecture.", "",
                                   "This page has a longer narration. It has two sentences."])
        print(f"\n  real voice: {res['out']} {res['seconds']:.2f}s, pages {[round(x, 2) for x in res['page_seconds']]}")
        self.assertGreater(res["page_seconds"][2], res["page_seconds"][0])
        keep = os.environ.get("LV_KEEP")
        if keep:
            shutil.copy(res["out"], keep)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
