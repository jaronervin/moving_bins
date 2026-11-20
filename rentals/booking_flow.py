"""
The renter's booking flow, one step per page:

    1. package    which package, how many weeks, dropoff date
    2. extras     extra totes, rental extras (dollies, blankets…), items for sale
    3. addresses  moving out of (dropoff) and moving into (pickup)
    4. delivery   delivery-by times, porch or present, optional time request
    5. contact    name, email, phone, notes
    6. review     check everything and book

Card details (milestone 4) and the agreement (milestone 5) will slot in
before booking.

Answers are kept in the visitor's session (stored server-side, so they can't
be tampered with) under the key "draft" until the booking is submitted.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from . import emails, maps, pricing, services, windows
from .forms import AddressForm, ContactForm, DeliveryForm, ExtrasForm, PackageForm, PickupAddressForm
from .links import booking_url
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
                form.add_error("package", f"We only have {free} {product.name.lower()}s free for those "
                                          f"dates. Try other dates or a smaller package.")
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
                form.add_error(None, f"We only have {free} {product.name.lower()}s free for those dates.")
        if form.is_valid():
            _save(request, addon_totes=addon, extras=form.extras())
            return redirect("book_addresses")
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
        dropoff_form = AddressForm(prefix="dropoff", initial=draft.get("dropoff"))
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

    if request.method == "POST":
        try:
            booking = services.submit_booking(draft)
        except services.NotEnoughStock as exc:
            messages.error(request, f"{exc} Please change your package or dates.")
            return redirect("book_package")
        except services.WindowFull as exc:
            messages.error(request, str(exc))
            return redirect("book_package")
        except pricing.OutOfRange as exc:
            messages.error(request, str(exc))
            return redirect("book_addresses")
        del request.session[SESSION_KEY]
        emails.booking_requested(booking)
        if booking.status == booking.Status.CONFIRMED:
            messages.success(request, "You're booked! We've emailed you a confirmation with a link back to this page.")
        else:
            messages.success(request, "Request sent! We've emailed you a copy with a link back to this page.")
        return redirect(booking_url(booking))

    dropoff_date, dropoff_window, pickup_date, pickup_window = _delivery_windows(draft)
    order = _order(draft)
    return _render(request, "review", "rentals/book/review.html", {
        "order": order, "items": order.items(),
        "dropoff_date": dropoff_date, "dropoff_window": dropoff_window,
        "pickup_date": pickup_date, "pickup_window": pickup_window,
    })


def start_over(request):
    request.session.pop(SESSION_KEY, None)
    return redirect(reverse("book_package"))
