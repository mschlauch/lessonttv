"""Tests for text_import.py (PDF reflow, EPUB chapters) and batch_jobs.py (mixed batch).

    python tests/test_import_batch.py          # no network needed (Edge voice is mocked)
"""
import os
import sys
import json
import shutil
import zipfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="lv_import_")
os.environ.setdefault("LOCALTTS_DIR", os.path.join(TMP, "home"))

import localtts_core as core            # noqa: E402
import lecture_video as lv              # noqa: E402
import batch_jobs as bj                 # noqa: E402
import text_import as ti                # noqa: E402
from test_lecture_video import make_pdf, make_script, edge_mock_mp3   # noqa: E402


def make_text_pdf(path, pages, bookmarks=()):
    """A real text PDF (Helvetica, WinAnsi) - one list of lines per page.
    bookmarks: (title, page index) entries for a flat outline."""
    objs = ["<< /Type /Catalog /Pages 2 0 R%s >>" % (" /Outlines %d 0 R" % (4 + 2 * len(pages)) if bookmarks else ""),
            None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages:
        ops = ["BT /F1 11 Tf 14 TL 60 780 Td"]
        for l in lines:
            esc = l.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            ops.append("(" + esc.decode("latin-1") + ") Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        content_no = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_no} 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    if bookmarks:
        root = len(objs) + 1
        first = root + 1
        n = len(bookmarks)
        objs.append(f"<< /Type /Outlines /First {first} 0 R /Last {first + n - 1} 0 R /Count {n} >>")
        for k, (title, page) in enumerate(bookmarks):
            me = first + k
            links = (f" /Prev {me - 1} 0 R" if k else "") + (f" /Next {me + 1} 0 R" if k < n - 1 else "")
            page_obj = kids[page].split()[0]
            objs.append(f"<< /Title ({title}) /Parent {root} 0 R{links} /Dest [{page_obj} 0 R /XYZ 0 842 0] >>")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        body = o if isinstance(o, bytes) else o.encode("latin-1")
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as f:
        f.write(out)


def make_epub(path, nav=True, ncx=False):
    container = ('<?xml version="1.0"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    manifest = ['<item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="c1" href="Text/part1.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="c2" href="Text/part%202.xhtml" media-type="application/xhtml+xml"/>']
    if nav:
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    if ncx:
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    toc_attr = ' toc="ncx"' if ncx else ""
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Das Testbuch</dc:title>'
           '<dc:language>de</dc:language></metadata><manifest>' + "".join(manifest) + '</manifest>'
           f'<spine{toc_attr}><itemref idref="cover"/><itemref idref="c1"/>'
           '<itemref idref="c2"/></spine></package>')
    cover = "<html><body><p>Titelseite</p></body></html>"
    part1 = ('<html xmlns:epub="http://www.idpf.org/2007/ops"><head><title>x</title><style>p{}</style></head><body>'
             '<h1 id="k1">Kapitel 1</h1><p>Erster   Absatz,\n  über zwei\nZeilen.<a epub:type="noteref" href="#n1">1</a></p>'
             '<h2 id="k1a">Abschnitt 1.1</h2><p>Text&nbsp;im Abschnitt.</p>'
             '<h1 id="k2">Kapitel 2</h1><p>Zweites Kapitel.<br/>Neue Zeile.</p></body></html>')
    part2 = '<html><body><h1>Kapitel 3</h1><p>Drittes Kapitel &amp; Ende.</p><script>var x=1;</script></body></html>'
    nav_doc = ('<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>'
               '<li><a href="Text/part1.xhtml#k1">Kapitel 1</a><ol><li><a href="Text/part1.xhtml#k1a">Abschnitt 1.1</a></li></ol></li>'
               '<li><a href="Text/part1.xhtml#k2">Kapitel 2</a></li>'
               '<li><a href="Text/part%202.xhtml">Kapitel 3</a></li></ol></nav>'
               '<nav epub:type="landmarks"><ol><li><a href="Text/cover.xhtml">Cover</a></li></ol></nav></body></html>')
    ncx_doc = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
               '<navPoint id="a"><navLabel><text>Kapitel 1</text></navLabel><content src="Text/part1.xhtml#k1"/>'
               '<navPoint id="b"><navLabel><text>Abschnitt 1.1</text></navLabel><content src="Text/part1.xhtml#k1a"/></navPoint>'
               '</navPoint><navPoint id="c"><navLabel><text>Kapitel 2</text></navLabel><content src="Text/part1.xhtml#k2"/></navPoint>'
               '<navPoint id="d"><navLabel><text>Kapitel 3</text></navLabel><content src="Text/part%202.xhtml"/></navPoint>'
               '</navMap></ncx>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/Text/cover.xhtml", cover)
        z.writestr("OEBPS/Text/part1.xhtml", part1)
        z.writestr("OEBPS/Text/part 2.xhtml", part2)
        if nav:
            z.writestr("OEBPS/nav.xhtml", nav_doc)
        if ncx:
            z.writestr("OEBPS/toc.ncx", ncx_doc)


class Reflow(unittest.TestCase):
    def test_joins_lines_and_hyphens(self):
        raw = ("Dies ist ein langer Absatz, der im PDF über mehrere Zei-\n"
               "len umbrochen wurde und eigentlich ein einziger Satz ist, der\n"
               "erst hier endet.\n"
               "Ein Nord-\nSüd-Gefälle bleibt erhalten.\n\n"
               "Zweiter Absatz nach einer Leerzeile.")
        out = ti.reflow(raw)
        paras = out.split("\n\n")
        self.assertEqual(len(paras), 3, out)
        self.assertIn("mehrere Zeilen umbrochen", paras[0])
        self.assertTrue(paras[0].endswith("erst hier endet."))
        self.assertIn("Nord-Süd-Gefälle", paras[1])
        self.assertNotIn("\n", paras[0])

    def test_bullets_start_paragraphs(self):
        out = ti.reflow("Einleitung zu einer Liste mit genug Text damit sie lang ist\n• erster Punkt\n• zweiter Punkt")
        self.assertEqual(out.count("\n\n"), 2)

    def test_text_pdf(self):
        pages = []
        for n in range(1, 6):
            pages.append([f"Kopfzeile des Buches {n}", "",
                          f"Dies ist der {n}. sehr lange Satz in einem PDF, der ueber mehrere Zei-",
                          "len geht und hier noch weiter laeuft, bis er schliesslich",
                          f"auf Seite {n} endet.", "Ein Nord-", f"Sued-Gefaelle im {['', 'Garten', 'Haus', 'Wald', 'Feld', 'Dorf'][n]}, mit Absatz am Ende.",
                          str(n)])
        pdf = os.path.join(TMP, "text.pdf")
        make_text_pdf(pdf, pages)
        text = ti.pdf_text(pdf)
        self.assertNotIn("Kopfzeile", text)               # running header removed
        self.assertIn("mehrere Zeilen geht", text)        # dehyphenated
        self.assertIn("schliesslich auf Seite 3 endet.", text)
        lines = text.split("\n")
        self.assertFalse(any(l.strip().isdigit() for l in lines), "page numbers left in")
        self.assertFalse(any(l and not l.strip() == l for l in lines))
        self.assertIn("\n\n", text)
        self.assertIn("Ein Nord-Sued-Gefaelle im Haus, mit Absatz am Ende.", text)

    def test_image_pdf_has_no_text(self):
        pdf = os.path.join(TMP, "img.pdf")
        make_pdf(pdf, 1)
        with self.assertRaises(ValueError):
            ti.pdf_text(pdf)


class Epub(unittest.TestCase):
    def check(self, book):
        self.assertEqual(book.title, "Das Testbuch")
        self.assertEqual(book.language, "de")
        self.assertEqual(book.max_depth(), 2)
        ch = book.chapters(1)
        self.assertEqual([c["title"] for c in ch], ["(Before the first chapter)", "Kapitel 1", "Kapitel 2", "Kapitel 3"])
        self.assertEqual(ch[0]["text"], "Titelseite")
        k1 = ch[1]["text"]
        self.assertIn("Erster Absatz, über zwei Zeilen.", k1)
        self.assertNotIn("Zeilen.1", k1)                    # footnote marker skipped
        self.assertIn("Abschnitt 1.1\n\nText im Abschnitt.", k1)
        self.assertEqual(ch[2]["text"], "Kapitel 2\n\nZweites Kapitel.\n\nNeue Zeile.")
        self.assertEqual(ch[3]["text"], "Kapitel 3\n\nDrittes Kapitel & Ende.")
        ch2 = book.chapters(2)
        self.assertEqual([c["title"] for c in ch2][1:3], ["Kapitel 1", "Abschnitt 1.1"])
        self.assertNotIn("Abschnitt", ch2[1]["text"])
        self.assertEqual(ch2[2]["level"], 2)

    def test_nav(self):
        p = os.path.join(TMP, "nav.epub")
        make_epub(p, nav=True)
        self.check(ti.EpubBook(p))

    def test_ncx(self):
        p = os.path.join(TMP, "ncx.epub")
        make_epub(p, nav=False, ncx=True)
        self.check(ti.EpubBook(p))

    def test_no_toc(self):
        p = os.path.join(TMP, "notoc.epub")
        make_epub(p, nav=False, ncx=False)
        ch = ti.EpubBook(p).chapters()
        self.assertEqual(len(ch), 3)                       # one per document
        self.assertEqual(ch[2]["title"], "Kapitel 3")

    def test_bad_file(self):
        p = os.path.join(TMP, "bad.epub")
        with open(p, "w") as f:
            f.write("nope")
        with self.assertRaises(ValueError):
            ti.EpubBook(p)


WORDS = ("Haus Baum Wald Feld Dorf Stadt Fluss Berg Wiese Garten Strasse Bruecke Turm Schule Kirche "
         "Markt Hafen Insel Kueste Tal").split()


def body(seed, n):
    """n varied filler lines (no two pages alike, so nothing looks like a header)."""
    return [f"Der {WORDS[(seed + i) % 20]} liegt neben dem {WORDS[(seed * 3 + i * 7) % 20]} und dem "
            f"{WORDS[(seed * 5 + i * 3) % 20]}, wie man sieht" for i in range(n)]


class PdfChapters(unittest.TestCase):
    def test_outline(self):
        pages = [["Vorwort des Buches"] + body(1, 25),
                 ["Kapitel Eins"] + body(2, 25),
                 body(3, 3) + ["Kapitel Zwei"] + body(4, 22),
                 body(5, 25)]
        pdf = os.path.join(TMP, "outline.pdf")
        make_text_pdf(pdf, pages, bookmarks=[("Vorwort des Buches", 0), ("Kapitel Eins", 1), ("Kapitel Zwei", 2)])
        book = ti.PdfBook(pdf)
        self.assertEqual(book.methods()[0], "outline")
        ch = book.chapters()
        self.assertEqual([c["title"] for c in ch], ["Vorwort des Buches", "Kapitel Eins", "Kapitel Zwei"])
        self.assertTrue(ch[2]["text"].startswith("Kapitel Zwei"), ch[2]["text"][:60])
        self.assertIn(body(3, 1)[0], ch[1]["text"])          # lines above the heading stay in chapter 1
        self.assertIn(body(5, 1)[0], ch[2]["text"])
        total = sum(len(l.split()) for p in pages for l in p)
        self.assertEqual(sum(c["words"] for c in ch), total)

    def test_headings(self):
        pages = [["Ein Buch ohne Lesezeichen"] + body(7, 5),
                 ["Kapitel 1", "Der Anfang"] + body(8, 25),
                 ["Teil der Loesung ist kein Kapitel"] + body(9, 25),
                 ["KAPITEL 2: Die Mitte"] + body(10, 25),
                 ["Epilog"] + body(11, 25)]
        pdf = os.path.join(TMP, "headings.pdf")
        make_text_pdf(pdf, pages)
        book = ti.PdfBook(pdf)
        self.assertEqual(book.methods(), ["headings", "length"])
        titles = [c["title"] for c in book.chapters()]
        self.assertEqual(titles, ["(Before the first chapter)", "Kapitel 1 - Der Anfang", "KAPITEL 2: Die Mitte", "Epilog"])

    def test_length_fallback(self):
        pages = [body(i, 30) for i in range(12)]
        pdf = os.path.join(TMP, "plain.pdf")
        make_text_pdf(pdf, pages)
        book = ti.PdfBook(pdf)
        self.assertEqual(book.methods(), ["length"])
        ch = book.chapters(max_words=1000)
        total = sum(len(l.split()) for p in pages for l in p)
        self.assertGreaterEqual(len(ch), 4)
        self.assertTrue(all(c["words"] <= 1000 for c in ch))
        self.assertEqual(sum(c["words"] for c in ch), total)
        self.assertTrue(ch[0]["title"].startswith(f"Part 1 of {len(ch)}"))

    def test_epub_by_length(self):
        p = os.path.join(TMP, "len.epub")
        make_epub(p)
        book = ti.EpubBook(p)
        self.assertEqual(book.methods(), ["toc", "length"])
        ch = book.chapters(method="length", max_words=50)
        self.assertEqual(len(ch), 1)
        self.assertIn("Drittes Kapitel", ch[0]["text"])

    def test_open_book(self):
        p = os.path.join(TMP, "x.epub")
        make_epub(p)
        self.assertEqual(ti.open_book(p).kind, "epub")


class Batch(unittest.TestCase):
    def test_old_items_are_audio(self):
        old = dict(lang="en", voice="k:af_heart", speed=1.0, text="Hi", name="")
        self.assertEqual(bj.kind(old), "audio")
        self.assertTrue(bj.is_valid(old))
        self.assertFalse(bj.is_valid(dict(kind="video", lang="en", voice="k:af_heart", speed=1)))
        self.assertFalse(bj.is_valid(dict(old, voice="x:nope")))

    def test_mixed_batch_and_page_audio(self):
        os.environ["LOCALTTS_EDGE_MOCK"] = edge_mock_mp3()
        lv.EDGE_PAUSE = 0
        d = tempfile.mkdtemp(dir=TMP)
        pdf = os.path.join(d, "deck.pdf")
        make_pdf(pdf, 3)
        js = os.path.join(d, "deck.script.json")
        make_script(js, ["One.", "", "Three."])
        with open(js) as f:
            data = json.load(f)
        data["pages"][2]["title"] = "Das Ende!"
        with open(js, "w") as f:
            json.dump(data, f)
        bad_js = os.path.join(d, "bad.json")
        make_script(bad_js, ["only one page"])
        out = os.path.join(d, "deck.mp4")
        # A file from an older run must disappear from the page audio folder.
        os.makedirs(lv.page_audio_dir(out))
        open(os.path.join(lv.page_audio_dir(out), "page_009_old.wav"), "w").close()
        items = [bj.audio_item("en", "e:en-US-AriaNeural", 1.0, "First audio.", name="eins"),
                 bj.video_item("en", "e:en-US-AriaNeural", 1.0, pdf, js, out, 360, 1.0),
                 bj.audio_item("en", "e:en-US-GuyNeural", 1.0, "Second audio.", source="book - ch 2"),
                 bj.video_item("en", "e:en-US-AriaNeural", 1.0, pdf, bad_js, os.path.join(d, "bad.mp4"), 360, 1.0)]
        folder = os.path.join(d, "audio")
        log = []
        synth = core.Synth()
        try:
            res, cancelled = bj.run(items, folder, synth, "balanced", ".mp3",
                                    on_progress=lambda *a: log.append(a))
        finally:
            synth.stop()
            os.environ.pop("LOCALTTS_EDGE_MOCK", None)
        self.assertFalse(cancelled)
        self.assertEqual([r[1] for r in res], [True, True, True, False])
        self.assertIn("does not match", res[3][3])
        self.assertEqual(sorted(os.listdir(folder)), sorted([res[0][2], res[2][2]]))
        self.assertTrue(res[0][2].endswith(".mp3") and res[0][2].startswith("eins"))
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(sorted(os.listdir(lv.page_audio_dir(out))), ["page_001_Page_1.wav", "page_003_Das_Ende.wav"])
        self.assertEqual(res[1][2], "deck.mp4")
        self.assertIn("deck.mp4", bj.describe(items[1]))
        self.assertTrue(bj.describe(items[2]).startswith("[book - ch 2]"))
        self.assertEqual(bj.existing_videos(items), [out])
        started = [a[0] for a in log if a[4] == "Preparing..."]
        self.assertEqual(started, [0, 1, 2, 3], "items must run in list order")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
