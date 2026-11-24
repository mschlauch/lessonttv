"""The app-wide batch: audio items (text -> WAV/MP3) and lecture-video items (PDF + script -> MP4).

Data model (saved in ~/.localtts/batch.json as a list):

  audio  {"kind": "audio", "lang", "voice", "speed", "text", "name", "source"?}
         Older batch files have no "kind"; such items are audio.
         "source" is only for display (e.g. "book.epub - Chapter 3").
  video  {"kind": "video", "lang", "voice", "speed", "pdf", "script", "out", "height", "hold"}
         No text is stored: the script is read when the batch runs, so edits made to the
         script after adding it are included. Validation runs again at that point.

Audio files go into the folder chosen when the batch is processed; each video goes to its
own "out" path, with its page audio in a folder next to it (see lecture_video).
"""
import os
import tempfile

import localtts_core as core
import lecture_video as lv


def kind(item):
    return item.get("kind", "audio")


def is_valid(item):
    if not isinstance(item, dict) or item.get("voice") not in core.VOICE_BY_ID:
        return False
    if kind(item) == "video":
        return bool(item.get("pdf") and item.get("script"))
    return kind(item) == "audio" and bool(item.get("text"))


def audio_item(lang, voice_id, speed, text, name="", source=""):
    it = dict(kind="audio", lang=lang, voice=voice_id, speed=round(float(speed), 2), text=text, name=name)
    if source:
        it["source"] = source
    return it


def video_item(lang, voice_id, speed, pdf, script, out, height, hold):
    return dict(kind="video", lang=lang, voice=voice_id, speed=round(float(speed), 2),
                pdf=os.path.abspath(pdf), script=os.path.abspath(script), out=os.path.abspath(out),
                height=int(height), hold=float(hold))


def describe(item):
    """Short text for the batch list."""
    if kind(item) == "video":
        h = item.get("height", lv.DEFAULT_HEIGHT)
        return f"{os.path.basename(item['pdf'])} -> {os.path.basename(item['out'])} ({h}p)"
    text = " ".join(item["text"].split())
    prefix = f"[{item['source']}] " if item.get("source") else ""
    text = prefix + text
    return (text[:90] + "...") if len(text) > 90 else text


def output_names(items, ext):
    """Display/file name per item: audio files numbered as before, videos by their output file."""
    audio_idx = [i for i, it in enumerate(items) if kind(it) == "audio"]
    names = dict(zip(audio_idx, core.batch_filenames([items[i] for i in audio_idx], ext)))
    return [names.get(i) or os.path.basename(it["out"]) for i, it in enumerate(items)]


def existing_videos(items):
    return [it["out"] for it in items if kind(it) == "video" and os.path.exists(it["out"])]


def run(items, folder, synth, level, ext=".wav", on_progress=None, should_stop=None):
    """Process every item. Blocking - call from a background thread.

    folder: where audio items are written (may be None if there are none).
    Items are processed in list order, so "item n of N" matches the row in the batch list (a
    voice model is reloaded only when the voice changes between items). A failing item is
    recorded and the rest continue. Returns (results, cancelled);
    results = [(index, ok, name, seconds_or_error)] in list order.
    on_progress(n, total, name, fraction_of_item, label) - same as localtts_core.run_batch.
    """
    names = output_names(items, ext)
    if any(kind(it) == "audio" for it in items):
        os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(tempfile.gettempdir(), f"localtts_batch_{os.getpid()}.wav")
    stop = should_stop or (lambda: False)
    results, cancelled = [], False
    for i, it in enumerate(items):
        if stop():
            cancelled = True
            break
        voice = core.VOICE_BY_ID[it["voice"]]

        def prog(frac, label, i=i):
            on_progress and on_progress(i, len(items), names[i], frac, label)
        try:
            prog(0.0, "Preparing...")
            if kind(it) == "video":
                res = lv.make_video(it["pdf"], it["script"], it["out"], voice, synth, speed=it["speed"],
                                    height=it.get("height", lv.DEFAULT_HEIGHT),
                                    silent_hold=it.get("hold", lv.SILENT_HOLD), level=level,
                                    on_progress=prog, should_stop=stop)
                results.append((i, True, names[i], res["seconds"]))
            else:
                req = core.build_request(voice, it["text"], it["speed"], tmp, progress=prog)
                path, secs = synth.run(req, level, lambda p, t, prog=prog: prog(p / t, f"part {p} of {t}"))
                core.write_audio(path, os.path.join(folder, names[i]))
                results.append((i, True, names[i], secs))
        except (core.Cancelled, lv.Cancelled):
            cancelled = True
            break
        except Exception as e:
            if stop():
                cancelled = True
                break
            results.append((i, False, names[i], str(e)))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return sorted(results), cancelled
