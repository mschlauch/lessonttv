"""Text import for LocalTTS: PDF (reflowed into paragraphs) and EPUB (split into chapters).

No GUI code lives here.

Why reflow: text taken out of a PDF has a hard line break at the end of every printed line.
LocalTTS treats each line break as the end of a paragraph, so the voice would pause after
every line. reflow() joins the lines of a paragraph, repairs words hyphenated at the line end,
and drops page numbers and repeated headers/footers.
"""
import os
import re
import posixpath
import statistics
import zipfile
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

# --------------------------------------------------------------------------- reflow

_LIGATURES = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
                            "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"})
_PAGE_NO = re.compile(r"^\W{0,3}((page|seite|p\.|s\.)\s*)?\d{1,4}(\s*(/|of|von)\s*\d{1,4})?\W{0,3}$", re.I)
_BULLET = re.compile(r"^([•▪◦·●■□\-–—*]\s|\(?\d{1,3}[.)]\s|\(?[a-zA-Z][.)]\s)")
_END = ".!?:;…\"'»«“”)]"


def _norm_line(s):
    return re.sub(r"\d+", "#", s.lower()).strip()


def _drop_running_lines(pages):
    """Remove headers/footers: lines repeated near the top or bottom of most pages."""
    if len(pages) < 4:
        return pages
    counts = {}
    for lines in pages:
        edge = {_norm_line(l) for l in lines[:2] + lines[-2:] if l and len(l) <= 80}
        for k in edge:
            counts[k] = counts.get(k, 0) + 1
    running = {k for k, c in counts.items() if c >= max(3, len(pages) * 0.5)}
    out = []
    for lines in pages:
        keep = list(lines)
        for idx in list(range(min(2, len(keep)))) + list(range(max(0, len(keep) - 2), len(keep))):
            if keep[idx] and _norm_line(keep[idx]) in running:
                keep[idx] = ""
        out.append(keep)
    return out


def clean_pages(pages):
    """Per page: list of stripped lines, with ligatures fixed, line-end hyphens marked,
    and (for more than one page) running headers/footers and page numbers removed."""
    norm = []
    for p in pages:
        p = (p or "").translate(_LIGATURES).replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
        # pdfium marks a hyphen at the end of a line with U+FFFE (and joins the lines itself);
        # some PDFs use a soft hyphen or U+0002 instead.
        p = re.sub(r"[\ufffe\x02](?=[^\W\d_])(?=[^A-Z\u00c0-\u00de])", "", p)
        p = p.replace("\ufffe", "-").replace("\u00ad\n", "\x02\n").replace("\u00ad", "")
        norm.append([l.strip() for l in p.split("\n")])
    if len(norm) > 1:
        norm = _drop_running_lines(norm)
        norm = [[l for l in page if not _PAGE_NO.match(l)] for page in norm]
    return norm


def reflow(text_or_pages):
    """Turn hard-wrapped text (a string, or one string per PDF page) into paragraphs.

    Returns paragraphs separated by a blank line; there are no line breaks inside a paragraph."""
    pages = [text_or_pages] if isinstance(text_or_pages, str) else list(text_or_pages)
    return join_lines([l for page in clean_pages(pages) for l in page])


def join_lines(lines):
    """Join already cleaned lines into paragraphs (see reflow)."""
    lengths = [len(l) for l in lines if len(l) > 20]
    typical = statistics.median(lengths) if lengths else 60

    paras, cur = [], ""
    for line in lines:
        if not line:                            # blank line = paragraph break
            if cur:
                paras.append(cur)
                cur = ""
            continue
        if cur and _BULLET.match(line):
            paras.append(cur)
            cur = ""
        if not cur:
            cur = line
        elif cur.endswith(("\x02", "-")) and len(cur) > 1 and cur[-2].isalpha() and line[:1].islower():
            cur = cur[:-1] + line               # "Zei-" + "le" -> "Zeile"
        elif cur.endswith("\x02"):
            cur = cur[:-1] + line
        elif cur.endswith("-") and len(cur) > 1 and cur[-2].isalpha() and line[:1].isupper():
            cur = cur + line                    # "Nord-" + "Süd" -> "Nord-Süd"
        else:
            cur = cur + " " + line
        if line[-1:] in _END and len(line) < 0.8 * typical:   # short line ending a sentence
            paras.append(cur)
            cur = ""
    if cur:
        paras.append(cur)
    paras = [re.sub(r"\s{2,}", " ", p.replace("\x02", "")).strip() for p in paras]
    return "\n\n".join(p for p in paras if p)


def pdf_text(path, first_page=None, last_page=None):
    """Text of a PDF, reflowed into paragraphs. Pages are 1-based and inclusive."""
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(path)
    except Exception as e:
        raise ValueError(f"Could not open the PDF: {e}")
    try:
        n = len(doc)
        a, b = max(1, first_page or 1), min(n, last_page or n)
        pages = []
        for i in range(a - 1, b):
            tp = doc[i].get_textpage()
            try:
                pages.append(tp.get_text_range())
            finally:
                tp.close()
    finally:
        doc.close()
    text = reflow(pages)
    if not text.strip():
        raise ValueError("This PDF contains no text (it may be scanned images). "
                         "Run it through OCR first, then open it again.")
    return text


# --------------------------------------------------------------------------- EPUB

_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "section", "article",
          "blockquote", "tr", "pre", "dt", "dd", "figcaption", "hr", "header", "footer",
          "aside", "table", "ul", "ol", "dl", "caption", "td", "th"}
_SKIP = {"head", "script", "style", "svg", "math", "rt", "rp", "template", "noscript"}
_VOID = {"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "embed", "source", "wbr"}


class _Blocks(HTMLParser):
    """XHTML -> list of ("block", text) and ("mark", id) events."""

    def __init__(self, wanted_ids):
        super().__init__(convert_charrefs=True)
        self.wanted = wanted_ids
        self.events, self.buf, self.skip = [], [], []

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if t:
            self.events.append(("block", t))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        el_id = a.get("id")
        if el_id and el_id in self.wanted:
            self._flush()
            self.events.append(("mark", el_id))
        kind = (a.get("epub:type") or "") + " " + (a.get("role") or "")
        if self.skip:
            if tag not in _VOID:
                self.skip.append(tag)
            return
        if tag in _SKIP or "noteref" in kind or "pagebreak" in kind:
            if tag not in _VOID:
                self.skip.append(tag)
            return
        if tag in _BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in self.skip:
                while self.skip and self.skip.pop() != tag:
                    pass
            return
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()
        return self.events


class _NavParser(HTMLParser):
    """EPUB 3 nav document: entries of the <nav epub:type="toc"> list with their depth."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_toc, self.depth, self.ol = 0, 0, 0
        self.points, self.href, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav" and "toc" in (a.get("epub:type") or "").split():
            self.in_toc = 1
        elif self.in_toc and tag == "nav":
            self.in_toc += 1
        elif self.in_toc and tag == "ol":
            self.ol += 1
        elif self.in_toc and tag == "a":
            self.href, self.text = a.get("href"), []

    def handle_endtag(self, tag):
        if not self.in_toc:
            return
        if tag == "nav":
            self.in_toc -= 1
        elif tag == "ol":
            self.ol -= 1
        elif tag == "a" and self.href is not None:
            self.points.append((max(1, self.ol), re.sub(r"\s+", " ", "".join(self.text)).strip(), self.href))
            self.href = None

    def handle_data(self, data):
        if self.in_toc and self.href is not None:
            self.text.append(data)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _join(base_file, href):
    href = urllib.parse.unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), href)) if href else base_file


class EpubBook:
    """An EPUB, read once. Use chapters(depth) to get one entry per chapter."""

    def __init__(self, path):
        self.path = path
        try:
            z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as e:
            raise ValueError(f"Not a readable EPUB file: {e}")
        with z:
            self._read(z)

    def _xml(self, z, name):
        return ET.fromstring(z.read(name))

    def _read(self, z):
        names = set(z.namelist())
        try:
            root = self._xml(z, "META-INF/container.xml")
            opf = next(e.get("full-path") for e in root.iter() if _local(e.tag) == "rootfile")
            pkg = self._xml(z, opf)
        except (KeyError, StopIteration, ET.ParseError) as e:
            raise ValueError(f"This EPUB has no readable package file ({e}).")
        meta = {}
        for e in pkg.iter():
            t = _local(e.tag)
            if t in ("title", "language") and t not in meta and (e.text or "").strip():
                meta[t] = e.text.strip()
        self.title = meta.get("title") or os.path.splitext(os.path.basename(self.path))[0]
        self.language = meta.get("language", "")
        manifest, nav_file, spine, ncx_id = {}, None, [], None
        for e in pkg.iter():
            t = _local(e.tag)
            if t == "item":
                href = _join(opf, e.get("href", ""))
                manifest[e.get("id")] = (href, e.get("media-type", ""))
                if "nav" in (e.get("properties") or "").split():
                    nav_file = href
            elif t == "spine":
                ncx_id = e.get("toc")
            elif t == "itemref" and e.get("linear", "yes") != "no":
                spine.append(e.get("idref"))
        self.spine = [manifest[i][0] for i in spine if i in manifest and manifest[i][0] in names
                      and "html" in manifest[i][1]]
        if not self.spine:
            raise ValueError("This EPUB has no readable chapters.")

        # Table of contents: EPUB 3 nav first, then EPUB 2 NCX.
        points = []
        if nav_file and nav_file in names:
            np_ = _NavParser()
            np_.feed(z.read(nav_file).decode("utf-8", "replace"))
            points = [(lvl, title, _join(nav_file, h), h.split("#", 1)[1] if "#" in h else None)
                      for lvl, title, h in np_.points if h]
        if not points:
            ncx = manifest.get(ncx_id, (None,))[0] or next(
                (h for h, mt in manifest.values() if mt == "application/x-dtbncx+xml"), None)
            if ncx and ncx in names:
                def walk(el, lvl):
                    for np_el in el:
                        if _local(np_el.tag) != "navPoint":
                            continue
                        lab = next((c for c in np_el if _local(c.tag) == "navLabel"), None)
                        label = " ".join(t.text.strip() for t in (lab.iter() if lab is not None else [])
                                         if _local(t.tag) == "text" and t.text)
                        src = next((c.get("src") for c in np_el if _local(c.tag) == "content"), None)
                        if src:
                            points.append((lvl, label, _join(ncx, src), src.split("#", 1)[1] if "#" in src else None))
                        walk(np_el, lvl + 1)
                nav_map = next((e for e in self._xml(z, ncx).iter() if _local(e.tag) == "navMap"), None)
                if nav_map is not None:
                    walk(nav_map, 1)
        spine_set = set(self.spine)
        self.points = [p for p in points if p[2] in spine_set]

        # Text of every spine document, with markers where TOC entries start.
        wanted = {}
        for _, _, f, frag in self.points:
            if frag:
                wanted.setdefault(f, set()).add(frag)
        self.stream = []                     # ("file", name) / ("mark", (file, id)) / ("block", text)
        for f in self.spine:
            parser = _Blocks(wanted.get(f, set()))
            parser.feed(z.read(f).decode("utf-8", "replace"))
            self.stream.append(("file", f))
            for kind, val in parser.close():
                self.stream.append(("mark", (f, val)) if kind == "mark" else (kind, val))

    def max_depth(self):
        return max((p[0] for p in self.points), default=1)

    def default_depth(self):
        """Level 1 of the table of contents, or level 2 if level 1 has only a few entries (parts)."""
        top = sum(1 for p in self.points if p[0] == 1)
        return 2 if top < 3 and self.max_depth() >= 2 else 1

    kind = "epub"

    def methods(self):
        """Ways to split this book, best first ("length" is always possible)."""
        return (["toc"] if self.points else ["files"]) + ["length"]

    def chapters(self, depth=None, method=None, max_words=3000):
        """List of dicts: title, level, text, words. Text before the first TOC entry is
        returned as a chapter with level 0 (cover, title page, ...)."""
        if method == "length":
            text = "\n\n".join(c["text"] for c in self._toc_chapters(None) if c["text"])
            return split_by_length(text, max_words)
        return self._toc_chapters(depth)

    def _toc_chapters(self, depth):
        depth = depth or self.default_depth()
        starts = {}
        for lvl, title, f, frag in self.points:
            if lvl <= depth:
                starts.setdefault((f, frag), (lvl, title or "Untitled"))
        if not starts:                       # no usable TOC: one chapter per document
            starts = {(f, None): (1, None) for f in self.spine}
        out, cur = [], dict(title="(Before the first chapter)", level=0, blocks=[])

        def begin(key):
            nonlocal cur
            out.append(cur)
            lvl, title = starts.pop(key)
            cur = dict(title=title, level=lvl, blocks=[])

        for kind, val in self.stream:
            if kind == "file" and (val, None) in starts:
                begin((val, None))
            elif kind == "mark" and val in starts:
                begin(val)
            elif kind == "block":
                cur["blocks"].append(val)
        out.append(cur)
        result = []
        for c in out:
            text = "\n\n".join(c["blocks"])
            if c["level"] == 0 and not text.strip():
                continue
            title = c["title"] or (c["blocks"][0][:60] if c["blocks"] else "Untitled")
            result.append(dict(title=title, level=c["level"], text=text, words=len(text.split())))
        return result


# --------------------------------------------------------------------------- splitting by length

WORDS_PER_MINUTE = 150          # for "about N min" and the maximum part length


def split_by_length(text, max_words, title_prefix="Part"):
    """Cut text into parts of at most max_words words, at paragraph (or sentence) boundaries."""
    max_words = max(50, int(max_words))
    pieces = []
    for para in text.split("\n\n"):
        if len(para.split()) <= max_words:
            pieces.append(para)
            continue
        cur = ""
        sentences = []
        for sent in re.split(r"(?<=[.!?…])\s+", para):       # very long paragraph: by sentence
            w = sent.split()
            sentences += [" ".join(w[i:i + max_words]) for i in range(0, len(w), max_words)] or [sent]
        for sent in sentences:
            if cur and len((cur + " " + sent).split()) > max_words:
                pieces.append(cur)
                cur = sent
            else:
                cur = (cur + " " + sent).strip()
        if cur:
            pieces.append(cur)
    parts, cur, n = [], [], 0
    for p in pieces:
        w = len(p.split())
        if cur and n + w > max_words:
            parts.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        parts.append(cur)
    out = []
    for k, ps in enumerate(parts, 1):
        t = "\n\n".join(ps)
        first = " ".join(t.split()[:6])
        out.append(dict(title=f"{title_prefix} {k} of {len(parts)}: {first}...", level=1, text=t,
                        words=len(t.split())))
    return out


METHOD_NAMES = {"toc": "table of contents", "files": "one per file in the book",
                "outline": "PDF bookmarks", "headings": "headings found in the text",
                "length": "by length (no chapters found)"}


# --------------------------------------------------------------------------- PDF books

_KEYWORDS = (r"kapitel|chapter|teil|part|buch|book|abschnitt|section|cap[ií]tulo|parte|luku|osa|"
             r"skyrius|dalis|kapitlet|del|lektion|lesson|unit|einheit")
_STANDALONE = (r"prolog|prologue|epilog|epilogue|vorwort|nachwort|einleitung|einführung|schluss|"
               r"preface|foreword|introduction|afterword|conclusion|prólogo|epílogo|introducción|"
               r"introdução|prefácio|conclusão|förord|inledning|efterord|johdanto|esipuhe|įžanga|pratarmė")
_NUM_WORDS = (r"eins|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|one|two|three|four|five|"
              r"six|seven|eight|nine|ten|eleven|twelve|erstes|zweites|drittes|first|second|third|uno|dos|tres|um")
_KEY_RE = re.compile(rf"^(?:{_KEYWORDS})\s+(?:\d{{1,3}}|[IVXLCDM]{{1,7}}|{_NUM_WORDS})\b\s*[:.\-–—]?\s*(.{{0,70}})$", re.I)
_STANDALONE_RE = re.compile(rf"^(?:{_STANDALONE})\s*[:.]?$", re.I)
_NUMBERED_RE = re.compile(r"^(?:\d{1,2}|[IVXLC]{1,6})\.?\s+(?=[A-ZÄÖÜÀ-Þ])[^.!?;]{2,60}$")


def _norm_title(s):
    return re.sub(r"[^\w]+", "", s.lower())


class PdfBook:
    """A text PDF, split into chapters by bookmarks, by headings, or by length."""

    kind = "pdf"

    def __init__(self, path):
        import pypdfium2 as pdfium
        self.path = path
        try:
            doc = pdfium.PdfDocument(path)
        except Exception as e:
            raise ValueError(f"Could not open the PDF: {e}")
        try:
            raw = []
            for i in range(len(doc)):
                tp = doc[i].get_textpage()
                try:
                    raw.append(tp.get_text_range())
                finally:
                    tp.close()
            self.outline = self._read_outline(doc)
            meta = {}
            try:
                meta = doc.get_metadata_dict() or {}
            except Exception:
                pass
        finally:
            doc.close()
        self.pages = clean_pages(raw)
        if not any(l for page in self.pages for l in page):
            raise ValueError("This PDF contains no text (it may be scanned images). "
                             "Run it through OCR first, then open it again.")
        self.title = (meta.get("Title") or "").strip() or os.path.splitext(os.path.basename(path))[0]
        self.language = ""                       # PDFs rarely say; the user picks it
        self.headings = self._find_headings()

    @staticmethod
    def _read_outline(doc):
        import pypdfium2 as pdfium
        import pypdfium2.raw as praw
        out = []
        try:
            for bm in doc.get_toc():
                title = (bm.get_title() or "").strip()
                dest = bm.get_dest()
                if dest is None:                 # bookmark with a "go to" action instead
                    act = praw.FPDFBookmark_GetAction(bm)
                    rd = praw.FPDFAction_GetDest(doc, act) if act else None
                    dest = pdfium.PdfDest(rd, pdf=doc) if rd else None
                page = dest.get_index() if dest is not None else None
                if title and page is not None and page >= 0:
                    out.append((getattr(bm, "level", 0) + 1, title, page))
        except Exception:
            return []
        return out

    def _locate(self, page, title):
        """Line index on `page` where a heading called `title` starts (0 if not found)."""
        want = _norm_title(title)[:30]
        if not want:
            return 0
        for i, line in enumerate(self.pages[page]):
            got = _norm_title(line)
            if got and (got.startswith(want) or (len(got) >= 3 and want.startswith(got))):
                return i
        return 0

    def _find_headings(self):
        """(page, line, title) of lines that look like chapter headings."""
        keyed, numbered = [], []
        for pi, lines in enumerate(self.pages):
            for li, line in enumerate(lines):
                if len(line) > 80:
                    continue
                if _KEY_RE.match(line) or _STANDALONE_RE.match(line):
                    nxt = lines[li + 1] if li + 1 < len(lines) else ""
                    title = line
                    if _KEY_RE.match(line) and not _KEY_RE.match(line).group(1) and 0 < len(nxt) <= 60 \
                            and nxt[-1:] not in ".,;":
                        title = f"{line} - {nxt}"          # "Kapitel 3" + title on the next line
                    keyed.append((pi, li, title))
                elif li < 3 and _NUMBERED_RE.match(line):
                    numbered.append((pi, li, line))
        found = keyed if len(keyed) >= 2 else numbered if len(numbered) >= 2 else []
        if found:                                          # headings must not be list items
            words = sum(len(l.split()) for page in self.pages for l in page)
            if words / (len(found) + 1) < 150:
                return []
        return found

    def methods(self):
        m = []
        if len(self.outline) >= 2:
            m.append("outline")
        if self.headings:
            m.append("headings")
        return m + ["length"]

    def max_depth(self):
        return max((lvl for lvl, _, _ in self.outline), default=1) if "outline" in self.methods() else 1

    def default_depth(self):
        top = sum(1 for lvl, _, _ in self.outline if lvl == 1)
        return 2 if top < 3 and self.max_depth() >= 2 else 1

    def chapters(self, depth=None, method=None, max_words=3000):
        method = method or self.methods()[0]
        if method == "length":
            return split_by_length(join_lines([l for page in self.pages for l in page]), max_words)
        if method == "outline":
            depth = depth or self.default_depth()
            marks = [(pg, self._locate(pg, t), t, lvl) for lvl, t, pg in self.outline if lvl <= depth]
        else:
            marks = [(pg, li, t, 1) for pg, li, t in self.headings]
        marks = sorted({(pg, li): (pg, li, t, lvl) for pg, li, t, lvl in reversed(marks)}.values())
        bounds = [(0, 0, "(Before the first chapter)", 0)] + marks + [(len(self.pages), 0, None, None)]
        out = []
        for (p0, l0, title, lvl), (p1, l1, _, _) in zip(bounds, bounds[1:]):
            lines = []
            for pg in range(p0, min(p1, len(self.pages) - 1) + 1):
                start = l0 if pg == p0 else 0
                end = l1 if pg == p1 else len(self.pages[pg])
                lines += self.pages[pg][start:end]
            text = join_lines(lines)
            if lvl == 0 and not text.strip():
                continue
            out.append(dict(title=title, level=lvl, text=text, words=len(text.split()), page=p0 + 1))
        return out


def open_book(path):
    """EpubBook or PdfBook, by file extension."""
    return PdfBook(path) if path.lower().endswith(".pdf") else EpubBook(path)
