"""Pages for the card step, and the Stripe webhook endpoint."""

import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import links, payments
from .models import Booking, BusinessSettings, Charge

log = logging.getLogger(__name__)
CARD_STATUSES = [Booking.Status.AWAITING_CARD, Booking.Status.REQUESTED, Booking.Status.CONFIRMED]


def _booking(token):
    reference = links.read_token(token)
    booking = Booking.objects.filter(reference=reference).select_related("customer").first() if reference else None
    if booking is None:
        raise Http404
    return booking


def card(request, token):
    """Enter (or update) the card for a booking with Stripe's embedded Checkout."""
    booking = _booking(token)
    if not payments.enabled() or booking.status not in CARD_STATUSES:
        return redirect(links.booking_url(booking))
    if booking.status == Booking.Status.AWAITING_CARD and booking.hold_expires_at and \
            booking.hold_expires_at < timezone.now():
        messages.error(request, "Your hold on those totes ran out. Please book again.")
        return redirect("book_package")
    return render(request, "rentals/card.html", {
        "booking": booking,
        "price_lines": booking.price_lines.all(),
        "total": payments.booking_total(booking),
        "updating": booking.status != Booking.Status.AWAITING_CARD,
        "publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
        "session_url": reverse("card_session", args=[token]),
        "settings": BusinessSettings.load(),
    })


@require_POST
def card_session(request, token):
    """Create the Checkout Session and hand its client secret to Stripe.js."""
    booking = _booking(token)
    if booking.status not in CARD_STATUSES:
        return JsonResponse({"error": "This booking can't take a card right now."}, status=400)
    done = request.build_absolute_uri(reverse("card_done", args=[token])) + "?session_id={CHECKOUT_SESSION_ID}"
    try:
        secret = payments.start_card_setup(booking, done)
    except stripe.StripeError:
        log.exception("Could not start card setup for %s", booking.reference)
        return JsonResponse({"error": "We couldn't reach our payment provider. Please try again."}, status=502)
    return JsonResponse({"clientSecret": secret})


def card_done(request, token):
    """Stripe sends the renter back here after Checkout."""
    booking = _booking(token)
    session_id = request.GET.get("session_id", "")
    was_awaiting = booking.status == Booking.Status.AWAITING_CARD
    try:
        saved = bool(session_id) and payments.finish_card_setup(booking, session_id)
    except stripe.StripeError:
        log.exception("Could not finish card setup for %s", booking.reference)
        saved = False
    if not saved:
        messages.error(request, "Your card wasn't saved. Please try again.")
        return redirect("card", token=token)
    booking.refresh_from_db()
    if was_awaiting and booking.status == Booking.Status.CONFIRMED:
        messages.success(request, "You're booked! We've emailed you a confirmation with a link back to this page.")
    elif was_awaiting:
        messages.success(request, "Request sent! We've emailed you a copy with a link back to this page.")
    else:
        messages.success(request, "Thanks, your card is updated.")
    return redirect(links.booking_url(booking))


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Stripe's server-to-server notices. Only signed events are processed."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse(status=503)
    try:
        event = payments.client().construct_event(
            request.body, request.headers.get("Stripe-Signature", ""), settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        return HttpResponse(status=400)
    try:
        result = payments.handle_event(event)
    except stripe.StripeError:
        log.exception("Stripe call failed while handling %s", event.id)
        return HttpResponse(status=500)  # Stripe retries later
    return JsonResponse({"received": True, "result": result})


def booking_payment_context(booking):
    """Extra context for the booking page: card status and charges."""
    failed = booking.charges.filter(kind=Charge.Kind.RENTAL, status=Charge.Status.FAILED).exists()
    good = booking.charges.filter(kind=Charge.Kind.RENTAL,
                                  status__in=[Charge.Status.AUTHORIZED, Charge.Status.CAPTURED]).exists()
    return {
        "payments_on": payments.enabled(),
        "needs_card": booking.status == Booking.Status.AWAITING_CARD,
        "card_problem": failed and not good and booking.status == Booking.Status.CONFIRMED,
        "charges": booking.charges.exclude(status=Charge.Status.PENDING).order_by("created_at"),
        "card_url": reverse("card", args=[links.make_token(booking)]),
    }

