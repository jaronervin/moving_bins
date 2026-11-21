"""
Booking operations that touch several tables at once.

These are the only places that should create bookings or change their
status, so the rules (no overbooking, review window, prices locked at
booking) live in one spot.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import inventory, pricing
from .models import Booking, BookingLine, BusinessSettings, Package, PriceLine, Product, counted


def availability_message(product, wanted):
    """What renters see when an item is past what we can promise online."""
    s = BusinessSettings.load()
    return (f"We'd need to check availability for {counted(wanted, product.name)} on those dates. "
            f"Contact us at {s.contact_email} or {s.contact_phone} and we'll see if we can have "
            f"them in time, or try fewer or other dates.")


class NotEnoughStock(Exception):
    def __init__(self, product, wanted, free):
        self.product, self.wanted, self.free = product, wanted, free
        super().__init__(f"Only {free} {product} available for those dates (asked for {wanted}).")


# ---------------------------------------------------------------------------
# What's being ordered, and its price
# ---------------------------------------------------------------------------


@dataclass
class Order:
    """A renter's choices: a package, how long, extra totes, extras."""

    package: Package
    weeks: int
    addon_totes: int = 0
    extras: dict = field(default_factory=dict)  # {Product: quantity}, rental extras and sale items

    def items(self):
        """[(product, quantity)] of everything that leaves the warehouse."""
        totals = {}

        def add(product, qty):
            if qty:
                totals[product] = totals.get(product, 0) + qty

        add(self.package.tote_product, self.package.totes + self.addon_totes)
        for item in self.package.items.select_related("product"):
            add(item.product, item.quantity)
        for product, qty in self.extras.items():
            add(product, qty)
        return sorted(totals.items(), key=lambda pq: pq[0].pk)


def delivery_fees(dropoff_miles, pickup_miles):
    """[(description, amount)] for addresses in the outer zone. Raises OutOfRange."""
    s = BusinessSettings.load()
    fees = []
    for what, miles in (("Dropoff", dropoff_miles), ("Pickup", pickup_miles)):
        if miles is None:
            continue
        amount = pricing.zone_fee(miles, s.free_radius_miles, s.max_radius_miles, s.extended_zone_fee,
                                  what=f"The {what.lower()} address")
        if amount:
            fees.append((f"{what} beyond {s.free_radius_miles} miles", amount))
    return fees


def quote(order, dropoff_miles=None, pickup_miles=None):
    s = BusinessSettings.load()
    pkg, tote = order.package, order.package.tote_product
    extras = [
        ("sale" if p.kind == Product.Kind.SALE else "rental", p.name, qty, p.price)
        for p, qty in sorted(order.extras.items(), key=lambda pq: (pq[0].kind, pq[0].sort_order, pq[0].name))
    ]
    return pricing.price_order(
        pricing.PackageTerms(pkg.name, pkg.totes, pkg.price, pkg.extra_week_price),
        order.weeks, s.min_weeks, s.max_weeks,
        addon_totes=order.addon_totes, addon_price=tote.addon_price,
        addon_extra_week_price=tote.addon_extra_week_price,
        extras=extras, delivery=delivery_fees(dropoff_miles, pickup_miles),
    )


def free_counts(order, dropoff_date, now=None):
    """[(product, wanted, free)] for every stock-checked item in the order."""
    pickup = dropoff_date + timedelta(days=7 * order.weeks)
    return [(p, qty, inventory.available(p, dropoff_date, pickup, now))
            for p, qty in order.items() if p.track_stock]


# ---------------------------------------------------------------------------
# Holding stock and locking prices
# ---------------------------------------------------------------------------


@transaction.atomic
def place_hold(booking, items, now=None, quote=None):
    """Save a booking with its items, checking stock and locking prices.

    ``booking`` must already have its customer, dates and dropoff address set.
    ``items`` is [(product, quantity), ...]. ``quote`` (a pricing.Quote) is
    stored as the booking's price lines. Raises NotEnoughStock if any
    stock-checked product can't be covered, in which case nothing is saved.

    The product rows are locked for the length of the transaction, so two
    renters submitting at the same moment are checked one after the other
    and can't both take the last totes.
    """
    now = now or timezone.now()
    booking.save()  # computes pickup_date from dropoff_date and weeks
    _check_stock(booking, items, now)
    _write_lines(booking, items, quote)
    booking.status = Booking.Status.REQUESTED
    booking.hold_expires_at = now + timedelta(hours=BusinessSettings.load().hold_hours)
    booking.save()
    return booking


def _check_stock(booking, items, now):
    """Lock the products and check each stock-tracked one is free for the
    booking's dates (not counting the booking itself). Raises NotEnoughStock."""
    product_ids = sorted(p.pk for p, _ in items)
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))
    for product, qty in items:
        if not product.track_stock:
            continue
        free = inventory.available(
            product, booking.dropoff_date, booking.pickup_date, now, exclude_booking=booking
        )
        if qty > free:
            raise NotEnoughStock(product, qty, free)


def _write_lines(booking, items, quote):
    booking.lines.all().delete()
    for product, qty in items:
        BookingLine.objects.create(booking=booking, product=product, quantity=qty)
    if quote is not None:
        booking.price_lines.all().delete()
        for i, line in enumerate(quote.lines):
            PriceLine.objects.create(booking=booking, kind=line.kind, description=line.description,
                                     quantity=line.quantity, unit_price=line.unit_price,
                                     amount=line.amount, sort=i)
        booking.renewal_week_price = quote.renewal_week_price
        booking.delivery_fee = quote.delivery_total


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


def _address(data):
    from .models import Address

    return Address.objects.create(
        line1=data["line1"], line2=data.get("line2", ""), city=data["city"],
        state=data["state"], zip_code=data["zip_code"],
        lat=data.get("lat"), lng=data.get("lng"), place_id=data.get("place_id", ""),
    )


def order_from_draft(draft):
    package = Package.objects.get(pk=draft["package_id"], active=True)
    extras = {}
    ids = [int(k) for k, v in draft.get("extras", {}).items() if int(v) > 0]
    for p in Product.objects.filter(pk__in=ids, active=True):
        extras[p] = int(draft["extras"][str(p.pk)])
    return Order(package=package, weeks=int(draft["weeks"]),
                 addon_totes=int(draft.get("addon_totes", 0)), extras=extras)


@transaction.atomic
def submit_booking(draft, now=None, signature=None):
    """Turn a finished booking-flow draft into a booking.

    ``draft`` is the dict the booking flow keeps in the session (see
    rentals/booking_flow.py). Everything is re-checked here, inside one
    transaction: distances, delivery-day space and stock. If anything fails,
    nothing is saved.
    """
    from datetime import date

    from . import windows
    from .models import Customer, TimeWindow, Visit

    now = now or timezone.now()
    order = order_from_draft(draft)
    dropoff_date = date.fromisoformat(draft["dropoff_date"])
    pickup_date = dropoff_date + timedelta(days=7 * order.weeks)
    pickup = draft.get("pickup")
    the_quote = quote(order, draft["dropoff"]["miles"], pickup["miles"] if pickup else None)

    # Each open date has one delivery window ("delivered by"). Lock them so two
    # renters can't take the last stop on a day at the same moment.
    found = {}
    for kind, day in (("dropoff", dropoff_date), ("pickup", pickup_date)):
        window = next(iter(windows.open_windows(day, now)), None)
        if window is None:
            raise WindowFull(f"We just filled up for {day:%A, %B %-d}. Please pick another date.")
        found[kind] = TimeWindow.objects.select_for_update().get(pk=window.pk)
        if windows.stops_left(found[kind], now) < 1:
            raise WindowFull(f"We just filled up for {day:%A, %B %-d}. Please pick another date.")

    contact = draft["contact"]
    customer = Customer.objects.filter(email__iexact=contact["email"]).order_by("-id").first()
    if customer is None:
        customer = Customer(email=contact["email"])
    customer.name = contact["name"]
    customer.phone = contact.get("phone", "")
    customer.save()

    delivery = draft.get("delivery", {})
    booking = Booking(
        customer=customer,
        package=order.package,
        dropoff_date=dropoff_date,
        weeks=order.weeks,
        dropoff_address=_address(draft["dropoff"]),
        pickup_address=_address(pickup) if pickup else None,
        dropoff_miles=round(draft["dropoff"]["miles"]),
        pickup_miles=round(pickup["miles"]) if pickup else None,
        presence=delivery.get("presence", Booking.Presence.PORCH),
        time_request=delivery.get("time_request", ""),
        access_notes=contact.get("access_notes", ""),
    )
    place_hold(booking, order.items(), now=now, quote=the_quote)
    if signature:  # {"typed_name", "version", "ip_address", "user_agent"}
        from .signing import record_signature

        record_signature(booking, now=now, **signature)

    Visit.objects.create(booking=booking, kind=Visit.Kind.DROPOFF, requested_window=found["dropoff"])
    Visit.objects.create(booking=booking, kind=Visit.Kind.PICKUP, requested_window=found["pickup"])

    from . import payments

    if payments.enabled():
        # Hold the totes briefly while the renter enters a card (Stripe Checkout).
        booking.status = Booking.Status.AWAITING_CARD
        booking.hold_expires_at = now + timedelta(minutes=BusinessSettings.load().card_entry_minutes)
        booking.save(update_fields=["status", "hold_expires_at", "updated_at"])
    elif BusinessSettings.load().auto_confirm:
        confirm_booking(booking, now=now)
    return booking


def card_saved(booking, now=None):
    """The renter's card is saved: the booking goes ahead (confirmed, or awaiting approval)."""
    now = now or timezone.now()
    if booking.status != Booking.Status.AWAITING_CARD:
        return booking
    if BusinessSettings.load().auto_confirm:
        return confirm_booking(booking, now=now)
    booking.status = Booking.Status.REQUESTED
    booking.hold_expires_at = now + timedelta(hours=BusinessSettings.load().hold_hours)
    booking.save(update_fields=["status", "hold_expires_at", "updated_at"])
    return booking


def confirm_booking(booking, now=None):
    """Confirm with the delivery days the renter chose.

    When this happens automatically at submit, the owners can still decline
    or ask for a change within ``review_hours``.
    """
    from .models import Visit

    now = now or timezone.now()
    for visit in booking.visits.all():
        visit.window = visit.window or visit.proposed_window or visit.requested_window
        visit.status = Visit.Status.CONFIRMED
        visit.save(update_fields=["window", "status"])
    booking.status = Booking.Status.CONFIRMED
    booking.hold_expires_at = None
    booking.review_until = now + timedelta(hours=BusinessSettings.load().review_hours)
    booking.save()
    return booking


# ---------------------------------------------------------------------------
# Owners' review window: decline, or ask for a change
# ---------------------------------------------------------------------------


class CannotDecline(Exception):
    pass


def _unload(booking, note):
    """Put any totes already scanned onto this booking's load back on the shelf,
    and cancel its dropoff/pickup visits (freeing the delivery stops)."""
    from .models import Bin, BinEvent, StockState, Visit

    for b in Bin.objects.select_for_update().filter(booking=booking, status=StockState.OUT):
        b.status, b.booking = StockState.READY, None
        b.save(update_fields=["status", "booking"])
        BinEvent.objects.create(bin=b, kind=BinEvent.Kind.UNLOADED, booking=booking, note=note)
    booking.visits.update(status=Visit.Status.CANCELLED)


def _check_review_window(booking, now):
    if booking.status != Booking.Status.CONFIRMED:
        raise CannotDecline(f"{booking.reference} is {booking.get_status_display().lower()}.")
    if booking.review_until is None or now > booking.review_until:
        raise CannotDecline(f"The {BusinessSettings.load().review_hours}-hour review window for "
                            f"{booking.reference} has passed. Contact the renter directly.")


@transaction.atomic
def decline_booking(booking, reason, now=None):
    """Owners turn down an auto-confirmed booking within the review window.

    Nothing has been charged yet (cards are charged at dropoff), so the renter
    owes nothing. Any totes already scanned onto the load go back on the shelf.
    """
    now = now or timezone.now()
    _check_review_window(booking, now)
    _unload(booking, "Booking declined")
    booking.status = Booking.Status.DECLINED
    booking.decline_reason = reason.strip()
    booking.cancelled_at = now
    booking.save()
    return booking


def request_change(booking, message, now=None):
    """Owners ask the renter to change something (e.g. a different dropoff day).

    The booking stays confirmed; the renter is emailed and sees the message
    on their booking page.
    """
    now = now or timezone.now()
    _check_review_window(booking, now)
    if not message.strip():
        raise CannotDecline("Write what you'd like the renter to change.")
    booking.change_request = message.strip()
    booking.change_requested_at = now
    booking.save(update_fields=["change_request", "change_requested_at", "updated_at"])
    return booking


# ---------------------------------------------------------------------------
# Cancelling (renter from their booking page, or owners)
# ---------------------------------------------------------------------------

CANCELLABLE_STATUSES = [Booking.Status.AWAITING_CARD, Booking.Status.REQUESTED,
                        Booking.Status.TIMES_PROPOSED, Booking.Status.CONFIRMED]


class CannotCancel(Exception):
    pass


def dropoff_starts_at(booking):
    """When the dropoff day's window opens (midnight if no window yet)."""
    from datetime import datetime, time

    visit = booking.visits.filter(kind="dropoff").select_related("window", "requested_window").first()
    window = visit and (visit.window or visit.requested_window)
    if window:
        return window.starts_at
    return timezone.make_aware(datetime.combine(booking.dropoff_date, time(0)))


def cancel_fee(booking, now=None):
    """What a renter would pay to cancel right now (0 before the cutoff, or if
    nothing was committed yet)."""
    now = now or timezone.now()
    s = BusinessSettings.load()
    if booking.status != Booking.Status.CONFIRMED:
        return pricing.ZERO
    if now < dropoff_starts_at(booking) - timedelta(hours=s.cancel_cutoff_hours):
        return pricing.ZERO
    rental = sum((l.amount for l in booking.price_lines.filter(kind__in=["package", "addon", "rental"])),
                 pricing.ZERO)
    return pricing.late_cancel_charge(rental, s.late_cancel_charge_percent)


def free_cancel_until(booking):
    return dropoff_starts_at(booking) - timedelta(hours=BusinessSettings.load().cancel_cutoff_hours)


@transaction.atomic
def cancel_booking(booking, by_owner=False, now=None):
    """Cancel before dropoff. Returns the late-cancellation Charge, if one was made.

    Renters cancelling inside the cutoff pay ``late_cancel_charge_percent`` of
    the rental (taken from the card hold if there is one). Owners never charge
    a fee here. Any card hold is released; totes and delivery stops are freed.
    """
    from . import payments

    now = now or timezone.now()
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    if booking.status not in CANCELLABLE_STATUSES:
        raise CannotCancel(f"{booking.reference} is {booking.get_status_display().lower()} and can't be "
                           "cancelled online. Please contact us.")
    fee = pricing.ZERO if by_owner else cancel_fee(booking, now)
    late_charge = None
    if payments.enabled():
        if fee and booking.stripe_payment_method_id:
            late_charge = payments.charge_late_cancel(booking)
        else:
            payments.release(booking)
    _unload(booking, "Booking cancelled")
    booking.status = Booking.Status.CANCELLED
    booking.cancelled_at = now
    booking.hold_expires_at = None
    booking.save()
    return late_charge


# ---------------------------------------------------------------------------
# Renter changes
# ---------------------------------------------------------------------------

CHANGEABLE_STATUSES = [Booking.Status.REQUESTED, Booking.Status.CONFIRMED]


class CannotChange(Exception):
    pass


def order_from_booking(booking):
    """The renter's choices behind a booking (package, weeks, extra totes, extras),
    worked out from its lines: whatever the package doesn't include is an extra."""
    package = booking.package
    if package is None:
        raise CannotChange("This booking was set up by hand. Please contact us to change it.")
    included = {package.tote_product: package.totes}
    for item in package.items.select_related("product"):
        included[item.product] = included.get(item.product, 0) + item.quantity
    addon, extras = 0, {}
    for line in booking.lines.select_related("product"):
        more = line.quantity - included.get(line.product, 0)
        if more <= 0:
            continue
        if line.product == package.tote_product:
            addon = more
        else:
            extras[line.product] = more
    return Order(package=package, weeks=booking.weeks, addon_totes=addon, extras=extras)


def changes_allowed_until(booking):
    return free_cancel_until(booking)


def _move_visit(booking, kind, day, now):
    """Point the booking's dropoff or pickup visit at the open window on ``day``."""
    from . import windows
    from .models import TimeWindow, Visit

    visit = booking.visits.filter(kind=kind).first()
    current = visit and (visit.window or visit.requested_window)
    if current and current.date == day:
        return
    window = next(iter(windows.open_windows(day, now)), None)
    if window is None:
        raise WindowFull(f"We're not delivering on {day:%A, %B %-d}, or it's full. Please pick another date.")
    window = TimeWindow.objects.select_for_update().get(pk=window.pk)
    if windows.stops_left(window, now) < 1:
        raise WindowFull(f"We just filled up for {day:%A, %B %-d}. Please pick another date.")
    if visit is None:
        visit = Visit(booking=booking, kind=kind)
    visit.requested_window = window
    if booking.status in Booking.COMMITTED_STATUSES:
        visit.window, visit.status = window, Visit.Status.CONFIRMED
    visit.save()


@transaction.atomic
def change_booking(booking, order, dropoff_date, now=None):
    """Change package, length, extras or dropoff date before the cutoff.

    Re-prices with today's prices and the booking's distances, and re-checks
    stock and delivery days, all in one transaction. Raises CannotChange,
    NotEnoughStock, WindowFull or a pricing error; nothing is saved then.
    """
    now = now or timezone.now()
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    if booking.status not in CHANGEABLE_STATUSES:
        raise CannotChange("This booking can't be changed online anymore. Please contact us.")
    if now >= changes_allowed_until(booking):
        hours = BusinessSettings.load().cancel_cutoff_hours
        raise CannotChange(f"Changes close {hours} hours before dropoff. Please contact us.")
    the_quote = quote(order, booking.dropoff_miles, booking.pickup_miles)
    booking.package = order.package
    booking.weeks = order.weeks
    booking.dropoff_date = dropoff_date
    booking.save()  # new pickup date
    _check_stock(booking, order.items(), now)
    _move_visit(booking, "dropoff", booking.dropoff_date, now)
    _move_visit(booking, "pickup", booking.pickup_date, now)
    _write_lines(booking, order.items(), the_quote)
    booking.save()
    return booking


class CannotExtend(Exception):
    pass


@transaction.atomic
def add_week(booking, now=None):
    """Keep the totes one more week (after dropoff). Charges the locked weekly
    renewal price to the saved card; nothing changes if that fails."""
    from . import payments
    from .models import Charge

    now = now or timezone.now()
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    if booking.status != Booking.Status.OUT:
        raise CannotExtend("You can add weeks once your totes are delivered. Before that, change your booking.")
    if timezone.localdate(now) >= booking.pickup_date:
        raise CannotExtend("It's pickup day, so it's too late to add a week online. Please contact us.")
    booking.renewals += 1
    booking.save()  # pickup moves a week later
    items = [(l.product, l.quantity) for l in booking.lines.select_related("product")]
    _check_stock(booking, items, now)
    _move_visit(booking, "pickup", booking.pickup_date, now)
    charge = None
    if payments.enabled() and booking.renewal_week_price:
        charge = payments.charge(booking, Charge.Kind.RENEWAL, booking.renewal_week_price,
                                 f"Extra week for {booking.reference}")
        if charge is None or charge.status != Charge.Status.CAPTURED:
            reason = (charge.failure_message if charge else "") or "the card was declined"
            transaction.set_rollback(True)
            raise CannotExtend(f"We couldn't charge your card ({reason}). Update your card and try again.")
    return booking, charge

EDITABLE_STATUSES = [Booking.Status.REQUESTED, Booking.Status.TIMES_PROPOSED, Booking.Status.CONFIRMED]


@transaction.atomic
def set_pickup_address(booking, address_data, miles):
    """Add (or change) the moving-in address before dropoff, updating delivery fees."""
    if booking.status not in EDITABLE_STATUSES:
        raise pricing.PricingError("This booking can't be changed online anymore.")
    fees = delivery_fees(booking.dropoff_miles or 0, miles)  # raises OutOfRange if too far
    booking.pickup_address = _address(address_data)
    booking.pickup_miles = round(miles)
    booking.price_lines.filter(kind=PriceLine.Kind.DELIVERY).delete()
    start = booking.price_lines.count()
    for i, (description, amount) in enumerate(fees):
        PriceLine.objects.create(booking=booking, kind=PriceLine.Kind.DELIVERY, description=description,
                                 quantity=1, unit_price=amount, amount=amount, sort=start + i)
    booking.delivery_fee = sum((a for _, a in fees), pricing.ZERO)
    booking.save()
    return booking
