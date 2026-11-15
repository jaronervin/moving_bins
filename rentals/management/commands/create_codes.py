"""Create tote codes ahead of time, e.g. to send off for sticker printing.

    python manage.py create_codes 100              # printed labels, not on totes yet
    python manage.py create_codes 60 --in-service  # totes already labelled and on the shelf

Reserved codes don't count as inventory. When a label goes on a tote, scan
it at the check-in page (with Good) or use "Put in service" on its bin page.
Print or export them from /ops/labels/?status=reserved.
"""

from django.core.management.base import BaseCommand

from rentals.bins import register_bins
from rentals.models import Product


class Command(BaseCommand):
    help = "Create new tote codes (reserved for printing unless --in-service)."

    def add_arguments(self, parser):
        parser.add_argument("count", type=int)
        parser.add_argument("--product", default="bin-27gal")
        parser.add_argument("--in-service", action="store_true",
                            help="The labels are already on totes that are ready to rent.")

    def handle(self, *args, count, product, in_service, **options):
        made = register_bins(Product.objects.get(slug=product), count,
                             reserved=not in_service, note="create_codes")
        state = "in service" if in_service else "reserved for printing"
        self.stdout.write(f"Created {len(made)} codes ({state}): " + " ".join(b.code for b in made))
