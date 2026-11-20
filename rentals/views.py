from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from . import emails, inventory, links, maps, payment_views, pricing, services, totes
from .forms import FindBookingForm, PickupAddressForm
from .models import Booking, BusinessSettings, Package, Product


def home(request):
    """Landing page: the packages, extras and impact numbers."""
    return render(request, "rentals/home.html", {
        "packages": Package.objects.filter(active=True).prefetch_related("items__product"),
        "rentals": Product.objects.filter(active=True, kind=Product.Kind.RENTAL),
        "sales": Product.objects.filter(active=True, kind=Product.Kind.SALE),
        "tote": Product.objects.filter(kind=Product.Kind.TOTE, active=True).first(),
        "settings": BusinessSettings.load(),
        "impact": inventory.impact_stats(),
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
    totes.remember_booking(request, booking)

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
                s = BusinessSettings.load()
                place, miles = maps.lookup(address, s.base_lat, s.base_lng)
                services.set_pickup_address(
                    booking, {**address, "lat": place.lat, "lng": place.lng,
                              "place_id": place.place_id}, miles)
                messages.success(request, "Thanks! Your moving-in address is saved.")
                return redirect(request.path)
            except (maps.AddressNotFound, pricing.PricingError) as exc:
                pickup_form.add_error(None, str(exc))
            except maps.MapsUnavailable as exc:
                messages.error(request, str(exc))

    price_lines = list(booking.price_lines.all())
    return render(request, "rentals/booking_detail.html", {
        "booking": booking,
        "lines": list(booking.lines.select_related("product")),
        "price_lines": price_lines,
        "visits": {v.kind: v for v in booking.visits.select_related(
            "requested_window", "proposed_window", "window")},
        "total": sum((l.amount for l in price_lines), pricing.to_cents(0)),
        "pickup_form": pickup_form,
        "settings": BusinessSettings.load(),
        **payment_views.booking_payment_context(booking),
        "token": token,
        "totes": totes.totes_for(booking),
        "totes_editable": booking.status == Booking.Status.OUT,
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


# ---------------------------------------------------------------------------
# The rental agreement
# ---------------------------------------------------------------------------


def agreement(request):
    """The current agreement, for reading before signing."""
    from . import signing

    version = signing.current_version()
    if version is None:
        raise Http404
    return render(request, "rentals/agreement.html", {"version": version, "body": signing.render_html(version)})


def signed_agreement(request, token):
    """The exact version this booking signed, with the signature record."""
    from . import signing

    reference = links.read_token(token)
    booking = Booking.objects.filter(reference=reference).first() if reference else None
    sig = getattr(booking, "signature", None) if booking else None
    if sig is None:
        raise Http404
    if request.GET.get("format") == "pdf":
        from django.http import HttpResponse

        response = HttpResponse(signing.pdf_bytes(sig), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="Sprout-Tote-agreement-{booking.reference}.pdf"'
        return response
    return render(request, "rentals/agreement.html", {
        "version": sig.agreement_version, "body": signing.render_html(sig.agreement_version),
        "signature": sig, "booking": booking,
    })
