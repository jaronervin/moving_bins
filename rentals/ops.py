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

from . import bins, inventory
from .models import Bin, Booking, Product, StockState

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)

MODES = {"load": "Load for dropoff", "pickup": "Pickup"}


@staff_required
def index(request):
    today = timezone.localdate()
    to_load = (Booking.objects.filter(status=Booking.Status.CONFIRMED,
                                      dropoff_date__lte=today + timedelta(days=14))
               .select_related("customer").order_by("dropoff_date"))
    out = Booking.objects.filter(status=Booking.Status.OUT).select_related("customer").order_by("pickup_date")
    picked = Booking.objects.filter(status=Booking.Status.PICKED_UP).select_related("customer")
    products = Product.objects.filter(is_serialized=True)
    return render(request, "rentals/ops/index.html", {
        "to_load": [(b, bins.load_progress(b)) for b in to_load],
        "out": [(b, bins.loaded(b).count()) for b in out],
        "picked": [(b, bins.check_in_progress(b)) for b in picked],
        "stock": [(p, inventory.counts(p)) for p in products],
        "states": StockState,
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
