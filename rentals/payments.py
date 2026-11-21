"""
Everything that talks to Stripe.

How money moves for a booking (see SPEC.md §6.1):

1. **Booking:** the renter enters a card in Stripe's embedded Checkout
   (setup mode). Nothing is charged; the card is saved to a Stripe Customer.
2. **About a day before dropoff:** an *authorization* (a pending hold) for the
   booking total: a PaymentIntent with ``capture_method=manual``, created
   off-session with the saved card. If it fails, the renter is emailed to
   update their card and the owners are told.
3. **Dropoff marked done:** the hold is *captured* (the actual charge). If the
   total went down since, we capture less; if it went up, we capture the hold
   and charge the difference.
4. **Later charges**, off-session with the saved card: a late cancellation
   (50% of the rental), extra weeks when totes aren't ready, lost or damaged
   totes.

Every attempt is recorded as a ``Charge`` row. Webhooks (``handle_event``)
keep those rows in step with Stripe.

Sales tax: Stripe Tax is not switched on yet. It needs a Maryland
registration in Stripe and product tax codes first; until then ``tax`` is 0.
"""

import logging
import secrets
import string
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import pricing
from .models import Booking, BusinessSettings, Charge

log = logging.getLogger(__name__)


class PaymentError(Exception):
    """A Stripe call failed in a way the owner or renter should hear about."""


def enabled():
    return bool(settings.STRIPE_SECRET_KEY)


def client():
    if settings.STRIPE_CA_BUNDLE:
        stripe.ca_bundle_path = settings.STRIPE_CA_BUNDLE
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY, max_network_retries=2)


def cents(amount):
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dollars(amount_cents):
    return (Decimal(amount_cents) / 100).quantize(Decimal("0.01"))


def booking_total(booking):
    """What the renter owes for the booking itself (price lines; tax added later)."""
    return sum((line.amount for line in booking.price_lines.all()), pricing.ZERO)


def _tag():
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))


def _payment_method_configuration():
    pmc = settings.STRIPE_PAYMENT_METHOD_CONFIGURATION
    return {"payment_method_configuration": pmc} if pmc else {}


# ---------------------------------------------------------------------------
# 1. Saving the card
# ---------------------------------------------------------------------------


def ensure_customer(customer):
    """The renter's Stripe Customer, created the first time."""
    if not customer.stripe_customer_id:
        c = client().v1.customers.create(params={
            "email": customer.email, "name": customer.name, "phone": customer.phone or None,
            "metadata": {"customer_id": str(customer.pk)},
        })
        customer.stripe_customer_id = c.id
        customer.save(update_fields=["stripe_customer_id"])
    return customer.stripe_customer_id


def start_card_setup(booking, return_url, updating=False):
    """Create an embedded Checkout Session (setup mode) and return its client secret.

    ``return_url`` must contain ``{CHECKOUT_SESSION_ID}``.
    """
    customer_id = ensure_customer(booking.customer)
    session = client().v1.checkout.sessions.create(params={
        "mode": "setup",
        "ui_mode": "embedded_page",
        "currency": "usd",
        "customer": customer_id,
        "return_url": return_url,
        "setup_intent_data": {
            "description": f"Card for Sprout Tote booking {booking.reference}",
            "metadata": {"booking": booking.reference},
        },
        "metadata": {"booking": booking.reference},
        # Full billing address so the bank checks it (AVS): evidence for the signature.
        "billing_address_collection": "required",
        # Setup mode's button always says "Save" (Stripe doesn't allow another
        # label here), so say what it does just above it.
        "custom_text": {"submit": {"message": (
            "Saving replaces the card on your booking. Nothing is charged now." if updating else
            "Saving your card completes your booking. Nothing is charged now; we hold the total "
            "the day before dropoff and charge it when your totes arrive.")}},
        "integration_identifier": f"sprouttote-card-setup-{_tag()}",
        **_payment_method_configuration(),
    })
    booking.stripe_checkout_session_id = session.id
    booking.save(update_fields=["stripe_checkout_session_id", "updated_at"])
    return session.client_secret


SAVED, NAME_MISMATCH = "saved", "name_mismatch"


def _meta(obj, key):
    """A metadata value from a Stripe object. Stripe's SDK objects aren't dicts
    (no .get), so convert first."""
    metadata = getattr(obj, "metadata", None)
    if metadata is None:
        return None
    if hasattr(metadata, "to_dict"):
        metadata = metadata.to_dict()
    return metadata.get(key)


@transaction.atomic
def finish_card_setup(booking, session_id):
    """Record the saved card once Checkout is complete.

    Safe to call twice (the return page and the webhook both call it). Returns
    SAVED, NAME_MISMATCH (the cardholder name doesn't match the signature, so
    the card isn't used and the booking waits), or False (not complete).
    """
    from . import signing

    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    session = client().v1.checkout.sessions.retrieve(
        session_id, params={"expand": ["setup_intent.payment_method"]})
    if _meta(session, "booking") != booking.reference or session.status != "complete":
        return False
    setup_intent = session.setup_intent
    if setup_intent.status != "succeeded":
        return False
    payment_method = setup_intent.payment_method
    if isinstance(payment_method, str):
        payment_method = client().v1.payment_methods.retrieve(payment_method)

    sig = signing.record_card_evidence(booking, payment_method)
    if sig is not None and sig.name_matches_card is False:
        return NAME_MISMATCH

    already = booking.stripe_payment_method_id == payment_method.id
    booking.stripe_payment_method_id = payment_method.id
    booking.save(update_fields=["stripe_payment_method_id", "updated_at"])
    if not already:
        client().v1.customers.update(booking.customer.stripe_customer_id, params={
            "invoice_settings": {"default_payment_method": payment_method.id}})
    if booking.status == Booking.Status.AWAITING_CARD:
        from . import emails, services

        services.card_saved(booking)
        emails.booking_requested(booking)
    elif booking.status == Booking.Status.CONFIRMED and due_for_authorization().filter(pk=booking.pk).exists():
        authorize(booking)  # a new card after a failed hold: try again now
    return SAVED


def retry_after_resign(booking):
    """After re-signing, use the card from the last Checkout if the names now match."""
    if booking.stripe_checkout_session_id:
        return finish_card_setup(booking, booking.stripe_checkout_session_id)
    return False


# ---------------------------------------------------------------------------
# 2-4. Holds, captures and later charges
# ---------------------------------------------------------------------------

_STATUS = {
    "requires_capture": Charge.Status.AUTHORIZED,
    "succeeded": Charge.Status.CAPTURED,
    "canceled": Charge.Status.RELEASED,
}


def _apply(charge, intent):
    charge.stripe_payment_intent_id = intent.id
    charge.status = _STATUS.get(intent.status, Charge.Status.FAILED if intent.status in (
        "requires_payment_method", "requires_action") else Charge.Status.PENDING)
    charge.captured_amount = dollars(intent.amount_received or 0)
    if charge.status == Charge.Status.FAILED and intent.last_payment_error:
        charge.failure_message = (intent.last_payment_error.message or "")[:255]
    charge.save()
    return charge


def _off_session(booking, charge, *, capture_manually=False, description=""):
    """Create and confirm a PaymentIntent with the saved card for ``charge``."""
    if not booking.stripe_payment_method_id:
        charge.status, charge.failure_message = Charge.Status.FAILED, "No saved card."
        charge.save()
        return charge
    params = {
        "amount": cents(charge.total),
        "currency": "usd",
        "customer": booking.customer.stripe_customer_id,
        "payment_method": booking.stripe_payment_method_id,
        "off_session": True,
        "confirm": True,
        "description": description or charge.description,
        "receipt_email": booking.customer.email,  # Stripe emails a receipt once charged
        "metadata": {"booking": booking.reference, "charge": str(charge.pk), "kind": charge.kind},
    }
    if capture_manually:
        params["capture_method"] = "manual"
    params = {k: v for k, v in params.items() if v is not None}
    try:
        intent = client().v1.payment_intents.create(
            params=params, options={"idempotency_key": f"charge-{booking.reference}-{charge.pk}"})
    except stripe.CardError as exc:
        charge.status = Charge.Status.FAILED
        charge.failure_message = (exc.user_message or str(exc))[:255]
        err_pi = getattr(exc.error, "payment_intent", None) if exc.error else None
        if err_pi:
            charge.stripe_payment_intent_id = err_pi.id
        charge.save()
        return charge
    return _apply(charge, intent)


def _rental_hold(booking):
    return booking.charges.filter(kind=Charge.Kind.RENTAL, status=Charge.Status.AUTHORIZED).first()


def authorize(booking):
    """Place the pending hold for the booking total. Returns the Charge."""
    existing = booking.charges.filter(kind=Charge.Kind.RENTAL,
                                      status__in=[Charge.Status.AUTHORIZED, Charge.Status.CAPTURED]).first()
    if existing:
        return existing
    charge = Charge.objects.create(booking=booking, kind=Charge.Kind.RENTAL, amount=booking_total(booking),
                                   description=f"Sprout Tote rental {booking.reference}")
    return _off_session(booking, charge, capture_manually=True)


def due_for_authorization(now=None):
    """Confirmed bookings whose dropoff is close enough to place the hold."""
    now = now or timezone.now()
    hours = BusinessSettings.load().authorize_hours_before_dropoff
    if not hours:
        return Booking.objects.none()
    last_day = timezone.localdate(now) + timedelta(days=max(1, -(-hours // 24)))
    held = Charge.objects.filter(kind=Charge.Kind.RENTAL,
                                 status__in=[Charge.Status.AUTHORIZED, Charge.Status.CAPTURED])
    return (Booking.objects.filter(status=Booking.Status.CONFIRMED, dropoff_date__lte=last_day)
            .exclude(pk__in=held.values("booking")).select_related("customer"))


def authorize_due(now=None):
    """Daily task: place holds for upcoming dropoffs. Returns (held, failed) lists."""
    from . import emails

    held, failed = [], []
    for booking in due_for_authorization(now):
        charge = authorize(booking)
        if charge.status == Charge.Status.AUTHORIZED:
            held.append(booking)
        else:
            failed.append(booking)
            emails.card_problem(booking, charge)
    return held, failed


def capture_at_dropoff(booking):
    """Charge for the booking when the totes are delivered. Returns a list of Charges."""
    total = booking_total(booking)
    hold = _rental_hold(booking)
    if hold is None:  # no hold (e.g. booked within a day): charge now
        charge = Charge.objects.create(booking=booking, kind=Charge.Kind.RENTAL, amount=total,
                                       description=f"Sprout Tote rental {booking.reference}")
        return [_off_session(booking, charge)]
    to_capture = min(total, hold.total)
    intent = client().v1.payment_intents.capture(
        hold.stripe_payment_intent_id, params={"amount_to_capture": cents(to_capture)},
        options={"idempotency_key": f"capture-{hold.stripe_payment_intent_id}"})
    _apply(hold, intent)
    results = [hold]
    if total > hold.total:  # the booking grew after the hold: charge the difference
        extra = Charge.objects.create(booking=booking, kind=Charge.Kind.RENTAL, amount=total - hold.total,
                                      description=f"Sprout Tote rental {booking.reference} (changes)")
        results.append(_off_session(booking, extra))
    return results


def release(booking):
    """Cancel any pending hold (booking declined or cancelled in time)."""
    hold = _rental_hold(booking)
    if hold is not None:
        intent = client().v1.payment_intents.cancel(hold.stripe_payment_intent_id)
        _apply(hold, intent)
    return hold


def charge(booking, kind, amount, description):
    """A later off-session charge (renewal week, damage). Returns the Charge."""
    if Decimal(amount) <= 0:
        return None
    c = Charge.objects.create(booking=booking, kind=kind, amount=Decimal(amount), description=description)
    return _off_session(booking, c)


def charge_late_cancel(booking):
    """Late cancellation: a percent of the rental, taken from the hold if there is one."""
    rental = sum((l.amount for l in booking.price_lines.filter(kind__in=["package", "addon", "rental"])),
                 pricing.ZERO)
    amount = pricing.late_cancel_charge(rental, BusinessSettings.load().late_cancel_charge_percent)
    hold = _rental_hold(booking)
    if hold is not None and amount <= hold.total:
        intent = client().v1.payment_intents.capture(
            hold.stripe_payment_intent_id, params={"amount_to_capture": cents(amount)},
            options={"idempotency_key": f"late-cancel-{hold.stripe_payment_intent_id}"})
        hold.kind = Charge.Kind.LATE_CANCEL
        hold.description = f"Late cancellation {booking.reference}"
        return _apply(hold, intent)
    release(booking)
    return charge(booking, Charge.Kind.LATE_CANCEL, amount, f"Late cancellation {booking.reference}")


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def handle_event(event):
    """Keep our records in step with Stripe. Returns a short description."""
    from . import emails

    obj = event.data.object
    kind = event.type
    if kind == "checkout.session.completed" and obj.mode == "setup":
        booking = Booking.objects.filter(reference=_meta(obj, "booking")).first()
        if booking:
            finish_card_setup(booking, obj.id)
            return f"card saved for {booking.reference}"
    elif kind.startswith("payment_intent."):
        charge = Charge.objects.filter(stripe_payment_intent_id=obj.id).first()
        if charge is None and _meta(obj, "charge"):
            charge = Charge.objects.filter(pk=_meta(obj, "charge")).first()
        if charge:
            _apply(charge, obj)
            return f"charge {charge.pk} is {charge.status}"
    elif kind == "charge.dispute.created":
        emails.owner_alert(f"Stripe dispute opened: {obj.id}",
                           f"A customer disputed a charge ({obj.charge}, ${dollars(obj.amount)}). "
                           "Respond in the Stripe Dashboard before the deadline.")
        return "dispute alert sent"
    return "ignored"
