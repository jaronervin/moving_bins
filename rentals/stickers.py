"""
Print-ready sticker files for a professional label printer.

* ``write_pdf``: one sticker per page, vector artwork (sharp at any size),
  2" x 2" trim plus 1/8" bleed on every side, fonts embedded.
* ``write_csv``: the same data as rows, for printers that generate the QR
  codes themselves from a spreadsheet ("variable data printing").

The QR code holds settings.QR_BASE_URL + code, e.g. HTTPS://SPROUTTOTE.COM/Q/K7Q.
Upper case lets the QR use its compact alphanumeric mode, so it stays small.
"""

import csv
from pathlib import Path

import segno
from django.conf import settings
from reportlab.lib.colors import black, white
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONTS = Path(__file__).parent / "fonts"
BRAND = "SPROUT TOTE CO."
TAGLINE = "Scan to label your tote"


def _register_fonts():
    if "StickerSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("StickerSans", str(FONTS / "LiberationSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("StickerMono", str(FONTS / "LiberationMono-Bold.ttf")))


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


def _spaced(c, text, font, size, cx, y, spacing):
    """Centered text with extra letter spacing."""
    width = pdfmetrics.stringWidth(text, font, size) + spacing * (len(text) - 1)
    t = c.beginText(cx - width / 2, y)
    t.setFont(font, size)
    t.setCharSpace(spacing)
    t.textOut(text)
    c.drawText(t)


def write_pdf(codes, path, trim_in=2.0, bleed_in=0.125, copies=1):
    """One sticker per page. Returns the number of pages written."""
    _register_fonts()
    trim, bleed = trim_in * inch, bleed_in * inch
    page = trim + 2 * bleed
    c = canvas.Canvas(str(path), pagesize=(page, page), initialFontName="StickerSans", initialFontSize=7)
    c.setTitle("Sprout Tote Co. tote labels")
    c.setAuthor("Sprout Tote Co.")
    cx = page / 2
    k = trim / (2 * inch)  # layout is designed for 2"; scale for other sizes
    pages = 0
    for code in codes:
        for _ in range(copies):
            # Trim box: where the sticker is cut. Everything stays 1/8" inside it.
            c.setTrimBox((bleed, bleed, page - bleed, page - bleed))
            c.setBleedBox((0, 0, page, page))
            c.setFillColor(white)
            c.rect(0, 0, page, page, stroke=0, fill=1)
            c.setFillColor(black)
            # Positions in points above the bottom trim edge (2" sticker = 144 pt).
            _spaced(c, BRAND, "StickerSans", 6.5 * k, cx, bleed + 130 * k, 1.1 * k)
            qr_size = 76 * k  # about 1.06": 25 modules of ~1 mm, clear space all round
            _draw_qr(c, qr_content(code), cx - qr_size / 2, bleed + 44 * k, qr_size)
            _spaced(c, code, "StickerMono", 22 * k, cx, bleed + 21 * k, 3 * k)
            _spaced(c, TAGLINE.upper(), "StickerSans", 5.5 * k, cx, bleed + 10 * k, 0.5 * k)
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
