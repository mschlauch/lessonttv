"""LocalTTS core: voice catalog, model downloads, and a resource-limited speech worker.

No GUI code lives here, so it can be tested and reused on its own.

Engines
  * Kokoro (via kokoro-onnx): English, Spanish, French, Italian, Brazilian Portuguese
  * Piper  (via piper-tts):   every European language with a Piper voice (see LANGUAGES);
                              the catalog follows Piper's VOICES.md, without multi-speaker models
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
import ssl
import json
import time
import wave
import shutil
import tempfile
import urllib.parse
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

LANGUAGES = [   # every European language Kokoro or Piper can speak (Latin script first)
    ("ca", "Català"),
    ("cs", "Čeština"),
    ("cy", "Cymraeg"),
    ("da", "Dansk"),
    ("de", "Deutsch"),
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("is", "Íslenska"),
    ("it", "Italiano"),
    ("lv", "Latviešu"),
    ("lb", "Lëtzebuergesch"),
    ("lt", "Lietuvių"),
    ("hu", "Magyar"),
    ("nl", "Nederlands"),
    ("no", "Norsk"),
    ("pl", "Polski"),
    ("pt-BR", "Português (Brasil)"),
    ("pt-PT", "Português (Portugal)"),
    ("ro", "Română"),
    ("sk", "Slovenčina"),
    ("sl", "Slovenščina"),
    ("fi", "Suomi"),
    ("sv", "Svenska"),
    ("tr", "Türkçe"),
    ("el", "Ελληνικά"),
    ("ru", "Русский"),
    ("sr", "Српски"),
    ("uk", "Українська"),
    ("ka", "ქართული"),
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
        _p("pt_PT", "tugão", "Tugão", ["medium"]),
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
    "ca": [
        _p("ca_ES", "upc_ona", "Upc Ona", ['medium', 'x_low']),
        _p("ca_ES", "upc_pau", "Upc Pau", ['x_low']),
        _e("ca-ES-JoanaNeural", "Joana (female)"),
        _e("ca-ES-EnricNeural", "Enric (male)"),
    ],
    "cs": [
        _p("cs_CZ", "jirka", "Jirka", ['medium', 'low']),
        _e("cs-CZ-VlastaNeural", "Vlasta (female)"),
        _e("cs-CZ-AntoninNeural", "Antonín (male)"),
    ],
    "cy": [
        _p("cy_GB", "bu_tts", "Bu Tts", ['medium']),
        _p("cy_GB", "gwryw_gogleddol", "Gwryw Gogleddol", ['medium']),
        _e("cy-GB-NiaNeural", "Nia (female)"),
        _e("cy-GB-AledNeural", "Aled (male)"),
    ],
    "da": [
        _p("da_DK", "talesyntese", "Talesyntese", ['medium']),
        _e("da-DK-ChristelNeural", "Christel (female)"),
        _e("da-DK-JeppeNeural", "Jeppe (male)"),
    ],
    "el": [
        _p("el_GR", "rapunzelina", "Rapunzelina", ['low']),
        _e("el-GR-AthinaNeural", "Athina (female)"),
        _e("el-GR-NestorasNeural", "Nestoras (male)"),
    ],
    "fr": [
        _k("ff_siwis", "Siwis (female)", "fr-fr"),
        _p("fr_FR", "siwis", "Siwis", ['medium', 'low']),
        _p("fr_FR", "tom", "Tom", ['medium']),
        _p("fr_FR", "upmc", "Upmc", ['medium']),
        _p("fr_FR", "gilles", "Gilles", ['low']),
        _e("fr-FR-DeniseNeural", "Denise (France, female)"),
        _e("fr-FR-HenriNeural", "Henri (France, male)"),
        _e("fr-BE-CharlineNeural", "Charline (Belgium, female)"),
        _e("fr-CH-ArianeNeural", "Ariane (Switzerland, female)"),
    ],
    "hu": [
        _p("hu_HU", "anna", "Anna", ['medium']),
        _p("hu_HU", "berta", "Berta", ['medium']),
        _p("hu_HU", "imre", "Imre", ['medium']),
        _e("hu-HU-NoemiNeural", "Noémi (female)"),
        _e("hu-HU-TamasNeural", "Tamás (male)"),
    ],
    "is": [
        _p("is_IS", "bui", "Bui", ['medium']),
        _p("is_IS", "salka", "Salka", ['medium']),
        _p("is_IS", "steinn", "Steinn", ['medium']),
        _p("is_IS", "ugla", "Ugla", ['medium']),
        _e("is-IS-GudrunNeural", "Guðrún (female)"),
        _e("is-IS-GunnarNeural", "Gunnar (male)"),
    ],
    "it": [
        _k("if_sara", "Sara (female)", "it"),
        _k("im_nicola", "Nicola (male)", "it"),
        _p("it_IT", "paola", "Paola", ['medium']),
        _p("it_IT", "riccardo", "Riccardo", ['x_low']),
        _e("it-IT-ElsaNeural", "Elsa (female)"),
        _e("it-IT-IsabellaNeural", "Isabella (female)"),
        _e("it-IT-DiegoNeural", "Diego (male)"),
    ],
    "ka": [
        _p("ka_GE", "natia", "Natia", ['medium']),
        _e("ka-GE-EkaNeural", "Eka (female)"),
        _e("ka-GE-GiorgiNeural", "Giorgi (male)"),
    ],
    "lb": [
        _p("lb_LU", "marylux", "Marylux", ['medium']),
    ],
    "lv": [
        _p("lv_LV", "aivars", "Aivars", ['medium']),
        _e("lv-LV-EveritaNeural", "Everita (female)"),
        _e("lv-LV-NilsNeural", "Nils (male)"),
    ],
    "nl": [
        _p("nl_NL", "pim", "Pim (Netherlands)", ['medium']),
        _p("nl_NL", "ronnie", "Ronnie (Netherlands)", ['medium']),
        _p("nl_BE", "nathalie", "Nathalie (Belgium)", ['medium', 'x_low']),
        _p("nl_BE", "rdh", "Rdh (Belgium)", ['medium', 'x_low']),
        _e("nl-NL-ColetteNeural", "Colette (Netherlands, female)"),
        _e("nl-NL-MaartenNeural", "Maarten (Netherlands, male)"),
        _e("nl-BE-DenaNeural", "Dena (Belgium, female)"),
        _e("nl-BE-ArnaudNeural", "Arnaud (Belgium, male)"),
    ],
    "no": [
        _p("no_NO", "talesyntese", "Talesyntese", ['medium']),
        _e("nb-NO-PernilleNeural", "Pernille (female)"),
        _e("nb-NO-FinnNeural", "Finn (male)"),
    ],
    "pl": [
        _p("pl_PL", "darkman", "Darkman", ['medium']),
        _p("pl_PL", "gosia", "Gosia", ['medium']),
        _p("pl_PL", "mc_speech", "Mc Speech", ['medium']),
        _e("pl-PL-ZofiaNeural", "Zofia (female)"),
        _e("pl-PL-MarekNeural", "Marek (male)"),
    ],
    "ro": [
        _p("ro_RO", "mihai", "Mihai", ['medium']),
        _e("ro-RO-AlinaNeural", "Alina (female)"),
        _e("ro-RO-EmilNeural", "Emil (male)"),
    ],
    "ru": [
        _p("ru_RU", "denis", "Denis", ['medium']),
        _p("ru_RU", "dmitri", "Dmitri", ['medium']),
        _p("ru_RU", "irina", "Irina", ['medium']),
        _p("ru_RU", "ruslan", "Ruslan", ['medium']),
        _e("ru-RU-SvetlanaNeural", "Svetlana (female)"),
        _e("ru-RU-DmitryNeural", "Dmitry (male)"),
    ],
    "sk": [
        _p("sk_SK", "lili", "Lili", ['medium']),
        _e("sk-SK-ViktoriaNeural", "Viktória (female)"),
        _e("sk-SK-LukasNeural", "Lukáš (male)"),
    ],
    "sl": [
        _p("sl_SI", "artur", "Artur", ['medium']),
        _e("sl-SI-PetraNeural", "Petra (female)"),
        _e("sl-SI-RokNeural", "Rok (male)"),
    ],
    "sr": [
        _p("sr_RS", "serbski_institut", "Serbski Institut", ['medium']),
        _e("sr-RS-SophieNeural", "Sophie (female)"),
        _e("sr-RS-NicholasNeural", "Nicholas (male)"),
    ],
    "tr": [
        _p("tr_TR", "dfki", "Dfki", ['medium']),
        _p("tr_TR", "fahrettin", "Fahrettin", ['medium']),
        _p("tr_TR", "fettah", "Fettah", ['medium']),
        _e("tr-TR-EmelNeural", "Emel (female)"),
        _e("tr-TR-AhmetNeural", "Ahmet (male)"),
    ],
    "uk": [
        _p("uk_UA", "ukrainian_tts", "Ukrainian Tts", ['medium']),
        _p("uk_UA", "lada", "Lada", ['x_low']),
        _e("uk-UA-PolinaNeural", "Polina (female)"),
        _e("uk-UA-OstapNeural", "Ostap (male)"),
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


class CertificateError(RuntimeError):
    """HTTPS certificate check failed (e.g. no CA list in the packaged Mac app, or a network
    that inspects HTTPS). Callers turn this into instructions for a manual download."""


def _is_cert_error(e):
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


class ManualDownloadError(RuntimeError):
    """A voice could not be downloaded automatically; str() holds instructions for the user,
    .files the (file name, URL) pairs and .folder where to put them."""

    def __init__(self, files, folder, reason=""):
        super().__init__(manual_download_message(files, folder, reason))
        self.files, self.folder = files, folder


def manual_download_message(files, folder, reason=""):
    """User instructions when a download failed the certificate check.
    files: list of (file name, URL) still missing."""
    if sys.platform == "darwin":
        how = ("On a Mac: in the Finder press Cmd+Shift+G (Go > Go to Folder), paste the folder "
               "path above and press Return. Then move the downloaded file(s) there.")
    elif sys.platform.startswith("win"):
        how = "On Windows: paste the folder path above into the address bar of File Explorer."
    else:
        how = "Open the folder above in your file manager" + (
            " (it is hidden: its name starts with a dot; Ctrl+L lets you type the path)." if "/." in folder else ".")
    lines = ["The voice could not be downloaded: this computer could not verify the security "
             "certificate of the download server. Your internet connection itself is probably fine.",
             "",
             "What you can do:",
             "",
             "1. Use an online voice instead: pick a voice marked \"Edge (online)\". Good for previews "
             "(your text is sent to Microsoft).",
             "",
             "2. Download the file(s) yourself in a web browser and put them into this folder "
             "(it has already been created):",
             "",
             f"   {folder}",
             ""]
    for name, url in files:
        lines += [f"   - {name}", f"     {url}"]
    lines += ["", how, "Then try again - the app finds the files and uses them offline."]
    if reason:
        lines += ["", f"(Technical detail: {reason})"]
    return "\n".join(lines)


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
        _remove_quietly(tmp)
        raise                                    # callers handle "not found" themselves
    except (urllib.error.URLError, OSError) as e:
        _remove_quietly(tmp)
        if _is_cert_error(e):
            raise CertificateError(str(getattr(e, "reason", e)))
        raise RuntimeError(f"Could not download {os.path.basename(dest)} "
                           f"({getattr(e, 'reason', e)}). Check your internet connection.")
    os.replace(tmp, dest)


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def kokoro_paths():
    paths = [find_file(os.path.join("kokoro", n)) for n in KOKORO_FILES]
    return paths if all(paths) else None


def ensure_kokoro(progress=None):
    """Return (model, voices) paths, downloading (~350 MB, once) if missing."""
    have = kokoro_paths()
    if have:
        return have
    out = []
    folder = os.path.join(home_dir(), "kokoro")
    for i, n in enumerate(KOKORO_FILES):
        dest = os.path.join(folder, n)
        if not os.path.isfile(dest):
            try:
                _download(KOKORO_BASE + n, dest,
                          (lambda g, t, i=i: progress and t and
                           progress((i + g / t) / len(KOKORO_FILES), f"Downloading Kokoro model ({n})")))
            except CertificateError as e:
                missing = [(m, KOKORO_BASE + m) for m in KOKORO_FILES if not os.path.isfile(os.path.join(folder, m))]
                raise ManualDownloadError(missing, folder, str(e)) from None
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
        # quote(): voice names may contain non-ASCII letters (pt_PT "tugão")
        url = PIPER_BASE + urllib.parse.quote(f"/{fam}/{voice['locale']}/{voice['name']}/{q}/{stem}")
        d = os.path.join(home_dir(), "piper")
        try:
            _download(url + ".onnx.json", os.path.join(d, stem + ".onnx.json"))
            _download(url + ".onnx", os.path.join(d, stem + ".onnx"),
                      lambda g, t: progress and t and progress(g / t, f"Downloading voice {voice['name']}"))
            return os.path.join(d, stem + ".onnx"), os.path.join(d, stem + ".onnx.json")
        except CertificateError as e:
            files = [(stem + ext, url + ext) for ext in (".onnx", ".onnx.json")
                     if not os.path.isfile(os.path.join(d, stem + ext))]
            raise ManualDownloadError(files, d, str(e)) from None
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
