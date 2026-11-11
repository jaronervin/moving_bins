"""
The renter's booking flow, one step per page:

    1. dates     how many bins, dropoff date, how many weeks
    2. addresses moving out of (dropoff) and moving into (pickup)
    3. times     dropoff and pickup windows
    4. contact   name, email, phone, notes
    5. review    check everything and submit → bins are held

Card details (milestone 4) and the agreement (milestone 5) will slot in
between contact and submit.

Answers are kept in the visitor's session (stored server-side, so they can't
be tampered with) under the key "draft" until the booking is submitted.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import emails, inventory, maps, pricing, services, windows
from .forms import AddressForm, ContactForm, PickupAddressForm, QuoteForm, TimesForm
from .links import booking_url
from .models import BusinessSettings, Product, TimeWindow

SESSION_KEY = "draft"
STEPS = [("dates", "Dates"), ("addresses", "Addresses"), ("times", "Times"),
         ("contact", "Contact"), ("review", "Review")]
# What each step needs from the steps before it.
REQUIRES = {
    "addresses": ["quantity"],
    "times": ["quantity", "dropoff"],
    "contact": ["quantity", "dropoff", "dropoff_window_id"],
    "review": ["quantity", "dropoff", "dropoff_window_id", "contact"],
}


def _product():
    return get_object_or_404(Product, slug="bin-27gal", active=True)


def _draft(request):
    return request.session.get(SESSION_KEY, {})


def _save(request, **values):
    draft = _draft(request)
    draft.update(values)
    request.session[SESSION_KEY] = draft


def _dates(draft):
    dropoff = date.fromisoformat(draft["dropoff_date"])
    return dropoff, dropoff + timedelta(days=7 * int(draft["weeks"]))


def _render(request, step, template, context):
    draft = _draft(request)
    done = {name for name, key in _KEY_SET_BY.items() if key in draft}
    context.update({"steps": STEPS, "step": step, "done": done, "draft": draft})
    return render(request, template, context)


# The draft key each step fills in, used to show finished steps as links.
_KEY_SET_BY = {"dates": "quantity", "addresses": "dropoff", "times": "dropoff_window_id",
               "contact": "contact", "review": "_never"}


def _guard(request, step):
    """Send the renter back to the first step they haven't finished."""
    draft = _draft(request)
    for needed in REQUIRES.get(step, []):
        if needed not in draft:
            first_missing = next(name for name, key in _KEY_SET_BY.items() if key == needed)
            return redirect(f"book_{first_missing}")
    return None


# ---------------------------------------------------------------------------


def dates(request):
    product = _product()
    draft = _draft(request)
    data = request.POST or request.GET or None
    if not data and "quantity" in draft:
        data = {k: draft[k] for k in ("quantity", "dropoff_date", "weeks")}
    form = QuoteForm(data, minimum_quantity=product.minimum_quantity,
                     min_notice_days=BusinessSettings.load().min_notice_days)
    suggestions = []

    if data and form.is_valid():
        qty, dropoff, weeks = (form.cleaned_data[k] for k in ("quantity", "dropoff_date", "weeks"))
        pickup = dropoff + timedelta(days=7 * weeks)
        free = inventory.available(product, dropoff, pickup)
        if qty > free:
            form.add_error("quantity", f"Only {free} bins are free for those dates.")
        if not windows.open_windows(dropoff):
            form.add_error("dropoff_date", "We have no dropoff times left that day.")
            suggestions = windows.next_open_dates(form.earliest)
        elif not windows.open_windows(pickup):
            form.add_error("weeks", f"We have no pickup times left on {pickup:%a %b %-d}. "
                                    "Try another length or start date.")
        if form.is_valid() and request.method == "POST":
            changed = (draft.get("dropoff_date"), draft.get("weeks")) != (dropoff.isoformat(), weeks)
            _save(request, product_id=product.pk, quantity=qty,
                  dropoff_date=dropoff.isoformat(), weeks=weeks)
            if changed:  # new dates: earlier window choices no longer apply
                for key in ("dropoff_window_id", "pickup_window_id"):
                    request.session[SESSION_KEY].pop(key, None)
                request.session.modified = True
            return redirect("book_addresses")

    quote = None
    if form.is_bound and form.is_valid():
        quote = services.quote([(product, form.cleaned_data["quantity"])], form.cleaned_data["weeks"])
    return _render(request, "dates", "rentals/book/dates.html", {
        "form": form, "quote": quote, "suggestions": suggestions,
    })


def _lookup_into(form, settings):
    """Look up a valid address form; returns the dict to store, or None with errors added."""
    address = {k: form.cleaned_data.get(k, "") for k in ("line1", "line2", "city", "state", "zip_code")}
    try:
        place, miles = maps.lookup(address, settings.base_address)
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
                if dropoff:
                    try:
                        services.check_addresses(dropoff["miles"], None)
                    except pricing.OutOfRange as exc:
                        dropoff_form.add_error(None, str(exc))
                if pickup:
                    try:
                        services.check_addresses(0, pickup["miles"])
                    except pricing.OutOfRange as exc:
                        pickup_form.add_error(None, str(exc))
                if not dropoff_form.errors and not pickup_form.errors:
                    _save(request, dropoff=dropoff, pickup=pickup)
                    return redirect("book_times")
            except maps.MapsUnavailable as exc:
                messages.error(request, str(exc))
    else:
        dropoff_form = AddressForm(prefix="dropoff", initial=draft.get("dropoff"))
        pickup_initial = draft.get("pickup") or ({"unknown": True} if "dropoff" in draft else None)
        pickup_form = PickupAddressForm(prefix="pickup", initial=pickup_initial)

    return _render(request, "addresses", "rentals/book/addresses.html", {
        "dropoff_form": dropoff_form, "pickup_form": pickup_form, "settings": settings,
    })


def times(request):
    if (r := _guard(request, "times")):
        return r
    draft = _draft(request)
    dropoff_date, pickup_date = _dates(draft)
    dropoff_windows = windows.open_windows(dropoff_date)
    pickup_windows = windows.open_windows(pickup_date)
    kwargs = {"dropoff_windows": dropoff_windows, "pickup_windows": pickup_windows}

    if request.method == "POST":
        form = TimesForm(request.POST, **kwargs)
        if form.is_valid():
            _save(request, dropoff_window_id=form.cleaned_data["dropoff_window"],
                  pickup_window_id=form.cleaned_data["pickup_window"])
            return redirect("book_contact")
    else:
        form = TimesForm(initial={"dropoff_window": draft.get("dropoff_window_id"),
                                  "pickup_window": draft.get("pickup_window_id")}, **kwargs)

    return _render(request, "times", "rentals/book/times.html", {
        "form": form, "dropoff_date": dropoff_date, "pickup_date": pickup_date,
        "no_times": not dropoff_windows or not pickup_windows,
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
    product = Product.objects.get(pk=draft["product_id"])
    dropoff_date, pickup_date = _dates(draft)
    pickup = draft.get("pickup")

    if request.method == "POST":
        try:
            booking = services.submit_booking(draft)
        except services.NotEnoughStock as exc:
            messages.error(request, f"{exc} Please change the number of bins or the dates.")
            return redirect("book_dates")
        except services.WindowFull as exc:
            messages.error(request, str(exc))
            return redirect("book_times")
        except pricing.OutOfRange as exc:
            messages.error(request, str(exc))
            return redirect("book_addresses")
        del request.session[SESSION_KEY]
        emails.booking_requested(booking)
        messages.success(request, "Request sent! We've emailed you a copy with a link back to this page.")
        return redirect(booking_url(booking))

    quote = services.quote([(product, draft["quantity"])], draft["weeks"],
                           pickup_miles=pickup["miles"] if pickup else None)
    windows_by_id = TimeWindow.objects.in_bulk([draft["dropoff_window_id"], draft["pickup_window_id"]])
    return _render(request, "review", "rentals/book/review.html", {
        "product": product, "quote": quote, "dropoff_date": dropoff_date, "pickup_date": pickup_date,
        "dropoff_window": windows_by_id.get(draft["dropoff_window_id"]),
        "pickup_window": windows_by_id.get(draft["pickup_window_id"]),
        "settings": BusinessSettings.load(),
    })


def start_over(request):
    request.session.pop(SESSION_KEY, None)
    return redirect(reverse("book_dates"))
