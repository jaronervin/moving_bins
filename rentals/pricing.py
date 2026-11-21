"""
Pricing rules: packages, extra totes, extras, sale items, delivery zone,
cancellation and damage charges.

Everything here is a plain function of its inputs (no database access), so
it's easy to test and reason about. Callers fetch packages, products and
settings from the database and pass the numbers in.

How a rental is priced (prices as of launch, all editable in admin):

* A **package** covers the minimum rental (2 weeks): e.g. 2BR, 40 totes,
  $169. Each week beyond that adds the package's extra-week price ($65).
* **Extra totes** cost ``addon_price`` each for the minimum rental ($5),
  plus ``addon_extra_week_price`` per tote per extra week ($3).
* **Rental extras** (dolly, blankets…) are a one-time fee per rental.
* **Sale items** (mattress bags, packing kits…) are a price each.
* **Delivery** is free within the free radius; each address in the outer
  zone adds a flat fee; beyond the maximum radius we don't serve.
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_cents(amount):
    """Round to cents the way people expect (0.005 → 0.01)."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


class PricingError(ValueError):
    """The order can't be priced (too short, too far, …). The message is shown to renters."""


class OutOfRange(PricingError):
    pass


# ---------------------------------------------------------------------------
# Distance and delivery zone
# ---------------------------------------------------------------------------


def straight_line_miles(lat1, lng1, lat2, lng2):
    """Great-circle ("as the crow flies") distance in miles."""
    r = 3958.8  # Earth's radius in miles
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def zone_fee(miles, free_radius, max_radius, fee, what="That address"):
    """Delivery fee for one address: free inside the free radius, ``fee`` in
    the outer zone, refused beyond ``max_radius``."""
    miles = float(miles)
    if miles > max_radius:
        # One decimal near the limit, so 35.4 doesn't read as "35 miles" and look allowed.
        shown = f"{miles:.1f}" if round(miles) <= max_radius else f"{miles:.0f}"
        raise OutOfRange(f"{what} is about {shown} miles away in a straight line; "
                         f"we serve within {max_radius} miles.")
    return ZERO if miles <= free_radius else to_cents(fee)


# ---------------------------------------------------------------------------
# A full quote
# ---------------------------------------------------------------------------


@dataclass
class PackageTerms:
    name: str
    totes: int
    price: Decimal  # for the minimum rental length
    extra_week_price: Decimal


@dataclass
class Line:
    kind: str  # package, addon, rental, sale, delivery
    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal


@dataclass
class Quote:
    weeks: int
    lines: list = field(default_factory=list)
    renewal_week_price: Decimal = ZERO  # charged per extra week if pickup is missed

    def _sum(self, *kinds):
        return sum((l.amount for l in self.lines if l.kind in kinds), ZERO)

    @property
    def rental_subtotal(self):
        """Package, extra totes and rental extras (what a late cancel is a percent of)."""
        return self._sum("package", "addon", "rental")

    @property
    def sales_subtotal(self):
        return self._sum("sale")

    @property
    def delivery_total(self):
        return self._sum("delivery")

    @property
    def total_before_tax(self):
        return self.rental_subtotal + self.sales_subtotal + self.delivery_total


def _weeks_label(weeks):
    return f"{weeks} week{'s' if weeks != 1 else ''}"


def price_order(package, weeks, min_weeks, max_weeks=None, addon_totes=0, addon_price=ZERO,
                addon_extra_week_price=ZERO, extras=(), delivery=()):
    """Price an order.

    ``package`` is PackageTerms. ``extras`` is [(kind, name, quantity, unit_price)]
    with kind "rental" or "sale". ``delivery`` is [(description, amount)].
    """
    if weeks < min_weeks:
        raise PricingError(f"Rentals are at least {min_weeks} weeks.")
    if max_weeks and weeks > max_weeks:
        raise PricingError(f"For more than {max_weeks} weeks, please contact us.")
    if addon_totes < 0:
        raise PricingError("Extra totes can't be negative.")
    extra_weeks = weeks - min_weeks
    q = Quote(weeks=weeks)

    pkg_amount = to_cents(Decimal(package.price) + Decimal(package.extra_week_price) * extra_weeks)
    q.lines.append(Line("package", f"{package.name} package, {_weeks_label(weeks)}", 1, pkg_amount, pkg_amount))

    if addon_totes:
        per_tote = to_cents(Decimal(addon_price) + Decimal(addon_extra_week_price) * extra_weeks)
        q.lines.append(Line("addon", f"Extra totes, {_weeks_label(weeks)}", addon_totes, per_tote,
                            to_cents(per_tote * addon_totes)))

    for kind, name, quantity, unit_price in extras:
        if quantity <= 0:
            continue
        if kind not in ("rental", "sale"):
            raise PricingError(f"Unknown extra kind {kind!r}.")
        q.lines.append(Line(kind, name, quantity, to_cents(unit_price), to_cents(Decimal(unit_price) * quantity)))

    for description, amount in delivery:
        if amount:
            q.lines.append(Line("delivery", description, 1, to_cents(amount), to_cents(amount)))

    q.renewal_week_price = to_cents(
        Decimal(package.extra_week_price) + Decimal(addon_extra_week_price) * addon_totes
    )
    return q


# ---------------------------------------------------------------------------
# Cancellation, damage, buying lead time
# ---------------------------------------------------------------------------


def late_cancel_charge(rental_subtotal, percent):
    """A late cancellation costs a percent of the rental (no delivery fee or sale items)."""
    return to_cents(Decimal(rental_subtotal) * Decimal(percent) / Decimal(100))


def damage_charge(broken, missing, lid_issues, replacement_price, lid_replacement_price):
    """Full replacement for broken or missing totes; lid price for lid-only issues."""
    bins = (broken + missing) * Decimal(replacement_price)
    lids = lid_issues * Decimal(lid_replacement_price or 0)
    return to_cents(bins + lids)


def business_days_before(day, n):
    """The date ``n`` business days (Mon–Fri) before ``day``."""
    while n > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day
