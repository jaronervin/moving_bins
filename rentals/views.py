from datetime import timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from . import emails, inventory, links, maps, pricing, services
from .forms import FindBookingForm, PickupAddressForm, QuoteForm
from .models import Booking, BusinessSettings, Product


def home(request):
    """Landing page with the quote calculator and impact numbers."""
    bins = get_object_or_404(Product, slug="bin-27gal", active=True)
    form = QuoteForm(request.GET or None, minimum_quantity=bins.minimum_quantity,
                     min_notice_days=BusinessSettings.load().min_notice_days)
    quote = available = pickup_date = error = None

    if form.is_valid():
        qty = form.cleaned_data["quantity"]
        dropoff = form.cleaned_data["dropoff_date"]
        weeks = form.cleaned_data["weeks"]
        pickup_date = dropoff + timedelta(days=7 * weeks)
        available = inventory.available(bins, dropoff, pickup_date)
        try:
            quote = services.quote([(bins, qty)], weeks)
        except pricing.PricingError as exc:
            error = str(exc)
        if quote and qty > available:
            error = f"Only {available} bins are free for those dates."

    return render(request, "rentals/home.html", {
        "form": form,
        "product": bins,
        "quote": quote,
        "available": available,
        "pickup_date": pickup_date,
        "error": error,
        "settings": BusinessSettings.load(),
        "impact": inventory.impact_stats(),
        "rate_table": [
            (n, pricing.to_cents(pricing.unit_rate(services.price_points(bins), n)),
             pricing.weekly_total(services.price_points(bins), n))
            for n in (20, 40, 60, 80, 100)
        ],
    })


# ---------------------------------------------------------------------------
# Renter's private booking page and "find my booking"
# ---------------------------------------------------------------------------




def booking_detail(request, token):
    reference = links.read_token(token)
    if reference is None:
        return render(request, "rentals/link_expired.html", status=404)
    booking = Booking.objects.filter(reference=reference).select_related(
        "customer", "dropoff_address", "pickup_address").first()
    if booking is None:
        raise Http404

    can_add_pickup = booking.status in services.EDITABLE_STATUSES
    pickup_form = None
    if can_add_pickup:
        pickup_form = PickupAddressForm(request.POST or None, prefix="pickup")
        pickup_form.fields.pop("unknown")
        for name in ("line1", "city", "state", "zip_code"):
            pickup_form.fields[name].required = True
        if request.method == "POST" and pickup_form.is_valid():
            address = {k: pickup_form.cleaned_data.get(k, "")
                       for k in ("line1", "line2", "city", "state", "zip_code")}
            try:
                place, miles = maps.lookup(address, BusinessSettings.load().base_address)
                services.set_pickup_address(
                    booking, {**address, "lat": place.lat, "lng": place.lng,
                              "place_id": place.place_id}, miles)
                messages.success(request, "Thanks! Your moving-in address is saved.")
                return redirect(request.path)
            except (maps.AddressNotFound, pricing.PricingError) as exc:
                pickup_form.add_error(None, str(exc))
            except maps.MapsUnavailable as exc:
                messages.error(request, str(exc))

    lines = list(booking.lines.select_related("product"))
    weekly = sum((line.locked_weekly_total for line in lines), pricing.to_cents(0))
    rental = weekly * booking.weeks
    return render(request, "rentals/booking_detail.html", {
        "booking": booking,
        "lines": lines,
        "visits": {v.kind: v for v in booking.visits.select_related(
            "requested_window", "proposed_window", "window")},
        "rental": rental,
        "total": rental + booking.pickup_fee,
        "pickup_form": pickup_form,
        "settings": BusinessSettings.load(),
    })


LINKABLE_STATUSES = [Booking.Status.REQUESTED, Booking.Status.TIMES_PROPOSED,
                     Booking.Status.CONFIRMED, Booking.Status.OUT, Booking.Status.PICKED_UP]


def find_booking(request):
    form = FindBookingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        found = list(Booking.objects.filter(customer__email__iexact=email,
                                            status__in=LINKABLE_STATUSES))
        if found:
            emails.booking_links(email, found)
        # Same message either way, so this can't be used to check who has booked.
        messages.success(request, f"If there are bookings under {email}, we've emailed the links.")
        return redirect("find_booking")
    return render(request, "rentals/find_booking.html", {"form": form})
