# Third-party components

LocalTTS itself is licensed under the GNU General Public License v3 (see `LICENSE`).
The packaged app (built with PyInstaller) contains the following components. Their licenses are
compatible with GPL v3. Versions are those used when this list was written; the build installs the
latest versions from PyPI, so check `requirements.txt` and the packages' own metadata for the exact ones.

## Bundled in the app

| Component | License | Notes |
|---|---|---|
| Python | PSF License | runtime |
| Tcl/Tk (tkinter) | Tcl/Tk license (BSD-style) | user interface |
| piper-tts | GPL-3.0-or-later | Piper voice engine |
| espeak-ng (via piper-tts / espeakng-loader) | GPL-3.0-or-later | phonemes for Piper and Kokoro |
| phonemizer | GPL-3.0-or-later | |
| localtts_lt (Lithuanian phonemizer, copied from OHF-Voice/piper1-gpl) | GPL-3.0 | |
| Lithuanian stress dictionary (in localtts_lt/lithuanian/) | CC BY 4.0 | from the liepa-tts corpus and svogunas/g2p-lt-lexicon - attribution required |
| kokoro-onnx | MIT | Kokoro voice engine |
| onnxruntime | MIT | |
| edge-tts | LGPL-3.0 | client for Microsoft's online read-aloud service (see below) |
| aiohttp | Apache-2.0 and MIT | used by edge-tts |
| certifi | MPL-2.0 | |
| numpy | BSD-3-Clause (and others, see package) | |
| soundfile | BSD-3-Clause | |
| libsndfile (bundled by soundfile) | LGPL-2.1-or-later | |
| pypdfium2 / PDFium | BSD-3-Clause, Apache-2.0, and PDFium's dependency licenses | PDF drawing and text |
| Pillow | MIT-CMU (HPND) | images |
| imageio-ffmpeg | BSD-2-Clause | |
| **FFmpeg** (binary shipped inside imageio-ffmpeg) | **GPL-3.0** (built with `--enable-gpl --enable-version3`; includes x264 and other GPL/LGPL libraries) | video encoding. Source: https://ffmpeg.org/download.html; the build scripts used for the binaries: https://github.com/BtbN/FFmpeg-Builds. The exact version is printed by `ffmpeg -version`. |
| PyInstaller bootloader | GPL-2.0-or-later with the bootloader exception | packaging |

## Downloaded at run time (not part of the app)

| What | License | Notes |
|---|---|---|
| Kokoro model and voices (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | Apache-2.0 | from the kokoro-onnx GitHub release (model by hexgrad) |
| Piper voices (`*.onnx` + `.onnx.json`) | **varies per voice** | from https://huggingface.co/rhasspy/piper-voices - each voice folder has a `MODEL_CARD` with its license. Many are public domain / CC0 or CC BY (attribution), some are **non-commercial** or have dataset restrictions. Check the card before publishing audio made with a voice. |
| Edge voices | Microsoft service terms | unofficial use of the Microsoft Edge read-aloud service. Not a licensed API; may stop working; check Microsoft's terms before commercial use of the audio. |

If you ship voice files inside the app folder (`models/`, see README), you redistribute them and
must follow each voice's license (e.g. include attribution).
