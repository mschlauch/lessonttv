@echo off
rem Builds LocalTTS.exe on Windows. Run from a folder whose path has no spaces.
python -m venv buildenv || exit /b 1
call buildenv\Scripts\activate.bat
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --onefile --windowed --name LocalTTS --collect-all piper --collect-all kokoro_onnx --collect-all espeakng_loader --collect-all phonemizer --collect-all language_tags --collect-all localtts_lt --collect-all edge_tts --collect-all soundfile --collect-all _soundfile_data --collect-all imageio_ffmpeg --collect-all pypdfium2 --collect-all pypdfium2_raw localtts_gui.py || exit /b 1
echo Done - see the dist folder.
