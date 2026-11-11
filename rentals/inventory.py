"""
Inventory: how many bins are in each state, how many are free on given dates,
and the operations that move bins between states.

All counts come from the StockMovement ledger:

    count in a state = (units moved into it) − (units moved out of it)

Bins are interchangeable, so we track counts per state, never individual bins.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import pricing
from .models import Booking, BookingLine, BusinessSettings, CheckIn, StockMovement, StockState

# States whose units will be (or already are) available to rent again.
RENTABLE_FLEET_STATES = [StockState.READY, StockState.OUT, StockState.AWAITING_CHECK_IN]


class InventoryError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def counts(product):
    """Units of ``product`` in each state, e.g. {"ready": 80, "out": 20, ...}."""
    result = {state.value: 0 for state in StockState}
    moves = StockMovement.objects.filter(product=product)
    for row in moves.exclude(to_state="").values("to_state").annotate(n=Sum("quantity")):
        result[row["to_state"]] += row["n"]
    for row in moves.exclude(from_state="").values("from_state").annotate(n=Sum("quantity")):
        result[row["from_state"]] -= row["n"]
    return result


def rentable_fleet(product):
    """Units that are rentable now or will be once back and checked in."""
    c = counts(product)
    return sum(c[s] for s in RENTABLE_FLEET_STATES)


# ---------------------------------------------------------------------------
# Availability on dates
# ---------------------------------------------------------------------------


def _active_lines(product, start, end, now, exclude_booking=None):
    """Booking lines for ``product`` that tie up units on any day in [start, end]."""
    holding = Q(booking__status__in=Booking.COMMITTED_STATUSES) | Q(
        booking__status__in=Booking.HOLD_STATUSES, booking__hold_expires_at__gt=now
    )
    qs = BookingLine.objects.filter(
        holding,
        product=product,
        booking__dropoff_date__lte=end,
        booking__pickup_date__gte=start,
    ).select_related("booking")
    if exclude_booking is not None:
        qs = qs.exclude(booking=exclude_booking)
    return qs


def booked_by_day(product, start, end, now=None, exclude_booking=None):
    """{date: units booked} for every day from ``start`` to ``end`` inclusive.

    A booking occupies its units from dropoff date through pickup date,
    both days included (bins are on a truck or at a house all of both days).
    """
    now = now or timezone.now()
    days = {start + timedelta(days=i): 0 for i in range((end - start).days + 1)}
    for line in _active_lines(product, start, end, now, exclude_booking):
        first = max(line.booking.dropoff_date, start)
        last = min(line.booking.pickup_date, end)
        d = first
        while d <= last:
            days[d] += line.quantity
            d += timedelta(days=1)
    return days


def available(product, start, end, now=None, exclude_booking=None):
    """How many units of ``product`` can still be booked for the whole range.

    = rentable fleet − the busiest day's bookings in the range.
    ``exclude_booking`` leaves one booking out, used when re-checking a change
    to that booking.
    """
    busiest = max(booked_by_day(product, start, end, now, exclude_booking).values(), default=0)
    return max(0, rentable_fleet(product) - busiest)


def ready_shortfalls(product, today=None, horizon_days=7):
    """Warn when upcoming dropoffs need more bins than are on the shelf.

    Returns a list of (date, needed_so_far, ready_now) for days where the
    confirmed dropoffs from today through that date exceed today's ready
    count. Bins returning in between still need check-in, so they're not
    counted. This is a conservative heads-up, not a booking rule.
    """
    today = today or timezone.localdate()
    ready_now = counts(product)[StockState.READY]
    lines = (
        BookingLine.objects.filter(
            product=product,
            booking__status=Booking.Status.CONFIRMED,
            booking__dropoff_date__gte=today,
            booking__dropoff_date__lte=today + timedelta(days=horizon_days),
        )
        .values("booking__dropoff_date")
        .annotate(n=Sum("quantity"))
        .order_by("booking__dropoff_date")
    )
    warnings, running = [], 0
    for row in lines:
        running += row["n"]
        if running > ready_now:
            warnings.append((row["booking__dropoff_date"], running, ready_now))
    return warnings


# ---------------------------------------------------------------------------
# Moving stock
# ---------------------------------------------------------------------------


def _move(product, quantity, from_state, to_state, reason, booking=None, user=None, note=""):
    if quantity <= 0:
        return None
    if from_state and counts(product)[from_state] < quantity:
        raise InventoryError(
            f"Only {counts(product)[from_state]} {product} are {StockState(from_state).label.lower()}; "
            f"can't move {quantity}."
        )
    return StockMovement.objects.create(
        product=product,
        quantity=quantity,
        from_state=from_state or "",
        to_state=to_state or "",
        reason=reason,
        booking=booking,
        created_by=user,
        note=note,
    )


def record_purchase(product, quantity, user=None, note=""):
    """New units arrive and go straight onto the shelf."""
    return _move(product, quantity, None, StockState.READY, StockMovement.Reason.PURCHASE, user=user, note=note)


@transaction.atomic
def record_dropoff(booking, user=None):
    """Bins leave the shelf for the renter's house."""
    for line in booking.lines.select_related("product"):
        _move(line.product, line.quantity, StockState.READY, StockState.OUT,
              StockMovement.Reason.DROPOFF, booking=booking, user=user)


@transaction.atomic
def record_pickup(booking, user=None):
    """Bins come back from the renter and wait to be cleaned and inspected."""
    for line in booking.lines.select_related("product"):
        _move(line.product, line.quantity, StockState.OUT, StockState.AWAITING_CHECK_IN,
              StockMovement.Reason.PICKUP, booking=booking, user=user)


@transaction.atomic
def record_check_in(booking, product, good, broken=0, lids_salvaged=0, lid_issues=0,
                    missing=0, user=None, notes="", now=None):
    """Inspect returned bins for one product of a booking.

    good + broken + lid_issues + missing must equal the number rented.
    Returns the CheckIn, whose ``damage_amount`` is what to charge the renter
    (zero if check-in happened after the deadline).
    """
    now = now or timezone.now()
    line = booking.lines.get(product=product)
    total = good + broken + lid_issues + missing
    if total != line.quantity:
        raise InventoryError(f"Counts add up to {total}, but {line.quantity} were rented.")
    if lids_salvaged > broken:
        raise InventoryError("Can't salvage more lids than there are broken bins.")
    if lids_salvaged and not product.spare_lid_product:
        raise InventoryError(f"No spare-lid item is set up for {product}.")

    reasons = StockMovement.Reason
    src = StockState.AWAITING_CHECK_IN
    _move(product, good, src, StockState.READY, reasons.CHECK_IN_GOOD, booking, user)
    _move(product, broken, src, StockState.RETIRED, reasons.CHECK_IN_BROKEN, booking, user)
    _move(product, lid_issues, src, StockState.OUT_OF_SERVICE, reasons.CHECK_IN_LID, booking, user)
    _move(product, missing, src, None, reasons.CHECK_IN_MISSING, booking, user)
    if lids_salvaged:
        _move(product.spare_lid_product, lids_salvaged, None, StockState.READY,
              reasons.LID_SALVAGED, booking, user)

    damage = pricing.damage_charge(
        broken, missing, lid_issues, product.replacement_price, product.lid_replacement_price
    )
    deadline_days = BusinessSettings.load().check_in_deadline_days
    picked_up_at = _picked_up_at(booking)
    late = picked_up_at is not None and now > picked_up_at + timedelta(days=deadline_days)
    if late and damage:
        notes = (notes + "\n" if notes else "") + (
            f"Checked in after the {deadline_days}-day deadline; ${damage} damage not charged."
        )
        damage = pricing.to_cents(0)

    return CheckIn.objects.create(
        booking=booking, product=product, good=good, broken=broken,
        lids_salvaged=lids_salvaged, lid_issues=lid_issues, missing=missing,
        damage_amount=damage, checked_in_at=now, checked_in_by=user, notes=notes,
    )


def _picked_up_at(booking):
    visit = booking.visits.filter(kind="pickup", completed_at__isnull=False).first()
    if visit:
        return visit.completed_at
    move = booking.movements.filter(reason=StockMovement.Reason.PICKUP).order_by("created_at").first()
    return move.created_at if move else None


@transaction.atomic
def repair_with_spare_lids(product, quantity, user=None):
    """Fit spare lids to bins that are out of service, putting them back on the shelf."""
    lid_product = product.spare_lid_product
    if lid_product is None:
        raise InventoryError(f"No spare-lid item is set up for {product}.")
    _move(lid_product, quantity, StockState.READY, None, StockMovement.Reason.LID_REPAIR, user=user)
    _move(product, quantity, StockState.OUT_OF_SERVICE, StockState.READY,
          StockMovement.Reason.LID_REPAIR, user=user)


def impact_stats():
    """Running totals for the homepage: moves served, bin-weeks, boxes avoided."""
    done = Booking.objects.filter(status__in=[Booking.Status.OUT, Booking.Status.PICKED_UP,
                                              Booking.Status.CLOSED])
    lines = BookingLine.objects.filter(booking__in=done, product__is_rentable=True)
    sets_rented = lines.aggregate(n=Sum("quantity"))["n"] or 0
    bin_weeks = sum(line.quantity * line.booking.total_weeks for line in lines.select_related("booking"))
    boxes = int(sets_rented * BusinessSettings.load().boxes_per_bin)
    retired = (
        StockMovement.objects.filter(to_state=StockState.RETIRED).aggregate(n=Sum("quantity"))["n"] or 0
    )
    return {
        "moves": done.count(),
        "bin_weeks": bin_weeks,
        "boxes_avoided": boxes,
        "bins_recycled": retired,
    }
