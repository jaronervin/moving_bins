"""Payments.

Two kinds of tests:

* ``PaymentFlowTests`` simulate Stripe (fast, offline) to check our own logic:
  when we hold, capture, release and charge, and how bookings move.
* ``LiveStripeTests`` call your real Stripe **test-mode** account. They only run
  when STRIPE_LIVE_TESTS=1 and a test secret key is configured:

      STRIPE_LIVE_TESTS=1 STRIPE_SECRET_KEY=sk_test_... python manage.py test rentals.tests.test_payments
"""

import os
import unittest
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest import mock

import stripe
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from rentals import links, payments, services
from rentals.models import Booking, Charge, PriceLine

from .helpers import make_booking
from .test_booking_flow import FlowTestCase


def intent(id="pi_1", status="requires_capture", amount=17700, received=0, error=None):
    return NS(id=id, status=status, amount=amount, amount_received=received, last_payment_error=error,
              metadata={})


def booking_with_card(total=D("177.00"), status=Booking.Status.CONFIRMED, days=1):
    b = make_booking(dropoff=timezone.localdate() + timedelta(days=days))
    b.status = status
    b.stripe_payment_method_id = "pm_1"
    b.save()
    b.customer.stripe_customer_id = "cus_1"
    b.customer.save()
    PriceLine.objects.create(booking=b, kind="package", description="2BR package, 2 weeks", unit_price=total,
                             amount=total)
    return b


@override_settings(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_fake",
                   OWNER_EMAILS=["owner@example.com"])
class PaymentFlowTests(TestCase):
    def setUp(self):
        self.stripe = mock.MagicMock()
        patcher = mock.patch.object(payments, "client", return_value=self.stripe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_authorize_places_a_manual_capture_hold(self):
        b = booking_with_card()
        self.stripe.v1.payment_intents.create.return_value = intent()
        charge = payments.authorize(b)
        self.assertEqual(charge.status, Charge.Status.AUTHORIZED)
        params = self.stripe.v1.payment_intents.create.call_args.kwargs["params"]
        self.assertEqual((params["amount"], params["capture_method"], params["off_session"]), (17700, "manual", True))
        self.assertNotIn("payment_method_types", params)  # dynamic payment methods, per Stripe guidance
        self.assertEqual(payments.authorize(b).pk, charge.pk)  # never holds twice
        self.assertEqual(self.stripe.v1.payment_intents.create.call_count, 1)

    def test_authorize_due_picks_upcoming_confirmed_bookings(self):
        soon = booking_with_card(days=1)
        later = booking_with_card(days=5)
        self.stripe.v1.payment_intents.create.return_value = intent()
        held, failed = payments.authorize_due()
        self.assertEqual((held, failed), ([soon], []))
        self.assertFalse(later.charges.exists())

    def test_failed_hold_emails_renter_and_owner(self):
        b = booking_with_card()
        err = stripe.CardError("Your card was declined.", None, "card_declined")
        self.stripe.v1.payment_intents.create.side_effect = err
        held, failed = payments.authorize_due()
        self.assertEqual(failed, [b])
        self.assertEqual(b.charges.get().status, Charge.Status.FAILED)
        self.assertIn("Action needed", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[1].to, ["owner@example.com"])
        # The booking page asks them to update their card.
        self.assertContains(self.client.get(links.booking_url(b)), "Please update your card")

    def test_capture_less_when_booking_shrank(self):
        b = booking_with_card()
        self.stripe.v1.payment_intents.create.return_value = intent()
        payments.authorize(b)
        b.price_lines.update(amount=D("150.00"))
        self.stripe.v1.payment_intents.capture.return_value = intent(status="succeeded", received=15000)
        [hold] = payments.capture_at_dropoff(b)
        self.assertEqual(self.stripe.v1.payment_intents.capture.call_args.kwargs["params"],
                         {"amount_to_capture": 15000})
        self.assertEqual((hold.status, hold.captured_amount), (Charge.Status.CAPTURED, D("150.00")))

    def test_capture_plus_extra_when_booking_grew(self):
        b = booking_with_card()
        self.stripe.v1.payment_intents.create.return_value = intent()
        payments.authorize(b)
        PriceLine.objects.create(booking=b, kind="rental", description="Dolly", unit_price=D("10"), amount=D("10"))
        self.stripe.v1.payment_intents.capture.return_value = intent(status="succeeded", received=17700)
        self.stripe.v1.payment_intents.create.return_value = intent(id="pi_2", status="succeeded", received=1000)
        hold, extra = payments.capture_at_dropoff(b)
        self.assertEqual((hold.captured_amount, extra.captured_amount), (D("177.00"), D("10.00")))

    def test_no_hold_charges_directly(self):
        b = booking_with_card()
        self.stripe.v1.payment_intents.create.return_value = intent(status="succeeded", received=17700)
        [c] = payments.capture_at_dropoff(b)
        self.assertEqual(c.status, Charge.Status.CAPTURED)
        self.assertNotIn("capture_method", self.stripe.v1.payment_intents.create.call_args.kwargs["params"])

    def test_late_cancel_takes_half_from_the_hold(self):
        b = booking_with_card()
        PriceLine.objects.create(booking=b, kind="sale", description="Packing kit", unit_price=D("35"), amount=D("35"))
        self.stripe.v1.payment_intents.create.return_value = intent(amount=21200)
        payments.authorize(b)
        self.stripe.v1.payment_intents.capture.return_value = intent(status="succeeded", received=8850)
        c = payments.charge_late_cancel(b)
        # 50% of the $177 rental; the packing kit isn't part of it.
        self.assertEqual(self.stripe.v1.payment_intents.capture.call_args.kwargs["params"],
                         {"amount_to_capture": 8850})
        self.assertEqual((c.kind, c.captured_amount), (Charge.Kind.LATE_CANCEL, D("88.50")))

    def test_release_cancels_the_hold(self):
        b = booking_with_card()
        self.stripe.v1.payment_intents.create.return_value = intent()
        payments.authorize(b)
        self.stripe.v1.payment_intents.cancel.return_value = intent(status="canceled")
        self.assertEqual(payments.release(b).status, Charge.Status.RELEASED)

    def test_webhook_rejects_unsigned_events(self):
        self.stripe.construct_event.side_effect = stripe.SignatureVerificationError("bad", "sig")
        r = self.client.post("/stripe/webhook/", b"{}", content_type="application/json",
                             HTTP_STRIPE_SIGNATURE="t=1,v1=bad")
        self.assertEqual(r.status_code, 400)

    def test_webhook_updates_charge(self):
        b = booking_with_card()
        self.stripe.v1.payment_intents.create.return_value = intent()
        charge = payments.authorize(b)
        event = NS(id="evt_1", type="payment_intent.canceled", data=NS(object=intent(status="canceled")))
        self.stripe.construct_event.return_value = event
        r = self.client.post("/stripe/webhook/", b"{}", content_type="application/json",
                             HTTP_STRIPE_SIGNATURE="t=1,v1=ok")
        self.assertEqual(r.status_code, 200)
        charge.refresh_from_db()
        self.assertEqual(charge.status, Charge.Status.RELEASED)


@override_settings(STRIPE_SECRET_KEY="sk_test_fake", MAPS_BACKEND="fake", OWNER_EMAILS=["owner@example.com"])
class CardStepFlowTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.stripe = mock.MagicMock()
        patcher = mock.patch.object(payments, "client", return_value=self.stripe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_booking_waits_for_card_then_confirms(self):
        r = self.walk()
        booking = Booking.objects.get()
        self.assertEqual(booking.status, Booking.Status.AWAITING_CARD)
        card_url = f"/b/{links.make_token(booking)}/card/"
        self.assertTrue(r["Location"].endswith("/card/"))
        self.assertEqual(len(mail.outbox), 0)  # no "you're booked" until the card is saved

        self.stripe.v1.customers.create.return_value = NS(id="cus_9")
        self.stripe.v1.checkout.sessions.create.return_value = NS(id="cs_9", client_secret="cs_secret")
        page = self.client.get(card_url)
        self.assertContains(page, "js.stripe.com/endive/stripe.js")
        r = self.client.post(card_url + "session")
        self.assertEqual(r.json(), {"clientSecret": "cs_secret"})
        params = self.stripe.v1.checkout.sessions.create.call_args.kwargs["params"]
        self.assertEqual((params["mode"], params["ui_mode"]), ("setup", "embedded_page"))
        self.assertIn("{CHECKOUT_SESSION_ID}", params["return_url"])

        self.stripe.v1.checkout.sessions.retrieve.return_value = NS(
            status="complete", metadata={"booking": booking.reference},
            setup_intent=NS(status="succeeded", payment_method="pm_9"))
        r = self.client.get(card_url + "done?session_id=cs_9")
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.stripe_payment_method_id), ("confirmed", "pm_9"))
        self.assertIn("You're booked", mail.outbox[0].subject)

        # Webhook arriving afterwards changes nothing (idempotent).
        payments.finish_card_setup(booking, "cs_9")
        self.assertEqual(len([m for m in mail.outbox if "You're booked" in m.subject]), 1)

    def test_unfinished_card_step_releases_the_totes(self):
        self.walk()
        booking = Booking.objects.get()
        self.assertEqual(services.expire_holds(now=timezone.now() + timedelta(minutes=31)), 1)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)


@unittest.skipUnless(os.environ.get("STRIPE_LIVE_TESTS") == "1", "set STRIPE_LIVE_TESTS=1 to call Stripe test mode")
class LiveStripeTests(TestCase):
    """Real calls to Stripe test mode with Stripe's test cards."""

    def setUp(self):
        from django.conf import settings
        self.assertTrue(settings.STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")), "test keys only")
        self.made = []

    def tearDown(self):
        for cus in self.made:
            payments.client().v1.customers.delete(cus)

    def booking(self, card="pm_card_visa"):
        b = booking_with_card()
        c = payments.client()
        b.customer.stripe_customer_id = ""
        b.customer.email = "live-test@example.com"
        b.customer.save()
        cus = payments.ensure_customer(b.customer)
        self.made.append(cus)
        # Stand-in for the renter typing a card into Checkout: save a test card.
        if card == "pm_card_chargeCustomerFail":  # saves fine, declines when charged
            b.stripe_payment_method_id = c.v1.payment_methods.attach(card, params={"customer": cus}).id
        else:
            si = c.v1.setup_intents.create(params={
                "customer": cus, "payment_method": card, "confirm": True, "usage": "off_session",
                "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"}})
            b.stripe_payment_method_id = si.payment_method
        b.save()
        return b

    def test_hold_then_partial_capture(self):
        b = self.booking()
        hold = payments.authorize(b)
        self.assertEqual(hold.status, Charge.Status.AUTHORIZED)
        b.price_lines.update(amount=D("150.00"))
        [captured] = payments.capture_at_dropoff(b)
        self.assertEqual((captured.status, captured.captured_amount), (Charge.Status.CAPTURED, D("150.00")))

    def test_card_declined_on_hold(self):
        # Saves fine, then declines when charged (Stripe test card).
        b = self.booking(card="pm_card_chargeCustomerFail")
        hold = payments.authorize(b)
        self.assertEqual(hold.status, Charge.Status.FAILED)
        self.assertTrue(hold.failure_message)

    def test_release_and_damage_charge(self):
        b = self.booking()
        payments.authorize(b)
        self.assertEqual(payments.release(b).status, Charge.Status.RELEASED)
        damage = payments.charge(b, Charge.Kind.DAMAGE, D("15.00"), "Lost tote")
        self.assertEqual((damage.status, damage.captured_amount), (Charge.Status.CAPTURED, D("15.00")))
