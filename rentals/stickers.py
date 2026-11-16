"""
Print-ready sticker files for a professional label printer.

* ``write_pdf``: one sticker per page, vector artwork (sharp at any size),
  2" x 2" trim plus an optional 1/8" bleed. Text is drawn as outlines, so
  printers need no fonts.
* ``write_eps``: the same, one .eps file per sticker (needs ``pdftops``
  from poppler-utils).
* ``write_csv``: the same data as rows, for printers that generate the QR
  codes themselves from a spreadsheet ("variable data printing").

The QR code holds settings.QR_BASE_URL + code, e.g. HTTPS://SPROUTTOTE.COM/Q/K7Q.
Upper case lets the QR use its compact alphanumeric mode, so it stays small.
"""

import csv
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import segno
from django.conf import settings
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from reportlab.lib.colors import black, white
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen import canvas

FONTS = Path(__file__).parent / "fonts"
BRAND = "SPROUT TOTE CO."
TAGLINE = "Scan to label your tote"


SANS = FONTS / "LiberationSans-Bold.ttf"
MONO = FONTS / "LiberationMono-Bold.ttf"


@lru_cache(maxsize=None)
def _font(path):
    return TTFont(str(path))


class _OutlinePen(BasePen):
    """Draws font glyph outlines onto a reportlab path, scaled and moved into place."""

    def __init__(self, glyph_set, path, scale, dx, dy):
        super().__init__(glyph_set)
        self.p, self.s, self.dx, self.dy = path, scale, dx, dy

    def _pt(self, pt):
        return self.dx + pt[0] * self.s, self.dy + pt[1] * self.s

    def _moveTo(self, pt):
        self.p.moveTo(*self._pt(pt))

    def _lineTo(self, pt):
        self.p.lineTo(*self._pt(pt))

    def _curveToOne(self, p1, p2, p3):
        self.p.curveTo(*self._pt(p1), *self._pt(p2), *self._pt(p3))

    def _closePath(self):
        self.p.close()


def _text_width(font_path, text, size, spacing):
    font = _font(font_path)
    scale = size / font["head"].unitsPerEm
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    return sum(hmtx[cmap[ord(ch)]][0] for ch in text) * scale + spacing * (len(text) - 1)


def _spaced(c, text, font_path, size, cx, y, spacing):
    """Centered text with letter spacing, drawn as filled outlines (no font needed)."""
    font = _font(font_path)
    scale = size / font["head"].unitsPerEm
    cmap, hmtx, glyphs = font.getBestCmap(), font["hmtx"], font.getGlyphSet()
    x = cx - _text_width(font_path, text, size, spacing) / 2
    path = c.beginPath()
    for ch in text:
        name = cmap[ord(ch)]
        glyphs[name].draw(_OutlinePen(glyphs, path, scale, x, y))
        x += hmtx[name][0] * scale + spacing
    c.drawPath(path, stroke=0, fill=1, fillMode=0)  # nonzero winding, as fonts expect


def qr_content(code):
    return settings.QR_BASE_URL + code


def _draw_qr(c, data, x, y, size):
    """Draw a QR code as vector squares with its bottom-left corner at (x, y)."""
    qr = segno.make_qr(data, error="m", boost_error=True)
    rows = [list(r) for r in qr.matrix_iter(scale=1, border=0)]
    n = len(rows)
    m = size / n
    c.setFillColor(black)
    for r, row in enumerate(rows):
        col = 0
        while col < n:  # merge runs of dark modules into one rectangle
            if row[col]:
                start = col
                while col < n and row[col]:
                    col += 1
                c.rect(x + start * m, y + (n - 1 - r) * m, (col - start) * m, m, stroke=0, fill=1)
            else:
                col += 1
    return n


def write_pdf(codes, path, trim_in=2.0, bleed_in=0.125, copies=1):
    """One sticker per page. Returns the number of pages written.

    ``bleed_in=0`` makes each page exactly the sticker size.
    """
    trim, bleed = trim_in * inch, bleed_in * inch
    page = trim + 2 * bleed
    # Text is drawn as outlines. Starting from a TrueType font (never used for
    # text) keeps reportlab from adding a default Helvetica reference that
    # printers' preflight checks would flag as a missing font.
    if "StickerBlank" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(RLTTFont("StickerBlank", str(SANS)))
    c = canvas.Canvas(str(path), pagesize=(page, page), pageCompression=1,
                      initialFontName="StickerBlank")
    c.setTitle("Sprout Tote Co. tote labels")
    c.setAuthor("Sprout Tote Co.")
    cx = page / 2
    k = trim / (2 * inch)  # layout is designed for 2"; scale for other sizes
    pages = 0
    for code in codes:
        for _ in range(copies):
            # Trim box: where the sticker is cut. Everything stays 1/8" inside it.
            if bleed:
                c.setTrimBox((bleed, bleed, page - bleed, page - bleed))
                c.setBleedBox((0, 0, page, page))
            c.setFillColor(white)
            c.rect(0, 0, page, page, stroke=0, fill=1)
            c.setFillColor(black)
            # Positions in points above the bottom trim edge (2" sticker = 144 pt).
            _spaced(c, BRAND, SANS, 6.5 * k, cx, bleed + 130 * k, 1.1 * k)
            qr_size = 76 * k  # about 1.06": 25 modules of ~1 mm, clear space all round
            _draw_qr(c, qr_content(code), cx - qr_size / 2, bleed + 44 * k, qr_size)
            _spaced(c, code, MONO, 22 * k, cx, bleed + 21 * k, 3 * k)
            _spaced(c, TAGLINE.upper(), SANS, 5.5 * k, cx, bleed + 10 * k, 0.5 * k)
            c.showPage()
            pages += 1
    c.save()
    return pages


def write_csv(codes, path, copies=1):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "qr_content", "quantity"])
        for code in codes:
            w.writerow([code, qr_content(code), copies])
    return len(codes)


def write_eps(codes, folder, trim_in=2.0, copies=1):
    """One .eps per sticker, exactly ``trim_in`` square, named <code>.eps.

    Returns the list of files written. Needs ``pdftops`` (poppler-utils).
    """
    if not shutil.which("pdftops"):
        raise RuntimeError("EPS export needs pdftops (install poppler-utils).")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "all.pdf"
        write_pdf(codes, pdf, trim_in=trim_in, bleed_in=0, copies=1)
        for page, code in enumerate(codes, start=1):
            for n in range(1, copies + 1):
                name = f"{code}.eps" if copies == 1 else f"{code}-{n}.eps"
                subprocess.run(["pdftops", "-eps", "-f", str(page), "-l", str(page), str(pdf),
                                str(folder / name)], check=True)
                written.append(folder / name)
    return written
