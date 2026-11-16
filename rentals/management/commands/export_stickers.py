"""Print-ready files for ordering tote stickers from a label printer.

    python manage.py export_stickers --out stickers/                 # all reserved codes
    python manage.py export_stickers --status ready --copies 3 --out stickers/
    python manage.py export_stickers --sample 6 --out sample/        # made-up codes, for quotes
    python manage.py export_stickers --bleed 0 --eps --out stickers/ # exactly 2", plus one .eps each

Writes stickers.pdf (one sticker per page; 1/8" bleed unless --bleed 0) and
stickers.csv (code, qr_content, quantity) into --out. With --eps, also an
eps/ folder with one file per sticker (needs pdftops from poppler-utils).
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from rentals import bins, stickers
from rentals.models import Bin


class Command(BaseCommand):
    help = "Export tote codes as a print-ready PDF and a CSV for a sticker printer."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Folder to write into.")
        parser.add_argument("--status", default="reserved", help="Which codes (default: reserved).")
        parser.add_argument("--copies", type=int, default=1, help="Stickers per code in the PDF/CSV.")
        parser.add_argument("--size", type=float, default=2.0, help="Sticker size in inches (square).")
        parser.add_argument("--bleed", type=float, default=0.125,
                            help="Extra margin outside the cut line, inches (0 = page is the sticker).")
        parser.add_argument("--eps", action="store_true", help="Also write one .eps per sticker.")
        parser.add_argument("--sample", type=int, default=0,
                            help="Make this many made-up codes instead (not saved), for quotes.")

    def handle(self, *args, out, status, copies, size, bleed, eps, sample, **options):
        folder = Path(out)
        folder.mkdir(parents=True, exist_ok=True)
        if sample:
            import secrets
            codes = []
            while len(codes) < sample:
                code = "".join(secrets.choice(bins.ALPHABET) for _ in range(bins.CODE_LENGTH))
                if code not in bins.BLOCKED_CODES and code not in codes:
                    codes.append(code)
        else:
            codes = list(Bin.objects.filter(status=status).order_by("code").values_list("code", flat=True))
        if not codes:
            self.stderr.write("No codes to export.")
            return
        pages = stickers.write_pdf(codes, folder / "stickers.pdf", trim_in=size, bleed_in=bleed,
                                   copies=copies)
        stickers.write_csv(codes, folder / "stickers.csv", copies=copies)
        self.stdout.write(f"{len(codes)} codes, {pages} stickers → {folder}/stickers.pdf and stickers.csv")
        if eps:
            files = stickers.write_eps(codes, folder / "eps", trim_in=size, copies=copies)
            self.stdout.write(f"{len(files)} EPS files → {folder}/eps/")
