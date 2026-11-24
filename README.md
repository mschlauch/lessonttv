# LocalTTS - text-to-speech and lecture videos

Type text, pick a language and voice, press Speak. Everything runs on your computer.
No account, no API key. Voices download once, then work offline.

The window has three tabs and one shared **batch** list underneath:
- **Text to speech** - type, paste or open text (`.txt`, `.md`, `.pdf`); speak it or save it.
- **Lecture video** - slide PDF + lecture script (`.json` from the script editor) -> narrated MP4.
- **Books (EPUB / PDF)** - split an e-book or a text PDF into chapters, one audio file per chapter.

**Languages (30):** Català, Čeština, Cymraeg, Dansk, Deutsch, English, Español, Français,
Íslenska, Italiano, Latviešu, Lëtzebuergesch, Lietuvių, Magyar, Nederlands, Norsk, Polski,
Português (Brasil), Português (Portugal), Română, Slovenčina, Slovenščina, Suomi, Svenska, Türkçe,
Ελληνικά, Русский, Српски, Українська, ქართული - every European language that has a Kokoro or Piper voice.
Engines: Kokoro (English, Spanish, French, Italian, Brazilian Portuguese; offline, best quality),
Piper (all languages; offline) and Edge (online preview voices, all languages except Luxembourgish).

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
4. Pick the **Video size** (480p, 720p, 1080p, 1440p or 2160p/4K - the width follows the slide shape),
   how long pages **without narration** are shown (default 2.5 s, silent; **0 = leave them out of the video**), and where to save.
5. **Make video**, or **Add to batch** to make it later together with other items. **Stop** cancels.
   When it is done you can play it or show it in its folder.

Next to the video, a folder `<video name>_audio/` holds the audio of every spoken page
(`page_003_Title.wav`). It is refreshed on every run; silent pages have no file.

How timing works: every page is spoken separately and the real length of that audio decides
how long the page is shown (the script's `est_duration_sec` is only an estimate and is not used).
Page changes are placed on the nearest video frame, so they never drift out of sync, even
over hundreds of pages.

The app **refuses** a script when: it is not a `lecture-script` with `schema_version` 3; the
PDF's real page count differs from the number of script pages; or the `pdf_page` numbers are
not exactly 1, 2, ... N (duplicates or gaps). It never guesses a mapping. If the script names a
different PDF file but everything else matches, it only warns.

Spoken: `narration` only (invisible and control characters are removed first). Never used:
`comment`, `prompt_note`, `title`, `slide_label` (they have no effect on the video).

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

## Opening PDFs in "Text to speech"
**Open file...** accepts text and PDF files. Text from a PDF has a line break after every printed
line, and the voice pauses at each one, so the app joins the lines of each paragraph, repairs words
hyphenated at the end of a line, and removes page numbers and repeated headers/footers. For text
pasted from elsewhere, **Remove line breaks** does the same to the text box (Ctrl+Z undoes it).
Scanned PDFs (images without text) need OCR first.

## Books (EPUB / PDF)
Choose an `.epub` or a text `.pdf`. The chapter list is filled automatically:
- **EPUB:** from the book's table of contents (sub-sections with *Level 2* and higher); without one,
  one chapter per file in the book. Footnote markers and page-break markers are not read.
- **PDF:** from the PDF's bookmarks if it has any; otherwise from headings in the text such as
  "Kapitel 3", "Chapter IV", "Teil 2 - Title", "Prolog", "Einleitung" (in all app languages), or numbered
  headings at the top of a page. If neither is found, the text is cut **by length** at paragraph ends
  (*Max. length per part*, default 20 min of speech).
- *Split by* lets you choose another method, e.g. by length even when chapters were found.
Text before the first chapter (cover, title page) is listed but not selected. Select chapters,
pick language/voice/speed at the bottom (the book's language is preselected for EPUBs), and
**Add selected chapters to batch**: one audio file per chapter, named `<book>_03_<chapter title>`.

## Batch export
The batch is shared by all tabs. It can contain audio items (text, book chapters) and lecture
videos, processed top to bottom in list order. **Stop** (next to **Process batch...**) cancels the run. **Process batch...** asks for a folder for the audio files (only if there are any); each
video is written to its own *Save video as* path. Existing videos are only replaced after a
question. A video item reads its script when the batch runs, so later script edits are included.

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
- A build only runs on the OS it was built on. To build all three without owning them, use
  GitHub: **push a tag** like `v1.0` (`git tag v1.0 && git push origin v1.0`). The workflow builds and
  tests all three, then creates a **GitHub Release** with `LocalTTS-windows.exe`, `LocalTTS-macos.zip`
  and `LocalTTS-linux.tar.gz` - a page you can send to colleagues. If any platform fails, no release
  is made. ("Run workflow" in the Actions tab only builds, without a release.)
- The workflow first runs `tests/test_lecture_video.py`, then builds, then starts the
  **built app itself** with `--selftest` (it makes a tiny video with a stand-in voice, using the
  bundled ffmpeg, PDF renderer and speech process). A red run means that platform's app is broken.
- Windows/macOS will warn about an unsigned app; click "More info > Run anyway" (Windows)
  or right-click > Open (macOS).

## Offline bundles
Voices are stored in `~/.localtts/`. To ship them with the app, copy that folder's
`kokoro/` and `piper/` subfolders into a `models/` folder next to the app.

## Licenses
LocalTTS is licensed under the GNU GPL v3 (`LICENSE`). The packaged app contains components under
other (GPL-compatible) licenses, among them FFmpeg (GPL v3) and Piper (GPL v3); see
`THIRD_PARTY_NOTICES.md`, which is also attached to every release.

Voices are downloaded at run time and each has its own license: Kokoro is Apache-2.0; Piper voices
vary (see each voice's MODEL_CARD on Hugging Face - some are non-commercial); Edge voices use
Microsoft's online service unofficially. Check before publishing audio commercially.

The Lithuanian phonemizer in `localtts_lt/` is GPL-3.0 (from Piper's development branch); its stress
dictionary is CC-BY-4.0 (liepa-tts corpus and svogunas/g2p-lt-lexicon). Keep that attribution.
