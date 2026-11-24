#!/usr/bin/env python3
"""Lecture video: slides (PDF) + lecture script (JSON, "lecture-script" v3) -> narrated MP4.

No GUI code lives here; localtts_gui.py has a "Lecture video" tab that calls make_video(),
and this file can also be run from the command line (see --help).

How it works
  1. Read the script and check it against the PDF. If anything does not match, stop
     with a clear message - never guess a page mapping.
  2. Speak each page's narration with a LocalTTS voice (Edge, Kokoro or Piper), one WAV
     per page. Finished pages are cached, so after editing one page only that page is
     spoken again.
  3. Measure every WAV (frames / sample rate). That measured length - never the script's
     est_duration_sec - decides how long the page is shown.
  4. Join all audio into one track. Show page N from where its audio starts to where it
     ends. Page change times are rounded to whole video frames from the running total,
     so rounding never adds up over many pages.
  5. Encode with the ffmpeg program that ships inside the imageio-ffmpeg package, so
     nobody has to install ffmpeg. The PDF is drawn with pypdfium2 (also bundled).

Pages with empty narration are shown for a fixed time (default 2.5 s) with silence.
The fields comment and prompt_note are never spoken (prompt_note is ignored for now).
"""
import os
import re
import sys
import json
import time
import wave
import shutil
import hashlib
import argparse
import subprocess
import unicodedata

import localtts_core as core

FORMAT = "lecture-script"
SCHEMA_VERSION = 3
FPS = 25
SILENT_HOLD = 2.5
RESOLUTIONS = [(480, "480p (small file)"), (720, "720p (HD)"), (1080, "1080p (Full HD)"),
               (1440, "1440p (sharp, large file)")]
DEFAULT_HEIGHT = 720
EDGE_RETRIES = 3          # attempts per page for the online voice (it rate-limits)
EDGE_PAUSE = 1.0          # seconds between online requests


class ScriptError(Exception):
    """The script or PDF cannot be used. The message is meant for the user."""


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- script

def load_script(path):
    """Read and check the JSON structure. Returns the parsed dict."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ScriptError(f"The script file is not valid JSON (line {e.lineno}): {e.msg}")
    except OSError as e:
        raise ScriptError(f"Could not read the script file: {e}")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ScriptError(f"This is not a lecture script (expected \"format\": \"{FORMAT}\"). "
                          "Export it again from the script editor.")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ScriptError(f"This script uses schema version {data.get('schema_version')!r}; only version "
                          f"{SCHEMA_VERSION} is supported. Older files can be converted with the "
                          "editor's convert-legacy script.")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ScriptError("The script has no pages.")
    for i, p in enumerate(pages, 1):
        if not isinstance(p, dict):
            raise ScriptError(f"Entry {i} in \"pages\" is not an object.")
        n = p.get("pdf_page")
        if not isinstance(n, int) or isinstance(n, bool):
            raise ScriptError(f"Entry {i} in \"pages\" has no valid pdf_page number.")
        if not isinstance(p.get("narration", ""), str):
            raise ScriptError(f"Page {n}: narration is not text.")
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    return data


def pdf_page_count(pdf_path):
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(pdf_path)
    except Exception as e:
        raise ScriptError(f"Could not open the PDF: {e}")
    try:
        return len(doc)
    finally:
        doc.close()


def validate(script, pdf_path):
    """Check script against the real PDF. Returns (pages sorted by pdf_page, warnings).

    Raises ScriptError when pages and PDF do not match one to one."""
    pages = script["pages"]
    n = pdf_page_count(pdf_path)
    nums = sorted(p["pdf_page"] for p in pages)
    dupes = sorted({x for x in nums if nums.count(x) > 1})
    missing = sorted(set(range(1, n + 1)) - set(nums))
    extra = sorted(x for x in set(nums) if x < 1 or x > n)
    problems = []
    if len(pages) != n:
        problems.append(f"The PDF has {n} pages but the script has {len(pages)} entries.")
    if dupes:
        problems.append("Page numbers used more than once: " + _short_list(dupes))
    if missing:
        problems.append("PDF pages without a script entry: " + _short_list(missing))
    if extra:
        problems.append(f"Script entries for pages the PDF does not have (it has {n}): " + _short_list(extra))
    if problems:
        raise ScriptError("The script does not match this PDF.\n" + "\n".join(problems) +
                          "\nCheck that you picked the right PDF, or fix the pages in the script editor.")
    warnings = []
    src = (script["meta"].get("source_pdf") or {})
    want = src.get("filename") if isinstance(src, dict) else None
    if want and want != os.path.basename(pdf_path):
        warnings.append(f"The script was written for \"{want}\", but you picked "
                        f"\"{os.path.basename(pdf_path)}\". The page count matches, so it will be used.")
    return sorted(pages, key=lambda p: p["pdf_page"]), warnings


def _short_list(xs, limit=12):
    s = ", ".join(str(x) for x in xs[:limit])
    return s + (f" (+{len(xs) - limit} more)" if len(xs) > limit else "")


_DROP = {"­", "​", "‌", "‍", "⁠", "﻿"}


def clean_text(text):
    """Make narration safe for TTS: drop invisible/control characters, tidy spaces."""
    text = unicodedata.normalize("NFC", text or "")
    out = []
    for ch in text:
        if ch in _DROP:
            continue
        if ch in "\n\t":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Zl", "Zp"):
            out.append("\n")
        elif cat[0] == "C":                       # control, private use, unassigned, surrogates
            out.append(" ")
        else:
            out.append(ch)
    text = re.sub(r"[ \t ]+", " ", "".join(out))
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def summary(script):
    """Facts for the user before generating."""
    pages = script["pages"]
    meta = script["meta"]
    silent = sorted(p["pdf_page"] for p in pages if not clean_text(p.get("narration", "")))
    est = sum(p.get("est_duration_sec") or 0 for p in pages if isinstance(p.get("est_duration_sec"), (int, float)))
    return dict(title=meta.get("title") or "", language=meta.get("language") or "",
                pages=len(pages), silent=silent, est_seconds=est)


# --------------------------------------------------------------------------- voices

def language_code(bcp47):
    """Map a BCP-47 tag like 'de-DE' to a LocalTTS language code, or None."""
    parts = re.split(r"[-_]", (bcp47 or "").strip().lower())
    prim, region = parts[0], next((p for p in parts[1:] if len(p) == 2), "")
    if prim == "pt":
        return "pt-BR" if region == "br" else "pt-PT"
    return prim if prim in ("en", "es", "de", "sv", "fi", "lt") else None


def default_voice(bcp47, engine="edge"):
    """Pick a voice for the script language. engine 'edge' (online) or 'offline'."""
    code = language_code(bcp47)
    if not code:
        return None
    voices = core.VOICES[code]
    parts = re.split(r"[-_]", bcp47.strip())
    locale = "-".join([parts[0].lower()] + [p.upper() for p in parts[1:2]]) if len(parts) > 1 else ""
    if engine == "edge":
        pool = [v for v in voices if v["engine"] == "edge"]
        return next((v for v in pool if locale and v["name"].startswith(locale + "-")), pool[0] if pool else None)
    pool = [v for v in voices if v["engine"] != "edge"]
    if code == "en" and locale == "en-GB":
        return next((v for v in pool if v.get("lang") == "en-gb"), pool[0])
    return pool[0] if pool else None


# --------------------------------------------------------------------------- pairing

def find_pdf_for_script(script_path):
    folder = os.path.dirname(os.path.abspath(script_path))
    try:
        with open(script_path, "r", encoding="utf-8-sig") as f:
            name = ((json.load(f).get("meta") or {}).get("source_pdf") or {}).get("filename")
    except Exception:
        name = None
    stem = os.path.basename(script_path)
    stem = stem[:-len(".script.json")] if stem.lower().endswith(".script.json") else os.path.splitext(stem)[0]
    for cand in (name, stem + ".pdf"):
        if cand and os.path.isfile(os.path.join(folder, os.path.basename(cand))):
            return os.path.join(folder, os.path.basename(cand))
    return None


def find_script_for_pdf(pdf_path):
    folder = os.path.dirname(os.path.abspath(pdf_path))
    base = os.path.basename(pdf_path)
    stem = os.path.splitext(base)[0]
    direct = os.path.join(folder, stem + ".script.json")
    if os.path.isfile(direct):
        return direct
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".json"))[:300]
    except OSError:
        return None
    for n in names:
        p = os.path.join(folder, n)
        try:
            if os.path.getsize(p) > 20_000_000:
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("format") == FORMAT and \
                    ((d.get("meta") or {}).get("source_pdf") or {}).get("filename") == base:
                return p
        except Exception:
            continue
    return None


def default_output(pdf_path):
    return os.path.splitext(os.path.abspath(pdf_path))[0] + ".mp4"


# --------------------------------------------------------------------------- tools

_FFMPEG = []


def bundled_ffmpeg():
    """Path of the ffmpeg binary inside the imageio-ffmpeg package (None if missing)."""
    try:
        from imageio_ffmpeg._utils import _get_bin_dir
        from imageio_ffmpeg._definitions import FNAME_PER_PLATFORM, get_platform
        exe = os.path.join(_get_bin_dir(), FNAME_PER_PLATFORM.get(get_platform(), ""))
        return exe if os.path.isfile(exe) else None
    except Exception:
        return None


def ffmpeg_exe():
    """The ffmpeg program shipped with the app (falls back to an installed one)."""
    if not _FFMPEG:
        exe = bundled_ffmpeg()
        # Packaging can drop the "executable" permission; without it imageio-ffmpeg would
        # silently look for a system ffmpeg, which colleagues usually do not have.
        if exe and not sys.platform.startswith("win") and not os.access(exe, os.X_OK):
            try:
                os.chmod(exe, 0o755)
            except OSError:
                pass
        import imageio_ffmpeg
        _FFMPEG.append(imageio_ffmpeg.get_ffmpeg_exe())
    return _FFMPEG[0]


def _popen_kwargs():
    if sys.platform.startswith("win"):            # no black console window from the GUI app
        return dict(creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {}


def media_duration(path):
    """Duration in seconds as reported by ffmpeg for a finished file."""
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", path],
                       capture_output=True, text=True, errors="replace", **_popen_kwargs())
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        raise RuntimeError(f"Could not read the length of {os.path.basename(path)}.")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def work_dir(pdf_path):
    stem = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(pdf_path))[0])[:40] or "lesson"
    h = hashlib.sha1(os.path.abspath(pdf_path).encode("utf-8")).hexdigest()[:8]
    return os.path.join(core.home_dir(), "video_work", f"{stem}-{h}")


def frame_size(pdf_path, height):
    """(width, height) of the video: the first page's shape at the chosen height, even numbers."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    try:
        w, h = doc[0].get_size()
    finally:
        doc.close()
    height = int(height) // 2 * 2
    width = max(2, int(round(height * w / h / 2)) * 2)
    return width, height


def render_pages(pdf_path, size, out_dir, on_page=None, should_stop=None):
    """Draw every page centred on a white canvas of exactly `size`. Returns PNG paths."""
    import pypdfium2 as pdfium
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    W, H = size
    paths = []
    doc = pdfium.PdfDocument(pdf_path)
    try:
        n = len(doc)
        for i in range(n):
            if should_stop and should_stop():
                raise Cancelled()
            page = doc[i]
            pw, ph = page.get_size()
            scale = min(W / pw, H / ph)
            img = page.render(scale=scale, fill_color=(255, 255, 255, 255)).to_pil().convert("RGB")
            if img.size != (W, H):
                canvas = Image.new("RGB", (W, H), "white")
                canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
                img = canvas
            p = os.path.join(out_dir, f"page_{i + 1:04d}.png")
            img.save(p, compress_level=1)
            paths.append(p)
            on_page and on_page(i + 1, n)
    finally:
        doc.close()
    return paths


# --------------------------------------------------------------------------- audio

def wav_frames(path):
    """(frames, sample rate) of a WAV, read from the file itself."""
    with wave.open(path, "rb") as w:
        return w.getnframes(), w.getframerate()


def synthesize_page(synth, voice, speed, text, cache_dir, level,
                    on_part=None, should_stop=None, sleep=time.sleep):
    """Speak `text` into a cached WAV. Returns (path, from_cache)."""
    key = hashlib.sha256(json.dumps([voice["id"], round(float(speed), 2), text]).encode("utf-8")).hexdigest()[:24]
    path = os.path.join(cache_dir, key + ".wav")
    if os.path.isfile(path):
        try:
            if wav_frames(path)[0] > 0:
                return path, True
        except Exception:
            pass
    os.makedirs(cache_dir, exist_ok=True)
    tmp = path[:-4] + ".part.wav"
    tries = EDGE_RETRIES if voice["engine"] == "edge" else 1
    for attempt in range(tries):
        try:
            req = core.build_request(voice, text, speed, tmp)
            synth.run(req, level, on_part)
            break
        except core.Cancelled:
            raise Cancelled()
        except core.NeedsDownload:
            raise
        except Exception:
            if attempt == tries - 1:
                raise
            for _ in range(int(5 * (attempt + 1) / 0.25)):     # wait, but stay responsive to Stop
                if should_stop and should_stop():
                    raise Cancelled()
                sleep(0.25)
    if wav_frames(tmp)[0] <= 0:
        raise RuntimeError("The voice produced no audio.")
    os.replace(tmp, path)
    return path, False


def build_soundtrack(entries, out_wav):
    """Join the page audio into one mono 16-bit WAV.

    entries: list of (wav_path or None, silent_seconds). Returns per-page frame counts and
    the sample rate; page lengths are exactly what is written, not an estimate."""
    import numpy as np
    rate = next((wav_frames(p)[1] for p, _ in entries if p), 24000)
    counts = []
    with wave.open(out_wav, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        for p, hold in entries:
            if p is None:
                n = max(1, int(round(hold * rate)))
                out.writeframes(np.zeros(n, np.int16).tobytes())
                counts.append(n)
                continue
            with wave.open(p, "rb") as w:
                ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                raw = w.readframes(w.getnframes())
            if sw != 2:
                raise RuntimeError(f"Unexpected audio format in {os.path.basename(p)}.")
            a = np.frombuffer(raw, np.int16)
            if ch > 1:
                a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
            if sr != rate:                                   # only if voices were mixed
                n_out = int(round(len(a) * rate / sr))
                a = np.interp(np.linspace(0, len(a) - 1, n_out), np.arange(len(a)), a).astype(np.int16)
            out.writeframes(a.tobytes())
            counts.append(len(a))
    return counts, rate


def page_times(counts, rate, fps=FPS):
    """Per-page display time in seconds, each a whole number of frames.

    Each page change is placed at the frame nearest to the true audio boundary, computed
    from the running total, so the error stays under half a frame and never accumulates."""
    out, total, prev = [], 0, 0
    for c in counts:
        total += c
        end = max(prev + 1, int(round(total / rate * fps)))
        out.append((end - prev) / fps)
        prev = end
    return out


# --------------------------------------------------------------------------- video

def encode(images, durations, audio, out_mp4, total_seconds, on_frac=None, should_stop=None, crf=26):
    """Still images with given display times + audio -> H.264/AAC MP4."""
    lst = os.path.join(os.path.dirname(audio), "pages.ffconcat")
    q = lambda p: "'" + p.replace("\\", "/").replace("'", "'\\''") + "'"
    with open(lst, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for img, d in zip(images, durations):
            f.write(f"file {q(img)}\nduration {d:.6f}\n")
        f.write(f"file {q(images[-1])}\n")          # the last entry needs repeating to be used
    tmp = out_mp4 + ".part.mp4"
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", lst, "-i", audio,
           "-map", "0:v:0", "-map", "1:a:0", "-vf", f"fps={FPS},format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", str(crf),
           "-c:a", "aac", "-b:a", "128k", "-t", f"{total_seconds:.6f}",
           "-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-nostats", tmp]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", **_popen_kwargs())
    import threading
    err = []
    reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    reader.start()
    cancelled = False
    with proc:
        for line in proc.stdout:
            if should_stop and should_stop():
                cancelled = True
                proc.terminate()
                break
            if line.startswith("out_time_us=") and on_frac:
                try:
                    on_frac(min(1.0, int(line.split("=", 1)[1]) / 1e6 / max(total_seconds, 0.001)))
                except ValueError:
                    pass
        try:
            proc.wait(30 if cancelled else None)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        reader.join(5)
    if cancelled:
        _remove(tmp)
        raise Cancelled()
    if proc.returncode != 0:
        _remove(tmp)
        raise RuntimeError("Video encoding failed:\n" + ("".join(err).strip()[-800:] or f"exit code {proc.returncode}"))
    os.replace(tmp, out_mp4)


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


# --------------------------------------------------------------------------- the whole job

def make_video(pdf_path, script_path, out_mp4, voice, synth, *, speed=1.0, height=DEFAULT_HEIGHT,
               silent_hold=SILENT_HOLD, level="balanced", on_progress=None, should_stop=None):
    """Blocking - call from a background thread. Returns a dict describing the result.

    on_progress(fraction 0..1, label). should_stop() -> True cancels (also call synth.cancel()).
    """
    prog = on_progress or (lambda f, l: None)
    stop = should_stop or (lambda: False)
    script = load_script(script_path)
    pages, warnings = validate(script, pdf_path)
    if isinstance(voice, str):
        voice = core.VOICE_BY_ID[voice]
    wd = work_dir(pdf_path)
    img_dir, audio_dir = os.path.join(wd, "pages"), os.path.join(wd, "audio")
    shutil.rmtree(img_dir, ignore_errors=True)
    n = len(pages)

    # Weights of the three phases in the progress bar.
    R, S = 0.08, 0.80

    prog(0.0, "Getting the voice ready...")
    core.ensure_voice(voice, lambda f, l: prog(0.0, l))

    size = frame_size(pdf_path, height)
    images = render_pages(pdf_path, size, img_dir,
                          lambda i, t: prog(R * i / t, f"Drawing slide {i} of {t}..."), stop)

    entries, silent, used, spoken_new = [], [], set(), 0
    for k, p in enumerate(pages):
        if stop():
            raise Cancelled()
        text = clean_text(p.get("narration", ""))
        base = R + S * k / n
        if not text:
            entries.append((None, float(silent_hold)))
            silent.append(p["pdf_page"])
            continue
        if voice["engine"] == "edge" and spoken_new:
            time.sleep(EDGE_PAUSE)                        # be gentle with the online service
        label = f"Speaking page {p['pdf_page']} of {n}"
        prog(base, label + "...")
        try:
            path, cached = synthesize_page(
                synth, voice, speed, text, audio_dir, level,
                on_part=lambda i, t, base=base, label=label: prog(base + S / n * i / t, f"{label} (part {i} of {t})"),
                should_stop=stop)
        except (Cancelled, core.Cancelled):
            raise Cancelled()
        except Exception as e:
            if stop():
                raise Cancelled()
            raise RuntimeError(f"Page {p['pdf_page']}: {e}") from e
        spoken_new += 0 if cached else 1
        used.add(os.path.basename(path))
        entries.append((path, 0.0))

    audio = os.path.join(wd, "soundtrack.wav")
    prog(R + S, "Joining the audio...")
    counts, rate = build_soundtrack(entries, audio)
    durations = page_times(counts, rate)
    total = sum(counts) / rate

    os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
    encode(images, durations, audio, out_mp4, total,
           lambda f: prog(R + S + (1 - R - S) * f, f"Making the video... {int(f * 100)}%"), stop)

    got = media_duration(out_mp4)
    if abs(got - total) > 0.25:
        warnings.append(f"The finished video is {got:.2f} s long, but the audio is {total:.2f} s.")

    for name in os.listdir(audio_dir) if os.path.isdir(audio_dir) else []:   # keep the cache small
        if name not in used:
            _remove(os.path.join(audio_dir, name))
    _remove(audio)
    shutil.rmtree(img_dir, ignore_errors=True)
    prog(1.0, "Done.")
    return dict(out=out_mp4, seconds=got, pages=n, silent=silent, size=size, spoken=spoken_new,
                cached=n - len(silent) - spoken_new, warnings=warnings,
                page_seconds=[c / rate for c in counts])


# --------------------------------------------------------------------------- self-test

def selftest(folder):
    """Make a tiny video with a stand-in online voice (no network). Run by the build
    workflow against the packaged app to prove that the bundled ffmpeg, the PDF renderer
    and the speech process work there. Writes selftest.txt into `folder`; returns 0 or 1."""
    import traceback
    folder = os.path.abspath(folder)
    os.makedirs(folder, exist_ok=True)
    report = os.path.join(folder, "selftest.txt")
    lines = []
    try:
        import numpy as np
        import soundfile as sf
        from PIL import Image
        os.environ["LOCALTTS_DIR"] = os.path.join(folder, "home")
        mp3 = os.path.join(folder, "voice.mp3")
        t = np.arange(24000) / 24000
        sf.write(mp3, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), 24000, format="MP3")
        os.environ["LOCALTTS_EDGE_MOCK"] = mp3                    # read by the speech process
        pdf = os.path.join(folder, "selftest.pdf")
        pages = [Image.new("RGB", (640, 360), c) for c in ("red", "green", "blue")]
        pages[0].save(pdf, save_all=True, append_images=pages[1:])
        js = os.path.join(folder, "selftest.script.json")
        with open(js, "w", encoding="utf-8") as f:
            json.dump(dict(format=FORMAT, schema_version=SCHEMA_VERSION,
                           meta=dict(language="en-US", source_pdf=dict(filename="selftest.pdf", page_count=3)),
                           pages=[dict(pdf_page=i + 1, narration=n) for i, n in enumerate(["One.", "", "Three."])]), f)
        global EDGE_PAUSE
        EDGE_PAUSE = 0
        synth = core.Synth()
        try:
            res = make_video(pdf, js, os.path.join(folder, "selftest.mp4"), "e:en-US-AriaNeural", synth,
                             height=240, silent_hold=1.0)
        finally:
            synth.stop()
        lines += [f"video: {res['out']}", f"length: {res['seconds']:.3f} s, pages {res['page_seconds']}",
                  f"ffmpeg used: {ffmpeg_exe()}", f"ffmpeg bundled: {bundled_ffmpeg()}"]
        if not bundled_ffmpeg() or os.path.realpath(ffmpeg_exe()) != os.path.realpath(bundled_ffmpeg()):
            raise RuntimeError("The bundled ffmpeg was not used.")
        if abs(res["seconds"] - sum(res["page_seconds"])) > 0.1 or res["warnings"]:
            raise RuntimeError(f"Unexpected result: {res}")
        lines.append("SELFTEST OK")
        code = 0
    except Exception:
        lines += [traceback.format_exc(), "SELFTEST FAILED"]
        code = 1
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code


# --------------------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description="Make a narrated video from a PDF and its lecture script (v3).")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--script", help="script JSON (default: found next to the PDF)")
    ap.add_argument("--out", help="output MP4 (default: next to the PDF)")
    ap.add_argument("--voice", help="voice id, e.g. e:de-DE-KatjaNeural, k:af_heart, p:de_DE-thorsten-medium")
    ap.add_argument("--offline", action="store_true", help="use the default offline voice instead of Edge")
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="video height in pixels (default 720)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--silent-hold", type=float, default=SILENT_HOLD,
                    help="seconds to show a page that has no narration (default 2.5)")
    ap.add_argument("--level", choices=["light", "balanced", "fast"], default="balanced")
    a = ap.parse_args(argv)
    script_path = a.script or find_script_for_pdf(a.pdf)
    if not script_path:
        sys.exit("No script found next to the PDF; pass --script.")
    try:
        script = load_script(script_path)
        lang = script["meta"].get("language", "")
        voice = core.VOICE_BY_ID.get(a.voice) if a.voice else default_voice(lang, "offline" if a.offline else "edge")
        if not voice:
            sys.exit(f"No voice for language {lang!r}; pass --voice (ids are in localtts_core.VOICES).")
        synth = core.Synth()
        last = [""]

        def show(f, label):
            if label != last[0]:
                print(f"[{f * 100:5.1f}%] {label}", flush=True)
                last[0] = label
        try:
            res = make_video(a.pdf, script_path, a.out or default_output(a.pdf), voice, synth,
                             speed=a.speed, height=a.height, silent_hold=a.silent_hold, level=a.level,
                             on_progress=show)
        finally:
            synth.stop()
    except ScriptError as e:
        sys.exit(str(e))
    print(f"\nSaved {res['out']} ({res['seconds']:.1f} s, {res['size'][0]}x{res['size'][1]}, "
          f"voice {voice['id']})")
    if res["silent"]:
        print(f"Pages without narration (shown {a.silent_hold:g} s each): {_short_list(res['silent'])}")
    for w in res["warnings"]:
        print("Warning:", w)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
