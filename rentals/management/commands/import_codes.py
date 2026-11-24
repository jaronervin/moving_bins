"""Load tote codes from a file (one per line; # comments), e.g. a printed batch.

    python manage.py import_codes rentals/codes/batch-1.txt

New codes are added as reserved (printed, not on a tote yet); codes already
in the database are left as they are, so it's safe to run on every deploy.
When a label goes on a tote, put it in service (check-in page or bin page).
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from rentals.bins import ALPHABET, BLOCKED_CODES
from rentals.models import Bin, BinEvent, Product, StockState


def read_codes(path):
    codes = []
    for line in Path(path).read_text().splitlines():
        code = line.split("#", 1)[0].strip().upper()
        if not code:
            continue
        if any(ch not in ALPHABET for ch in code) or code in BLOCKED_CODES:
            raise CommandError(f"Not a valid tote code: {code!r}")
        codes.append(code)
    if len(set(codes)) != len(codes):
        raise CommandError("The file has duplicate codes.")
    return codes


class Command(BaseCommand):
    help = "Add tote codes from a file as reserved (skips codes that already exist)."

    def add_arguments(self, parser):
        parser.add_argument("files", nargs="+")
        parser.add_argument("--product", default="bin-27gal")

    @transaction.atomic
    def handle(self, *args, files, product, **options):
        product = Product.objects.get(slug=product)
        for path in files:
            codes = read_codes(path)
            existing = set(Bin.objects.filter(code__in=codes).values_list("code", flat=True))
            added = 0
            for code in codes:
                if code in existing:
                    continue
                b = Bin.objects.create(product=product, code=code, status=StockState.RESERVED)
                BinEvent.objects.create(bin=b, kind=BinEvent.Kind.REGISTERED, note=f"Imported from {Path(path).name}")
                added += 1
            self.stdout.write(f"{Path(path).name}: {added} new code(s), {len(existing)} already there.")
