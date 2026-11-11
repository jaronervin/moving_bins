"""Fill a local database with something to look at. Never run in production.

    python manage.py seed_dev
"""

from datetime import time

from django.core.management.base import BaseCommand

from rentals import inventory
from rentals.models import Product, StockMovement, WindowTemplate
from rentals.windows import generate_windows


class Command(BaseCommand):
    help = "Add 100 bin sets to the shelf and a weekend window pattern (local dev only)."

    def handle(self, *args, **options):
        bins = Product.objects.get(slug="bin-27gal")
        if not StockMovement.objects.filter(product=bins).exists():
            inventory.record_purchase(bins, 100, note="Initial stock (dev seed)")
        for weekday in (5, 6):  # Saturday, Sunday
            for start, end in ((time(8), time(10)), (time(10), time(12)), (time(13), time(15))):
                WindowTemplate.objects.get_or_create(
                    weekday=weekday, start=start, defaults={"end": end, "max_stops": 3}
                )
        created = generate_windows()
        self.stdout.write(f"Stock: {inventory.counts(bins)}; created {created} window(s).")
