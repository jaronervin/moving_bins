"""
Booking operations that touch several tables at once.

These are the only places that should create holds or change a booking's
status, so the rules (no overbooking, hold expiry) live in one spot.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import inventory, pricing
from .models import Booking, BookingLine, BusinessSettings, Product


class NotEnoughStock(Exception):
    def __init__(self, product, wanted, free):
        self.product, self.wanted, self.free = product, wanted, free
        super().__init__(f"Only {free} {product} available for those dates (asked for {wanted}).")


def price_points(product):
    return [(p.quantity, p.unit_rate) for p in product.price_points.all()]


def quote(items, weeks, pickup_miles=None):
    """Price ``items`` [(product, quantity), ...] for ``weeks`` weeks.

    If ``pickup_miles`` is given, the pickup fee is included (and an
    OutOfRange error is raised if it's too far).
    """
    s = BusinessSettings.load()
    fee = 0
    if pickup_miles is not None:
        _, fee = pricing.pickup_fee(
            pickup_miles, s.pickup_free_miles, s.pickup_max_miles, s.pickup_rate_per_mile
        )
    return pricing.build_quote(
        [(p.name, price_points(p), qty) for p, qty in items], weeks, fee
    )


@transaction.atomic
def place_hold(booking, items, now=None):
    """Submit a booking request: check stock, lock in prices, hold the bins.

    ``booking`` must already have its customer, dates and dropoff address set.
    ``items`` is [(product, quantity), ...]. Raises NotEnoughStock if any
    product can't be covered, in which case nothing is saved.

    The product rows are locked for the length of the transaction, so two
    renters submitting at the same moment are checked one after the other
    and can't both take the last bins.
    """
    now = now or timezone.now()
    product_ids = sorted(p.pk for p, _ in items)
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))

    booking.save()  # computes pickup_date from dropoff_date and weeks
    for product, qty in items:
        free = inventory.available(
            product, booking.dropoff_date, booking.pickup_date, now, exclude_booking=booking
        )
        if qty > free:
            raise NotEnoughStock(product, qty, free)

    booking.lines.all().delete()
    for product, qty in items:
        rate = pricing.unit_rate(price_points(product), qty)
        BookingLine.objects.create(
            booking=booking,
            product=product,
            quantity=qty,
            locked_unit_rate=rate,
            locked_weekly_total=pricing.to_cents(rate * qty),
        )

    booking.status = Booking.Status.REQUESTED
    booking.hold_expires_at = now + timedelta(hours=BusinessSettings.load().hold_hours)
    booking.save()
    return booking


def expire_holds(now=None):
    """Mark requests nobody answered in time as expired. Returns how many."""
    now = now or timezone.now()
    return Booking.objects.filter(
        status__in=Booking.HOLD_STATUSES, hold_expires_at__lte=now
    ).update(status=Booking.Status.EXPIRED, updated_at=now)
