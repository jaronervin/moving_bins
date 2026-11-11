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
    PricePoint,
    Product,
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
        ("Base", {"fields": ["base_address", "base_lat", "base_lng"]}),
        ("Service area", {"fields": ["dropoff_max_miles", "pickup_free_miles",
                                     "pickup_max_miles", "pickup_rate_per_mile"]}),
        ("Holds, cancellation, payment", {"fields": ["min_notice_days", "hold_hours", "cancel_cutoff_hours",
                                                     "late_cancel_charge_percent",
                                                     "authorize_hours_before_dropoff"]}),
        ("Operations", {"fields": ["check_in_deadline_days", "vehicle_capacity", "boxes_per_bin"]}),
    ]

    def has_add_permission(self, request):
        return not BusinessSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class PricePointInline(admin.TabularInline):
    model = PricePoint
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "is_rentable", "active", "ready", "out", "awaiting", "out_of_service", "retired"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [PricePointInline]
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
        obj.created_by = request.user
        super().save_model(request, obj, form, change)


class BookingLineInline(admin.TabularInline):
    model = BookingLine
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
    inlines = [BookingLineInline, VisitInline, ChargeInline, CheckInInline]

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
    list_display = ["__str__", "max_stops", "active"]
    list_editable = ["max_stops", "active"]


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


@admin.register(CheckIn)
class CheckInAdmin(admin.ModelAdmin):
    list_display = ["booking", "product", "good", "broken", "lid_issues", "missing", "damage_amount", "checked_in_at"]
