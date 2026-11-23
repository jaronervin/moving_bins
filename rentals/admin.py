"""Owner-facing admin screens. Custom owner pages (schedule, routes, at-the-door)
come in a later milestone; until then this is the back office."""

from django.contrib import admin
from django.utils.html import format_html_join

from . import inventory
from .models import (
    Address,
    AgreementVersion,
    Booking,
    BookingLine,
    BusinessSettings,
    Charge,
    CheckIn,
    Customer,
    EmailUsage,
    Package,
    PackageItem,
    PriceLine,
    Product,
    Upgrade,
    UpgradeItem,
    Signature,
    StockMovement,
    StockState,
    TimeWindow,
    Visit,
    WindowTemplate,
)


@admin.register(BusinessSettings)
class BusinessSettingsAdmin(admin.ModelAdmin):
    fieldsets = [
        ("Contact (shown on the site)", {"fields": ["contact_email", "contact_phone"]}),
        ("Service area", {"fields": ["base_address", "base_lat", "base_lng", "free_radius_miles",
                                     "max_radius_miles", "extended_zone_fee"]}),
        ("Rental length", {"fields": ["min_weeks", "max_weeks"]}),
        ("Booking, review and cancellation", {"fields": [
            "auto_confirm", "review_hours", "min_notice_days", "hold_hours", "cancel_cutoff_hours",
            "late_cancel_charge_percent", "authorize_hours_before_dropoff"]}),
        ("Operations", {"fields": ["check_in_deadline_days", "purchase_lead_business_days",
                                    "vehicle_capacity", "boxes_per_bin", "contents_retention_days"]}),
    ]

    def has_add_permission(self, request):
        return not BusinessSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class PackageItemInline(admin.TabularInline):
    model = PackageItem
    extra = 0


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ["name", "totes", "price", "extra_week_price", "active", "sort_order"]
    list_editable = ["price", "extra_week_price", "active", "sort_order"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [PackageItemInline]


class UpgradeItemInline(admin.TabularInline):
    model = UpgradeItem
    extra = 0


@admin.register(Upgrade)
class UpgradeAdmin(admin.ModelAdmin):
    list_display = ["name", "price", "extra_week_price", "active"]
    list_editable = ["price", "extra_week_price", "active"]
    inlines = [UpgradeItemInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "price", "active", "track_stock", "ready", "out", "awaiting",
                    "out_of_service", "retired"]
    list_filter = ["kind", "active"]
    prepopulated_fields = {"slug": ["name"]}
    readonly_fields = ["stock_summary"]

    def _count(self, obj, state):
        return inventory.counts(obj)[state]

    @admin.display(description="Ready")
    def ready(self, obj):
        return self._count(obj, StockState.READY)

    @admin.display(description="Out")
    def out(self, obj):
        return self._count(obj, StockState.OUT)

    @admin.display(description="Awaiting check-in")
    def awaiting(self, obj):
        return self._count(obj, StockState.AWAITING_CHECK_IN)

    @admin.display(description="Needs lid")
    def out_of_service(self, obj):
        return self._count(obj, StockState.OUT_OF_SERVICE)

    @admin.display(description="Retired")
    def retired(self, obj):
        return self._count(obj, StockState.RETIRED)

    @admin.display(description="Stock right now")
    def stock_summary(self, obj):
        if not obj.pk:
            return "—"
        c = inventory.counts(obj)
        return format_html_join("", "<div>{}: <b>{}</b></div>",
                                ((StockState(k).label, v) for k, v in c.items()))


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """The ledger. Add rows for purchases and corrections; never edit or delete."""

    list_display = ["created_at", "product", "quantity", "from_state", "to_state", "reason", "booking", "note"]
    list_filter = ["product", "reason", "to_state", "from_state"]
    search_fields = ["note", "booking__reference"]
    autocomplete_fields = ["booking"]
    exclude = ["created_by"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if obj.product.is_serialized:
            # Tracked bins aren't counted in the ledger: a purchase creates bins with codes.
            if obj.reason == StockMovement.Reason.PURCHASE and obj.to_state == StockState.READY:
                made = inventory.record_purchase(obj.product, obj.quantity, user=request.user, note=obj.note)
                self.message_user(request, f"Added {len(made)} bins. Print their labels from Bins.")
            else:
                self.message_user(request, "Bins are tracked one by one: change them under Bins.", level="error")
            return
        obj.created_by = request.user
        super().save_model(request, obj, form, change)


class BookingLineInline(admin.TabularInline):
    model = BookingLine
    extra = 0


class PriceLineInline(admin.TabularInline):
    model = PriceLine
    extra = 0


class VisitInline(admin.StackedInline):
    model = Visit
    extra = 0


class ChargeInline(admin.TabularInline):
    model = Charge
    extra = 0
    readonly_fields = ["created_at"]


class CheckInInline(admin.TabularInline):
    model = CheckIn
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["reference", "customer", "bins", "dropoff_date", "pickup_date", "status", "hold_expires_at"]
    list_filter = ["status", "dropoff_date"]
    search_fields = ["reference", "customer__name", "customer__email"]
    date_hierarchy = "dropoff_date"
    readonly_fields = ["reference", "pickup_date", "created_at", "updated_at"]
    autocomplete_fields = ["customer"]
    raw_id_fields = ["dropoff_address", "pickup_address"]
    inlines = [PriceLineInline, BookingLineInline, VisitInline, ChargeInline, CheckInInline]
    actions = ["cancel_selected"]

    def has_delete_permission(self, request, obj=None):
        # Bookings are never deleted: they hold the signed agreement and payment
        # records. Cancel instead (action menu, or the renter's own link).
        return False

    def changelist_view(self, request, extra_context=None):
        from django.contrib import messages as m

        if request.method == "GET" and not request.GET:
            m.info(request, "To get rid of a booking, tick it and choose \"Cancel selected bookings\" from the "
                            "action menu. Bookings are kept (not deleted) because they hold the signed agreement "
                            "and payment records.")
        return super().changelist_view(request, extra_context)

    @admin.action(description="Cancel selected bookings (no fee, no email: for test or duplicate bookings)")
    def cancel_selected(self, request, queryset):
        from . import services

        done, skipped = 0, []
        for booking in queryset:
            try:
                services.cancel_booking(booking, by_owner=True)
                done += 1
            except services.CannotCancel:
                skipped.append(booking.reference)
        self.message_user(request, f"Cancelled {done} booking(s); their totes and delivery stops are free again.")
        if skipped:
            self.message_user(request, "Not cancelled (already finished, cancelled or out with the renter): "
                              + ", ".join(skipped), level="warning")

    @admin.display(description="Units")
    def bins(self, obj):
        return sum(line.quantity for line in obj.lines.all())


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone", "created_at"]
    search_fields = ["name", "email", "phone"]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    search_fields = ["line1", "city", "zip_code"]


@admin.register(WindowTemplate)
class WindowTemplateAdmin(admin.ModelAdmin):
    """Weekly delivery pattern. Saving one fills in its dated delivery days
    (the next 60 days) straight away; the daily job keeps extending them."""

    list_display = ["__str__", "max_stops", "active"]
    list_editable = ["max_stops", "active"]
    actions = ["fill_delivery_days"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._fill(request)

    def _fill(self, request):
        from .windows import generate_windows

        created = generate_windows()
        if created:
            self.message_user(request, f"Added {created} delivery day(s) for the next 60 days.")

    @admin.action(description="Fill in delivery days for the next 60 days")
    def fill_delivery_days(self, request, queryset):
        from .windows import generate_windows

        created = generate_windows()
        self.message_user(request, f"Added {created} delivery day(s)." if created
                          else "Delivery days were already filled in for the next 60 days.")


@admin.register(TimeWindow)
class TimeWindowAdmin(admin.ModelAdmin):
    list_display = ["date", "start", "end", "max_stops", "is_open"]
    list_editable = ["max_stops", "is_open"]
    list_filter = ["is_open"]
    date_hierarchy = "date"


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ["booking", "kind", "status", "amount", "tax", "created_at"]
    list_filter = ["kind", "status"]

    def has_delete_permission(self, request, obj=None):
        return False  # payment records are kept; refunds happen in Stripe


@admin.register(AgreementVersion)
class AgreementVersionAdmin(admin.ModelAdmin):
    list_display = ["version", "effective_at", "sha256"]
    readonly_fields = ["sha256"]

    def get_readonly_fields(self, request, obj=None):
        # Once anyone has signed a version, its text is frozen.
        if obj and Signature.objects.filter(agreement_version=obj).exists():
            return ["version", "text", "effective_at", "sha256"]
        return self.readonly_fields


@admin.register(Signature)
class SignatureAdmin(admin.ModelAdmin):
    list_display = ["booking", "typed_name", "card_name", "signed_at", "avs_postal_code_check"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # signed agreements are kept: they're the evidence in a dispute


@admin.register(CheckIn)
class CheckInAdmin(admin.ModelAdmin):
    list_display = ["booking", "product", "good", "broken", "lid_issues", "missing", "damage_amount", "checked_in_at"]


# ---------------------------------------------------------------------------
# Individually tracked bins
# ---------------------------------------------------------------------------

from django.shortcuts import redirect  # noqa: E402
from django.urls import reverse  # noqa: E402

from .models import Bin, BinContents, BinEvent  # noqa: E402


class BinEventInline(admin.TabularInline):
    model = BinEvent
    extra = 0
    can_delete = False
    readonly_fields = ["at", "kind", "booking", "by", "note"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Bin)
class BinAdmin(admin.ModelAdmin):
    list_display = ["code", "product", "status", "booking", "moves", "created_at"]
    list_filter = ["status", "product"]
    search_fields = ["code", "booking__reference"]
    readonly_fields = ["code", "product", "booking", "moves", "created_at"]
    fields = ["code", "product", "status", "booking", "moves", "created_at", "notes"]
    inlines = [BinEventInline]
    actions = ["print_labels"]

    @admin.action(description="Print QR labels for selected bins")
    def print_labels(self, request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(f"{reverse('ops_labels')}?ids={ids}")

    def has_add_permission(self, request):
        return False  # add bins with the "register bins" purchase (Stock movements) or seed

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            BinEvent.objects.create(bin=obj, kind=BinEvent.Kind.ADJUSTED, by=request.user,
                                    note=f"Status set to {obj.get_status_display()}")
        super().save_model(request, obj, form, change)


@admin.register(BinContents)
class BinContentsAdmin(admin.ModelAdmin):
    list_display = ["bin", "booking", "room", "fragile", "open_first", "updated_at"]
    search_fields = ["bin__code", "booking__reference"]


@admin.register(EmailUsage)
class EmailUsageAdmin(admin.ModelAdmin):
    list_display = ["month", "sent", "warned"]
