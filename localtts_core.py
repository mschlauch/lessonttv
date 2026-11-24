"""LocalTTS core: voice catalog, model downloads, and a resource-limited speech worker.

No GUI code lives here, so it can be tested and reused on its own.

Engines
  * Kokoro (via kokoro-onnx): English, Spanish, Brazilian Portuguese
  * Piper  (via piper-tts):   English, Spanish, Portuguese (BR + PT), German,
                              Swedish, Finnish, Lithuanian
  * Edge   (via edge-tts):    online Microsoft voices for quick previews
                              (needs internet; unofficial service)

Synthesis runs in a separate process so that
  * its CPU priority and thread count can be limited,
  * all of its RAM is returned to the system when it is stopped, and
  * a crash or out-of-memory kill cannot take the window down with it.
"""
import os
import re
import sys
import json
import time
import wave
import shutil
import tempfile
import urllib.request
import urllib.error
import multiprocessing as mp

# --------------------------------------------------------------------------- paths


def home_dir():
    return os.environ.get("LOCALTTS_DIR") or os.path.join(os.path.expanduser("~"), ".localtts")


def app_base():
    f = sys.executable if getattr(sys, "frozen", False) else __file__
    return os.path.dirname(os.path.abspath(f))


def search_dirs():
    """Model folders, in order: 'models' next to the app (for offline bundles), then home."""
    return [os.path.join(app_base(), "models"), home_dir()]


def find_file(rel):
    for d in search_dirs():
        p = os.path.join(d, rel)
        if os.path.isfile(p):
            return p
    return None


# --------------------------------------------------------------------------- catalog

LANGUAGES = [
    ("en", "English"), ("es", "Español"), ("pt-BR", "Português (Brasil)"),
    ("pt-PT", "Português (Portugal)"), ("de", "Deutsch"), ("sv", "Svenska"),
    ("fi", "Suomi"), ("lt", "Lietuvių"),
]


def _k(name, label, lang):
    return dict(engine="kokoro", id="k:" + name, name=name, lang=lang,
                label=f"{label} - Kokoro")


def _p(locale, name, label, qualities):
    return dict(engine="piper", id=f"p:{locale}-{name}-{qualities[0]}", locale=locale,
                name=name, qualities=qualities, label=f"{label} - Piper")


def _e(name, label):
    return dict(engine="edge", id="e:" + name, name=name, label=f"{label} - Edge (online)")


VOICES = {
    "en": [
        _k("af_heart", "Heart (US, female)", "en-us"), _k("af_bella", "Bella (US, female)", "en-us"),
        _k("af_nicole", "Nicole (US, female)", "en-us"), _k("af_sarah", "Sarah (US, female)", "en-us"),
        _k("af_sky", "Sky (US, female)", "en-us"), _k("af_nova", "Nova (US, female)", "en-us"),
        _k("af_alloy", "Alloy (US, female)", "en-us"), _k("af_aoede", "Aoede (US, female)", "en-us"),
        _k("af_jessica", "Jessica (US, female)", "en-us"), _k("af_kore", "Kore (US, female)", "en-us"),
        _k("af_river", "River (US, female)", "en-us"),
        _k("am_adam", "Adam (US, male)", "en-us"), _k("am_echo", "Echo (US, male)", "en-us"),
        _k("am_eric", "Eric (US, male)", "en-us"), _k("am_fenrir", "Fenrir (US, male)", "en-us"),
        _k("am_liam", "Liam (US, male)", "en-us"), _k("am_michael", "Michael (US, male)", "en-us"),
        _k("am_onyx", "Onyx (US, male)", "en-us"), _k("am_puck", "Puck (US, male)", "en-us"),
        _k("bf_alice", "Alice (UK, female)", "en-gb"), _k("bf_emma", "Emma (UK, female)", "en-gb"),
        _k("bf_isabella", "Isabella (UK, female)", "en-gb"), _k("bf_lily", "Lily (UK, female)", "en-gb"),
        _k("bm_daniel", "Daniel (UK, male)", "en-gb"), _k("bm_fable", "Fable (UK, male)", "en-gb"),
        _k("bm_george", "George (UK, male)", "en-gb"), _k("bm_lewis", "Lewis (UK, male)", "en-gb"),
        _p("en_US", "lessac", "Lessac (US)", ["medium"]),
        _p("en_GB", "alba", "Alba (UK)", ["medium"]),
        _e("en-US-AriaNeural", "Aria (US, female)"), _e("en-US-JennyNeural", "Jenny (US, female)"),
        _e("en-US-GuyNeural", "Guy (US, male)"), _e("en-GB-SoniaNeural", "Sonia (UK, female)"),
        _e("en-GB-RyanNeural", "Ryan (UK, male)"),
    ],
    "es": [
        _k("ef_dora", "Dora (female)", "es"), _k("em_alex", "Alex (male)", "es"),
        _k("em_santa", "Santa (male)", "es"),
        _p("es_ES", "davefx", "Davefx (Spain)", ["medium"]),
        _p("es_ES", "sharvard", "Sharvard (Spain)", ["medium"]),
        _e("es-ES-ElviraNeural", "Elvira (Spain, female)"), _e("es-ES-AlvaroNeural", "Álvaro (Spain, male)"),
        _e("es-MX-DaliaNeural", "Dalia (Mexico, female)"),
    ],
    "pt-BR": [
        _k("pf_dora", "Dora (female)", "pt-br"), _k("pm_alex", "Alex (male)", "pt-br"),
        _k("pm_santa", "Santa (male)", "pt-br"),
        _p("pt_BR", "faber", "Faber", ["medium"]), _p("pt_BR", "cadu", "Cadu", ["medium"]),
        _p("pt_BR", "jeff", "Jeff", ["medium"]), _p("pt_BR", "edresson", "Edresson", ["low"]),
        _e("pt-BR-FranciscaNeural", "Francisca (female)"), _e("pt-BR-AntonioNeural", "Antônio (male)"),
    ],
    "pt-PT": [
        _p("pt_PT", "tugao", "Tugão", ["medium"]),
        _e("pt-PT-RaquelNeural", "Raquel (female)"), _e("pt-PT-DuarteNeural", "Duarte (male)"),
    ],
    "de": [
        _p("de_DE", "thorsten", "Thorsten", ["medium"]),
        _p("de_DE", "thorsten", "Thorsten (high quality, larger)", ["high"]),
        _p("de_DE", "eva_k", "Eva K (compact)", ["x_low"]),
        _p("de_DE", "ramona", "Ramona", ["low"]),
        _p("de_DE", "kerstin", "Kerstin", ["low", "medium", "x_low"]),
        _p("de_DE", "karlsson", "Karlsson", ["low", "medium", "x_low"]),
        _e("de-DE-KatjaNeural", "Katja (female)"), _e("de-DE-ConradNeural", "Conrad (male)"),
    ],
    "sv": [
        _p("sv_SE", "lisa", "Lisa", ["medium"]), _p("sv_SE", "nst", "NST", ["medium"]),
        _p("sv_SE", "alma", "Alma", ["medium"]),
        _e("sv-SE-SofieNeural", "Sofie (female)"), _e("sv-SE-MattiasNeural", "Mattias (male)"),
    ],
    "fi": [
        _p("fi_FI", "harri", "Harri", ["medium"]),
        _p("fi_FI", "harri", "Harri (compact)", ["low"]),
        _e("fi-FI-NooraNeural", "Noora (female)"), _e("fi-FI-HarriNeural", "Harri (male)"),
    ],
    "lt": [
        _p("lt_LT", "reginute1", "Reginute", ["medium", "low", "x_low", "high"]),
        _e("lt-LT-OnaNeural", "Ona (female)"), _e("lt-LT-LeonasNeural", "Leonas (male)"),
    ],
}

VOICE_BY_ID = {v["id"]: v for vs in VOICES.values() for v in vs}
LANG_NAMES = dict(LANGUAGES)

# --------------------------------------------------------------------------- download

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


class NeedsDownload(Exception):
    pass


def _download(url, dest, cb=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "LocalTTS/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if cb:
                    cb(got, total)
    except urllib.error.HTTPError:
        raise                                    # callers handle "not found" themselves
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Could not download {os.path.basename(dest)} "
                           f"({getattr(e, 'reason', e)}). Check your internet connection.")
    os.replace(tmp, dest)


def kokoro_paths():
    paths = [find_file(os.path.join("kokoro", n)) for n in KOKORO_FILES]
    return paths if all(paths) else None


def ensure_kokoro(progress=None):
    """Return (model, voices) paths, downloading (~350 MB, once) if missing."""
    have = kokoro_paths()
    if have:
        return have
    out = []
    for i, n in enumerate(KOKORO_FILES):
        dest = os.path.join(home_dir(), "kokoro", n)
        if not os.path.isfile(dest):
            _download(KOKORO_BASE + n, dest,
                      (lambda g, t, i=i: progress and t and
                       progress((i + g / t) / len(KOKORO_FILES), f"Downloading Kokoro model ({n})")))
        out.append(dest)
    return out


def piper_paths(voice):
    for q in voice["qualities"]:
        stem = f"{voice['locale']}-{voice['name']}-{q}"
        onnx = find_file(os.path.join("piper", stem + ".onnx"))
        cfg = find_file(os.path.join("piper", stem + ".onnx.json"))
        if onnx and cfg:
            return onnx, cfg
    return None


def ensure_piper(voice, progress=None):
    """Return (onnx, config) paths for a Piper voice, downloading if missing."""
    have = piper_paths(voice)
    if have:
        return have
    fam = voice["locale"].split("_")[0]
    errors = []
    for q in voice["qualities"]:
        stem = f"{voice['locale']}-{voice['name']}-{q}"
        url = f"{PIPER_BASE}/{fam}/{voice['locale']}/{voice['name']}/{q}/{stem}"
        d = os.path.join(home_dir(), "piper")
        try:
            _download(url + ".onnx.json", os.path.join(d, stem + ".onnx.json"))
            _download(url + ".onnx", os.path.join(d, stem + ".onnx"),
                      lambda g, t: progress and t and progress(g / t, f"Downloading voice {voice['name']}"))
            return os.path.join(d, stem + ".onnx"), os.path.join(d, stem + ".onnx.json")
        except urllib.error.HTTPError as e:
            errors.append(f"{q}: HTTP {e.code}")
            for ext in (".onnx.json", ".onnx", ".onnx.part"):   # clean partial files
                try:
                    os.remove(os.path.join(d, stem + ext))
                except OSError:
                    pass
    raise RuntimeError(f"Voice not found on the server ({', '.join(errors)}).")


def is_ready(voice):
    if voice["engine"] == "edge":
        return True                      # nothing to download (but needs internet to speak)
    return bool(kokoro_paths() if voice["engine"] == "kokoro" else piper_paths(voice))


def ensure_voice(voice, progress=None):
    if voice["engine"] == "edge":
        return []
    return ensure_kokoro(progress) if voice["engine"] == "kokoro" else ensure_piper(voice, progress)


# --------------------------------------------------------------------------- performance

def perf_settings(level):
    """Thread count, CPU duty cycle (0 = no pauses) and priority for a level."""
    cpu = os.cpu_count() or 2
    if level == "light":
        return dict(threads=1, duty=1.0, prio=2)
    if level == "fast":
        return dict(threads=cpu if cpu <= 2 else max(2, min(4, cpu // 2)), duty=0.0, prio=0)
    return dict(threads=max(1, min(2, cpu // 4)), duty=0.0, prio=1)      # balanced


def perf_description(level):
    s = perf_settings(level)
    pr = {2: "lowest priority", 1: "lower priority", 0: "normal priority"}[s["prio"]]
    extra = ", pauses to keep CPU use around half" if s["duty"] else ""
    return f"{s['threads']} CPU thread{'s' if s['threads'] > 1 else ''}, {pr}{extra}"


def _set_priority(prio):
    if prio == 0:
        return
    try:
        if sys.platform.startswith("win"):
            import ctypes
            cls = {1: 0x4000, 2: 0x40}[prio]       # BELOW_NORMAL / IDLE
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), cls)
        else:
            os.nice({1: 10, 2: 19}[prio])
    except Exception:
        pass


# --------------------------------------------------------------------------- text

def split_text(text, maxlen=250):
    """Split into sentence-sized chunks so memory use stays flat for long texts."""
    chunks = []
    for para in re.split(r"\n+", text.strip()):
        para = re.sub(r"[ \t]+", " ", para).strip()
        if not para:
            continue
        cur = ""
        for s in re.split(r"(?<=[.!?…。！？])\s+", para):
            if len(cur) + len(s) + 1 <= maxlen:
                cur = (cur + " " + s).strip()
                continue
            if cur:
                chunks.append(cur)
            while len(s) > maxlen:
                cut = max(s.rfind(", ", 0, maxlen), s.rfind(" ", 0, maxlen))
                cut = cut if cut > 50 else maxlen
                chunks.append(s[:cut].strip())
                s = s[cut:].strip()
            cur = s
        if cur:
            chunks.append(cur)
    return [c for c in chunks if c]


# --------------------------------------------------------------------------- worker process

def _ort_options(threads):
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.intra_op_num_threads = threads
    o.inter_op_num_threads = 1
    o.enable_cpu_mem_arena = False       # hand memory back to the OS promptly
    return o


def _load_engine(req, threads, cache):
    key = (req["engine"], tuple(req["files"]))
    if key in cache:
        return cache[key]
    cache.clear()
    import gc
    gc.collect()
    import onnxruntime as ort
    if req["engine"] == "kokoro":
        from kokoro_onnx import Kokoro
        sess = ort.InferenceSession(req["files"][0], sess_options=_ort_options(threads),
                                    providers=["CPUExecutionProvider"])
        eng = Kokoro.from_session(sess, req["files"][1])
    else:
        from piper import PiperVoice
        from piper.config import PiperConfig
        with open(req["files"][1], "r", encoding="utf-8") as f:
            cfg_dict = json.load(f)
        # The Lithuanian voice declares phoneme_type "lithuanian", which the released
        # piper-tts does not know yet. Load it as a normal voice, then swap in the
        # bundled Lithuanian phonemizer (espeak-ng + stress dictionary).
        lithuanian = cfg_dict.get("phoneme_type") == "lithuanian"
        if lithuanian:
            cfg_dict["phoneme_type"] = "espeak"
        cfg = PiperConfig.from_dict(cfg_dict)
        sess = ort.InferenceSession(req["files"][0], sess_options=_ort_options(threads),
                                    providers=["CPUExecutionProvider"])
        eng = PiperVoice(sess, cfg)
        if lithuanian:
            from localtts_lt import LithuanianPhonemizer
            eng.phonemize = LithuanianPhonemizer().phonemize
    cache[key] = eng
    return eng


async def _edge_save(edge_tts, text, voice, rate, path):
    mock = os.environ.get("LOCALTTS_EDGE_MOCK")     # test hook: use a prepared MP3 instead of the network
    if mock:
        shutil.copyfile(mock, path)
        return
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def _edge_chunk(text, voice, speed):
    import asyncio
    import numpy as np
    import soundfile as sf
    import edge_tts
    rate = f"{int(round((speed - 1.0) * 100)):+d}%"
    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        try:
            asyncio.run(_edge_save(edge_tts, text, voice, rate, tmp))
        except edge_tts.exceptions.NoAudioReceived:
            raise RuntimeError(f"The online voice returned no audio for this text ({voice}).")
        except Exception as e:
            raise RuntimeError("Could not reach Microsoft's online voice service "
                               f"({type(e).__name__}). Edge voices need an internet connection.")
        data, sr = sf.read(tmp, dtype="float32", always_2d=True)
        return np.asarray(data.mean(axis=1), dtype=np.float32), int(sr)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _synth_chunk(eng, req, text):
    import numpy as np
    if req["engine"] == "edge":
        return _edge_chunk(text, req["voice"], req["speed"])
    if req["engine"] == "kokoro":
        a, sr = eng.create(text, voice=req["voice"], speed=req["speed"], lang=req["lang"])
        return np.asarray(a, dtype=np.float32), int(sr)
    from piper import SynthesisConfig
    parts, sr = [], 22050
    for ch in eng.synthesize(text, SynthesisConfig(length_scale=1.0 / req["speed"])):
        parts.append(ch.audio_float_array)
        sr = ch.sample_rate
    return (np.concatenate(parts) if parts else np.zeros(0, np.float32)), int(sr)


def _worker_main(conn, level):
    s = perf_settings(level)
    _set_priority(s["prio"])
    os.environ["OMP_NUM_THREADS"] = str(s["threads"])
    import numpy as np
    cache = {}
    while True:
        try:
            req = conn.recv()
        except (EOFError, OSError):
            return
        if req.get("cmd") == "quit":
            return
        try:
            eng = None if req["engine"] == "edge" else _load_engine(req, s["threads"], cache)
            chunks, out, w, secs = req["chunks"], req["out"], None, 0.0
            for i, text in enumerate(chunks):
                t0 = time.time()
                a, sr = _synth_chunk(eng, req, text)
                if w is None:
                    w = wave.open(out, "wb")
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(sr)
                gap = np.zeros(int(sr * 0.12), np.float32)
                pcm = (np.clip(np.concatenate([a, gap]), -1, 1) * 32767).astype(np.int16)
                w.writeframes(pcm.tobytes())
                secs += len(pcm) / sr
                conn.send(("progress", i + 1, len(chunks)))
                if s["duty"]:
                    time.sleep((time.time() - t0) * s["duty"])
            if w is None:
                raise RuntimeError("No speakable text.")
            w.close()
            conn.send(("done", out, secs))
        except Exception as e:                                   # report, keep worker alive
            conn.send(("error", f"{type(e).__name__}: {e}"))


class Cancelled(Exception):
    pass


class Synth:
    """Parent-side handle to the worker process."""

    def __init__(self):
        self.proc = self.conn = self.level = None
        self._cancel = False

    def _ensure(self, level):
        if self.proc and self.proc.is_alive() and self.level == level:
            return
        self.stop()
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main, args=(child, level), daemon=True)
        self.proc.start()
        child.close()
        self.conn, self.level = parent, level

    def running(self):
        return bool(self.proc and self.proc.is_alive())

    def run(self, req, level, on_progress=None):
        """Blocking. Returns (wav_path, seconds). Call from a background thread."""
        self._cancel = False
        self._ensure(level)
        proc, conn = self.proc, self.conn          # local refs: cancel() may clear self.*
        try:
            conn.send(req)
            while True:
                if conn.poll(0.25):
                    msg = conn.recv()
                    if msg[0] == "progress":
                        on_progress and on_progress(msg[1], msg[2])
                    elif msg[0] == "done":
                        return msg[1], msg[2]
                    else:
                        raise RuntimeError(msg[1])
                elif not proc.is_alive():
                    raise EOFError
        except (EOFError, OSError):
            if self._cancel:
                raise Cancelled()
            code = proc.exitcode
            self.stop()
            raise RuntimeError(f"The speech process stopped unexpectedly (exit code {code}). "
                               "If the computer was low on memory, try the Light setting "
                               "or shorter text.")

    def cancel(self):
        self._cancel = True
        self.stop()

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.is_alive():
            p.terminate()
            p.join(2)
        try:
            self.conn and self.conn.close()
        except Exception:
            pass
        self.conn = None


def build_request(voice, text, speed, out_path, progress=None):
    files = list(ensure_voice(voice, progress))
    req = dict(engine=voice["engine"], files=files, speed=float(speed),
               chunks=split_text(text, 600 if voice["engine"] == "edge" else 250), out=out_path)
    if voice["engine"] == "kokoro":
        req.update(voice=voice["name"], lang=voice["lang"])
    elif voice["engine"] == "edge":
        req.update(voice=voice["name"])
    return req


# --------------------------------------------------------------------------- batch export

def _slug(text, maxlen=40):
    t = re.sub(r"[^\w\- ]+", "", text, flags=re.UNICODE).strip().replace(" ", "_")
    return t[:maxlen].strip("_")


def batch_filenames(items, ext):
    """One unique file name per item, numbered in list order."""
    used, names = set(), []
    for i, it in enumerate(items, 1):
        v = VOICE_BY_ID[it["voice"]]
        label = v["label"].split(" - ")[0].split(" (")[0]
        if it.get("name"):
            stem = _slug(it["name"], 60) or f"item{i}"
        else:
            words = " ".join(it["text"].split()[:4])
            stem = f"{i:03d}_{it['lang']}_{_slug(label, 14)}_{_slug(words, 28)}"
        name, n = stem + ext, 2
        while name.lower() in used:
            name = f"{stem}_{n}{ext}"
            n += 1
        used.add(name.lower())
        names.append(name)
    return names


def write_audio(wav_path, dest):
    """Copy a WAV to dest, converting to MP3 when dest ends in .mp3."""
    if dest.lower().endswith(".mp3"):
        import soundfile as sf
        data, sr = sf.read(wav_path, dtype="float32")
        sf.write(dest, data, sr, format="MP3")
    else:
        shutil.copyfile(wav_path, dest)


def run_batch(items, folder, synth, level, ext=".wav", on_progress=None, should_stop=None):
    """Synthesize every item into `folder`. Blocking - call from a background thread.

    Items are processed grouped by voice, so each voice loads only once; file names still
    follow the list order. A failing item is recorded and the batch continues.
    Returns (results, cancelled); results = [(index, ok, file_name, seconds_or_error)].
    """
    os.makedirs(folder, exist_ok=True)
    names = batch_filenames(items, ext)
    order = sorted(range(len(items)), key=lambda i: items[i]["voice"])
    tmp = os.path.join(tempfile.gettempdir(), f"localtts_batch_{os.getpid()}.wav")
    results, cancelled = [], False
    for n, i in enumerate(order):
        if should_stop and should_stop():
            cancelled = True
            break
        it = items[i]
        voice = VOICE_BY_ID[it["voice"]]

        def prog(frac, label, n=n, i=i):
            on_progress and on_progress(n, len(items), names[i], frac, label)
        try:
            prog(0.0, "Preparing...")
            req = build_request(voice, it["text"], it["speed"], tmp, progress=prog)
            path, secs = synth.run(req, level, lambda p, t, prog=prog: prog(p / t, f"part {p} of {t}"))
            write_audio(path, os.path.join(folder, names[i]))
            results.append((i, True, names[i], secs))
        except Cancelled:
            cancelled = True
            break
        except Exception as e:
            results.append((i, False, names[i], str(e)))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return sorted(results), cancelled
