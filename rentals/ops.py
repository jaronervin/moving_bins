"""
Owner tools for tracked bins (staff login required):

  /ops/                       what to load, pick up and check in
  /ops/b/<ref>/load/          scan bins onto a dropoff, then mark it done
  /ops/b/<ref>/pickup/        scan bins back at pickup, then mark it done
  /ops/check-in/              scan cleaned bins: good / broken / needs lid
  /ops/bin/<code>/            one bin's history; fit a spare lid; mark found
  /ops/labels/                printable QR label sheets

The scanner pages use the phone's camera and call /ops/api/scan for each
code read, so the count updates without reloading.
"""

import json
from datetime import timedelta

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

import stripe

from . import bins, inventory, payments
from .models import Bin, Booking, BusinessSettings, Charge, Product, StockState

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)

MODES = {"load": "Load for dropoff", "pickup": "Pickup"}


def _system_status():
    """Which outside services are really switched on. Never shows a key."""
    from django.conf import settings as s

    stripe_key = s.STRIPE_SECRET_KEY or ""
    mode = "live" if stripe_key.startswith(("sk_live", "rk_live")) else "test"
    smtp = "smtp" in s.EMAIL_BACKEND
    return [
        ("Address checks", s.MAPS_BACKEND == "google",
         "Google" if s.MAPS_BACKEND == "google" else "Estimated from ZIP codes (GOOGLE_MAPS_API_KEY not set)"),
        ("Address suggestions", bool(s.GOOGLE_MAPS_BROWSER_KEY),
         "On" if s.GOOGLE_MAPS_BROWSER_KEY else "Off (GOOGLE_MAPS_BROWSER_KEY not set)"),
        ("Payments", bool(stripe_key), f"Stripe, {mode} mode" if stripe_key else "Off (STRIPE_SECRET_KEY not set)"),
        ("Stripe webhook", s.STRIPE_WEBHOOK_SECRET.startswith("whsec_"),
         "Set" if s.STRIPE_WEBHOOK_SECRET.startswith("whsec_") else "Not set"),
        ("Email", smtp and bool(s.EMAIL_HOST), f"Sending via {s.EMAIL_HOST}" if smtp and s.EMAIL_HOST
         else "Not sent: written to the server log"),
    ]


@staff_required
def index(request):
    today = timezone.localdate()
    to_load = (Booking.objects.filter(status=Booking.Status.CONFIRMED,
                                      dropoff_date__lte=today + timedelta(days=14))
               .select_related("customer").order_by("dropoff_date"))
    out = Booking.objects.filter(status=Booking.Status.OUT).select_related("customer").order_by("pickup_date")
    picked = Booking.objects.filter(status=Booking.Status.PICKED_UP).select_related("customer")
    reviewable = (Booking.objects.filter(status=Booking.Status.CONFIRMED, review_until__gt=timezone.now())
                  .select_related("customer", "dropoff_address", "pickup_address").order_by("review_until"))
    products = Product.objects.filter(is_serialized=True)
    return render(request, "rentals/ops/index.html", {
        "reviewable": reviewable,
        "to_load": [(b, bins.load_progress(b)) for b in to_load],
        "out": [(b, bins.loaded(b).count()) for b in out],
        "picked": [(b, bins.check_in_progress(b)) for b in picked],
        "stock": [(p, inventory.counts(p)) for p in products],
        "buy": [n for n in (inventory.purchase_needs(p) for p in products) if n],
        "payment_problems": (Charge.objects.filter(status=Charge.Status.FAILED,
                                                    booking__status__in=[Booking.Status.CONFIRMED, Booking.Status.OUT,
                                                                         Booking.Status.PICKED_UP, Booking.Status.CLOSED])
                             .select_related("booking__customer").order_by("-created_at")[:10]),
        "states": StockState,
        "system": _system_status(),
        "settings": BusinessSettings.load(),
    })


@staff_required
def day(request):
    """One day's dropoffs and pickups in driving order, with a route link."""
    from datetime import date as date_cls

    from . import routes

    try:
        day = date_cls.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    stops = routes.stops_for(day)
    return render(request, "rentals/ops/day.html", {
        "day": day, "prev": day - timedelta(days=1), "next": day + timedelta(days=1),
        "stops": stops, "links": routes.maps_links(stops),
        "dropoffs": sum(1 for s in stops if s.kind == "dropoff"),
        "pickups": sum(1 for s in stops if s.kind == "pickup"),
        "missing": [s for s in stops if not s.address],
    })


def _booking(reference):
    return get_object_or_404(Booking.objects.select_related("customer"), reference=reference)


@staff_required
def scanner(request, reference, mode):
    if mode not in MODES:
        return redirect("ops_index")
    booking = _booking(reference)
    if request.method == "POST" and request.POST.get("action") == "finish":
        try:
            if mode == "load":
                bins.complete_dropoff(booking, request.user)
                messages.success(request, f"Dropoff for {booking.reference} marked done.")
                _charge_at_dropoff(request, booking)
            else:
                unscanned = bins.complete_pickup(booking, request.user)
                note = f" Scan the {len(unscanned)} remaining totes at check-in." if unscanned else ""
                messages.success(request, f"Pickup for {booking.reference} marked done.{note}")
            return redirect("ops_index")
        except bins.BinError as exc:
            messages.error(request, str(exc))
    return render(request, "rentals/ops/scanner.html", {
        "booking": booking, "mode": mode, "title": MODES[mode], **_scan_state(booking, mode),
    })


def _charge_at_dropoff(request, booking):
    """Capture the hold (or charge the card) once the totes are delivered."""
    if not payments.enabled():
        return
    try:
        results = payments.capture_at_dropoff(booking)
    except stripe.StripeError as exc:
        messages.error(request, f"Charging {booking.reference} failed: {exc.user_message or exc}. "
                                "Check the booking in the Stripe Dashboard.")
        return
    failed = [c for c in results if c.status != Charge.Status.CAPTURED]
    if failed:
        messages.error(request, f"Charge for {booking.reference} didn't go through: "
                                f"{failed[0].failure_message or 'declined'}. Contact the renter.")
    else:
        total = sum(c.captured_amount for c in results)
        messages.success(request, f"Charged ${total} to {booking.customer.name}'s card.")


def _scan_state(booking, mode):
    if mode == "load":
        progress = bins.load_progress(booking)
        done, total = sum(p[1] for p in progress), sum(p[2] for p in progress)
        scanned = list(bins.loaded(booking).values_list("code", flat=True))
    else:
        done, total = bins.pickup_progress(booking)
        scanned = list(Bin.objects.filter(booking=booking, status=StockState.AWAITING_CHECK_IN)
                       .values_list("code", flat=True))
    return {"done": done, "total": total, "scanned": sorted(scanned)}


@staff_required
def check_in(request):
    from django.db.models import Q

    waiting = (Bin.objects.filter(Q(status=StockState.AWAITING_CHECK_IN)
                                  | Q(status=StockState.OUT, booking__status=Booking.Status.PICKED_UP))
               .select_related("booking").order_by("booking__reference", "code"))
    ready_to_finish = [
        (b, bins.check_in_progress(b))
        for b in Booking.objects.filter(status=Booking.Status.PICKED_UP).select_related("customer")
    ]
    return render(request, "rentals/ops/check_in.html", {
        "waiting": waiting, "bookings": ready_to_finish, "mode": "check_in",
    })


@staff_required
@require_POST
def finish_check_in_view(request, reference):
    booking = _booking(reference)
    try:
        summaries = bins.finish_check_in(booking, request.user)
        damage = sum(s.damage_amount for s in summaries)
        if damage and payments.enabled():
            c = payments.charge(booking, Charge.Kind.DAMAGE, damage,
                                f"Lost/damaged totes, booking {booking.reference}")
            if c is None or c.status != Charge.Status.CAPTURED:
                messages.error(request, f"Damage charge of ${damage} didn't go through"
                                        f"{': ' + c.failure_message if c and c.failure_message else ''}.")
        missing = sum(s.missing for s in summaries)
        msg = f"{booking.reference} closed."
        if missing:
            msg += f" {missing} not returned."
        if damage:
            msg += f" Damage to charge: ${damage}."
        messages.success(request, msg)
    except bins.BinError as exc:
        messages.error(request, str(exc))
    return redirect("ops_check_in")


@staff_required
@require_POST
def scan_api(request):
    """One scan from a scanner page. Body: {mode, reference?, code, undo?, outcome?, lid_salvaged?}."""
    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "message": "Bad request."}, status=400)
    mode, code, undo = data.get("mode"), data.get("code", ""), bool(data.get("undo"))
    try:
        if mode == "check_in":
            b = bins.check_in_bin(code, data.get("outcome", "good"),
                                  lid_salvaged=bool(data.get("lid_salvaged")), user=request.user)
            label = {"ready": "good", "retired": "broken, retired", "out_of_service": "needs a lid"}[b.status]
            if b.events.filter(kind="activated").exists() and b.moves == 0:
                label = "new tote, now in service"
            return JsonResponse({"ok": True, "code": b.code, "message": f"{b.code}: {label}"})
        booking = Booking.objects.get(reference=data.get("reference"))
        actions = {("load", False): bins.load_bin, ("load", True): bins.unload_bin,
                   ("pickup", False): bins.pickup_bin, ("pickup", True): bins.undo_pickup_bin}
        if (mode, undo) not in actions:
            return JsonResponse({"ok": False, "message": "Unknown scan mode."}, status=400)
        b = actions[(mode, undo)](booking, code, request.user)
        state = _scan_state(booking, mode)
        verb = "removed" if undo else "added"
        return JsonResponse({"ok": True, "code": b.code, "message": f"{b.code} {verb}", **state})
    except Booking.DoesNotExist:
        return JsonResponse({"ok": False, "message": "Unknown booking."}, status=404)
    except bins.BinError as exc:
        return JsonResponse({"ok": False, "message": str(exc)})


@staff_required
def bin_detail(request, code):
    b = get_object_or_404(Bin.objects.select_related("product", "booking"), code=code.upper())
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "repair":
                bins.repair_with_spare_lid(b.code, request.user)
                messages.success(request, f"{b.code} has a new lid and is back on the shelf.")
            elif action == "activate":
                bins.activate_bin(b.code, request.user)
                messages.success(request, f"{b.code} is on a tote and in service.")
            elif action == "found":
                bins.found_bin(b.code, request.user)
                messages.success(request, f"{b.code} is back; check it in after cleaning.")
        except bins.BinError as exc:
            messages.error(request, str(exc))
        return redirect("ops_bin", code=b.code)
    return render(request, "rentals/ops/bin.html", {
        "bin": b, "events": b.events.select_related("booking", "by")[:50],
    })


def qr_svg(code):
    """Inline SVG of the QR code for a bin's label URL."""
    qr = segno.make_qr(settings.QR_BASE_URL + code, error="m", boost_error=True)
    return mark_safe(qr.svg_inline(scale=1, border=2, omitsize=True, dark="#000", light="#fff"))


@staff_required
def labels(request):
    """Printable label sheet. ?codes=K7Q,ABC or ?status=ready; &copies=2 for two per bin."""
    qs = Bin.objects.all()
    if request.GET.get("codes"):
        qs = qs.filter(code__in=[bins.normalize_code(c) for c in request.GET["codes"].split(",")])
    elif request.GET.get("ids"):
        qs = qs.filter(id__in=[int(i) for i in request.GET["ids"].split(",") if i.isdigit()])
    else:
        qs = qs.filter(status=request.GET.get("status", StockState.READY))
    copies = max(1, min(4, int(request.GET.get("copies", 2) or 2)))
    items = [{"code": b.code, "svg": qr_svg(b.code)} for b in qs.order_by("code") for _ in range(copies)]
    return render(request, "rentals/ops/labels.html", {
        "items": items, "copies": copies, "domain": settings.QR_BASE_URL.split("//")[-1].split("/")[0].lower(),
    })


@staff_required
@require_POST
def decline(request, reference):
    """Decline an auto-confirmed booking, or ask the renter for a change, within the review window."""
    from . import emails, services

    booking = _booking(reference)
    message = request.POST.get("reason", "").strip()
    try:
        if request.POST.get("action") == "change":
            services.request_change(booking, message)
            emails.change_requested(booking)
            messages.success(request, f"Asked {booking.customer.name} for a change to {booking.reference}.")
        else:
            services.decline_booking(booking, message)
            if payments.enabled():
                payments.release(booking)
            emails.booking_declined(booking)
            messages.success(request, f"{booking.reference} declined; {booking.customer.name} has been emailed.")
    except services.CannotDecline as exc:
        messages.error(request, str(exc))
    return redirect("ops_index")
