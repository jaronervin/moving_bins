"""Signing the rental agreement at booking, the card-name check, and the copies we keep."""

from types import SimpleNamespace as NS
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, override_settings

from rentals import links, payments, signing
from rentals.models import AgreementVersion, Booking

from .test_booking_flow import SIGN, FlowTestCase
from .test_payments import checkout_done


class NamesMatchTests(SimpleTestCase):
    def test_matches(self):
        for typed, card in [("Pat O'Brien", "PAT OBRIEN"), ("Pat O'Brien", "Pat J. O'Brien"),
                            ("Dr. Pat O'Brien Jr.", "pat o'brien"), ("José Núñez", "JOSE NUNEZ"),
                            ("Mary-Kate Smith", "Mary Kate Smith")]:
            self.assertTrue(signing.names_match(typed, card), (typed, card))

    def test_mismatches(self):
        for typed, card in [("Patricia O'Brien", "Pat O'Brien"), ("Pat Renter", "Sam Renter"),
                            ("Pat Renter", ""), ("", "Pat Renter")]:
            self.assertFalse(signing.names_match(typed, card), (typed, card))


class SigningFlowTests(FlowTestCase):
    def test_agreement_is_loaded(self):
        version = signing.current_version()
        self.assertEqual(version.version, "2026-11-draft")
        self.assertEqual(len(version.sha256), 64)

    def test_review_requires_the_box_and_a_full_name(self):
        self.step_package()
        self.client.post("/book/extras/", {"addon_totes": 0})
        from .test_booking_flow import address
        self.client.post("/book/addresses/", {**address("dropoff"), "pickup-unknown": "on"})
        self.client.post("/book/delivery/", {"presence": "porch"})
        self.client.post("/book/contact/", {"name": "Pat Renter", "email": "p@example.com", "phone": "1"})
        page = self.client.get("/book/review/")
        self.assertContains(page, "Sign and book")
        self.assertContains(page, 'value="Pat Renter"')
        self.assertEqual(self.client.post("/book/review/", {"typed_name": "Pat Renter"}).status_code, 200)
        r = self.client.post("/book/review/", {"agree": "on", "typed_name": "Pat"})
        self.assertContains(r, "full name")
        self.assertFalse(Booking.objects.exists())

    def test_signature_is_recorded(self):
        self.walk()
        booking = Booking.objects.get()
        sig = booking.signature
        self.assertEqual(sig.typed_name, "Pat Renter")
        self.assertEqual(sig.email, "pat@example.com")
        self.assertEqual(sig.ip_address, "127.0.0.1")
        self.assertEqual(sig.agreement_sha256, AgreementVersion.current().sha256)
        self.assertIsNone(sig.name_matches_card)  # no card yet

    def test_confirmation_attaches_the_pdf(self):
        self.walk()
        booking = Booking.objects.get()
        (name, content, mimetype), = mail.outbox[0].attachments
        self.assertEqual((name, mimetype), (f"Sprout-Tote-agreement-{booking.reference}.pdf", "application/pdf"))
        self.assertTrue(content.startswith(b"%PDF"))

    def test_agreement_pages(self):
        self.assertContains(self.client.get("/agreement/"), "Rental Agreement")
        self.walk()
        booking = Booking.objects.get()
        url = f"/b/{links.make_token(booking)}/agreement/"
        self.assertContains(self.client.get(url), "Pat Renter")
        pdf = self.client.get(url + "?format=pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertContains(self.client.get(links.booking_url(booking)), "signed rental agreement")
        self.assertEqual(self.client.get("/b/bad-token/agreement/").status_code, 404)

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_forwarded_ip_behind_proxy(self):
        self.client.defaults["HTTP_X_FORWARDED_FOR"] = "203.0.113.9, 10.0.0.1"
        self.walk()
        self.assertEqual(Booking.objects.get().signature.ip_address, "203.0.113.9")


@override_settings(STRIPE_SECRET_KEY="sk_test_fake", MAPS_BACKEND="fake", OWNER_EMAILS=["owner@example.com"])
class CardNameTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.stripe = mock.MagicMock()
        patcher = mock.patch.object(payments, "client", return_value=self.stripe)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.walk()
        self.booking = Booking.objects.get()
        self.token = links.make_token(self.booking)
        self.booking.stripe_checkout_session_id = "cs_9"
        self.booking.stripe_customer_id = "cus_9"
        self.booking.save()

    def done(self, card_name):
        self.stripe.v1.checkout.sessions.retrieve.return_value = checkout_done(self.booking, card_name)
        return self.client.get(f"/b/{self.token}/card/done?session_id=cs_9")

    def test_card_evidence_recorded(self):
        self.done("PAT RENTER")
        sig = self.booking.signature
        sig.refresh_from_db()
        self.assertEqual((sig.card_name, sig.card_brand, sig.card_last4), ("PAT RENTER", "visa", "4242"))
        self.assertEqual((sig.avs_line1_check, sig.avs_postal_code_check), ("pass", "pass"))
        self.assertTrue(sig.name_matches_card)

    def test_someone_elses_card_must_resign(self):
        r = self.done("Sam Payer")
        self.assertRedirects(r, f"/b/{self.token}/sign/", fetch_redirect_response=False)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.AWAITING_CARD)
        self.assertEqual(self.booking.stripe_payment_method_id, "")
        self.assertEqual(len(mail.outbox), 0)

        page = self.client.get(f"/b/{self.token}/sign/")
        self.assertContains(page, "Sam Payer")
        r = self.client.post(f"/b/{self.token}/sign/", {"agree": "on", "typed_name": "Pat Renter"})
        self.assertRedirects(r, f"/b/{self.token}/sign/", fetch_redirect_response=False)  # still wrong

        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(f"/b/{self.token}/sign/", {"agree": "on", "typed_name": "Sam Payer"})
        self.booking.refresh_from_db()
        self.assertEqual((self.booking.status, self.booking.stripe_payment_method_id), ("confirmed", "pm_9"))
        self.assertEqual(self.booking.signature.typed_name, "Sam Payer")
        self.assertIn("You're booked", mail.outbox[0].subject)
        self.assertEqual(len(mail.outbox[0].attachments), 1)


class NoSignatureFieldsTests(FlowTestCase):
    def test_sign_fields_ignored_when_no_agreement(self):
        AgreementVersion.objects.all().delete()
        self.walk()
        self.assertFalse(hasattr(Booking.objects.get(), "signature"))
        self.assertEqual(SIGN["typed_name"], "Pat Renter")
