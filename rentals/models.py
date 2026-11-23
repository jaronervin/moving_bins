"""
Database tables for the rental business.

Reading guide:
  * Product .................. what we rent or sell, and its add-on/flat price
  * Package / PackageItem ..... the tote packages renters choose from
  * StockMovement ............. inventory ledger (every change in bin counts)
  * Customer / Address ........ who rents, and where
  * Booking / BookingLine ..... a rental order and what's in it (quantities)
  * PriceLine ................. the order's price breakdown, locked at booking
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

def counted(quantity, name):
    """ "1 dolly", "3 dollies", "45 27-gallon totes with lid", "2 packing kits (kraft…)".

    Plurals the main noun: the last word before any " with " or " (" part.
    """
    name = name[0].lower() + name[1:] if name[:1].isupper() and not name[1:2].isupper() else name
    if quantity == 1:
        return f"1 {name}"
    cut = len(name)
    for marker in (" with ", " ("):
        i = name.find(marker)
        if i != -1:
            cut = min(cut, i)
    head, tail = name[:cut], name[cut:]
    if head.endswith("y") and head[-2:-1] not in "aeiou":
        head = head[:-1] + "ies"
    elif head.endswith(("ss", "x", "ch", "sh")):
        head += "es"
    elif head.endswith("s"):
        pass  # already plural, e.g. "door jamb guards"
    else:
        head += "s"
    return f"{quantity} {head}{tail}"


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
    base_lat = models.DecimalField(
        max_digits=9, decimal_places=6, default=Decimal("39.352000"),
        help_text="Center of the service area (default: approx. center of ZIP 21214).",
    )
    base_lng = models.DecimalField(max_digits=9, decimal_places=6, default=Decimal("-76.563000"))

    free_radius_miles = models.PositiveIntegerField(
        default=20, help_text="Dropoff and pickup are free within this radius (straight line)."
    )
    max_radius_miles = models.PositiveIntegerField(
        default=35, help_text="Addresses farther than this are refused."
    )
    extended_zone_fee = models.DecimalField(
        max_digits=7, decimal_places=2, default=Decimal("25.00"),
        help_text="Charged for each address (dropoff, pickup) between the free radius and the "
        "maximum. PLACEHOLDER: confirm the real amount before launch.",
    )

    min_weeks = models.PositiveIntegerField(default=2, help_text="Shortest rental offered online.")
    max_weeks = models.PositiveIntegerField(default=4, help_text="Longest rental offered online.")

    contact_email = models.EmailField(default="hello@sprouttoteco.com")
    contact_phone = models.CharField(
        max_length=30, default="703-703-2511",
        help_text="Shown on the site. PLACEHOLDER: replace with the real number before launch.",
    )

    min_notice_days = models.PositiveIntegerField(
        default=3, help_text="Earliest dropoff a renter can book, in days from today."
    )
    auto_confirm = models.BooleanField(
        default=True,
        help_text="Confirm bookings as soon as they're submitted, with the times the renter chose. "
        "Untick to approve each request by hand.",
    )
    review_hours = models.PositiveIntegerField(
        default=48,
        help_text="With auto-confirm: how long after booking you can still decline it or ask for a change.",
    )
    card_entry_minutes = models.PositiveIntegerField(
        default=30, help_text="How long totes are held while a renter enters their card."
    )
    hold_hours = models.PositiveIntegerField(
        default=48, help_text="How long a request holds bins while awaiting a response."
    )
    cancel_cutoff_hours = models.PositiveIntegerField(
        default=72, help_text="Cancelling or changing closer than this to dropoff: late fee / not online."
    )
    late_cancel_charge_percent = models.PositiveIntegerField(
        default=50, help_text="Percent of the rental charged for a late cancellation."
    )
    dropoff_reminder_days = models.PositiveIntegerField(
        default=3, help_text="Email renters this many days before dropoff (0 = don't)."
    )
    pickup_reminder_days = models.PositiveIntegerField(
        default=2, help_text="Email renters this many days before pickup (0 = don't)."
    )
    authorize_hours_before_dropoff = models.PositiveIntegerField(
        default=24,
        help_text="Place a pending hold on the card this long before dropoff. 0 = don't.",
    )
    check_in_deadline_days = models.PositiveIntegerField(
        default=3, help_text="Damage can only be charged if check-in happens within this."
    )
    contents_retention_days = models.PositiveIntegerField(
        default=30, help_text="Renters' tote contents notes are deleted this long after pickup."
    )
    purchase_lead_business_days = models.PositiveIntegerField(
        default=3,
        help_text="Business days from ordering more totes to having them ready.",
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
    """Something we rent, sell, or just keep track of.

    * totes: rented in packages, or as add-ons at ``addon_price`` for the
      first rental period plus ``addon_extra_week_price`` per extra week;
    * rental extras (dollies, blankets…): a one-time ``price`` per rental;
    * sale items (mattress bags, packing kits…): ``price`` each, not returned;
    * parts (spare lids): tracked only, never offered.
    """

    class Kind(models.TextChoices):
        TOTE = "tote", "Tote (packages and add-ons)"
        RENTAL = "rental", "Rental extra (one-time fee, returned)"
        SALE = "sale", "For sale (not returned)"
        PART = "part", "Part (tracked only)"


    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.RENTAL)
    is_rentable = models.BooleanField(default=True)
    is_serialized = models.BooleanField(
        default=False,
        help_text="Each unit has its own QR label and is tracked one by one (see Bin). "
        "Otherwise units are only counted, through the stock ledger.",
    )
    active = models.BooleanField(default=True, help_text="Shown to renters.")
    minimum_quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="Rental extras: one-time fee per rental. Sale items: price each.",
    )
    addon_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="Totes: price per extra tote for the minimum rental length.",
    )
    addon_extra_week_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="Totes: price per extra tote for each week beyond the minimum.",
    )
    track_stock = models.BooleanField(
        default=True,
        help_text="Check stock before booking. Untick for items you haven't counted yet "
        "(they're then never refused).",
    )
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
    planned_capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Take bookings as if you own this many, and buy more as bookings come in. "
        "Blank = only what you actually own.",
    )
    lead_business_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Business days from deciding to buy more to having them ready (0 = same day). "
        "Blank = the business setting.",
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


class Package(models.Model):
    """A tote package, e.g. "2BR: 40 totes, 2 dollies, $169 for two weeks"."""

    name = models.CharField(max_length=60)
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=200, blank=True)
    tote_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    totes = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=8, decimal_places=2, help_text="For the minimum rental length.")
    extra_week_price = models.DecimalField(max_digits=8, decimal_places=2)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "totes"]

    def __str__(self):
        return self.name


class PackageItem(models.Model):
    """Something included in a package at no extra cost, e.g. 2 dollies."""

    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.quantity} × {self.product}"

    @property
    def label(self):
        """e.g. "1 dolly", "3 dollies", "1 hand truck"."""
        return counted(self.quantity, self.product.name)


# ---------------------------------------------------------------------------
# Inventory ledger
# ---------------------------------------------------------------------------


class StockState(models.TextChoices):
    READY = "ready", "Ready (on the shelf)"
    OUT = "out", "Out with a renter"
    AWAITING_CHECK_IN = "awaiting_check_in", "Awaiting check-in"
    OUT_OF_SERVICE = "out_of_service", "Out of service (needs a lid)"
    RETIRED = "retired", "Retired (recycled)"
    MISSING = "missing", "Missing (not returned)"
    RESERVED = "reserved", "Code printed, not on a tote yet"


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
        SOLD = "sold", "Sold to a renter"
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
        AWAITING_CARD = "awaiting_card", "Waiting for card details"
        REQUESTED = "requested", "Requested, awaiting owner"
        TIMES_PROPOSED = "times_proposed", "New times proposed, awaiting renter"
        CONFIRMED = "confirmed", "Confirmed"
        OUT = "out", "Out with renter"
        PICKED_UP = "picked_up", "Picked up, awaiting check-in"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired (no response)"
        DECLINED = "declined", "Declined by us"

    # Statuses that hold bins whatever the clock says.
    COMMITTED_STATUSES = [Status.CONFIRMED, Status.OUT]
    # Statuses that hold bins only until hold_expires_at.
    HOLD_STATUSES = [Status.AWAITING_CARD, Status.REQUESTED, Status.TIMES_PROPOSED]

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
    delivery_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="Extended-zone fees for the dropoff and/or pickup address.",
    )
    access_notes = models.TextField(blank=True, help_text="Porch, gate codes, etc.")

    class Presence(models.TextChoices):
        PORCH = "porch", "Leave at the door/porch (renter's risk)"
        PRESENT = "present", "Renter will be there"

    presence = models.CharField(max_length=10, choices=Presence.choices, default=Presence.PORCH)
    time_request = models.CharField(
        max_length=200, blank=True, help_text="Renter's note asking for a more specific time."
    )
    package = models.ForeignKey("Package", on_delete=models.PROTECT, null=True, blank=True)
    renewal_week_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"),
        help_text="Charged per extra week if bins aren't ready at pickup (locked at booking).",
    )
    change_request = models.TextField(blank=True, help_text="What the owners asked the renter to change.")
    change_requested_at = models.DateTimeField(null=True, blank=True)

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    review_until = models.DateTimeField(
        null=True, blank=True, help_text="Auto-confirmed bookings can be declined until this time."
    )
    decline_reason = models.CharField(max_length=500, blank=True)
    stripe_payment_method_id = models.CharField(max_length=64, blank=True)
    stripe_checkout_session_id = models.CharField(max_length=128, blank=True)
    confirmation_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="When the renter's booking confirmation email went out (blank = it didn't)."
    )
    dropoff_reminded_at = models.DateTimeField(null=True, blank=True)
    pickup_reminded_at = models.DateTimeField(null=True, blank=True)

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
    """How many of one product a booking takes (package totes + add-ons together)."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["booking", "product"], name="one_line_per_product")
        ]

    def __str__(self):
        return f"{self.quantity} × {self.product}"


class PriceLine(models.Model):
    """One line of a booking's price, locked when booked (e.g. "2BR package, 3 weeks")."""

    class Kind(models.TextChoices):
        PACKAGE = "package", "Package"
        ADDON = "addon", "Extra totes"
        RENTAL = "rental", "Rental extra"
        SALE = "sale", "Sale item"
        DELIVERY = "delivery", "Delivery"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="price_lines")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["booking", "sort", "id"]

    def __str__(self):
        return f"{self.description}: ${self.amount}"


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
        RENTAL = "rental", "Booking total"
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
    captured_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"), help_text="What was actually charged."
    )
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
    card_name = models.CharField(max_length=150, blank=True, help_text="Name on the card (from Stripe).")
    card_brand = models.CharField(max_length=20, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    email = models.EmailField()
    avs_line1_check = models.CharField(max_length=20, blank=True, help_text="From Stripe.")
    avs_postal_code_check = models.CharField(max_length=20, blank=True, help_text="From Stripe.")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    signed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.typed_name} signed v{self.agreement_version.version} for {self.booking.reference}"

    @property
    def name_matches_card(self):
        """None until a card is saved; then whether the typed name matches the cardholder."""
        if not self.card_name:
            return None
        from .signing import names_match

        return names_match(self.typed_name, self.card_name)


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


# ---------------------------------------------------------------------------
# Individually tracked bins (QR labels)
# ---------------------------------------------------------------------------


class Bin(models.Model):
    """One physical bin with its own label code, e.g. "K7Q".

    For serialized products, these rows ARE the inventory: counts per state
    come from here, not from the StockMovement ledger. Every change is also
    written to BinEvent, so each bin has a full history.
    """

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bins")
    code = models.CharField(max_length=8, unique=True)
    status = models.CharField(max_length=32, choices=StockState.choices, default=StockState.READY)
    booking = models.ForeignKey(
        "Booking",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bins",
        help_text="The booking this bin is currently out with (cleared at check-in).",
    )
    moves = models.PositiveIntegerField(default=0, help_text="Rentals completed.")
    created_at = models.DateTimeField(default=timezone.now)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class BinEvent(models.Model):
    """Something that happened to one bin. Never edited."""

    class Kind(models.TextChoices):
        REGISTERED = "registered", "Added to inventory"
        LOADED = "loaded", "Loaded for dropoff"
        UNLOADED = "unloaded", "Taken off a load (scan undone)"
        PICKED_UP = "picked_up", "Picked up"
        PICKUP_UNDONE = "pickup_undone", "Pickup scan undone"
        CHECKED_IN_GOOD = "checked_in_good", "Checked in: good"
        CHECKED_IN_BROKEN = "checked_in_broken", "Checked in: broken (retired)"
        CHECKED_IN_LID = "checked_in_lid", "Checked in: lid missing/broken"
        MARKED_MISSING = "marked_missing", "Not returned"
        FOUND = "found", "Found after being missing"
        ACTIVATED = "activated", "Label put on a tote; in service"
        LID_REPAIRED = "lid_repaired", "Fitted with a spare lid"
        ADJUSTED = "adjusted", "Status changed by hand"

    bin = models.ForeignKey(Bin, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    booking = models.ForeignKey(
        "Booking", on_delete=models.SET_NULL, null=True, blank=True, related_name="bin_events"
    )
    at = models.DateTimeField(default=timezone.now)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.bin}: {self.get_kind_display()}"


class BinContents(models.Model):
    """What a renter wrote about one bin during one rental.

    Belongs to the rental, not the bin: the next renter starts blank. Deleted
    ``contents_retention_days`` after pickup.
    """

    booking = models.ForeignKey("Booking", on_delete=models.CASCADE, related_name="bin_contents")
    bin = models.ForeignKey(Bin, on_delete=models.CASCADE, related_name="contents_notes")
    contents = models.TextField(blank=True)
    room = models.CharField(max_length=60, blank=True)
    fragile = models.BooleanField(default=False)
    open_first = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "bin contents"
        constraints = [
            models.UniqueConstraint(fields=["booking", "bin"], name="one_note_per_bin_per_booking")
        ]

    def __str__(self):
        return f"{self.bin} in {self.booking.reference}"


class EmailUsage(models.Model):
    """Emails sent through Postmark per calendar month (counted per recipient,
    like Postmark does), so owners are warned before the plan's limit."""

    month = models.DateField(unique=True, help_text="First day of the month.")
    sent = models.PositiveIntegerField(default=0)
    warned = models.PositiveIntegerField(default=0, help_text="Highest warning level sent (percent).")

    class Meta:
        verbose_name_plural = "email usage"

    def __str__(self):
        return f"{self.month:%B %Y}: {self.sent}"
