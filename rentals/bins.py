"""
Individually tracked bins: label codes and everything that happens to a bin.

The life of a bin:

    ready ──load──► out ──pickup scan (optional)──► awaiting_check_in ──check-in──► ready
                     └──────────── or straight to check-in at the warehouse ─────┘
                     │                                         ├─broken──► retired
                     │                                         └─lid─────► out_of_service ──spare lid──► ready
                     └── not picked up when check-in is finished ──► missing ──found──► awaiting_check_in

Owners scan bins at base while loading (assigns them to the booking), may
scan them at pickup (optional), then check each in back at the warehouse. Every step writes a
BinEvent, and the bin's ``status`` is the source of truth for counts.
"""

import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import pricing
from .models import (
    Bin,
    BinContents,
    BinEvent,
    Booking,
    BusinessSettings,
    CheckIn,
    StockMovement,
    StockState,
    Visit,
)

# No 0/O, 1/I/L: easy to read off a bin and type in.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 3
# Combinations we never print on a bin.
BLOCKED_CODES = {
    "ASS", "FUK", "FUC", "FCK", "FKU", "SEX", "KKK", "FAG", "TIT", "CUM", "DIK", "DIC",
    "XXX", "WTF", "NAZ", "GAY", "PMS", "STD", "VAG", "PEE", "POO", "DMN", "HEL", "SUC",
    "SUK", "JEW", "NGR", "NGA", "RAP", "KYS", "DED", "GUN", "WAR", "SHT", "FKN",
}


class BinError(ValueError):
    """A scan or action that can't be done; the message is shown to the owner."""


def normalize_code(raw):
    """Turn whatever was scanned or typed into a bin code.

    Accepts "k7q", " K7Q ", or a full label URL like
    "HTTPS://SPROUTTOTE.COM/Q/K7Q". Returns "" if nothing usable.
    """
    text = (raw or "").strip().upper().split("?")[0].split("#")[0].rstrip("/")
    if "/Q/" in text:
        text = text.rsplit("/Q/", 1)[1]
    text = text.strip()
    return text if text.isalnum() and 1 <= len(text) <= 8 else ""


def new_code():
    """A random unused code. Codes are never reused, even for retired bins."""
    for _ in range(1000):
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if code not in BLOCKED_CODES and not Bin.objects.filter(code=code).exists():
            return code
    raise BinError("Ran out of codes; time to use 4-character codes.")


@transaction.atomic
def register_bins(product, quantity, user=None, note="", reserved=False):
    """Create ``quantity`` fresh codes.

    By default each is a tote on the shelf. With ``reserved=True`` they're
    codes printed ahead of time: not inventory until a label is put on a tote
    and scanned in (see ``activate_bin``).
    """
    created = []
    status = StockState.RESERVED if reserved else StockState.READY
    for _ in range(quantity):
        b = Bin.objects.create(product=product, code=new_code(), status=status)
        BinEvent.objects.create(bin=b, kind=BinEvent.Kind.REGISTERED, by=user, note=note)
        created.append(b)
    return created


def counts(product):
    result = {state.value: 0 for state in StockState}
    for row in Bin.objects.filter(product=product).values("status").annotate(n=Count("id")):
        result[row["status"]] = row["n"]
    return result


def _get(code, lock=True):
    code = normalize_code(code)
    if not code:
        raise BinError("That doesn't look like a tote code.")
    qs = Bin.objects.select_related("product", "booking")
    if lock:
        qs = qs.select_for_update(of=("self",))  # lock the bin row only, not its booking
    b = qs.filter(code=code).first()
    if b is None:
        raise BinError(f"No tote with code {code}.")
    return b


def _event(b, kind, booking=None, user=None, note=""):
    return BinEvent.objects.create(bin=b, kind=kind, booking=booking, by=user, note=note)


def _line_quantity(booking, product):
    line = booking.lines.filter(product=product).first()
    return line.quantity if line else 0


# ---------------------------------------------------------------------------
# Loading for dropoff (at base)
# ---------------------------------------------------------------------------


def loaded(booking, product=None):
    """Bins currently assigned to a booking and not yet picked up."""
    qs = Bin.objects.filter(booking=booking, status=StockState.OUT)
    return qs.filter(product=product) if product else qs


@transaction.atomic
def load_bin(booking, code, user=None):
    """Scan a bin onto a booking's load. Returns the bin."""
    if booking.status != Booking.Status.CONFIRMED:
        raise BinError(f"{booking.reference} is {booking.get_status_display().lower()}, not ready to load.")
    b = _get(code)
    if b.booking_id == booking.pk and b.status == StockState.OUT:
        raise BinError(f"{b.code} is already on this load.")
    if b.status != StockState.READY:
        where = f" ({b.booking.reference})" if b.booking else ""
        raise BinError(f"{b.code} isn't on the shelf: {b.get_status_display().lower()}{where}.")
    wanted = _line_quantity(booking, b.product)
    if not wanted:
        raise BinError(f"{booking.reference} didn't order {b.product}.")
    if loaded(booking, b.product).count() >= wanted:
        raise BinError(f"This load already has all {wanted} {b.product}.")
    b.status, b.booking = StockState.OUT, booking
    b.save(update_fields=["status", "booking"])
    _event(b, BinEvent.Kind.LOADED, booking, user)
    return b


@transaction.atomic
def unload_bin(booking, code, user=None):
    """Undo a loading scan (put the bin back on the shelf)."""
    if booking.status != Booking.Status.CONFIRMED:
        raise BinError("The dropoff is already done; this can't be undone here.")
    b = _get(code)
    if b.booking_id != booking.pk or b.status != StockState.OUT:
        raise BinError(f"{b.code} isn't on this load.")
    b.status, b.booking = StockState.READY, None
    b.save(update_fields=["status", "booking"])
    _event(b, BinEvent.Kind.UNLOADED, booking, user)
    return b


def load_progress(booking):
    """[(product, loaded, wanted)] for each tracked product in the booking."""
    return [
        (line.product, loaded(booking, line.product).count(), line.quantity)
        for line in booking.lines.select_related("product")
        if line.product.is_serialized
    ]


@transaction.atomic
def complete_dropoff(booking, user=None, now=None):
    """Mark the dropoff done. Every tracked unit must be scanned onto the load."""
    from . import inventory

    now = now or timezone.now()
    if booking.status != Booking.Status.CONFIRMED:
        raise BinError(f"{booking.reference} is {booking.get_status_display().lower()}.")
    if not booking.pickup_address_id:
        raise BinError("The renter hasn't given their moving-in address yet. Don't drop off without it.")
    short = [f"{n} of {want} {p}" for p, n, want in load_progress(booking) if n != want]
    if short:
        raise BinError("Load isn't complete: " + "; ".join(short) + ".")
    try:
        inventory.record_dropoff(booking, user)  # counted (non-serialized) items, if any
    except inventory.InventoryError as exc:
        raise BinError(f"{exc} If you just bought some, record the purchase first (Admin › Stock movements › "
                       "Add, kind Purchase), then mark the dropoff again.") from exc
    booking.visits.filter(kind=Visit.Kind.DROPOFF).update(status=Visit.Status.DONE, completed_at=now)
    booking.status = Booking.Status.OUT
    booking.save(update_fields=["status", "updated_at"])
    return booking


# ---------------------------------------------------------------------------
# Pickup
# ---------------------------------------------------------------------------


@transaction.atomic
def pickup_bin(booking, code, user=None):
    if booking.status != Booking.Status.OUT:
        raise BinError(f"{booking.reference} isn't out with the renter.")
    b = _get(code)
    if b.booking_id == booking.pk and b.status == StockState.AWAITING_CHECK_IN:
        raise BinError(f"{b.code} is already scanned.")
    if b.booking_id != booking.pk or b.status != StockState.OUT:
        other = f" It belongs to {b.booking.reference}." if b.booking and b.booking_id != booking.pk else ""
        raise BinError(f"{b.code} isn't part of this rental.{other}")
    b.status = StockState.AWAITING_CHECK_IN
    b.save(update_fields=["status"])
    _event(b, BinEvent.Kind.PICKED_UP, booking, user)
    return b


@transaction.atomic
def undo_pickup_bin(booking, code, user=None):
    if booking.status != Booking.Status.OUT:
        raise BinError("The pickup is already finished; this can't be undone here.")
    b = _get(code)
    if b.booking_id != booking.pk or b.status != StockState.AWAITING_CHECK_IN:
        raise BinError(f"{b.code} wasn't scanned on this pickup.")
    b.status = StockState.OUT
    b.save(update_fields=["status"])
    _event(b, BinEvent.Kind.PICKUP_UNDONE, booking, user)
    return b


def pickup_progress(booking):
    """(scanned, total) tracked bins for a booking's pickup."""
    scanned = Bin.objects.filter(booking=booking, status=StockState.AWAITING_CHECK_IN).count()
    return scanned, scanned + loaded(booking).count()


@transaction.atomic
def complete_pickup(booking, user=None, now=None):
    """Mark the pickup done. Returns codes of bins not scanned (likely missing)."""
    from . import inventory

    now = now or timezone.now()
    if booking.status != Booking.Status.OUT:
        raise BinError(f"{booking.reference} isn't out with the renter.")
    inventory.record_pickup(booking, user)
    booking.visits.filter(kind=Visit.Kind.PICKUP).update(status=Visit.Status.DONE, completed_at=now)
    booking.status = Booking.Status.PICKED_UP
    booking.save(update_fields=["status", "updated_at"])
    return list(loaded(booking).values_list("code", flat=True))


# ---------------------------------------------------------------------------
# Check-in after cleaning
# ---------------------------------------------------------------------------

OUTCOMES = {
    "good": (StockState.READY, BinEvent.Kind.CHECKED_IN_GOOD),
    "broken": (StockState.RETIRED, BinEvent.Kind.CHECKED_IN_BROKEN),
    "lid": (StockState.OUT_OF_SERVICE, BinEvent.Kind.CHECKED_IN_LID),
}


@transaction.atomic
def check_in_bin(code, outcome="good", lid_salvaged=False, user=None):
    """Inspect one returned bin. ``outcome`` is good, broken or lid."""
    from . import inventory

    if outcome not in OUTCOMES:
        raise BinError("Choose good, broken or lid.")
    b = _get(code)
    if b.status == StockState.RESERVED:
        # A new tote with a pre-printed label: scanning it in puts it in service.
        if outcome != "good":
            raise BinError(f"{b.code} is a new label; scan it with Good to put the tote in service.")
        return activate_bin(b.code, user)
    back_unscanned = (b.status == StockState.OUT and b.booking is not None
                      and b.booking.status == Booking.Status.PICKED_UP)
    if back_unscanned:
        # Picked up without scanning at the door: this scan is its return.
        _event(b, BinEvent.Kind.PICKED_UP, b.booking, user, note="Scanned at check-in")
    elif b.status != StockState.AWAITING_CHECK_IN:
        raise BinError(f"{b.code} isn't waiting for check-in ({b.get_status_display().lower()}).")
    status, kind = OUTCOMES[outcome]
    booking = b.booking
    b.status, b.booking, b.moves = status, None, b.moves + 1
    b.save(update_fields=["status", "booking", "moves"])
    _event(b, kind, booking, user)
    if outcome == "broken" and lid_salvaged and b.product.spare_lid_product:
        inventory._move(b.product.spare_lid_product, 1, None, StockState.READY,
                        StockMovement.Reason.LID_SALVAGED, booking=booking, user=user,
                        note=f"From broken bin {b.code}")
    return b


def check_in_progress(booking):
    """(checked, waiting, not_scanned) bin counts for one booking.

    ``waiting`` = scanned at pickup but not yet checked in; ``not_scanned`` =
    not scanned since dropoff (normal if you scan at the warehouse; any still
    unscanned when the rental is closed are marked missing).
    """
    waiting = Bin.objects.filter(booking=booking, status=StockState.AWAITING_CHECK_IN).count()
    not_returned = loaded(booking).count()
    checked = BinEvent.objects.filter(booking=booking, kind__in=[k for _, k in OUTCOMES.values()]).count()
    return checked, waiting, not_returned


@transaction.atomic
def finish_check_in(booking, user=None, now=None):
    """Close out a booking once its returned bins are inspected.

    Bins never scanned at pickup become missing. Returns a CheckIn summary per
    tracked product, whose ``damage_amount`` is what to charge the renter
    (zero if this happens after the check-in deadline).
    """
    from . import inventory

    now = now or timezone.now()
    if booking.status != Booking.Status.PICKED_UP:
        raise BinError(f"{booking.reference} isn't waiting for check-in.")
    waiting = Bin.objects.filter(booking=booking, status=StockState.AWAITING_CHECK_IN)
    if waiting.exists():
        codes = ", ".join(waiting.values_list("code", flat=True)[:10])
        raise BinError(f"Still to check in: {codes}.")

    for b in loaded(booking).select_for_update():
        b.status, b.booking = StockState.MISSING, None
        b.save(update_fields=["status", "booking"])
        _event(b, BinEvent.Kind.MARKED_MISSING, booking, user)

    deadline = BusinessSettings.load().check_in_deadline_days
    picked_up_at = inventory._picked_up_at(booking)
    late = picked_up_at is not None and now > picked_up_at + timedelta(days=deadline)

    summaries = []
    for line in booking.lines.select_related("product"):
        product = line.product
        if not product.is_serialized:
            continue
        events = BinEvent.objects.filter(booking=booking, bin__product=product)
        tally = {kind: events.filter(kind=kind).values("bin").distinct().count() for kind in (
            BinEvent.Kind.CHECKED_IN_GOOD, BinEvent.Kind.CHECKED_IN_BROKEN,
            BinEvent.Kind.CHECKED_IN_LID, BinEvent.Kind.MARKED_MISSING)}
        good, broken = tally[BinEvent.Kind.CHECKED_IN_GOOD], tally[BinEvent.Kind.CHECKED_IN_BROKEN]
        lid, missing = tally[BinEvent.Kind.CHECKED_IN_LID], tally[BinEvent.Kind.MARKED_MISSING]
        damage = pricing.damage_charge(broken, missing, lid, product.replacement_price,
                                       product.lid_replacement_price)
        notes = ""
        if late and damage:
            notes = f"Checked in after the {deadline}-day deadline; ${damage} damage not charged."
            damage = pricing.to_cents(0)
        missing_codes = list(events.filter(kind=BinEvent.Kind.MARKED_MISSING)
                             .values_list("bin__code", flat=True))
        if missing_codes:
            notes = (notes + "\n" if notes else "") + "Not returned: " + ", ".join(missing_codes)
        summaries.append(CheckIn.objects.create(
            booking=booking, product=product, good=good, broken=broken, lid_issues=lid,
            missing=missing, damage_amount=damage, checked_in_at=now, checked_in_by=user,
            notes=notes,
        ))

    booking.status = Booking.Status.CLOSED
    booking.save(update_fields=["status", "updated_at"])
    return summaries


# ---------------------------------------------------------------------------
# Repairs and corrections
# ---------------------------------------------------------------------------


@transaction.atomic
def repair_with_spare_lid(code, user=None):
    """Fit a spare lid to a bin that's out of service, putting it back on the shelf."""
    from . import inventory

    b = _get(code)
    if b.status != StockState.OUT_OF_SERVICE:
        raise BinError(f"{b.code} doesn't need a lid.")
    lids = b.product.spare_lid_product
    if lids is None or inventory.counts(lids)[StockState.READY] < 1:
        raise BinError("No spare lids on the shelf.")
    inventory._move(lids, 1, StockState.READY, None, StockMovement.Reason.LID_REPAIR, user=user,
                    note=f"Fitted to {b.code}")
    b.status = StockState.READY
    b.save(update_fields=["status"])
    _event(b, BinEvent.Kind.LID_REPAIRED, user=user)
    return b


@transaction.atomic
def activate_bin(code, user=None):
    """A pre-printed label went onto a new tote: it's now on the shelf."""
    b = _get(code)
    if b.status != StockState.RESERVED:
        raise BinError(f"{b.code} is already in service.")
    b.status = StockState.READY
    b.save(update_fields=["status"])
    _event(b, BinEvent.Kind.ACTIVATED, user=user)
    return b


@transaction.atomic
def found_bin(code, user=None):
    """A missing bin turned up: it goes to check-in like any returned bin."""
    b = _get(code)
    if b.status != StockState.MISSING:
        raise BinError(f"{b.code} isn't marked missing.")
    b.status = StockState.AWAITING_CHECK_IN
    b.save(update_fields=["status"])
    _event(b, BinEvent.Kind.FOUND, user=user)
    return b


# ---------------------------------------------------------------------------
# Renter contents notes
# ---------------------------------------------------------------------------


def purge_old_contents(now=None):
    """Delete renters' contents notes once the retention period after pickup ends."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=BusinessSettings.load().contents_retention_days)
    picked_up_long_ago = Q(
        booking__visits__kind=Visit.Kind.PICKUP,
        booking__visits__status=Visit.Status.DONE,
        booking__visits__completed_at__lt=cutoff,
    )
    ids = list(BinContents.objects.filter(picked_up_long_ago).values_list("id", flat=True))
    return BinContents.objects.filter(id__in=ids).delete()[0]
