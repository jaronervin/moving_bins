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


# ---------------------------------------------------------------------------
# Submitting a booking from the renter's booking flow
# ---------------------------------------------------------------------------


class WindowFull(Exception):
    pass


def check_addresses(dropoff_miles, pickup_miles):
    """Apply the service-area rules. Returns (dropoff_miles, pickup_miles, fee).

    ``pickup_miles`` may be None (address not known yet), in which case the fee
    is 0 for now. Raises pricing.OutOfRange.
    """
    s = BusinessSettings.load()
    dropoff = pricing.check_dropoff_distance(dropoff_miles, s.dropoff_max_miles)
    if pickup_miles is None:
        return dropoff, None, pricing.to_cents(0)
    pickup, fee = pricing.pickup_fee(pickup_miles, s.pickup_free_miles, s.pickup_max_miles,
                                     s.pickup_rate_per_mile)
    return dropoff, pickup, fee


def _address(data):
    from .models import Address

    return Address.objects.create(
        line1=data["line1"], line2=data.get("line2", ""), city=data["city"],
        state=data["state"], zip_code=data["zip_code"],
        lat=data.get("lat"), lng=data.get("lng"), place_id=data.get("place_id", ""),
    )


@transaction.atomic
def submit_booking(draft, now=None):
    """Turn a finished booking-flow draft into a held booking.

    ``draft`` is the dict the booking flow keeps in the session (see
    rentals/booking_flow.py). Everything is re-checked here, inside one
    transaction: distances, window space and stock. If anything fails,
    nothing is saved.
    """
    from datetime import date

    from . import windows
    from .models import Customer, TimeWindow, Visit

    now = now or timezone.now()
    product = Product.objects.get(pk=draft["product_id"])
    dropoff_date = date.fromisoformat(draft["dropoff_date"])
    weeks = int(draft["weeks"])
    pickup_date = dropoff_date + timedelta(days=7 * weeks)

    pickup = draft.get("pickup")
    dropoff_miles, pickup_miles, fee = check_addresses(
        draft["dropoff"]["miles"], pickup["miles"] if pickup else None
    )

    # Lock the two windows so two renters can't take the last stop at once.
    window_ids = sorted({draft["dropoff_window_id"], draft["pickup_window_id"]})
    locked = {w.pk: w for w in TimeWindow.objects.select_for_update().filter(pk__in=window_ids)}
    dropoff_window = locked.get(draft["dropoff_window_id"])
    pickup_window = locked.get(draft["pickup_window_id"])
    for window, day in ((dropoff_window, dropoff_date), (pickup_window, pickup_date)):
        if window is None or not window.is_open or window.date != day:
            raise WindowFull("That delivery time is no longer offered. Please pick another.")
        if windows.stops_left(window, now) < 1:
            raise WindowFull(f"{window} just filled up. Please pick another time.")

    contact = draft["contact"]
    customer = Customer.objects.filter(email__iexact=contact["email"]).order_by("-id").first()
    if customer is None:
        customer = Customer(email=contact["email"])
    customer.name = contact["name"]
    customer.phone = contact.get("phone", "")
    customer.save()

    booking = Booking(
        customer=customer,
        dropoff_date=dropoff_date,
        weeks=weeks,
        dropoff_address=_address(draft["dropoff"]),
        pickup_address=_address(pickup) if pickup else None,
        dropoff_miles=dropoff_miles,
        pickup_miles=pickup_miles,
        pickup_fee=fee,
        access_notes=contact.get("access_notes", ""),
    )
    place_hold(booking, [(product, int(draft["quantity"]))], now=now)

    Visit.objects.create(booking=booking, kind=Visit.Kind.DROPOFF, requested_window=dropoff_window)
    Visit.objects.create(booking=booking, kind=Visit.Kind.PICKUP, requested_window=pickup_window)
    return booking


EDITABLE_STATUSES = [Booking.Status.REQUESTED, Booking.Status.TIMES_PROPOSED, Booking.Status.CONFIRMED]


@transaction.atomic
def set_pickup_address(booking, address_data, miles):
    """Add (or change) the moving-in address before dropoff."""
    if booking.status not in EDITABLE_STATUSES:
        raise pricing.PricingError("This booking can't be changed online anymore.")
    _, pickup_miles, fee = check_addresses(booking.dropoff_miles or 0, miles)
    booking.pickup_address = _address(address_data)
    booking.pickup_miles = pickup_miles
    booking.pickup_fee = fee
    booking.save()
    return booking
