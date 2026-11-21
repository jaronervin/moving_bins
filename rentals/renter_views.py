"""Renter self-service from the private booking link: cancel, change, add a week.

All three are confirm-then-act pages (no JavaScript dialogs). The rules live
in rentals.services (cancel_booking, change_booking, add_week).
"""

from datetime import timedelta

from django.contrib import messages
from django.shortcuts import redirect, render

from . import emails, payments, pricing, services, windows
from .forms import ExtrasForm, PackageForm
from .links import booking_url
from .models import BusinessSettings, Package, Product
from .payment_views import _booking


def cancel(request, token):
    booking = _booking(token)
    if booking.status not in services.CANCELLABLE_STATUSES:
        messages.error(request, "This booking can't be cancelled online. Please contact us.")
        return redirect(booking_url(booking))
    fee = services.cancel_fee(booking)
    if request.method == "POST":
        try:
            late = services.cancel_booking(booking)
        except services.CannotCancel as exc:
            messages.error(request, str(exc))
            return redirect(booking_url(booking))
        booking.refresh_from_db()
        emails.booking_cancelled(booking, late)
        messages.success(request, "Your booking is cancelled. We've emailed you a confirmation.")
        return redirect(booking_url(booking))
    return render(request, "rentals/cancel.html", {
        "booking": booking, "fee": fee, "free_until": services.free_cancel_until(booking),
        "settings": BusinessSettings.load(), "token": token,
    })


class _Preview(Exception):
    def __init__(self, quote):
        self.quote = quote


def change(request, token):
    booking = _booking(token)
    preview = None
    s = BusinessSettings.load()
    try:
        if booking.status not in services.CHANGEABLE_STATUSES:
            raise services.CannotChange("This booking can't be changed online anymore. Please contact us.")
        current = services.order_from_booking(booking)
    except services.CannotChange as exc:
        messages.error(request, str(exc))
        return redirect(booking_url(booking))
    products = Product.objects.filter(active=True, kind__in=[Product.Kind.RENTAL, Product.Kind.SALE])
    package_form = PackageForm(request.POST or None, min_notice_days=s.min_notice_days, min_weeks=s.min_weeks,
                               max_weeks=s.max_weeks, initial={
                                   "package": current.package.pk, "weeks": current.weeks,
                                   "dropoff_date": booking.dropoff_date})
    extras_form = ExtrasForm(request.POST or None, products=products, initial={
        "addon_totes": current.addon_totes, **{f"x_{p.pk}": q for p, q in current.extras.items()}})
    if request.method == "POST" and package_form.is_valid() and extras_form.is_valid():
        d = package_form.cleaned_data
        extras = {p: int(q) for p in products if (q := extras_form.cleaned_data.get(f"x_{p.pk}"))}
        order = services.Order(package=d["package"], weeks=d["weeks"],
                               addon_totes=extras_form.cleaned_data.get("addon_totes") or 0, extras=extras)
        try:
            if request.POST.get("confirm") != "1":  # first show the new price
                preview = services.quote(order, booking.dropoff_miles, booking.pickup_miles)
                raise _Preview(preview)
            services.change_booking(booking, order, d["dropoff_date"])
        except _Preview as p:
            preview = p.quote
        except services.NotEnoughStock as exc:
            package_form.add_error(None, services.availability_message(exc.product, exc.wanted))
        except (services.WindowFull, services.CannotChange, pricing.PricingError) as exc:
            package_form.add_error(None, str(exc))
        else:
            booking.refresh_from_db()
            emails.booking_changed(booking)
            messages.success(request, "Your booking is updated. We've emailed you the new details.")
            return redirect(booking_url(booking))
    return render(request, "rentals/change.html", {
        "booking": booking, "form": package_form, "extras_form": extras_form,
        "packages": Package.objects.filter(active=True).prefetch_related("items__product"),
        "rentals": extras_form.rows(Product.Kind.RENTAL), "sales": extras_form.rows(Product.Kind.SALE),
        "tote": current.package.tote_product, "settings": s, "token": token,
        "until": services.changes_allowed_until(booking), "preview": preview,
        "current_total": sum((l.amount for l in booking.price_lines.all()), pricing.ZERO),
        "open_dates": windows.next_open_dates(package_form.earliest),
    })


def add_week(request, token):
    booking = _booking(token)
    if request.method == "POST":
        try:
            booking, charge = services.add_week(booking)
        except (services.CannotExtend, services.WindowFull, services.NotEnoughStock) as exc:
            message = (services.availability_message(exc.product, exc.wanted)
                       if isinstance(exc, services.NotEnoughStock) else str(exc))
            messages.error(request, message)
            return redirect(booking_url(booking))
        emails.booking_extended(booking, charge)
        messages.success(request, f"Done! Your pickup is now {booking.pickup_date:%A, %B %-d}.")
        return redirect(booking_url(booking))
    return render(request, "rentals/add_week.html", {
        "booking": booking, "settings": BusinessSettings.load(), "token": token,
        "new_pickup": booking.pickup_date + timedelta(days=7),
        "payments_on": payments.enabled(),
    })
