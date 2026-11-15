"""
Renter-facing tote pages.

Scanning a tote's QR code opens /q/<CODE>. What the visitor sees depends on
who they are:

* the renter (this browser has opened their private booking link) →
  a short form: what's inside, which room, fragile, open first;
* an owner who is logged in → that bin's page in the back office;
* anyone else (a mover, a neighbor, the next renter) → just "this is a
  Sprout Tote Co. tote", never the contents, name or address.

The renter's browser is remembered in its session when the booking link is
opened, so anyone the renter shares the link with can label totes too.
"""

from django import forms
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from . import bins
from .links import booking_url
from .models import Bin, BinContents, Booking, StockState

SESSION_KEY = "my_bookings"
ROOM_SUGGESTIONS = ["Kitchen", "Living room", "Dining room", "Bedroom 1", "Bedroom 2",
                    "Bedroom 3", "Kids' room", "Bathroom", "Office", "Basement", "Garage",
                    "Laundry", "Closet"]


def remember_booking(request, booking):
    """Called when a valid booking link is opened in this browser."""
    refs = request.session.get(SESSION_KEY, [])
    if booking.reference not in refs:
        request.session[SESSION_KEY] = (refs + [booking.reference])[-10:]


def renter_has(request, booking):
    return booking is not None and booking.reference in request.session.get(SESSION_KEY, [])


class ContentsForm(forms.ModelForm):
    class Meta:
        model = BinContents
        fields = ["contents", "room", "fragile", "open_first"]
        labels = {"contents": "What's inside?", "room": "Room at the new place",
                  "fragile": "Fragile", "open_first": "Open first"}
        widgets = {
            "contents": forms.Textarea(attrs={"rows": 4, "placeholder": "e.g. pots and pans, baking sheets"}),
            "room": forms.TextInput(attrs={"list": "room-list", "placeholder": "e.g. Kitchen"}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


def tote(request, code):
    """Landing page for a scanned (or typed) tote code."""
    clean = bins.normalize_code(code)
    if clean != code:
        return redirect("tote", code=clean) if clean else redirect("find_tote")
    b = Bin.objects.select_related("booking", "product").filter(code=clean).first()

    if request.user.is_authenticated and request.user.is_staff and b is not None:
        return redirect(reverse("ops_bin", args=[b.code]))

    booking = b.booking if b else None
    if b is None or not renter_has(request, booking):
        return render(request, "rentals/tote_public.html", {"code": clean, "known": b is not None})

    editable = b.status == StockState.OUT and booking.status == Booking.Status.OUT
    note = BinContents.objects.filter(booking=booking, bin=b).first()
    form = ContentsForm(request.POST or None, instance=note or BinContents(booking=booking, bin=b))
    if not editable:
        for field in form.fields.values():
            field.disabled = True
    if request.method == "POST" and editable and form.is_valid():
        form.save()
        messages.success(request, f"Saved tote {b.code}.")
        return redirect(booking_url(booking) + "#totes")

    used_rooms = set(BinContents.objects.filter(booking=booking).exclude(room="")
                     .values_list("room", flat=True))
    return render(request, "rentals/tote_edit.html", {
        "bin": b, "booking": booking, "form": form, "editable": editable,
        "rooms": sorted(used_rooms | set(ROOM_SUGGESTIONS)),
        "booking_link": booking_url(booking),
    })


def find_tote(request):
    """Type a code instead of scanning."""
    code = bins.normalize_code(request.GET.get("code", ""))
    if code:
        return redirect("tote", code=code)
    if "code" in request.GET:
        messages.error(request, "Codes are 3 letters or numbers, like K7Q.")
    return render(request, "rentals/tote_find.html")


def totes_for(booking):
    """Every tote on this rental, with the renter's notes, sorted by room then code."""
    notes = {n.bin_id: n for n in BinContents.objects.filter(booking=booking)}
    rows = Bin.objects.filter(Q(booking=booking) | Q(id__in=notes.keys())).distinct()
    out = [{"bin": b, "note": notes.get(b.id)} for b in rows]
    out.sort(key=lambda r: ((r["note"].room.lower() if r["note"] and r["note"].room else "~"), r["bin"].code))
    return out
