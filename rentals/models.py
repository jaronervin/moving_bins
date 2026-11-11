"""
Database tables for the rental business.

Reading guide:
  * Product / PricePoint ...... what we rent and how it's priced
  * StockMovement ............. inventory ledger (every change in bin counts)
  * Customer / Address ........ who rents, and where
  * Booking / BookingLine ..... a rental order and what's in it
  * TimeWindow / WindowTemplate  delivery windows the owners publish
  * Visit ..................... one dropoff or pickup for a booking
  * Charge .................... money moving through Stripe
  * AgreementVersion / Signature  the rental agreement and who signed it
  * CheckIn ................... inspection results after a pickup
  * BusinessSettings .......... one row of business rules editable in admin

Money is always stored as Decimal (never float) so cents add up exactly.
"""

import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ---------------------------------------------------------------------------
# Business settings (a single row)
# ---------------------------------------------------------------------------


class BusinessSettings(models.Model):
    """Business rules the owners can change in admin without touching code.

    There is only ever one row. Use ``BusinessSettings.load()`` to get it.
    """

    base_address = models.CharField(
        max_length=255,
        default="Baltimore, MD 21214",
        help_text="Where trips start and end. Distances are measured from here.",
    )
    base_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    base_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    dropoff_max_miles = models.PositiveIntegerField(
        default=30, help_text="Dropoffs farther than this (driving miles) are refused."
    )
    pickup_free_miles = models.PositiveIntegerField(
        default=30, help_text="Pickups up to this distance are free."
    )
    pickup_max_miles = models.PositiveIntegerField(
        default=60, help_text="Pickups farther than this are refused."
    )
    pickup_rate_per_mile = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("3.00"),
        help_text="Charged per mile beyond the free distance.",
    )

    hold_hours = models.PositiveIntegerField(
        default=48, help_text="How long a request holds bins while awaiting a response."
    )
    cancel_cutoff_hours = models.PositiveIntegerField(
        default=48, help_text="Cancelling closer than this to dropoff costs the late fee."
    )
    late_cancel_charge_percent = models.PositiveIntegerField(
        default=50, help_text="Percent of the rental charged for a late cancellation."
    )
    authorize_hours_before_dropoff = models.PositiveIntegerField(
        default=24,
        help_text="Place a pending hold on the card this long before dropoff. 0 = don't.",
    )
    check_in_deadline_days = models.PositiveIntegerField(
        default=3, help_text="Damage can only be charged if check-in happens within this."
    )
    vehicle_capacity = models.PositiveIntegerField(
        default=100, help_text="Bin sets that fit in one trip."
    )
    boxes_per_bin = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=Decimal("2.5"),
        help_text="Cardboard boxes one bin replaces (for impact stats).",
    )

    class Meta:
        verbose_name = "business settings"
        verbose_name_plural = "business settings"

    def __str__(self):
        return "Business settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # force a single row
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ---------------------------------------------------------------------------
# Products and pricing
# ---------------------------------------------------------------------------


class Product(models.Model):
    """Something we keep in inventory. Bins are the first; dollies etc. later.

    ``is_rentable`` is False for things we only track, like spare lids.
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    is_rentable = models.BooleanField(default=True)
    active = models.BooleanField(default=True, help_text="Shown to renters.")
    minimum_quantity = models.PositiveIntegerField(default=1)
    replacement_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Charged per unit lost or broken.",
    )
    lid_replacement_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Charged per lid lost or broken (leave blank if no lid).",
    )
    spare_lid_product = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Where usable lids from broken units are tracked.",
    )
    stripe_tax_code = models.CharField(max_length=32, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class PricePoint(models.Model):
    """One point on a product's volume-pricing line.

    The per-unit rate for an order is interpolated in a straight line between
    points and held flat past the last point. See ``rentals.pricing``.
    Bins: (20, $5.00) and (100, $3.00).
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_points")
    quantity = models.PositiveIntegerField()
    unit_rate = models.DecimalField(
        max_digits=8, decimal_places=4, help_text="Per unit, per week."
    )

    class Meta:
        ordering = ["product", "quantity"]
        constraints = [
            models.UniqueConstraint(fields=["product", "quantity"], name="unique_price_point")
        ]

    def __str__(self):
        return f"{self.product}: {self.quantity} @ ${self.unit_rate}"


# ---------------------------------------------------------------------------
# Inventory ledger
# ---------------------------------------------------------------------------


class StockState(models.TextChoices):
    READY = "ready", "Ready (on the shelf)"
    OUT = "out", "Out with a renter"
    AWAITING_CHECK_IN = "awaiting_check_in", "Awaiting check-in"
    OUT_OF_SERVICE = "out_of_service", "Out of service (needs a lid)"
    RETIRED = "retired", "Retired (recycled)"


class StockMovement(models.Model):
    """One change in inventory: ``quantity`` units moved from one state to another.

    A blank ``from_state`` means the units came from outside (e.g. purchased or
    a salvaged lid). A blank ``to_state`` means they left entirely (e.g. lost
    by a renter, or a spare lid used up). The current count in any state is
    "everything moved in minus everything moved out", see ``rentals.inventory``.

    Rows are never edited or deleted; mistakes are fixed with a new
    correcting movement so the history stays honest.
    """

    class Reason(models.TextChoices):
        PURCHASE = "purchase", "Purchased"
        DROPOFF = "dropoff", "Dropped off to renter"
        PICKUP = "pickup", "Picked up from renter"
        CHECK_IN_GOOD = "check_in_good", "Checked in: good"
        CHECK_IN_BROKEN = "check_in_broken", "Checked in: broken (retired)"
        CHECK_IN_LID = "check_in_lid", "Checked in: lid missing/broken"
        CHECK_IN_MISSING = "check_in_missing", "Checked in: missing"
        LID_SALVAGED = "lid_salvaged", "Lid saved from a broken bin"
        LID_REPAIR = "lid_repair", "Fitted with a spare lid"
        ADJUSTMENT = "adjustment", "Manual adjustment"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="movements")
    quantity = models.PositiveIntegerField()
    from_state = models.CharField(max_length=32, choices=StockState.choices, blank=True)
    to_state = models.CharField(max_length=32, choices=StockState.choices, blank=True)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    booking = models.ForeignKey(
        "Booking", on_delete=models.PROTECT, null=True, blank=True, related_name="movements"
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        src = self.get_from_state_display() if self.from_state else "outside"
        dst = self.get_to_state_display() if self.to_state else "gone"
        return f"{self.quantity} × {self.product}: {src} → {dst}"

    def clean(self):
        if not self.from_state and not self.to_state:
            raise ValidationError("A movement needs a from state, a to state, or both.")
        if self.from_state and self.from_state == self.to_state:
            raise ValidationError("From and to states are the same.")
        if self.quantity == 0:
            raise ValidationError("Quantity must be at least 1.")


# ---------------------------------------------------------------------------
# Customers and addresses
# ---------------------------------------------------------------------------


class Customer(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Address(models.Model):
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, default="MD")
    zip_code = models.CharField(max_length=10)
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    place_id = models.CharField(max_length=255, blank=True, help_text="Google Maps place id.")

    class Meta:
        verbose_name_plural = "addresses"

    def __str__(self):
        unit = f" {self.line2}" if self.line2 else ""
        return f"{self.line1}{unit}, {self.city}, {self.state} {self.zip_code}"


# ---------------------------------------------------------------------------
# Delivery windows
# ---------------------------------------------------------------------------


class WindowTemplate(models.Model):
    """A repeating weekly window, e.g. every Saturday 8–10am.

    ``rentals.windows.generate_windows`` turns these into dated TimeWindows,
    which can then be edited or closed one by one.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

    weekday = models.IntegerField(choices=Weekday.choices)
    start = models.TimeField()
    end = models.TimeField()
    max_stops = models.PositiveIntegerField(default=3)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["weekday", "start"]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start:%-I:%M%p}–{self.end:%-I:%M%p}"

    def clean(self):
        if self.end <= self.start:
            raise ValidationError("End must be after start.")


class TimeWindow(models.Model):
    """A specific dated window renters can choose for a dropoff or pickup."""

    date = models.DateField()
    start = models.TimeField()
    end = models.TimeField()
    max_stops = models.PositiveIntegerField(default=3)
    is_open = models.BooleanField(default=True, help_text="Untick to stop new bookings.")

    class Meta:
        ordering = ["date", "start"]
        constraints = [
            models.UniqueConstraint(fields=["date", "start"], name="unique_window_start")
        ]

    def __str__(self):
        return f"{self.date:%a %b %-d} {self.start:%-I:%M%p}–{self.end:%-I:%M%p}"

    def clean(self):
        if self.end <= self.start:
            raise ValidationError("End must be after start.")

    @property
    def starts_at(self):
        """Window start as a timezone-aware datetime."""
        from datetime import datetime

        return timezone.make_aware(datetime.combine(self.date, self.start))


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------


def _new_reference():
    # Short code renters can read over the phone: no 0/O/1/I confusion.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "MB-" + "".join(secrets.choice(alphabet) for _ in range(6))


class Booking(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (not submitted)"
        REQUESTED = "requested", "Requested, awaiting owner"
        TIMES_PROPOSED = "times_proposed", "New times proposed, awaiting renter"
        CONFIRMED = "confirmed", "Confirmed"
        OUT = "out", "Out with renter"
        PICKED_UP = "picked_up", "Picked up, awaiting check-in"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired (no response)"

    # Statuses that hold bins whatever the clock says.
    COMMITTED_STATUSES = [Status.CONFIRMED, Status.OUT]
    # Statuses that hold bins only until hold_expires_at.
    HOLD_STATUSES = [Status.REQUESTED, Status.TIMES_PROPOSED]

    reference = models.CharField(max_length=12, unique=True, default=_new_reference, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bookings")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    dropoff_date = models.DateField()
    weeks = models.PositiveIntegerField(default=1)
    pickup_date = models.DateField(editable=False, help_text="Dropoff date + 7 × weeks.")
    renewals = models.PositiveIntegerField(
        default=0, help_text="Extra weeks added because pickup was missed."
    )

    dropoff_address = models.ForeignKey(
        Address, on_delete=models.PROTECT, related_name="+", help_text="House moving out of."
    )
    pickup_address = models.ForeignKey(
        Address,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text="House moving into. May be added after booking, but before dropoff.",
    )
    dropoff_miles = models.PositiveIntegerField(null=True, blank=True)
    pickup_miles = models.PositiveIntegerField(null=True, blank=True)
    pickup_fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    access_notes = models.TextField(blank=True, help_text="Porch, gate codes, etc.")

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    stripe_payment_method_id = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["dropoff_date", "id"]
        indexes = [models.Index(fields=["status", "dropoff_date", "pickup_date"])]

    def __str__(self):
        return f"{self.reference} ({self.customer.name}, {self.dropoff_date:%b %-d})"

    def save(self, *args, **kwargs):
        self.pickup_date = self.dropoff_date + timedelta(days=7 * (self.weeks + self.renewals))
        super().save(*args, **kwargs)

    @property
    def total_weeks(self):
        return self.weeks + self.renewals

    def holds_inventory(self, now=None):
        """Does this booking currently tie up bins on its dates?"""
        if self.status in self.COMMITTED_STATUSES:
            return True
        if self.status in self.HOLD_STATUSES:
            now = now or timezone.now()
            return self.hold_expires_at is not None and self.hold_expires_at > now
        return False


class BookingLine(models.Model):
    """One product in a booking. The weekly rate is locked when booked."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    locked_unit_rate = models.DecimalField(max_digits=8, decimal_places=4)
    locked_weekly_total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["booking", "product"], name="one_line_per_product")
        ]

    def __str__(self):
        return f"{self.quantity} × {self.product}"


class Visit(models.Model):
    """A dropoff or a pickup for a booking, with the window negotiation."""

    class Kind(models.TextChoices):
        DROPOFF = "dropoff", "Dropoff"
        PICKUP = "pickup", "Pickup"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested by renter"
        PROPOSED = "proposed", "Owner proposed another window"
        CONFIRMED = "confirmed", "Confirmed"
        DONE = "done", "Done"
        MISSED = "missed", "Missed (bins not ready)"
        CANCELLED = "cancelled", "Cancelled"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="visits")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    requested_window = models.ForeignKey(
        TimeWindow, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    proposed_window = models.ForeignKey(
        TimeWindow, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    window = models.ForeignKey(
        TimeWindow,
        on_delete=models.PROTECT,
        related_name="visits",
        null=True,
        blank=True,
        help_text="The agreed window.",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    completed_at = models.DateTimeField(null=True, blank=True)
    quantity_handled = models.PositiveIntegerField(null=True, blank=True)
    photo = models.ImageField(upload_to="visits/%Y/%m/", null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["booking", "kind"]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.booking.reference}"


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


class Charge(models.Model):
    """A Stripe payment (or refund) tied to a booking."""

    class Kind(models.TextChoices):
        RENTAL = "rental", "Rental + pickup fee"
        LATE_CANCEL = "late_cancel", "Late cancellation"
        RENEWAL = "renewal", "Renewal week"
        DAMAGE = "damage", "Damage / loss"
        REFUND = "refund", "Refund"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AUTHORIZED = "authorized", "Authorized (hold, not charged)"
        CAPTURED = "captured", "Charged"
        RELEASED = "released", "Hold released"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="charges")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Before tax.")
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    description = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=64, blank=True)
    failure_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} ${self.total} ({self.get_status_display()})"

    @property
    def total(self):
        return self.amount + self.tax


# ---------------------------------------------------------------------------
# Rental agreement
# ---------------------------------------------------------------------------


class AgreementVersion(models.Model):
    """A version of the rental agreement text. Never edit one that's been signed;
    add a new version instead."""

    version = models.CharField(max_length=20, unique=True)
    text = models.TextField()
    sha256 = models.CharField(max_length=64, editable=False)
    effective_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-effective_at"]

    def __str__(self):
        return f"Agreement v{self.version}"

    def save(self, *args, **kwargs):
        self.sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        return cls.objects.filter(effective_at__lte=timezone.now()).first()


class Signature(models.Model):
    """Evidence that a specific person agreed to a specific agreement version."""

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="signature")
    agreement_version = models.ForeignKey(AgreementVersion, on_delete=models.PROTECT)
    agreement_sha256 = models.CharField(max_length=64, help_text="Hash of the text shown.")
    typed_name = models.CharField(max_length=150)
    card_name = models.CharField(max_length=150, help_text="Name on the payment method.")
    email = models.EmailField()
    avs_line1_check = models.CharField(max_length=20, blank=True, help_text="From Stripe.")
    avs_postal_code_check = models.CharField(max_length=20, blank=True, help_text="From Stripe.")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    signed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.typed_name} signed v{self.agreement_version.version} for {self.booking.reference}"


# ---------------------------------------------------------------------------
# Check-in after pickup
# ---------------------------------------------------------------------------


class CheckIn(models.Model):
    """Inspection of returned bins for one booking and product.

    good + broken + lid_issues + missing must equal what was rented.
    ``lids_salvaged`` = how many of the broken bins had a usable lid.
    """

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="check_ins")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    good = models.PositiveIntegerField(default=0)
    broken = models.PositiveIntegerField(default=0, help_text="Bin broken; set is retired.")
    lids_salvaged = models.PositiveIntegerField(default=0, help_text="Usable lids from broken bins.")
    lid_issues = models.PositiveIntegerField(default=0, help_text="Bin fine, lid missing/broken.")
    missing = models.PositiveIntegerField(default=0, help_text="Bin never came back.")
    damage_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    checked_in_at = models.DateTimeField(default=timezone.now)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["booking", "product"], name="one_check_in_per_product")
        ]

    def __str__(self):
        return f"Check-in {self.booking.reference}: {self.product}"

    @property
    def total(self):
        return self.good + self.broken + self.lid_issues + self.missing
