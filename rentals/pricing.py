"""
Pricing rules: volume rates, delivery fees, cancellation and damage charges.

Everything here is a plain function of its inputs (no database access), so it
is easy to test and reason about. Callers fetch price points and settings from
the database and pass them in.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_cents(amount):
    """Round a Decimal to cents the way people expect (0.005 → 0.01)."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def round_miles(miles):
    """Distances are billed in whole miles, rounded to the nearest mile."""
    return int(Decimal(str(miles)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class PricingError(ValueError):
    """The request can't be priced (too few units, out of range, ...)."""


class OutOfRange(PricingError):
    pass


# ---------------------------------------------------------------------------
# Volume pricing
# ---------------------------------------------------------------------------


def unit_rate(points, quantity):
    """Per-unit weekly rate for an order of ``quantity`` units.

    ``points`` is a list of (quantity, rate) pairs. The rate is a straight line
    between neighboring points and stays flat past the last point. Ordering
    fewer than the first point's quantity is not allowed.

    Bins use [(20, 5.00), (100, 3.00)], so 60 bins → $4.00 each.

    The rate is rounded to the cent, so "35 bins × $4.63" on a receipt
    multiplies out to exactly the total shown.
    """
    if not points:
        raise PricingError("No price points set for this product.")
    points = sorted((int(q), Decimal(r)) for q, r in points)

    first_q, first_rate = points[0]
    if quantity < first_q:
        raise PricingError(f"Minimum order is {first_q}.")

    for (q0, r0), (q1, r1) in zip(points, points[1:]):
        if q0 <= quantity <= q1:
            # Linear interpolation: how far along from q0 to q1 are we?
            fraction = Decimal(quantity - q0) / Decimal(q1 - q0)
            return to_cents(r0 + (r1 - r0) * fraction)

    return to_cents(points[-1][1])  # past the last point: flat


def weekly_total(points, quantity):
    """Price for ``quantity`` units for one week, in cents."""
    return to_cents(unit_rate(points, quantity) * quantity)


# ---------------------------------------------------------------------------
# Distance rules
# ---------------------------------------------------------------------------


def check_dropoff_distance(miles, max_miles):
    """Dropoffs have a hard limit and no fee. Returns the billed miles."""
    billed = round_miles(miles)
    if billed > max_miles:
        raise OutOfRange(
            f"Dropoff address is {billed} miles away; we deliver within {max_miles} miles."
        )
    return billed


def pickup_fee(miles, free_miles, max_miles, rate_per_mile):
    """Fee for a pickup ``miles`` driving miles from base.

    Free up to ``free_miles``, then ``rate_per_mile`` for each mile beyond.
    Refused past ``max_miles``. Returns (billed_miles, fee).

    e.g. 42 miles, 30 free, $3/mile → (42, $36.00)
    """
    billed = round_miles(miles)
    if billed > max_miles:
        raise OutOfRange(
            f"Pickup address is {billed} miles away; we pick up within {max_miles} miles."
        )
    extra = max(0, billed - free_miles)
    return billed, to_cents(Decimal(extra) * Decimal(rate_per_mile))


# ---------------------------------------------------------------------------
# A full quote
# ---------------------------------------------------------------------------


@dataclass
class QuoteLine:
    product_name: str
    quantity: int
    unit_rate: Decimal  # per unit per week, in cents (e.g. 4.63)
    weekly_total: Decimal  # quantity × unit_rate


@dataclass
class Quote:
    weeks: int
    lines: list = field(default_factory=list)
    pickup_fee: Decimal = Decimal("0.00")

    @property
    def weekly_subtotal(self):
        return sum((line.weekly_total for line in self.lines), Decimal("0.00"))

    @property
    def rental_subtotal(self):
        return self.weekly_subtotal * self.weeks

    @property
    def total_before_tax(self):
        return self.rental_subtotal + self.pickup_fee


def build_quote(items, weeks, pickup_fee_amount=Decimal("0")):
    """Price an order.

    ``items`` is a list of (product_name, price_points, quantity).
    """
    if weeks < 1:
        raise PricingError("Rentals are at least one week.")
    quote = Quote(weeks=weeks, pickup_fee=to_cents(pickup_fee_amount))
    for name, points, quantity in items:
        rate = unit_rate(points, quantity)
        quote.lines.append(
            QuoteLine(
                product_name=name,
                quantity=quantity,
                unit_rate=rate,
                weekly_total=to_cents(rate * quantity),
            )
        )
    return quote


# ---------------------------------------------------------------------------
# Cancellation and damage
# ---------------------------------------------------------------------------


def late_cancel_charge(rental_subtotal, percent):
    """What a late cancellation costs: a percent of the rental, no pickup fee."""
    return to_cents(Decimal(rental_subtotal) * Decimal(percent) / Decimal(100))


def damage_charge(broken, missing, lid_issues, replacement_price, lid_replacement_price):
    """Full replacement for broken or missing bins; lid price for lid-only issues."""
    bins = (broken + missing) * Decimal(replacement_price)
    lids = lid_issues * Decimal(lid_replacement_price or 0)
    return to_cents(bins + lids)
