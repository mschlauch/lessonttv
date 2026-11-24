# Lecture video - handoff notes

Implements `docs/HANDOFF_preview_video.md` inside the LocalTTS app (see `docs/HANDOFF_localtts.md`
for the app itself). Read those first.

## Files
- `lecture_video.py` - engine, no GUI: validation, voice choice, PDF drawing, per-page speech
  (via `localtts_core.Synth`), soundtrack, ffmpeg encode, `selftest()`, command line.
- `localtts_gui.py` - the window now has two tabs; "Lecture video" is `_build_video` and the `_v_*` methods.
- `tests/test_lecture_video.py` - no network needed; `LV_REAL_VOICE=k:af_heart` adds a real-voice run.

## Decisions (made with the project owner)
- Engine: any LocalTTS voice; default is the Edge voice for the script language, user can pick
  Kokoro/Piper. Preference is remembered per language (`settings.json` -> `video_voices`).
- Silent pages: shown for a fixed time (default 2.5 s, set in the GUI / `--silent-hold`), with silence.
- `prompt_note`, `comment`: ignored. Never spoken, never shown.
- Resolution selectable (480/720/1080/1440 p height; width from the first page's aspect ratio,
  other page shapes are centred on white).
- Output: `<pdf stem>.mp4` next to the PDF by default. Work files: `~/.localtts/video_work/<stem>-<hash>/`
  (audio cache per page, keyed by voice + speed + cleaned text; unused cache entries are deleted after
  each successful run).

## Timing (the important part)
- Each page's WAV length is read from the file (frames / rate). `est_duration_sec` is never used.
- All page audio is joined into one WAV in Python; the video is one ffmpeg concat-demuxer pass of
  still images. Page change k is placed at `round(cumulative_audio_seconds_k * 25)` frames, so the
  video/audio offset is < 1/2 frame everywhere and does not accumulate (`page_times`, tested over 200 pages).
- After encoding, the MP4 length is read back with ffmpeg and compared with the audio (warning if > 0.25 s).
- Note: LocalTTS adds 0.12 s of silence after every chunk; it is part of the measured length.

## Bundling (for colleagues without ffmpeg/poppler)
- `imageio-ffmpeg` ships a static ffmpeg per platform; `pypdfium2` ships PDFium. Both are collected
  with `--collect-all imageio_ffmpeg pypdfium2 pypdfium2_raw` (build.sh, build.bat, workflow).
- `ffmpeg_exe()` restores the executable bit if packaging dropped it, because otherwise imageio-ffmpeg
  silently falls back to a system ffmpeg (which would hide the bug on a developer machine).
- On Windows, ffmpeg is started with `CREATE_NO_WINDOW` (no console flashes from the windowed app).

## GUI threading fix (affects the whole app)
Background threads used to call `self.after(0, ...)`. With Tk 9.0 (python-build-standalone 3.11)
those calls were silently lost: the window stayed "busy" forever. Now threads put work on a
`queue.Queue` that the window drains every 50 ms (`App._ui` / `_drain_ui`). This also covers Speak and batch.

## Verified (Linux sandbox, Python 3.11 with Tk 9.0, Xvfb)
- Unit tests incl. real ffmpeg: page colours sampled at the start and end of every page match the
  expected slide; mixed page shapes; validation errors; pairing; cache; cancel; Edge path via
  `LOCALTTS_EDGE_MOCK`.
- Real Kokoro voice end to end (`k:af_heart`), from the command line, the test suite and the real GUI.
- Real Tk window driven by a script: both tabs render, loading a PDF finds a renamed script via
  `meta.source_pdf.filename`, progress/status update, "Video ready" dialog.
- `build.sh` (PyInstaller --onefile, Linux): packaged app passes `--selftest` using the ffmpeg bundled
  inside it, and its window starts. (Locally the Tk 9 library had to be put on LD_LIBRARY_PATH - a quirk
  of the standalone Python used here; the workflow's setup-python 3.11 has Tk 8.6.)

## NOT verified
- Windows and macOS builds: only via the workflow's self-test, which has not been run yet.
- Real Edge voices and Piper voices (network blocked in the sandbox).
- Very long lectures (hundreds of pages) for speed/disk use; the timing maths is tested for that.
- Unsigned-app warnings on Windows/macOS for colleagues (documented in README).

## Ideas not done
- A queue of several lessons ("batch videos").
- Optional `prompt_note` rewriting step before synthesis.
- Crossfades between pages (would need the fps-quantisation care described in the original handoff).
