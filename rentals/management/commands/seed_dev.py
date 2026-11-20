"""Fill a local database with something to look at. Never run in production.

    python manage.py seed_dev
"""

from datetime import time

from django.core.management.base import BaseCommand

from rentals import bins, inventory
from rentals.models import Product, WindowTemplate
from rentals.windows import generate_windows


class Command(BaseCommand):
    help = "Launch-like local data: 60 totes in service, 90 reserved codes, a delivery window every day."

    def handle(self, *args, **options):
        totes = Product.objects.get(slug="bin-27gal")
        if not totes.bins.exists():
            bins.register_bins(totes, 60, note="dev seed: in service")
            bins.register_bins(totes, 90, reserved=True, note="dev seed: printed, not on a tote")
        # General rule: every day, totes ready for pickup by 9 AM, deliveries done by 1 PM.
        for weekday in range(7):
            WindowTemplate.objects.get_or_create(
                weekday=weekday, start=time(9), defaults={"end": time(13), "max_stops": 4}
            )
        created = generate_windows()
        self.stdout.write(f"Totes: {inventory.counts(totes)}; created {created} window(s).")
