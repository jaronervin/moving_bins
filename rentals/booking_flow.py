"""
The renter's booking flow, one step per page:

    1. package    which package, how many weeks, dropoff date
    2. extras     extra totes, rental extras (dollies, blankets…), items for sale
    3. addresses  moving out of (dropoff) and moving into (pickup)
    4. delivery   delivery-by times, porch or present, optional time request
    5. contact    name, email, phone, notes
    6. review     check everything, sign the agreement, and book
                  (then the card page, when payments are on)

Answers are kept in the visitor's session (stored server-side, so they can't
be tampered with) under the key "draft" until the booking is submitted.
"""

import re
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from . import emails, inventory, maps, payments, pricing, services, signing, windows
from .forms import AddressForm, ContactForm, DeliveryForm, ExtrasForm, PackageForm, PickupAddressForm, SignForm
from .links import booking_url, make_token
from .models import BusinessSettings, Package, Product

SESSION_KEY = "draft"
STEPS = [("package", "Package"), ("extras", "Extras"), ("addresses", "Addresses"),
         ("delivery", "Delivery"), ("contact", "Contact"), ("review", "Review")]
# The draft key each step fills in.
KEY_SET_BY = {"package": "package_id", "extras": "extras", "addresses": "dropoff",
              "delivery": "delivery", "contact": "contact", "review": "_never"}


def _draft(request):
    return request.session.get(SESSION_KEY, {})


def _save(request, **values):
    draft = _draft(request)
    draft.update(values)
    request.session[SESSION_KEY] = draft


def _dates(draft):
    dropoff = date.fromisoformat(draft["dropoff_date"])
    return dropoff, dropoff + timedelta(days=7 * int(draft["weeks"]))


def _guard(request, step):
    """Send the renter back to the first step they haven't finished."""
    draft = _draft(request)
    for name, _ in STEPS:
        if name == step:
            return None
        if KEY_SET_BY[name] not in draft:
            return redirect(f"book_{name}")
    return None


def _order(draft):
    return services.order_from_draft({"extras": {}, "addon_totes": 0, **draft})


def _render(request, step, template, context):
    draft = _draft(request)
    quote = None
    if "package_id" in draft:
        try:
            pickup = draft.get("pickup")
            quote = services.quote(_order(draft), draft.get("dropoff", {}).get("miles"),
                                   pickup["miles"] if pickup else None)
        except (pricing.PricingError, Package.DoesNotExist):
            quote = None
    context.update({
        "steps": STEPS, "step": step, "draft": draft, "quote": quote,
        "done": {name for name, key in KEY_SET_BY.items() if key in draft},
        "settings": BusinessSettings.load(),
    })
    return render(request, template, context)


# ---------------------------------------------------------------------------


def package(request):
    s = BusinessSettings.load()
    draft = _draft(request)
    data = request.POST or request.GET or None
    if not data and "package_id" in draft:
        data = {"package": draft["package_id"], "weeks": draft["weeks"], "dropoff_date": draft["dropoff_date"]}
    form = PackageForm(data, min_notice_days=s.min_notice_days, min_weeks=s.min_weeks, max_weeks=s.max_weeks)
    if request.method == "GET" and request.GET.get("package") and not request.GET.get("dropoff_date"):
        form = PackageForm(initial={"package": request.GET["package"]}, min_notice_days=s.min_notice_days,
                           min_weeks=s.min_weeks, max_weeks=s.max_weeks)
    suggestions = []

    if request.method == "POST" and form.is_valid():
        pkg, weeks, dropoff = (form.cleaned_data[k] for k in ("package", "weeks", "dropoff_date"))
        pickup = dropoff + timedelta(days=7 * weeks)
        for product, wanted, free in services.free_counts(services.Order(pkg, weeks), dropoff):
            if wanted > free:
                form.add_error("package", services.availability_message(product, wanted))
        if not windows.open_windows(dropoff):
            form.add_error("dropoff_date", "We're not delivering that day, or it's full.")
            suggestions = windows.next_open_dates(form.earliest)
        elif not windows.open_windows(pickup):
            form.add_error("weeks", f"We can't pick up on {pickup:%A, %B %-d} (the end of that rental). "
                                    "Try another length or start date.")
        if form.is_valid():
            _save(request, package_id=pkg.pk, weeks=weeks, dropoff_date=dropoff.isoformat())
            return redirect("book_extras")

    return _render(request, "package", "rentals/book/package.html", {
        "form": form, "packages": Package.objects.filter(active=True).prefetch_related("items__product"),
        "suggestions": suggestions,
    })


def extras(request):
    if (r := _guard(request, "extras")):
        return r
    draft = _draft(request)
    products = Product.objects.filter(active=True, kind__in=[Product.Kind.RENTAL, Product.Kind.SALE])
    initial = {"addon_totes": draft.get("addon_totes", 0),
               **{f"x_{k}": v for k, v in draft.get("extras", {}).items()}}
    form = ExtrasForm(request.POST or None, initial=initial, products=products)
    if request.method == "POST" and form.is_valid():
        addon = form.cleaned_data.get("addon_totes") or 0
        order = services.order_from_draft({**draft, "addon_totes": addon, "extras": form.extras()})
        dropoff, _ = _dates(draft)
        for product, wanted, free in services.free_counts(order, dropoff):
            if wanted > free:
                form.add_error(None, services.availability_message(product, wanted))
        if form.is_valid():
            _save(request, addon_totes=addon, extras=form.extras())
            return redirect("book_addresses")
    # Items with none left for these dates say "contact us" (we may be able to buy more in time).
    dropoff, pickup = _dates(draft)
    package_qty = dict(services.order_from_draft({"extras": {}, "addon_totes": 0, **draft}).items())
    for p in form.products:
        p.contact_for_availability = (
            p.track_stock and inventory.available(p, dropoff, pickup) <= package_qty.get(p, 0))
    tote = Package.objects.get(pk=draft["package_id"]).tote_product
    return _render(request, "extras", "rentals/book/extras.html", {
        "form": form, "tote": tote,
        "rentals": form.rows(Product.Kind.RENTAL), "sales": form.rows(Product.Kind.SALE),
    })


def _lookup_into(form, settings):
    """Look up a valid address form; returns the dict to store, or None with errors added."""
    address = {k: form.cleaned_data.get(k, "") for k in ("line1", "line2", "city", "state", "zip_code")}
    try:
        place, miles = maps.lookup(address, settings.base_lat, settings.base_lng)
    except maps.AddressNotFound as exc:
        form.add_error(None, str(exc))
        return None
    return {**address, "formatted": place.formatted, "lat": place.lat, "lng": place.lng,
            "place_id": place.place_id, "miles": round(miles, 1)}


def addresses(request):
    if (r := _guard(request, "addresses")):
        return r
    draft = _draft(request)
    settings = BusinessSettings.load()

    if request.method == "POST":
        dropoff_form = AddressForm(request.POST, prefix="dropoff")
        pickup_form = PickupAddressForm(request.POST, prefix="pickup")
        if dropoff_form.is_valid() and pickup_form.is_valid():
            try:
                dropoff = _lookup_into(dropoff_form, settings)
                pickup = None
                if not pickup_form.cleaned_data["unknown"]:
                    pickup = _lookup_into(pickup_form, settings)
                for form, place in ((dropoff_form, dropoff), (pickup_form, pickup)):
                    if place:
                        try:
                            services.delivery_fees(place["miles"], None)
                        except pricing.OutOfRange as exc:
                            form.add_error(None, str(exc))
                if not dropoff_form.errors and not pickup_form.errors:
                    _save(request, dropoff=dropoff, pickup=pickup)
                    return redirect("book_delivery")
            except maps.MapsUnavailable as exc:
                messages.error(request, str(exc))
    else:
        dropoff_form = AddressForm(prefix="dropoff", initial=draft.get("dropoff") or request.session.get("area_check"))
        pickup_initial = draft.get("pickup") or ({"unknown": True} if "dropoff" in draft else None)
        pickup_form = PickupAddressForm(prefix="pickup", initial=pickup_initial)

    return _render(request, "addresses", "rentals/book/addresses.html", {
        "dropoff_form": dropoff_form, "pickup_form": pickup_form,
    })


def _delivery_windows(draft):
    dropoff_date, pickup_date = _dates(draft)
    return (dropoff_date, next(iter(windows.open_windows(dropoff_date)), None),
            pickup_date, next(iter(windows.open_windows(pickup_date)), None))


def delivery(request):
    if (r := _guard(request, "delivery")):
        return r
    draft = _draft(request)
    form = DeliveryForm(request.POST or None, initial=draft.get("delivery"))
    if request.method == "POST" and form.is_valid():
        _save(request, delivery={"presence": form.cleaned_data["presence"],
                                 "time_request": form.cleaned_data["time_request"]})
        return redirect("book_contact")
    dropoff_date, dropoff_window, pickup_date, pickup_window = _delivery_windows(draft)
    return _render(request, "delivery", "rentals/book/delivery.html", {
        "form": form, "dropoff_date": dropoff_date, "dropoff_window": dropoff_window,
        "pickup_date": pickup_date, "pickup_window": pickup_window,
    })


def contact(request):
    if (r := _guard(request, "contact")):
        return r
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            _save(request, contact=form.cleaned_data)
            return redirect("book_review")
    else:
        form = ContactForm(initial=_draft(request).get("contact"))
    return _render(request, "contact", "rentals/book/contact.html", {"form": form})


def review(request):
    if (r := _guard(request, "review")):
        return r
    draft = _draft(request)

    version = signing.current_version()
    sign_form = SignForm(request.POST or None,
                         initial={"typed_name": draft.get("contact", {}).get("name", "")})
    if request.method == "POST" and (version is None or sign_form.is_valid()):
        return _submit(request, draft, version, sign_form)

    dropoff_date, dropoff_window, pickup_date, pickup_window = _delivery_windows(draft)
    order = _order(draft)
    return _render(request, "review", "rentals/book/review.html", {
        "order": order, "items": order.items(), "payments_on": payments.enabled(),
        "agreement": version, "sign_form": sign_form,
        "dropoff_date": dropoff_date, "dropoff_window": dropoff_window,
        "pickup_date": pickup_date, "pickup_window": pickup_window,
    })


def _submit(request, draft, version, sign_form):
    """Create the booking (with its signature), then go to the card step or the booking page."""
    signature = None
    if version is not None:
        signature = {"typed_name": sign_form.cleaned_data["typed_name"], "version": version,
                     **signing.evidence_from_request(request)}
    try:
        booking = services.submit_booking(draft, signature=signature)
    except services.NotEnoughStock as exc:
        messages.error(request, services.availability_message(exc.product, exc.wanted))
        return redirect("book_package")
    except services.WindowFull as exc:
        messages.error(request, str(exc))
        return redirect("book_package")
    except pricing.OutOfRange as exc:
        messages.error(request, str(exc))
        return redirect("book_addresses")
    del request.session[SESSION_KEY]
    if booking.status == booking.Status.AWAITING_CARD:
        return redirect("card", token=make_token(booking))
    emails.booking_requested(booking)
    if booking.status == booking.Status.CONFIRMED:
        messages.success(request, "You're booked! We've emailed you a confirmation with a link back to this page.")
    else:
        messages.success(request, "Request sent! We've emailed you a copy with a link back to this page.")
    return redirect(booking_url(booking))


# ---------------------------------------------------------------------------
# "Do we deliver to you?" (first page and home page)
# ---------------------------------------------------------------------------

AREA_CHECKS_PER_VISIT = 15  # each check is a paid Google lookup


def _split_formatted(formatted):
    """'100 Light St, Baltimore, MD 21202, USA' → address fields (best effort, for prefilling)."""
    parts = [p.strip() for p in formatted.split(",")]
    if parts and parts[-1] in ("USA", "United States"):
        parts = parts[:-1]
    found = re.match(r"([A-Z]{2})\s+(\d{5})", parts[-1]) if len(parts) >= 3 else None
    street = parts[-3] if found else ""
    if not re.match(r"\d", street):  # a town or ZIP, not a street address: nothing to prefill
        return None
    return {"line1": street, "line2": "", "city": parts[-2], "state": found.group(1),
            "zip_code": found.group(2)}


def check_area(request):
    """Answer "do you deliver here, and is there a fee?" for one address, before booking."""
    from django.http import JsonResponse

    s = BusinessSettings.load()
    text = (request.POST.get("area-line1") or "").strip()[:200]
    status, message = "error", ""
    checks = request.session.get("area_checks", 0)
    if request.method != "POST":
        return redirect("book_package")
    if checks >= AREA_CHECKS_PER_VISIT:
        message = f"That's a lot of checks! Email us at {s.contact_email} and we'll tell you."
    elif len(text) < 3:
        message = "Type a street address, town or ZIP code."
    else:
        request.session["area_checks"] = checks + 1
        not_found = "We couldn't find that near Baltimore. Try a street address, town or ZIP code."
        try:
            place, miles = maps.lookup({"line1": text}, s.base_lat, s.base_lng, precise=False)
            if miles > 3 * s.max_radius_miles:  # matched somewhere far off: probably not what they meant
                raise maps.AddressNotFound(not_found)
            fee = pricing.zone_fee(miles, s.free_radius_miles, s.max_radius_miles, s.extended_zone_fee)
        except maps.AddressNotFound:
            message = not_found
        except maps.MapsUnavailable:
            message = "We couldn't check that right now. Please try again in a minute."
        except pricing.OutOfRange:
            status = "out"
            shown = f"{miles:.1f}" if round(miles) <= s.max_radius_miles else f"{miles:.0f}"
            message = (f"Sorry, we don't deliver there yet: it's about {shown} miles from us in a straight "
                       f"line, and we go up to {s.max_radius_miles}. If you're just outside, email "
                       f"{s.contact_email}; we may be able to help.")
        else:
            where = place.formatted.replace(", USA", "")
            if fee:
                status = "fee"
                message = (f"We deliver to {where}. It's about {miles:.0f} miles from us, so it adds "
                           f"${fee:.0f} for each address beyond {s.free_radius_miles} miles.")
            else:
                status = "free"
                message = f"Good news: we deliver free to {where} (about {miles:.0f} miles from us)."
            fields = _split_formatted(place.formatted)
            if fields:
                request.session["area_check"] = fields
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"status": status, "message": message})
    (messages.success if status in ("free", "fee") else messages.error)(request, message)
    return redirect(request.POST.get("next") or "book_package")


def start_over(request):
    request.session.pop(SESSION_KEY, None)
    return redirect(reverse("book_package"))
