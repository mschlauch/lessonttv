#!/usr/bin/env sh
# Builds LocalTTS for the OS you run this on (Linux or macOS).
# Run it from a folder whose path has NO spaces or parentheses.
set -e
python3 -m venv buildenv
. buildenv/bin/activate
python -m pip install -r requirements.txt pyinstaller
if [ "$(uname)" = "Darwin" ]; then MODE="--windowed"; else MODE="--onefile"; fi
python -m PyInstaller $MODE --name LocalTTS --collect-all piper --collect-all kokoro_onnx --collect-all espeakng_loader --collect-all phonemizer --collect-all language_tags --collect-all localtts_lt --collect-all edge_tts --collect-all soundfile --collect-all _soundfile_data --collect-all imageio_ffmpeg --collect-all pypdfium2 --collect-all pypdfium2_raw localtts_gui.py
echo "Done - see the dist folder."
