# LocalTTS - handoff for the next agent

Read this first. It says what the project is, how it works, what is verified vs. not,
what was decided and why, and what to do next. Facts below were observed during development
unless marked **(unverified)**.

## 1. Goal (from the user)
An offline-capable desktop GUI (Tkinter) that turns text into speech, "ready to go", that the
user can build into a standalone app and give to other people on other computers.

Requirements the user stated, in order of appearance:
- Free, no API key; Kokoro and Piper voices (Coqui rejected: heavy, PyTorch, company defunct).
- Languages: English, Spanish, Portuguese **Brazil AND Portugal**, German, Swedish, Finnish, Lithuanian.
- Limit CPU/RAM (an earlier version made their computer crash; cause never identified).
- Batch export: buttons "Add to batch", "Remove from batch", "Process batch".
- Edge TTS voices for fast preview generation.
- Distribute the built app to other computers (Windows included, but the user has no Windows).

The user is on Linux (Python 3.11, conda `(base)` plus a venv). They build and run things
themselves and report errors back; they have not yet reported success with the latest version.

## 2. Files
```
localtts_core.py     engine layer, no GUI (catalog, downloads, worker process, batch)
localtts_gui.py      Tkinter app; talks only to localtts_core
localtts_lt/         VENDORED Lithuanian phonemizer (+ lithuanian/*.tsv data), see section 5
tests/               headless test scripts (see section 8)
build.sh / build.bat PyInstaller build for the current OS
.github/workflows/build.yml   builds Windows+macOS+Linux on GitHub Actions
requirements.txt     kokoro-onnx, piper-tts, edge-tts, soundfile, numpy
README.md            user-facing docs
```

## 3. Architecture
- **GUI thread** (Tkinter) -> background `threading.Thread` -> `core.Synth.run()` (blocking).
- **Worker process** (`multiprocessing`, `spawn` context, `_worker_main`) does all synthesis.
  Reasons: set CPU priority/threads per "Computer load" level, free ALL model RAM by killing it,
  instant cancel (terminate), and a crash/OOM cannot kill the window.
  Protocol over a `Pipe`: parent sends one request dict; worker replies
  `("progress", i, n)`, then `("done", wav_path, seconds)` or `("error", message)`.
  Worker writes audio chunk by chunk into a WAV file (memory stays flat).
- `Synth` respawns the worker if the level changes or it died. GUI stops it after 90 s idle.
  `Synth.run` keeps local refs to proc/conn because `cancel()` clears them from another thread
  (this race was a real bug, fixed).
- **Engines** (all in worker):
  - `kokoro` via `kokoro-onnx` (model files `kokoro-v1.0.onnx`, `voices-v1.0.bin` from the
    thewh1teagle/kokoro-onnx GitHub release `model-files-v1.0`). Languages: en-us, en-gb, es, pt-br.
  - `piper` via `piper-tts` (voice `.onnx` + `.onnx.json` from Hugging Face
    `rhasspy/piper-voices`, path `{lang}/{locale}/{name}/{quality}/{locale}-{name}-{quality}.onnx`).
  - `edge` via `edge-tts` (online; returns MP3, decoded with `soundfile`).
- **Catalog**: `core.VOICES[lang_code]` list of dicts (`engine`, `id`, `label`, ...);
  `core.VOICE_BY_ID`. IDs: `k:<name>`, `p:<locale>-<name>-<quality>`, `e:<ShortName>`.
- **Performance levels** (`perf_settings`): Light = 1 thread, idle priority, sleeps as long as
  each chunk took (~50% duty); Balanced = clamp(cpu//4,1,2) threads, below-normal priority;
  Fast = all cores if <=2 else clamp(cpu//2,2,4), normal priority. ORT session uses
  `enable_cpu_mem_arena=False`, `inter_op_num_threads=1`.
- **Chunking**: `split_text(text, 250)` (600 for Edge). A chunk is one `create()`/`synthesize()` call.
- **Batch**: `core.run_batch(items, folder, synth, level, ext, on_progress, should_stop)`;
  items = `{lang, voice(id), speed, text, name}`; processed grouped by voice id; per-item errors
  collected; returns `(results, cancelled)`. MP3 output via `soundfile` (libsndfile >= 1.1).
- **Storage**: `~/.localtts/` (override with env `LOCALTTS_DIR`): `kokoro/`, `piper/`,
  `settings.json`, `batch.json`. A `models/` folder next to the app is searched first
  (for offline bundles).

## 4. Decisions and the evidence behind them (do not re-litigate without new evidence)
1. **sherpa-onnx was tried first and dropped.** With the `kokoro-multi-lang-v1_0` package only
   English and Chinese work; Spanish, Italian and Portuguese skip every word ("OOV word skipped",
   ~1.3 s of empty audio) and French errors with an empty lexicon. `kokoro-onnx` handles
   es/it/pt-br/fr correctly (plausible durations; pronunciation not listened to).
2. **int8 Kokoro model rejected**: slower (10.2 s vs 5.1 s on 1 thread) and ~same RAM (~855 MB).
3. **Thread count**: on a 2-core test box 2 threads were ~2x faster than 1, but 4 threads were slower
   than 2. Hence conservative defaults.
4. **RAM measured** (worker RSS): Kokoro 0.5-0.9 GB; Piper voice ~0.17-0.2 GB.
5. **German has no Kokoro voice**; Piper is used for de/sv/fi/lt/pt-PT. Finnish and Lithuanian have
   one voice each (Harri; Reginute).
6. **Piper release lacks Lithuanian phoneme type**: see section 5.
7. **PyInstaller**: the project path must have no spaces/parentheses (ldd output parsing breaks;
   user hit `IsADirectoryError`). A stale PyPI package `pathlib` also breaks PyInstaller (user hit
   it; fix: `pip uninstall pathlib` or use a clean venv). Required flags:
   `--collect-all piper kokoro_onnx espeakng_loader phonemizer language_tags localtts_lt edge_tts
   soundfile _soundfile_data`. `multiprocessing.freeze_support()` must run first in the entry
   script (it does). macOS builds use `--windowed` without `--onefile`.

## 5. The Lithuanian workaround (fragile; revisit first)
The Lithuanian voice's `.onnx.json` has `"phoneme_type": "lithuanian"`. `piper-tts` 1.8.0 (latest
on PyPI when written) has no such enum value, so `PiperConfig.from_dict` raises. Upstream
`OHF-Voice/piper1-gpl` main has `PhonemeType.LITHUANIAN` and `phonemize_lithuanian.py`
(espeak-ng IPA + 189k-word stress dictionary).
What we did: copied that file + 3 TSVs into `localtts_lt/`, changed only the import
(`ESPEAK_LOCK` does not exist in 1.8.0, so a private lock is defined). In
`core._load_engine`: if config says lithuanian -> load as "espeak", construct `PiperVoice`, then
`eng.phonemize = LithuanianPhonemizer().phonemize`.
**Action:** check whether a newer `piper-tts` release supports it natively; if so delete
`localtts_lt/`, the override, and the `--collect-all localtts_lt` flags.
Limits: no number/abbreviation expansion (the voice package ships its own; digits get wrong
case endings), homographs differing only in accent cannot be distinguished.
Licenses: code GPL-3.0; dictionary CC-BY-4.0 (keep attribution; README mentions it).

## 6. Verified vs NOT verified
Verified (in the Linux sandbox, Python 3.13):
- Kokoro en / es / pt-BR and Piper German (`eva_k` x_low) synthesize; levels, progress, cancel,
  recovery after cancel, idle stop.
- Catalog sanity (all Kokoro voice names exist in `voices-v1.0.bin`; unique ids).
- Kokoro download from GitHub; Piper downloader (against a local mock HTTP server, incl. quality
  fallback and 404 handling).
- Batch: mixed engines, forced failure, duplicate names, WAV+MP3, cancel.
- GUI *logic* via a Tk stub (`tests/test_gui2.py`): voice lists, batch add/remove/persist/process, cancel.
- PyInstaller `--onefile` Linux build of a CLI wrapper around `localtts_core` spoke Kokoro, Piper,
  Lithuanian (stand-in model) and Edge (mock) correctly, incl. worker spawn when frozen.

NOT verified - treat as open risks:
- **The real Tkinter window has never been launched** (no Tk in the sandbox). Layout, widget
  behaviour, playback buttons on a real desktop are untested.
- **Windows and macOS** builds/playback (`winsound`, `afplay`) and the GitHub workflow (YAML syntax
  only). The packaged *GUI* was never built, only the CLI wrapper.
- **Real Piper voices from Hugging Face** (blocked in sandbox). Unknowns: Lithuanian quality folder
  (code tries medium, low, x_low, high); whether the Portuguese (Portugal) folder is `tugao` or
  `tugão` in file URLs; qualities for `kerstin`/`karlsson` (several tried).
- **The real Lithuanian voice** (only the phonemizer and a German model with a fake config).
- **Edge service**: unreachable from the sandbox; voice ShortNames come from memory and were not
  checked against `edge_tts.list_voices()`. The Edge path was tested with `LOCALTTS_EDGE_MOCK`
  (a test hook in `_edge_save`).
- **Audio quality** of every voice: nobody has listened.
- The original **crash** on the user's machine: cause unknown (suspects: all-cores-minus-one
  threads, whole text in one call, ~1 GB RAM). Current mitigations are untested on their hardware.

## 7. Suggested next steps
1. Ask the user for the result of running the latest version (any error text, RAM/CPU of their machine).
2. If possible get a machine with Tk: launch `python localtts_gui.py`, fix layout/behaviour.
3. Validate Edge voice names with `edge_tts.list_voices()` on a networked machine; prune bad ones.
4. Run the GitHub workflow; fix Windows/macOS packaging issues (likely: missing hidden imports,
   macOS `.app` icon/permissions, unsigned-app warnings).
5. Check for a newer `piper-tts` (section 5).
6. Possible improvements: persist speed on slider change; finer progress for short texts (chunks are
   <=250 chars so short texts show "part 1 of 1"); real RAM cap is NOT implemented (only chunking,
   threads, idle release); hard cap would need OS-specific work.

## 8. Tests and fixtures
`tests/*.py` are the headless scripts used during development. They were written for the sandbox:
hard-coded `/tmp` and `/home/claude` paths; adjust them. Fixtures they expect (recreate):
- `LOCALTTS_DIR=/tmp/ltts_home` containing `kokoro/kokoro-v1.0.onnx`, `kokoro/voices-v1.0.bin`
  (from the kokoro-onnx GitHub release) and `piper/de_DE-eva_k-x_low.onnx(.json)` (extract from
  `vits-piper-de_DE-eva_k-x_low.tar.bz2` in the k2-fsa/sherpa-onnx `tts-models` GitHub release).
- Lithuanian stand-in: copy that German model to `piper/lt_LT-reginute1-medium.onnx` and its json with
  `phoneme_type` set to `lithuanian` and `espeak.voice` set to `lt` (sound is meaningless; tests plumbing).
- `/tmp/edge_mock.mp3`: any MP3 (e.g. `soundfile.write(..., format="MP3")`), used with env
  `LOCALTTS_EDGE_MOCK=/tmp/edge_mock.mp3`.
- `test_gui2.py` contains a minimal Tk stub so GUI logic runs without Tk.
Scripts using `multiprocessing` must be run from a FILE with an `if __name__ == "__main__":` guard
(running from stdin/heredoc made the spawned worker die).
- `test_core.py` levels/progress/cancel; `test_batch.py` batch; `test_lt.py` Lithuanian plumbing;
  `test_gui.py` older GUI-logic test (pre-batch).

## 9. Sandbox/tooling notes
- Outbound network is an allowlist: GitHub release downloads and PyPI work; `huggingface.co` and
  Microsoft's Edge endpoints do not. `gh api` was not enabled for these repos.
- No `tkinter`, `apt` install failed. `rm -rf *` is blocked by a safety check; create fresh dirs instead.
- Heavy installs here: `pip install --break-system-packages`.

## 10. Style of working that suited this user
They want finished, runnable deliverables and plain explanations; they test on their own machine and
paste errors. Say clearly what was and was not tested. Send the updated zip after each change.
