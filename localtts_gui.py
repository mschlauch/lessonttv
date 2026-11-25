#!/usr/bin/env python3
"""LocalTTS - offline text-to-speech desktop app (Kokoro + Piper voices)."""
import os
import sys
import json
import shutil
import tempfile
import queue
import threading
import subprocess
import multiprocessing
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import localtts_core as core
import lecture_video as lv
import batch_jobs as bj
import text_import

LEVELS = [("light", "Light"), ("balanced", "Balanced"), ("fast", "Fast")]
IDLE_SECONDS = 90          # free the speech process (and its RAM) after this much inactivity
SETTINGS = os.path.join(core.home_dir(), "settings.json")
BATCH_FILE = os.path.join(core.home_dir(), "batch.json")


def load_settings():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_batch():
    try:
        with open(BATCH_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        return [i for i in items if bj.is_valid(i)]
    except Exception:
        return []


def save_batch(items):
    try:
        os.makedirs(core.home_dir(), exist_ok=True)
        with open(BATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception:
        pass


def save_settings(d):
    try:
        os.makedirs(core.home_dir(), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LocalTTS")
        self.geometry("920x900")
        self.minsize(760, 760)
        self.synth = core.Synth()
        self.busy = False
        self.play_proc = None
        self.last = None                                  # (key, wav_path)
        self.out_wav = os.path.join(tempfile.gettempdir(), f"localtts_{os.getpid()}.wav")
        self.idle_job = None
        self.stop_flag = False
        self.batch = load_batch()
        st = self.settings = load_settings()
        self.lang_code = tk.StringVar(value=st.get("lang", "en"))
        self.level = tk.StringVar(value=st.get("level", "balanced"))
        self.speed = tk.DoubleVar(value=st.get("speed", 1.0))
        self.v_speed = tk.DoubleVar(value=st.get("video_speed", 1.0))
        self.v_hold = tk.StringVar(value=str(st.get("video_hold", lv.SILENT_HOLD)))
        self.v_voices = dict(st.get("video_voices") or {})        # language code -> voice id
        self.last_video = None
        self._uiq = queue.Queue()      # background threads -> window (see _ui)
        self._build()
        self._fill_voices(st.get("voice"))
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(50, self._drain_ui)

    # Background threads must not call Tk directly (not even after()): with some Tk builds
    # the call is silently lost. They put work on a queue that the window empties itself.
    def _ui(self, fn, *a):
        self._uiq.put((fn, a))

    def _drain_ui(self):
        try:
            while True:
                fn, a = self._uiq.get_nowait()
                try:
                    fn(*a)
                except Exception as e:                       # keep the window alive
                    print("UI update failed:", e, file=sys.stderr)
        except queue.Empty:
            pass
        self.after(50, self._drain_ui)

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = dict(padx=10, pady=4)
        # Shared by both tabs, packed bottom-up: status line, progress bar, computer load.
        self.status = ttk.Label(self, text="Ready.", anchor="w")
        self.status.pack(side="bottom", fill="x", padx=10, pady=(2, 8))
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.progress.pack(side="bottom", fill="x", padx=10)
        perf = ttk.LabelFrame(self, text="Computer load")
        perf.pack(side="bottom", fill="x", padx=10, pady=4)
        for val, name in LEVELS:
            ttk.Radiobutton(perf, text=name, value=val, variable=self.level,
                            command=self._on_level).pack(side="left", padx=8, pady=2)
        self.perf_lbl = ttk.Label(perf, text="", foreground="#666")
        self.perf_lbl.pack(side="left", padx=8)
        self._on_level(save=False)

        bf = ttk.LabelFrame(self, text="Batch (audio files, chapters and lecture videos)")
        bf.pack(side="bottom", fill="x", padx=10, pady=4)
        r2 = ttk.Frame(bf)
        r2.pack(side="bottom", fill="x", padx=6, pady=(2, 6))
        self.btn_remove = ttk.Button(r2, text="Remove from batch", command=self._batch_remove)
        self.btn_remove.pack(side="left")
        self.btn_clear = ttk.Button(r2, text="Clear", command=self._batch_clear)
        self.btn_clear.pack(side="left", padx=4)
        self.btn_process = ttk.Button(r2, text="Process batch...", command=self._batch_process)
        self.btn_process.pack(side="right")
        ttk.Button(r2, text="Stop", command=self._stop).pack(side="right", padx=(0, 6))
        self.fmt_cb = ttk.Combobox(r2, state="readonly", width=5, values=["WAV", "MP3"])
        self.fmt_cb.current(0)
        self.fmt_cb.pack(side="right", padx=6)
        ttk.Label(r2, text="Audio format").pack(side="right")
        tf = ttk.Frame(bf)
        tf.pack(fill="x", padx=6, pady=(4, 0))
        self.tree = ttk.Treeview(tf, columns=("n", "type", "lang", "voice", "speed", "text"),
                                 show="headings", height=5, selectmode="extended")
        for col, title, w in (("n", "#", 34), ("type", "Type", 52), ("lang", "Language", 110),
                              ("voice", "Voice", 150), ("speed", "Speed", 50), ("text", "What", 380)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor="w", stretch=(col == "text"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        tts, video, books = ttk.Frame(self.nb), ttk.Frame(self.nb), ttk.Frame(self.nb)
        self.nb.add(tts, text="Text to speech")
        self.nb.add(video, text="Lecture video")
        self.nb.add(books, text="Books (EPUB / PDF)")

        top = ttk.Frame(tts)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Language").grid(row=0, column=0, sticky="w")
        self.lang_cb = ttk.Combobox(top, state="readonly", width=22,
                                    values=[n for _, n in core.LANGUAGES])
        self.lang_cb.current([c for c, _ in core.LANGUAGES].index(self.lang_code.get()))
        self.lang_cb.grid(row=0, column=1, sticky="w", padx=8)
        self.lang_cb.bind("<<ComboboxSelected>>", lambda e: self._on_lang())
        ttk.Label(top, text="Voice").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.voice_cb = ttk.Combobox(top, state="readonly", width=48)
        self.voice_cb.grid(row=1, column=1, columnspan=3, sticky="we", padx=8, pady=(6, 0))
        self.voice_cb.bind("<<ComboboxSelected>>", lambda e: self._update_voice_note())
        self.voice_note = ttk.Label(top, text="", foreground="#666")
        self.voice_note.grid(row=2, column=1, columnspan=3, sticky="w", padx=8)
        ttk.Label(top, text="Speed").grid(row=0, column=2, padx=(16, 4))
        self.speed_lbl = ttk.Label(top, text="", width=6)
        ttk.Scale(top, from_=0.6, to=1.6, variable=self.speed, length=130,
                  command=lambda v: self._speed_text()).grid(row=0, column=3)
        self.speed_lbl.grid(row=0, column=4)
        self._speed_text()
        top.columnconfigure(3, weight=1)

        self.text = tk.Text(tts, wrap="word", undo=True, font=("TkDefaultFont", 12), height=8)
        self.text.pack(fill="both", expand=True, **pad)
        self.text.insert("1.0", "Hello! Type or paste some text here, then press Speak.")

        bar = ttk.Frame(tts)
        bar.pack(fill="x", **pad)
        self.btn_speak = ttk.Button(bar, text="Speak", command=lambda: self._start(play=True))
        self.btn_speak.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="Stop", command=self._stop)
        self.btn_stop.pack(side="left", padx=6)
        self.btn_save = ttk.Button(bar, text="Save as WAV...", command=self._save)
        self.btn_save.pack(side="left")
        ttk.Button(bar, text="Open file...", command=self._open_file).pack(side="right")
        ttk.Button(bar, text="Remove line breaks", command=self._join_lines).pack(side="right", padx=6)
        r1 = ttk.Frame(tts)
        r1.pack(fill="x", padx=10, pady=(0, 6))
        self.btn_add = ttk.Button(r1, text="Add to batch", command=self._batch_add)
        self.btn_add.pack(side="left")
        ttk.Label(r1, text="  File name (optional)").pack(side="left")
        self.name_entry = ttk.Entry(r1, width=28)
        self.name_entry.pack(side="left", padx=4)

        self._build_video(video)
        self._build_books(books)
        self.batch_buttons = [self.btn_add, self.btn_remove, self.btn_clear, self.btn_process]
        self._refresh_tree()
        if self.settings.get("tab") in (1, 2):
            self.nb.select(self.settings["tab"])
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._persist())

    def _speed_text(self):
        self.speed_lbl.config(text=f"{self.speed.get():.2f}x")

    def _set_status(self, msg):
        self.status.config(text=msg)

    def _fill_voices(self, wanted=None):
        self.voices = core.VOICES[self.lang_code.get()]
        self.voice_cb.config(values=[v["label"] for v in self.voices])
        idx = next((i for i, v in enumerate(self.voices) if v["id"] == wanted), 0)
        self.voice_cb.current(idx)
        self._update_voice_note()

    def _current_voice(self):
        return self.voices[self.voice_cb.current()]

    @staticmethod
    def _voice_note(v):
        if v["engine"] == "edge":
            return ("Online preview voice (Microsoft Edge, unofficial): your text is sent to Microsoft. "
                    "Needs internet; may stop working. For finished material use a Kokoro or Piper voice.")
        if core.is_ready(v):
            return "Installed - works offline."
        size = "about 350 MB, shared by all Kokoro voices" if v["engine"] == "kokoro" else "about 20-120 MB"
        return f"Not downloaded yet ({size}); it downloads the first time you use it."

    def _update_voice_note(self):
        self.voice_note.config(text=self._voice_note(self._current_voice()))
        if hasattr(self, "v_voice_note") and getattr(self, "v_voices_list", None):
            self.v_voice_note.config(text=self._voice_note(self._v_current_voice()))
        if hasattr(self, "b_voice"):
            self.b_voice.note.config(text=self._voice_note(self.b_voice.get()[1]))

    def _on_lang(self):
        self.lang_code.set(core.LANGUAGES[self.lang_cb.current()][0])
        self._fill_voices()
        self._persist()

    def _on_level(self, save=True):
        self.perf_lbl.config(text=core.perf_description(self.level.get()))
        if save:
            self._persist()

    def _persist(self):
        self.settings.update(lang=self.lang_code.get(), level=self.level.get(),
                             speed=round(self.speed.get(), 2), voice=self._current_voice()["id"])
        if hasattr(self, "nb"):
            self.settings.update(tab=self.nb.index("current"), video_speed=round(self.v_speed.get(), 2),
                                 video_height=self._v_height(), video_voices=self.v_voices)
            if hasattr(self, "b_voice"):
                self.settings.update(self.b_voice.state())
            try:
                self.settings["book_max_min"] = float(self.b_max.get().replace(",", "."))
            except ValueError:
                pass
            try:
                self.settings["video_hold"] = float(self.v_hold.get().replace(",", "."))
            except ValueError:
                pass
        save_settings(self.settings)

    def _set_busy(self, busy):
        self.busy = busy
        self.btn_speak.config(state="disabled" if busy else "normal")
        self.btn_save.config(state="disabled" if busy else "normal")
        for b in self.batch_buttons + self.video_buttons + self.book_buttons:
            b.config(state="disabled" if busy else "normal")
        if not busy:
            self._v_update_play()

    # ------------------------------------------------------------ synthesis
    def _key(self):
        return (self._current_voice()["id"], round(self.speed.get(), 2),
                self.text.get("1.0", "end").strip())

    def _start(self, play, save_to=None):
        text = self.text.get("1.0", "end").strip()
        if not text:
            self._set_status("Nothing to read - type some text first.")
            return
        if self.busy:
            return
        self._stop_playback()
        self._cancel_idle()
        self.last = None
        voice, level, speed = self._current_voice(), self.level.get(), self.speed.get()
        key = self._key()
        self._persist()
        self._set_busy(True)
        self.progress.config(value=0)
        self._set_status("Preparing...")

        ui = self._ui

        def dl_progress(frac, label):
            ui(self._progress, frac, label)

        def work():
            try:
                req = core.build_request(voice, text, speed, self.out_wav, progress=dl_progress)
                ui(self._set_status, "Generating speech...")
                path, secs = self.synth.run(
                    req, level, lambda i, n: ui(self._progress, i / n, f"Generating... part {i} of {n}"))
                ui(self._done, key, path, secs, play, save_to)
            except core.Cancelled:
                ui(self._cancelled)
            except Exception as e:
                ui(self._fail, e)
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, frac, label):
        self.progress.config(value=frac)
        self._set_status(label)

    def _finish(self):
        self._set_busy(False)
        self.progress.config(value=0)
        self._arm_idle()
        self._update_voice_note()

    def _done(self, key, path, secs, play, save_to):
        self._finish()
        self.last = (key, path)
        self._set_status(f"Done - {secs:.1f} s of audio.")
        if save_to:
            shutil.copyfile(path, save_to)
            self._set_status(f"Saved {save_to}")
        if play:
            self._play(path)

    def _cancelled(self):
        self._finish()
        self._set_status("Stopped.")

    def _fail(self, e, title="Could not generate speech"):
        self._finish()
        self._set_status("Error.")
        if isinstance(e, core.ManualDownloadError):
            ManualDownloadDialog(self, e)
        else:
            messagebox.showerror(title, str(e))

    # -------------------------------------------------------- idle handling
    def _arm_idle(self):
        self._cancel_idle()
        self.idle_job = self.after(IDLE_SECONDS * 1000, self._idle_release)

    def _cancel_idle(self):
        if self.idle_job:
            self.after_cancel(self.idle_job)
            self.idle_job = None

    def _idle_release(self):
        self.idle_job = None
        if not self.busy:
            self.synth.stop()           # frees all model memory; reloads on next use

    # ------------------------------------------------------------- playback
    def _play(self, path):
        try:
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            cmds = [["afplay", path]] if sys.platform == "darwin" else [
                ["paplay", path], ["aplay", "-q", path], ["play", "-q", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]]
            for c in cmds:
                if shutil.which(c[0]):
                    self.play_proc = subprocess.Popen(c)
                    return
            self._set_status("No audio player found - use 'Save as WAV...' instead.")
        except Exception as e:
            self._set_status(f"Playback failed: {e}")

    def _stop_playback(self):
        if sys.platform.startswith("win"):
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self.play_proc and self.play_proc.poll() is None:
            self.play_proc.terminate()
        self.play_proc = None

    def _stop(self):
        self._stop_playback()
        if self.busy:
            self.stop_flag = True
            self.synth.cancel()         # ends generation immediately and frees its memory

    # ---------------------------------------------------------------- batch
    def _refresh_tree(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for i, it in enumerate(self.batch, 1):
            v = core.VOICE_BY_ID[it["voice"]]
            self.tree.insert("", "end", values=(
                i, "Video" if bj.kind(it) == "video" else "Audio",
                core.LANG_NAMES.get(it["lang"], it["lang"]), v["label"].split(" - ")[0],
                f"{it['speed']:.2f}", bj.describe(it)))
        if self.batch:
            self.tree.see(self.tree.get_children()[-1])

    def _batch_append(self, items):
        self.batch.extend(items)
        save_batch(self.batch)
        self._refresh_tree()
        n = len(self.batch)
        self._set_status(f"Added {len(items)} to the batch ({n} item{'s' if n != 1 else ''} in total).")

    def _batch_add(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            self._set_status("Nothing to add - type some text first.")
            return
        self._batch_append([bj.audio_item(self.lang_code.get(), self._current_voice()["id"],
                                          self.speed.get(), text, self.name_entry.get().strip())])
        self.name_entry.delete(0, "end")

    def _batch_remove(self):
        rows = sorted((self.tree.index(i) for i in self.tree.selection()), reverse=True)
        if not rows:
            self._set_status("Select one or more rows in the batch list first.")
            return
        for r in rows:
            del self.batch[r]
        save_batch(self.batch)
        self._refresh_tree()
        self._set_status(f"Removed {len(rows)} item{'s' if len(rows) != 1 else ''} from the batch.")

    def _batch_clear(self):
        if self.batch and messagebox.askyesno("Clear batch", "Remove all items from the batch?"):
            self.batch.clear()
            save_batch(self.batch)
            self._refresh_tree()
            self._set_status("Batch cleared.")

    def _batch_process(self):
        if not self.batch:
            self._set_status("The batch is empty - use 'Add to batch' first.")
            return
        items, level = [dict(i) for i in self.batch], self.level.get()
        folder = None
        if any(bj.kind(i) == "audio" for i in items):
            folder = filedialog.askdirectory(title="Choose the folder for the audio files",
                                             initialdir=self.settings.get("batch_dir"))
            if not folder:
                return
            self.settings["batch_dir"] = folder
        exist = bj.existing_videos(items)
        if exist and not messagebox.askyesno(
                "Replace videos?", f"{len(exist)} video(s) already exist and will be replaced:\n"
                + "\n".join(os.path.basename(p) for p in exist[:8]) + ("\n..." if len(exist) > 8 else "")):
            return
        ext = ".mp3" if self.fmt_cb.get() == "MP3" else ".wav"
        self._persist()
        self._stop_playback()
        self._cancel_idle()
        self.stop_flag = False
        self._set_busy(True)
        self.progress.config(value=0)

        ui = self._ui

        def on_progress(n, total, name, frac, label):
            ui(self._progress, (n + frac) / total, f"Batch item {n + 1} of {total}: {name} - {label}")

        def work():
            try:
                res, cancelled = bj.run(items, folder, self.synth, level, ext,
                                        on_progress, lambda: self.stop_flag)
                ui(self._batch_done, res, cancelled, folder, items)
            except Exception as e:
                ui(self._fail, e)
        threading.Thread(target=work, daemon=True).start()

    def _batch_done(self, results, cancelled, folder, items):
        self._finish()
        total = len(items)
        ok = [r for r in results if r[1]]
        bad = [r for r in results if not r[1]]
        head = (f"Stopped - {len(ok)} of {total} items done" if cancelled
                else f"Done - {len(ok)} of {total} items")
        where = []
        if folder:
            where.append(f"audio in {folder}")
        if any(bj.kind(items[r[0]]) == "video" for r in ok):
            where.append("videos next to their PDFs (page audio in a *_audio folder beside each)")
        self._set_status(head + (" - " + "; ".join(where) if where else ""))
        videos = [items[r[0]]["out"] for r in ok if bj.kind(items[r[0]]) == "video"]
        if videos:
            self.last_video = videos[-1]
            self._v_update_play()
        if bad:
            short = lambda err: ("the voice could not be downloaded (see the next window)"
                                 if isinstance(err, core.ManualDownloadError) else err)
            lines = "\n".join(f"- {r[2]}: {short(r[3])}" for r in bad[:6])
            more = f"\n(+{len(bad) - 6} more)" if len(bad) > 6 else ""
            messagebox.showwarning("Some items failed", f"{len(ok)} done, {len(bad)} failed:\n{lines}{more}")
            seen = set()
            for r in bad:
                if isinstance(r[3], core.ManualDownloadError) and r[3].folder not in seen:
                    seen.add(r[3].folder)
                    ManualDownloadDialog(self, r[3])

    # --------------------------------------------------------- lecture video
    def _build_video(self, tab):
        pad = dict(padx=10, pady=4)
        self.v_pdf, self.v_json, self.v_out = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.v_ready = False
        self.v_voices_list = []

        ff = ttk.LabelFrame(tab, text="1. Slides and script")
        ff.pack(fill="x", **pad)
        self.v_pick_btns = []
        for r, (label, var, cmd) in enumerate((("Slides (PDF)", self.v_pdf, self._v_pick_pdf),
                                               ("Script (JSON)", self.v_json, self._v_pick_json))):
            ttk.Label(ff, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=3)
            ttk.Entry(ff, textvariable=var, state="readonly").grid(row=r, column=1, sticky="we", padx=4)
            b = ttk.Button(ff, text="Choose...", command=cmd)
            b.grid(row=r, column=2, padx=6)
            self.v_pick_btns.append(b)
        self.v_info = ttk.Label(ff, justify="left", wraplength=760,
                                text="Choose the slides PDF. If its script (.json) is in the same folder, "
                                     "it is found automatically.")
        self.v_info.grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(4, 6))
        ff.columnconfigure(1, weight=1)
        ff.bind("<Configure>", lambda e: self.v_info.config(wraplength=max(300, e.width - 30)))

        vf = ttk.LabelFrame(tab, text="2. Voice")
        vf.pack(fill="x", **pad)
        ttk.Label(vf, text="Language").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.v_lang_cb = ttk.Combobox(vf, state="readonly", width=22, values=[n for _, n in core.LANGUAGES])
        self.v_lang_cb.grid(row=0, column=1, sticky="w", padx=4)
        self.v_lang_cb.bind("<<ComboboxSelected>>", lambda e: self._v_fill_voices())
        ttk.Label(vf, text="Speed").grid(row=0, column=2, padx=(16, 4))
        ttk.Scale(vf, from_=0.6, to=1.6, variable=self.v_speed, length=130,
                  command=lambda v: self.v_speed_lbl.config(text=f"{self.v_speed.get():.2f}x")
                  ).grid(row=0, column=3, sticky="w")
        self.v_speed_lbl = ttk.Label(vf, text=f"{self.v_speed.get():.2f}x", width=6)
        self.v_speed_lbl.grid(row=0, column=4, sticky="w")
        ttk.Label(vf, text="Voice").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.v_voice_cb = ttk.Combobox(vf, state="readonly", width=48)
        self.v_voice_cb.grid(row=1, column=1, columnspan=4, sticky="we", padx=4)
        self.v_voice_cb.bind("<<ComboboxSelected>>", lambda e: self._v_on_voice())
        self.v_voice_note = ttk.Label(vf, text="", foreground="#666")
        self.v_voice_note.grid(row=2, column=1, columnspan=4, sticky="w", padx=4, pady=(0, 4))
        vf.columnconfigure(4, weight=1)
        lang = self.lang_code.get()
        self.v_lang_cb.current([c for c, _ in core.LANGUAGES].index(lang))
        self._v_fill_voices()

        of = ttk.LabelFrame(tab, text="3. Video")
        of.pack(fill="x", **pad)
        ttk.Label(of, text="Video size").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.v_size_cb = ttk.Combobox(of, state="readonly", width=24, values=[n for _, n in lv.RESOLUTIONS])
        heights = [h for h, _ in lv.RESOLUTIONS]
        want = self.settings.get("video_height", lv.DEFAULT_HEIGHT)
        self.v_size_cb.current(heights.index(want) if want in heights else heights.index(lv.DEFAULT_HEIGHT))
        self.v_size_cb.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(of, text="Pages without narration are shown for").grid(row=1, column=0, columnspan=2,
                                                                          sticky="w", padx=6, pady=3)
        hold = ttk.Frame(of)
        hold.grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Spinbox(hold, from_=0, to=60, increment=0.5, width=5, textvariable=self.v_hold).pack(side="left")
        ttk.Label(hold, text="seconds, silent (0 = leave these pages out)").pack(side="left", padx=4)
        ttk.Label(of, text="Save video as").grid(row=2, column=0, sticky="w", padx=6, pady=(3, 6))
        ttk.Entry(of, textvariable=self.v_out).grid(row=2, column=1, columnspan=2, sticky="we", padx=4)
        b_out = ttk.Button(of, text="Choose...", command=self._v_pick_out)
        b_out.grid(row=2, column=3, padx=6)
        of.columnconfigure(2, weight=1)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", **pad)
        self.btn_make = ttk.Button(bar, text="Make video", command=self._v_make)
        self.btn_make.pack(side="left")
        self.btn_v_add = ttk.Button(bar, text="Add to batch", command=self._v_add_batch)
        self.btn_v_add.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Stop", command=self._stop).pack(side="left", padx=6)
        self.btn_play_video = ttk.Button(bar, text="Play video", command=lambda: self._open_path(self.last_video))
        self.btn_play_video.pack(side="left")
        self.btn_show_video = ttk.Button(bar, text="Show in folder",
                                         command=lambda: self._open_path(os.path.dirname(self.last_video)))
        self.btn_show_video.pack(side="left", padx=6)
        self.video_buttons = self.v_pick_btns + [b_out, self.btn_make, self.btn_v_add]
        self._v_update_play()

    def _v_height(self):
        return lv.RESOLUTIONS[max(0, self.v_size_cb.current())][0]

    def _v_code(self):
        return core.LANGUAGES[self.v_lang_cb.current()][0]

    def _v_fill_voices(self, wanted=None):
        code = self._v_code()
        self.v_voices_list = core.VOICES[code]
        self.v_voice_cb.config(values=[v["label"] for v in self.v_voices_list])
        wanted = wanted or self.v_voices.get(code)
        if not any(v["id"] == wanted for v in self.v_voices_list):
            wanted = lv.default_voice(code)["id"]
        self.v_voice_cb.current(next(i for i, v in enumerate(self.v_voices_list) if v["id"] == wanted))
        self.v_voice_note.config(text=self._voice_note(self._v_current_voice()))

    def _v_current_voice(self):
        return self.v_voices_list[self.v_voice_cb.current()]

    def _v_on_voice(self):
        self.v_voices[self._v_code()] = self._v_current_voice()["id"]
        self.v_voice_note.config(text=self._voice_note(self._v_current_voice()))
        self._persist()

    def _v_update_play(self):
        ok = bool(self.last_video and os.path.isfile(self.last_video))
        for b in (self.btn_play_video, self.btn_show_video):
            b.config(state="normal" if ok and not self.busy else "disabled")

    def _v_pick_pdf(self):
        path = filedialog.askopenfilename(title="Choose the slides", filetypes=[("PDF files", "*.pdf *.PDF")],
                                          initialdir=self.settings.get("video_dir"))
        if not path:
            return
        self.v_pdf.set(path)
        self.v_json.set(lv.find_script_for_pdf(path) or "")
        self.v_out.set(lv.default_output(path))
        self.settings["video_dir"] = os.path.dirname(path)
        self._v_check(new_script=True)

    def _v_pick_json(self):
        start = os.path.dirname(self.v_pdf.get()) if self.v_pdf.get() else self.settings.get("video_dir")
        path = filedialog.askopenfilename(title="Choose the script",
                                          filetypes=[("Lecture script", "*.json"), ("All files", "*.*")],
                                          initialdir=start)
        if not path:
            return
        self.v_json.set(path)
        pdf = lv.find_pdf_for_script(path)
        if pdf and pdf != self.v_pdf.get():
            self.v_pdf.set(pdf)
            self.v_out.set(lv.default_output(pdf))
        self.settings["video_dir"] = os.path.dirname(path)
        self._v_check(new_script=True)

    def _v_pick_out(self):
        cur = self.v_out.get() or (lv.default_output(self.v_pdf.get()) if self.v_pdf.get() else "lecture.mp4")
        path = filedialog.asksaveasfilename(title="Save video as", defaultextension=".mp4",
                                            filetypes=[("MP4 video", "*.mp4")],
                                            initialdir=os.path.dirname(cur) or None,
                                            initialfile=os.path.basename(cur))
        if path:
            self.v_out.set(path)

    def _v_info(self, text, error=False):
        self.v_info.config(text=text, foreground="#b00020" if error else "")

    def _v_check(self, new_script=False):
        """Read the script, check it against the PDF and show what was found."""
        self.v_ready = False
        pdf, js = self.v_pdf.get(), self.v_json.get()
        if not pdf:
            self._v_info("Choose the slides PDF.")
            return False
        if not js:
            self._v_info("No script was found next to this PDF - choose the script (.json) file.", error=True)
            return False
        try:
            script = lv.load_script(js)
            _, warnings = lv.validate(script, pdf)
        except lv.ScriptError as e:
            self._v_info(str(e), error=True)
            return False
        s = lv.summary(script)
        est = int(s["est_seconds"])
        lines = [f"{'“' + s['title'] + '” - ' if s['title'] else ''}{s['pages']} pages, "
                 f"language {s['language'] or '(not set)'}, roughly {est // 60}:{est % 60:02d} min of speech."]
        if s["silent"]:
            lines.append(f"{len(s['silent'])} page(s) without narration ({lv._short_list(s['silent'])}) "
                         "are shown silently for the time set below (0 seconds leaves them out).")
        code = lv.language_code(s["language"])
        if new_script:
            if code:
                self.v_lang_cb.current([c for c, _ in core.LANGUAGES].index(code))
                pref = self.v_voices.get(code)
                default = lv.default_voice(s["language"])
                self._v_fill_voices(pref or (default and default["id"]))
        if not code:
            lines.append(f"This app has no voices for “{s['language']}”. Choose a language and voice below.")
        lines += warnings
        self._v_info("\n".join(lines))
        self.v_ready = True
        return True

    def _v_params(self, title="Cannot make the video"):
        """Checked settings of the video tab as a dict, or None (after telling the user why)."""
        if not self._v_check():
            messagebox.showerror(title, self.v_info.cget("text"))
            return None
        pdf, js = self.v_pdf.get(), self.v_json.get()
        out = self.v_out.get().strip() or lv.default_output(pdf)
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
        out = os.path.abspath(out)
        self.v_out.set(out)
        try:
            hold = float(self.v_hold.get().replace(",", "."))
            if not 0 <= hold <= 60:
                raise ValueError
        except ValueError:
            messagebox.showerror(title, "Enter a time between 0 and 60 seconds for pages without narration (0 leaves them out).")
            return None
        return dict(pdf=pdf, js=js, out=out, hold=hold, voice=self._v_current_voice(),
                    speed=self.v_speed.get(), height=self._v_height())

    def _v_add_batch(self):
        p = self._v_params("Cannot add to the batch")
        if not p:
            return
        if any(bj.kind(i) == "video" and i["out"] == p["out"] for i in self.batch):
            if not messagebox.askyesno("Already in the batch",
                                       f"{os.path.basename(p['out'])} is already in the batch. Add it again?"):
                return
        self._batch_append([bj.video_item(self._v_code(), p["voice"]["id"], p["speed"], p["pdf"], p["js"],
                                          p["out"], p["height"], p["hold"])])
        self._persist()

    def _v_make(self):
        if self.busy:
            return
        p = self._v_params()
        if not p:
            return
        pdf, js, out, hold = p["pdf"], p["js"], p["out"], p["hold"]
        if os.path.exists(out) and not messagebox.askyesno(
                "Replace video?", f"{os.path.basename(out)} already exists. Replace it?"):
            return
        voice, level, speed, height = p["voice"], self.level.get(), p["speed"], p["height"]
        self._persist()
        self._stop_playback()
        self._cancel_idle()
        self.stop_flag = False
        self._set_busy(True)
        self.progress.config(value=0)
        self._set_status("Starting...")

        ui = self._ui

        def work():
            try:
                res = lv.make_video(pdf, js, out, voice, self.synth, speed=speed, height=height,
                                    silent_hold=hold, level=level,
                                    on_progress=lambda f, l: ui(self._progress, f, l),
                                    should_stop=lambda: self.stop_flag)
                ui(self._v_done, res)
            except (lv.Cancelled, core.Cancelled):
                ui(self._cancelled)
            except lv.ScriptError as e:
                ui(self._fail, e, "Cannot make the video")
            except Exception as e:
                ui(self._fail, e, "Could not make the video")
        threading.Thread(target=work, daemon=True).start()

    def _v_done(self, res):
        self.last_video = res["out"]
        self._finish()
        secs = int(round(res["seconds"]))
        self._set_status(f"Done - {secs // 60}:{secs % 60:02d} min video saved: {res['out']}")
        msg = [f"Saved {os.path.basename(res['out'])}",
               f"Length {secs // 60}:{secs % 60:02d} min, {res['size'][0]} x {res['size'][1]} pixels, "
               f"{res['pages']} pages."]
        if res["skipped"]:
            msg.append(f"Left out (no narration): pages {lv._short_list(res['skipped'])}.")
        elif res["silent"]:
            msg.append(f"Pages without narration: {lv._short_list(res['silent'])}.")
        msg.append(f"Page audio: {os.path.basename(res['audio_dir'])}/ (next to the video).")
        msg += res["warnings"]
        if messagebox.askyesno("Video ready", "\n".join(msg) + "\n\nPlay it now?"):
            self._open_path(res["out"])

    def _open_path(self, path):
        if not path or not os.path.exists(path):
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._set_status(f"Could not open {path}: {e}")

    # ----------------------------------------------------------------- books
    def _build_books(self, tab):
        pad = dict(padx=10, pady=4)
        self.b_path = tk.StringVar()
        self.b_book, self.b_chapters, self.b_methods = None, [], []

        ff = ttk.LabelFrame(tab, text="1. Book")
        ff.pack(fill="x", **pad)
        ttk.Label(ff, text="EPUB or PDF").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(ff, textvariable=self.b_path, state="readonly").grid(row=0, column=1, sticky="we", padx=4)
        b_pick = ttk.Button(ff, text="Choose...", command=self._b_pick)
        b_pick.grid(row=0, column=2, padx=6)
        self.b_info = ttk.Label(ff, justify="left", wraplength=760,
                                text="Choose an e-book (.epub) or a text PDF. It is split into chapters; "
                                     "each selected chapter becomes one audio file in the batch.")
        self.b_info.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 6))
        ff.columnconfigure(1, weight=1)
        ff.bind("<Configure>", lambda e: self.b_info.config(wraplength=max(300, e.width - 30)))

        vf = ttk.LabelFrame(tab, text="3. Voice")
        vf.pack(side="bottom", fill="x", **pad)
        self.b_voice = VoicePicker(vf, self, "book", self.settings)
        self.b_voice.pack(fill="x")
        bar = ttk.Frame(tab)
        bar.pack(side="bottom", fill="x", **pad)
        self.btn_b_add = ttk.Button(bar, text="Add selected chapters to batch", command=self._b_add)
        self.btn_b_add.pack(side="right")

        cf = ttk.LabelFrame(tab, text="2. Chapters")
        cf.pack(fill="both", expand=True, **pad)
        row = ttk.Frame(cf)
        row.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Label(row, text="Split by").pack(side="left")
        self.b_method_cb = ttk.Combobox(row, state="readonly", width=30)
        self.b_method_cb.pack(side="left", padx=4)
        self.b_method_cb.bind("<<ComboboxSelected>>", lambda e: self._b_fill())
        self.b_level_lbl = ttk.Label(row, text="  Level")
        self.b_level_cb = ttk.Combobox(row, state="readonly", width=3)
        self.b_level_cb.bind("<<ComboboxSelected>>", lambda e: self._b_fill())
        self.b_max = tk.StringVar(value=f"{self.settings.get('book_max_min', 20):g}")
        self.b_max_frame = ttk.Frame(row)
        ttk.Label(self.b_max_frame, text="  Max. length per part").pack(side="left")
        sp = ttk.Spinbox(self.b_max_frame, from_=2, to=180, increment=1, width=5, textvariable=self.b_max,
                         command=self._b_fill)
        sp.pack(side="left", padx=4)
        sp.bind("<Return>", lambda e: self._b_fill())
        sp.bind("<FocusOut>", lambda e: self._b_fill())
        ttk.Label(self.b_max_frame, text="min").pack(side="left")
        tf = ttk.Frame(cf)
        tf.pack(fill="both", expand=True, padx=6, pady=2)
        self.b_tree = ttk.Treeview(tf, columns=("n", "title", "words", "min"), show="headings",
                                   selectmode="extended", height=6)
        for col, title, w, anchor in (("n", "#", 40, "w"), ("title", "Chapter", 460, "w"),
                                      ("words", "Words", 80, "e"), ("min", "About", 80, "e")):
            self.b_tree.heading(col, text=title)
            self.b_tree.column(col, width=w, anchor=anchor, stretch=(col == "title"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.b_tree.yview)
        self.b_tree.configure(yscrollcommand=sb.set)
        self.b_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        r2 = ttk.Frame(cf)
        r2.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Button(r2, text="Select all",
                   command=lambda: self.b_tree.selection_set(self.b_tree.get_children())).pack(side="left")
        ttk.Button(r2, text="Select none", command=lambda: self.b_tree.selection_set(())).pack(side="left", padx=6)
        ttk.Button(r2, text="Show in text box", command=self._b_to_text).pack(side="left")
        self.b_sel_lbl = ttk.Label(r2, text="", foreground="#666")
        self.b_sel_lbl.pack(side="right")
        self.b_tree.bind("<<TreeviewSelect>>", lambda e: self._b_sel_info())
        self.book_buttons = [b_pick, self.btn_b_add]

    def _b_pick(self):
        path = filedialog.askopenfilename(
            title="Choose a book", initialdir=self.settings.get("book_dir"),
            filetypes=[("E-books and PDFs", "*.epub *.EPUB *.pdf *.PDF"), ("EPUB", "*.epub *.EPUB"),
                       ("PDF", "*.pdf *.PDF")])
        if path:
            self._b_load(path)

    def _b_load(self, path):
        self.settings["book_dir"] = os.path.dirname(path)
        self._set_status(f"Reading {os.path.basename(path)}...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            book = text_import.open_book(path)
        except (ValueError, OSError) as e:
            self._set_status("Could not open the book.")
            self.b_info.config(text=str(e), foreground="#b00020")
            messagebox.showerror("Could not open the book", str(e))
            return
        finally:
            self.config(cursor="")
        self.b_book = book
        self.b_path.set(path)
        self.b_methods = book.methods()
        self.b_method_cb.config(values=[text_import.METHOD_NAMES[m].capitalize() for m in self.b_methods])
        self.b_method_cb.current(0)
        code = lv.language_code(book.language)
        if code:
            self.b_voice.set_language(code)
        depth = book.max_depth()
        self.b_level_cb.config(values=list(range(1, depth + 1)))
        self.b_level_cb.set(book.default_depth())
        self._b_fill()
        self._set_status(f"Opened {os.path.basename(path)}.")

    def _b_method(self):
        return self.b_methods[max(0, self.b_method_cb.current())] if self.b_methods else None

    def _b_max_words(self):
        try:
            minutes = min(180.0, max(2.0, float(self.b_max.get().replace(",", "."))))
        except ValueError:
            minutes = 20.0
        return int(minutes * text_import.WORDS_PER_MINUTE)

    def _b_fill(self):
        book = self.b_book
        if not book:
            return
        method = self._b_method()
        for w in (self.b_level_lbl, self.b_level_cb, self.b_max_frame):
            w.pack_forget()
        if method in ("toc", "outline") and book.max_depth() > 1:
            self.b_level_lbl.pack(side="left")
            self.b_level_cb.pack(side="left", padx=4)
        if method == "length":
            self.b_max_frame.pack(side="left")
        depth = int(self.b_level_cb.get() or 1)
        self.b_chapters = book.chapters(depth=depth, method=method, max_words=self._b_max_words())
        for iid in self.b_tree.get_children():
            self.b_tree.delete(iid)
        first = 0 if self.b_chapters and self.b_chapters[0]["level"] == 0 else 1
        for k, ch in enumerate(self.b_chapters):
            mins = ch["words"] / text_import.WORDS_PER_MINUTE
            self.b_tree.insert("", "end", iid=str(k), values=(
                k + first, "    " * max(0, ch["level"] - 1) + ch["title"], f"{ch['words']:,}",
                f"{mins:.0f} min" if mins >= 1 else "<1 min"))
        self.b_tree.selection_set([str(k) for k, ch in enumerate(self.b_chapters) if ch["level"] > 0 and ch["words"]])
        lang = book.language or "not given"
        how = text_import.METHOD_NAMES[method]
        lines = [f"\u201c{book.title}\u201d - {'e-book' if book.kind == 'epub' else 'PDF'}, "
                 f"language: {lang}. {len(self.b_chapters)} parts, split by {how}."]
        if book.kind == "pdf" and method == "length":
            lines.append("No chapters were found (no bookmarks, no headings such as \u201cChapter 3\u201d), "
                         "so the text is cut at paragraph ends into parts of at most the length set below.")
        if not lv.language_code(book.language):
            lines.append("Check the language and voice at the bottom.")
        self.b_info.config(text="\n".join(lines), foreground="")
        self._b_sel_info()

    def _b_selected(self):
        return [(int(i), self.b_chapters[int(i)]) for i in sorted(self.b_tree.selection(), key=int)
                if self.b_chapters[int(i)]["words"]]

    def _b_sel_info(self):
        sel = self._b_selected()
        words = sum(ch["words"] for _, ch in sel)
        mins = words / text_import.WORDS_PER_MINUTE
        size = f"{mins / 60:.1f} h" if mins >= 60 else f"{max(1, round(mins))} min"
        self.b_sel_lbl.config(text=f"{len(sel)} selected, about {size} of speech" if sel else "")

    def _b_to_text(self):
        sel = self._b_selected()
        if not sel:
            self._set_status("Select one or more chapters first.")
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n\n".join(ch["text"] for _, ch in sel))
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, sel[0][1]["title"][:60])
        self.nb.select(0)
        self._set_status(f"{len(sel)} chapter(s) put in the text box.")

    def _b_add(self):
        if not self.b_book:
            self._set_status("Choose a book first.")
            return
        sel = self._b_selected()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select one or more chapters that contain text.")
            return
        lang, voice, speed = self.b_voice.get()
        stem = os.path.splitext(os.path.basename(self.b_book.path))[0]
        first = 0 if self.b_chapters[0]["level"] == 0 else 1
        width = 3 if len(self.b_chapters) >= 100 else 2
        items = [bj.audio_item(lang, voice["id"], speed, ch["text"],
                               name=f"{stem}_{k + first:0{width}d}_{ch['title'][:50]}",
                               source=f"{stem[:24]} - {ch['title'][:40]}") for k, ch in sel]
        self._batch_append(items)
        self._persist()

    # ------------------------------------------------------------- file ops
    def _save(self):
        path = filedialog.asksaveasfilename(defaultextension=".wav",
                                            filetypes=[("WAV audio", "*.wav")])
        if not path:
            return
        if self.last and self.last[0] == self._key() and os.path.isfile(self.last[1]):
            shutil.copyfile(self.last[1], path)         # already generated - no recompute
            self._set_status(f"Saved {path}")
        else:
            self._start(play=False, save_to=path)

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open text or PDF (whole text into the text box)",
            filetypes=[("Text, PDF, EPUB", "*.txt *.md *.pdf *.PDF *.epub *.EPUB"), ("Text files", "*.txt *.md"),
                       ("PDF", "*.pdf *.PDF"), ("EPUB e-books", "*.epub *.EPUB"), ("All", "*.*")],
            initialdir=self.settings.get("open_dir"))
        if not path:
            return
        self.settings["open_dir"] = os.path.dirname(path)
        ext = os.path.splitext(path)[1].lower()
        self._set_status(f"Reading {os.path.basename(path)}...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            if ext == ".epub":
                self.nb.select(2)
                self._b_load(path)
                return
            if ext == ".pdf":
                text = text_import.pdf_text(path)
                note = (" (line breaks inside paragraphs removed; for one audio file per chapter, "
                        "use the Books tab)")
            else:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                note = ""
        except (ValueError, OSError) as e:
            self._set_status("Could not open the file.")
            messagebox.showerror("Could not open the file", str(e))
            return
        finally:
            self.config(cursor="")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, os.path.splitext(os.path.basename(path))[0])
        self._set_status(f"Opened {os.path.basename(path)}: {len(text.split()):,} words{note}.")

    def _join_lines(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            return
        new = text_import.reflow(text)
        self.text.edit_separator()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", new)
        self.text.edit_separator()
        self._set_status("Line breaks inside paragraphs removed (Ctrl+Z undoes it).")

    def _quit(self):
        self._stop_playback()
        self.synth.stop()
        for p in (self.out_wav,):
            try:
                os.remove(p)
            except OSError:
                pass
        self.destroy()


class ManualDownloadDialog(tk.Toplevel):
    """Shows how to download a voice by hand: selectable text, links, and the target folder."""

    def __init__(self, app, err):
        super().__init__(app)
        self.app, self.err = app, err
        self.title("Voice could not be downloaded")
        self.transient(app)
        txt = tk.Text(self, wrap="word", width=78, height=24, font=("TkDefaultFont", 11),
                      padx=10, pady=10, relief="flat")
        txt.insert("1.0", str(err))
        # Read-only, but text can still be selected and copied.
        txt.bind("<Key>", lambda e: None if (e.state & 0x4 or e.state & 0x8) and e.keysym.lower() in ("c", "a")
                 else "break")
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        sb = ttk.Scrollbar(self, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=(8, 4))
        txt.pack(fill="both", expand=True, padx=(8, 0), pady=(8, 4))
        ttk.Button(bar, text="Open download links", command=self._open_links).pack(side="left")
        ttk.Button(bar, text="Open folder", command=lambda: app._open_path(err.folder)).pack(side="left", padx=6)
        ttk.Button(bar, text="Copy text", command=self._copy).pack(side="left")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.focus_set()

    def _open_links(self):
        import webbrowser
        for _, url in self.err.files:
            webbrowser.open(url)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(str(self.err))
        self.app._set_status("Instructions copied to the clipboard.")


class VoicePicker(ttk.Frame):
    """Language + voice + speed, remembering the chosen voice per language (settings key <key>_voices)."""

    def __init__(self, parent, app, key, settings):
        super().__init__(parent)
        self.app, self.key = app, key
        self.voices_by_lang = dict(settings.get(f"{key}_voices") or {})
        self.speed = tk.DoubleVar(value=settings.get(f"{key}_speed", 1.0))
        ttk.Label(self, text="Language").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.lang_cb = ttk.Combobox(self, state="readonly", width=22, values=[n for _, n in core.LANGUAGES])
        self.lang_cb.grid(row=0, column=1, sticky="w", padx=4)
        self.lang_cb.bind("<<ComboboxSelected>>", lambda e: self._fill())
        ttk.Label(self, text="Speed").grid(row=0, column=2, padx=(16, 4))
        self.speed_lbl = ttk.Label(self, width=6)
        ttk.Scale(self, from_=0.6, to=1.6, variable=self.speed, length=130,
                  command=lambda v: self.speed_lbl.config(text=f"{self.speed.get():.2f}x")).grid(row=0, column=3, sticky="w")
        self.speed_lbl.config(text=f"{self.speed.get():.2f}x")
        self.speed_lbl.grid(row=0, column=4, sticky="w")
        ttk.Label(self, text="Voice").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.voice_cb = ttk.Combobox(self, state="readonly", width=48)
        self.voice_cb.grid(row=1, column=1, columnspan=4, sticky="we", padx=4)
        self.voice_cb.bind("<<ComboboxSelected>>", lambda e: self._on_voice())
        self.note = ttk.Label(self, foreground="#666")
        self.note.grid(row=2, column=1, columnspan=4, sticky="w", padx=4, pady=(0, 4))
        self.columnconfigure(4, weight=1)
        self.set_language(settings.get(f"{key}_lang") or app.lang_code.get())

    def set_language(self, code):
        codes = [c for c, _ in core.LANGUAGES]
        self.lang_cb.current(codes.index(code) if code in codes else 0)
        self._fill()

    def code(self):
        return core.LANGUAGES[self.lang_cb.current()][0]

    def _fill(self):
        code = self.code()
        self.voices = core.VOICES[code]
        self.voice_cb.config(values=[v["label"] for v in self.voices])
        want = self.voices_by_lang.get(code)
        if not any(v["id"] == want for v in self.voices):
            want = lv.default_voice(code)["id"]
        self.voice_cb.current(next(i for i, v in enumerate(self.voices) if v["id"] == want))
        self.note.config(text=App._voice_note(self.voices[self.voice_cb.current()]))

    def _on_voice(self):
        v = self.voices[self.voice_cb.current()]
        self.voices_by_lang[self.code()] = v["id"]
        self.note.config(text=App._voice_note(v))
        self.app._persist()

    def get(self):
        """(language code, voice dict, speed)."""
        return self.code(), self.voices[self.voice_cb.current()], round(self.speed.get(), 2)

    def state(self):
        return {f"{self.key}_voices": self.voices_by_lang, f"{self.key}_speed": round(self.speed.get(), 2),
                f"{self.key}_lang": self.code()}


if __name__ == "__main__":
    multiprocessing.freeze_support()        # required for the packaged app
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":     # used by the build workflow
        sys.exit(lv.selftest(sys.argv[2]))
    App().mainloop()
