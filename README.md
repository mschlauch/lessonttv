# LocalTTS - text-to-speech and lecture videos

Type text, pick a language and voice, press Speak. Everything runs on your computer.
No account, no API key. Voices download once, then work offline.

The **Lecture video** tab turns a slide PDF and its lecture script (exported from the
script editor as `.json`) into a narrated MP4 video - see below.

**Languages:** English, Spanish, Portuguese (Brazil), Portuguese (Portugal), German,
Swedish, Finnish, Lithuanian. Engines: Kokoro (English, Spanish, Brazilian Portuguese)
and Piper (all of the above).

## Run from source
    pip install -r requirements.txt
    python localtts_gui.py
(Python 3.9+ with Tk. On Linux: `sudo apt install python3-tk`.)

## Lecture video (PDF + script -> MP4)
1. Open the **Lecture video** tab and press **Choose...** next to *Slides (PDF)*.
   If the script `.json` is in the same folder it is picked automatically (it is matched by
   the PDF name stored inside the script, so a renamed download is still found).
   Otherwise choose it yourself.
2. The box below shows what was found: title, number of pages, language, pages without
   narration - or, in red, why the script and PDF do not match (see below).
3. Language and voice are preset from the script's language. **Edge (online)** voices are
   the quick default; Kokoro/Piper voices work offline. The app remembers your choice per language.
4. Pick the **Video size** (480p, 720p, 1080p or 1440p - the width follows the slide shape),
   how long pages **without narration** are shown (default 2.5 s, silent), and where to save.
5. **Make video**. **Stop** cancels. When it is done you can play it or show it in its folder.

How timing works: every page is spoken separately and the real length of that audio decides
how long the page is shown (the script's `est_duration_sec` is only an estimate and is not used).
Page changes are placed on the nearest video frame, so they never drift out of sync, even
over hundreds of pages.

The app **refuses** a script when: it is not a `lecture-script` with `schema_version` 3; the
PDF's real page count differs from the number of script pages; or the `pdf_page` numbers are
not exactly 1, 2, ... N (duplicates or gaps). It never guesses a mapping. If the script names a
different PDF file but everything else matches, it only warns.

Spoken: `narration` only (invisible and control characters are removed first). Never used:
`comment`, `prompt_note` (reserved for a later AI rewriting step), `title`, `slide_label`.

Speeding up re-runs: each page's audio is kept in `~/.localtts/video_work/`, so after editing
the narration of one page only that page is spoken again. That folder can be deleted at any time.

Edge voices: the app pauses briefly between pages and retries a page up to 3 times, because
the online service limits fast requests. If a page still fails, no video is made and the error
names the page.

Command line (same engine, for scripts and testing):

    python lecture_video.py --pdf 01_intro.pdf                 # finds 01_intro.script.json
    python lecture_video.py --pdf a.pdf --script a.json --height 1080 --voice p:de_DE-thorsten-medium
    python lecture_video.py --pdf a.pdf --offline --silent-hold 3

Video encoding uses an ffmpeg program bundled with the app (`imageio-ffmpeg`), and the PDF is
drawn with `pypdfium2` - nothing extra has to be installed.

## Batch export
Set text, language, voice and speed, then **Add to batch** (optionally with a file name).
Select rows and **Remove from batch**, or **Clear**. **Process batch...** asks for a folder and
writes one WAV or MP3 per item (named `001_de_Thorsten_first_words.wav`, or your file name).
Items are processed grouped by voice so each voice loads only once; one failing item does not
stop the rest, and **Stop** cancels the run. The batch list is saved between sessions.

## Edge voices (quick previews)
Voices marked "Edge (online)" use Microsoft's online read-aloud service through `edge-tts`:
instant, nothing to download, many natural voices - but they need internet, run on
Microsoft's servers (your text leaves your computer), and the service is unofficial, so it can
change or stop working. Use the Kokoro/Piper voices for offline work and final output.

## "Computer load" setting
| Setting  | What it does |
|----------|--------------|
| Light    | 1 CPU thread, lowest priority, pauses between parts so CPU use stays around half. Slowest, gentlest. |
| Balanced | Few CPU threads (1-2), lower priority. Default. |
| Fast     | Up to half your cores (2-4), normal priority. |

Other safeguards: text is read in sentence-sized parts (memory stays flat for long
texts), generation runs in a separate process that is freed after 90 s idle, and
**Stop** ends generation immediately. Kokoro needs roughly 0.5-0.9 GB RAM while active;
Piper voices need about 0.2 GB.

## Build a double-click app
- **Linux / macOS:** `./build.sh`   **Windows:** `build.bat`   (result in `dist/`)
- Put the project in a folder path **without spaces or parentheses** (PyInstaller fails otherwise).
- A build only runs on the OS it was built on. To build all three without owning them,
  run the "Build LocalTTS" workflow on GitHub (Actions tab > Run workflow, or push a tag
  like `v1.0`); download the three results from the run page.
- The workflow first runs `tests/test_lecture_video.py`, then builds, then starts the
  **built app itself** with `--selftest` (it makes a tiny video with a stand-in voice, using the
  bundled ffmpeg, PDF renderer and speech process). A red run means that platform's app is broken.
- Windows/macOS will warn about an unsigned app; click "More info > Run anyway" (Windows)
  or right-click > Open (macOS).

## Offline bundles
Voices are stored in `~/.localtts/`. To ship them with the app, copy that folder's
`kokoro/` and `piper/` subfolders into a `models/` folder next to the app.

## Licenses
The Lithuanian voice uses a phonemizer copied from Piper's development branch (GPL-3.0,
in `localtts_lt/`); its stress dictionary is CC-BY-4.0 (liepa-tts corpus and
svogunas/g2p-lt-lexicon). Keep that attribution if you redistribute.

`piper-tts` is GPL-3.0, so distributing a build that includes it means distributing under
GPL terms (include the source). Kokoro is Apache-2.0. Each Piper voice has its own license
(see its model card on Hugging Face) - check before commercial use.
