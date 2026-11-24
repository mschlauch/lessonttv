# Handoff: Preview Video Generator (TTS engine unspecified)

This document hands off the next piece of the pipeline: taking the JSON
scripts produced by `lecture_script_builder/` (the browser-based script
editor), together with their source PDFs, and generating preview videos
using some TTS engine or engines (not chosen yet - that decision is
explicitly left to whoever implements this).

This is a specification of *known logic and constraints*, not an
implementation. No code exists yet in this directory.

## 1. What you're given

Two files per lesson/video, produced by `lecture_script_builder/`:

- A **PDF**: the slide deck, one physical page per visual frame you'll show.
- A **JSON script** (format `lecture-script`, schema v3): one narration
  entry per PDF page, naming the text to speak while that page is shown.

### File pairing convention

`lecture_script_builder`'s export dialog defaults the JSON's filename to
`<pdf-stem>.script.json` (e.g. `01_intro.pdf` → `01_intro.script.json`),
but **the user can rename the download**, so don't hard-depend on that
exact suffix if you can avoid it. The robust approach: accept an explicit
PDF+JSON pair per invocation (like `tts_lecture_video --redeskript/--pdf`
already does), and/or scan a folder for `*.json` files and read each one's
`meta.source_pdf.filename` to find its matching PDF - that field is set
by the app itself at export time and is unlikely to go stale (see
validation below for why you should still double check it).

## 2. The v3 schema

```json
{
  "format": "lecture-script",
  "schema_version": 3,
  "meta": {
    "title": "...",
    "lecturer": "...",
    "institution": "...",
    "language": "de-DE",
    "notes": "...",
    "words_per_minute": 125,
    "source_pdf": { "filename": "01_intro.pdf", "page_count": 12 },
    "total_est_duration_sec": 183,
    "exported_at": "2026-11-24T10:00:00.000Z",
    "app_version": "1.0.0"
  },
  "pages": [
    {
      "pdf_page": 1,
      "slide_label": "1",
      "title": "Introduction",
      "narration": "Welcome to this course...",
      "comment": "",
      "prompt_note": "",
      "est_duration_sec": 14
    }
  ]
}
```

### Field reference

| Field | Meaning | Reliability / how to use it |
| --- | --- | --- |
| `format` / `schema_version` | Must be exactly `"lecture-script"` / `3`. | **Validate this first.** Anything else (older "v2" or legacy German formats, or a future v4) is out of scope for this tool - reject with a clear error rather than guessing field mappings. There's a one-time conversion script, `lecture_script_builder/scripts/convert-legacy.mjs`, for migrating older files to v3; this tool should never need to understand old formats itself. |
| `meta.language` | BCP-47 code (e.g. `de-DE`, `en-US`), free text, not a closed enum. | Use this to pick the TTS voice/language. It may be anything the user typed - don't assume it's from a fixed list; have a sensible fallback (e.g. a default voice, or a clear error) if your chosen TTS engine doesn't support the exact code. |
| `meta.words_per_minute` | The speaking-rate constant used to *estimate* `est_duration_sec` in the editor. | **Not authoritative for video timing** - see section 4. |
| `meta.source_pdf.{filename,page_count}` | What the editor had loaded when exported. | Useful for sanity-checking you've got the right PDF (see validation), but the actual PDF's page count is ground truth, not this field. |
| `meta.title/lecturer/institution/notes` | Free-text descriptive metadata. | Not spoken. Use `title` for e.g. the output filename or an opening card if you want one; purely optional. |
| `pages[].pdf_page` | **1-based physical PDF page index.** Always exactly `1..page_count` with no gaps or duplicates in a file this editor exported. | This is the whole point of the v3 schema versus everything before it: **one `pages[]` entry = one PDF page, always, directly** - no text-matching, no positional-group-mapping, no slide-vs-animation-step guessing. If you're familiar with `tts_lecture_video/generate_video.py`'s old pipeline (grouping PDF pages by matching leading `pdftotext` output, then handing pages out to segments positionally), **none of that machinery is needed here.** Render page N, show it while page N's narration plays, move to N+1. That's the entire slide-mapping logic. |
| `pages[].slide_label` | The presenter's own label for grouping animation steps of one logical slide (e.g. `"6"`, `"6a"`, `"6b"`). Free text, may be empty. | Not needed for basic page-by-page playback. Could be used for a smoother defaults (e.g. a per-slide instead of per-page crossfade/transition style), but that's a nice-to-have, not a requirement. |
| `pages[].title` | Short page heading. | Not spoken. Optional on-screen caption if you want one; not required. |
| `pages[].narration` | **The exact text to synthesize for this page.** May be empty (a silent page - see section 5). | This is literally what you hand to the TTS engine. No cleanup should be needed (the editor already handles the basics), but see section 6 for incompatible-character stripping, a known TTS gotcha from this project's history. |
| `pages[].comment` | Internal team note. | **Never spoken, never shown in the video.** For human reviewers only. |
| `pages[].prompt_note` | "Instructions for a later AI step, such as how to rewrite this narration." | **Never spoken as-is.** See section 7 - whether/how to act on this is an open design question for you to resolve, not a solved part of this handoff. |
| `pages[].est_duration_sec` | A planning estimate (`round(words / words_per_minute * 60)`), recalculated by the editor on every export - never hand-authored. | **Not the real audio duration.** Treat it as a rough total-length preview for the human editor, nothing more. See section 4. |

### Validation before processing (don't trust the file blindly)

The editor enforces a consistent `pages[]` (exactly 1..page_count, no
gaps/duplicates) at export time, but the file can be hand-edited
afterward, or you may be handed a stale PDF that no longer matches. Before
generating anything:

1. Check `format === "lecture-script" && schema_version === 3`.
2. Load the PDF, get its real page count, and confirm it equals
   `pages.length` (not just `meta.source_pdf.page_count`, which is a
   snapshot, not live truth).
3. Confirm `pages[].pdf_page` is exactly the set `{1, ..., page_count}`
   with no duplicates (sort and check, don't assume order in the array -
   though the editor does write them in `pdf_page` order already).
4. If any of this fails, **stop and report it clearly** rather than
   guessing a best-effort mapping - this project's whole history (see
   `tts_lecture_video/README.md`'s "known pitfalls", and
   `lecture_script_builder`'s own Fix Pages screen) is about *not*
   silently papering over page-count/numbering mismatches, because that's
   exactly the kind of bug that's expensive to notice later in a finished
   video.

## 3. Suggested pipeline shape

This mirrors `tts_lecture_video/generate_video.py`'s existing architecture
(worth reading for reference, especially its `--batch` mode and
`work_directory/` conventions), minus the slide-mapping complexity it no
longer needs:

1. Parse + validate the JSON (section 2).
2. Render each PDF page to an image (`pdftoppm`, or `pdf.js` if you want a
   single JS-only toolchain matching `lecture_script_builder`'s own
   approach - either is fine, this project has working examples of both).
3. For each page with non-empty `narration`, synthesize audio with the
   chosen TTS engine(s), in `meta.language`.
4. Measure each synthesized clip's **actual** duration (e.g. via
   `ffprobe`) - this, not `est_duration_sec`, is what drives how long that
   page is shown.
5. Assemble the video: each page's image held for its actual audio
   duration, muxed with the concatenated audio. `tts_lecture_video`'s
   `concat`-demuxer approach is a reasonable starting point.
6. Output a preview-quality video (lower resolution/bitrate than a final
   render - see `tts_lecture_video --mode preview` for precedent).

### Lessons from this project's history worth reusing

- **Never trust a TTS/recording library's reported or "planned" duration
  over the real, measured output duration.** This exact mistake (driving
  video timing off a planned/reported duration instead of the actual
  decoded one) caused two separate, nontrivial audio/video desync bugs
  elsewhere in this repo (`lecture_webcam_recording`'s room-mode fps
  issue, and its crossfade-duration-quantization bug - see that
  project's README for the full post-mortems if you want the details).
  The pattern to avoid: computing timing from something you expected
  versus something you measured. Always measure.
- If you chain multiple clips with a crossfade/transition (rather than
  simple hard cuts), be aware that normalizing video to a fixed fps for
  the transition can quantize each clip's duration slightly, and that
  this can compound across many transitions - `lecture_webcam_recording`'s
  README documents the exact mechanism and fix if you end up needing it.
  If you're doing simple hard cuts between pages (no crossfade), this
  doesn't apply.
- `edge-tts` (used by `tts_lecture_video` today) is free but unofficial/
  reverse-engineered and rate-limits aggressively - if you use it, keep
  batch sizes small with pauses between, per its existing README.
- Remove/replace characters outside TTS-safe ranges before synthesizing
  (`tts_lecture_video` and `lecture_script_builder`'s dropped TTS-relay
  prototype both had to do this - vertical tabs and similar control
  characters, common in OCR'd or copy-pasted text, can break some TTS
  backends). `narration` text from the editor hasn't been specifically
  scrubbed for this, so don't assume it's already clean.

## 4. `est_duration_sec` is a planning estimate, not ground truth

It's computed once, client-side, as `round(words / words_per_minute * 60)`
- a rough heuristic for the human editing the script to gauge total
lesson length, nothing more. It is **recalculated fresh on every export**
(never hand-typed), but it has no relationship to how long any specific
TTS engine will actually take to speak the text - that varies by voice,
language, SSML prosody settings, and the engine itself. **Always measure
the real synthesized audio's duration** and use that for video timing;
never use `est_duration_sec` for anything beyond an optional
sanity-check/progress-estimate display.

## 5. Empty narration (silent pages)

`pages[].narration` may legitimately be `""` - the editor explicitly
supports "a silent page" (see its own spec). `est_duration_sec` will be
`0` for these. **This is an open decision for you to make**, not
something previously resolved:

- Show the page for some default hold duration (e.g. 2-3 seconds) with no
  audio?
- Show it only as long as the *next* page's audio needs to start, i.e.
  effectively skip/merge it?
- Something else entirely (e.g. flag it back to the user as "needs
  narration" and skip generating preview video until it's filled in)?

Pick something and document your choice - just don't silently default to
`0` seconds (a page that's genuinely invisible in the output is
confusing to debug later).

## 6. Language / voice selection

`meta.language` is free-text BCP-47 (not a closed enum - see the field
table). Map it to whatever your TTS engine(s) expose. If you support
multiple engines, you'll need a per-engine mapping from BCP-47 code to
that engine's voice list; don't assume every engine recognizes the exact
same codes (e.g. engines vary on whether they want `de-DE` vs `de` vs a
specific named voice).

## 7. `prompt_note`: an explicitly unresolved question

The schema's own description (from `lecture_script_builder`'s spec) is:
*"Instructions for a later AI step, such as how to rewrite this
narration."* This strongly implies a pre-TTS rewriting step was
anticipated (e.g. an LLM call that takes `narration` + `prompt_note` and
produces an adjusted narration text before synthesis - shorter, more
casual, whatever the note asks for), but **this was never built, decided,
or scoped in detail**. Options, none chosen yet:

- Ignore `prompt_note` entirely for now (simplest; just means any notes
  the user wrote go unused until a later pass).
- Implement a rewriting step that feeds `narration` + `prompt_note` to an
  LLM and synthesizes the result instead of the raw `narration`.
- Surface `prompt_note` to a human reviewer rather than automating it.

This needs a decision (and ideally, checking in with the project owner)
before building anything around it - don't assume either direction
silently.

## 8. TTS engine(s): deliberately unspecified

No engine has been chosen for this tool. Prior art in this repo:

- `tts_lecture_video` uses `edge-tts` (free, unofficial/reverse-engineered
  Microsoft endpoint - see its README's pitfalls, and
  `lecture_script_builder/README.md`'s git history for a detailed
  write-up of just how deep that reverse-engineering goes, from an
  abandoned attempt to call it directly from a browser).
- A real **Azure AI Speech** (Cognitive Services) integration was
  considered during `lecture_script_builder`'s planning as a more robust,
  officially-supported alternative with a real free tier, but was never
  implemented (the project settled on keeping `edge-tts`, preview-quality
  only, for a different tool's scope).

Whatever you choose, a clean seam is worth building regardless of engine
choice: a small interface like `synthesize(text, language) -> (audio_file,
actual_duration)`, so adding a second or third engine later doesn't
require touching the pipeline logic in section 3.

## 9. Output

No naming/location convention is mandated yet. For consistency with the
rest of this repo, consider mirroring `tts_lecture_video`'s pattern (a
`work_directory/` for intermediate files, final video copied back next to
the source PDF/JSON, named after their shared stem) unless there's a
reason to diverge.
