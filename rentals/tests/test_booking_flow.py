"""The renter's booking flow, end to end through the web pages.

Uses the fake maps backend: straight-line distances come from a ZIP table in
rentals/maps.py (21218 → 4 mi, 21401 → 26 mi, 20001 → 36 mi, 21702 → 48 mi).
Free within 20 miles, $25 per address from 20 to 35 miles, refused beyond.
"""

import re
from datetime import time, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from rentals import inventory, links, services
from rentals.models import Booking, BusinessSettings, Package, Product, TimeWindow, Visit

from .helpers import assert_redirects_to_booking, bins


def address(prefix, zip_code="21218", line1="10 Elm St"):
    return {f"{prefix}-line1": line1, f"{prefix}-line2": "", f"{prefix}-city": "Baltimore",
            f"{prefix}-state": "MD", f"{prefix}-zip_code": zip_code}


def pkg(slug):
    return Package.objects.get(slug=slug)


def product(slug):
    return Product.objects.get(slug=slug)


@override_settings(MAPS_BACKEND="fake", OWNER_EMAILS=["owner@example.com"])
class FlowTestCase(TestCase):
    weeks = 3

    def setUp(self):
        inventory.record_purchase(bins(), 60)
        self.dropoff = timezone.localdate() + timedelta(days=10)
        self.pickup = self.dropoff + timedelta(days=7 * self.weeks)
        self.drop_window = TimeWindow.objects.create(date=self.dropoff, start=time(9), end=time(13), max_stops=1)
        self.pick_window = TimeWindow.objects.create(date=self.pickup, start=time(9), end=time(13))
        User.objects.create_user("owner", password="x", is_staff=True)

    def step_package(self, package="2br", weeks=None, dropoff=None):
        return self.client.post("/book/", {"package": pkg(package).pk, "weeks": weeks or self.weeks,
                                           "dropoff_date": (dropoff or self.dropoff).isoformat()})

    def walk(self, package="2br", addons=0, extras=None, pickup_zip="21401", pickup_unknown=False,
             presence="porch", time_request=""):
        """Fill in every step; returns the response to the final submit."""
        self.assertRedirects(self.step_package(package), "/book/extras/")
        data = {"addon_totes": addons, **{f"x_{product(s).pk}": q for s, q in (extras or {}).items()}}
        self.assertRedirects(self.client.post("/book/extras/", data), "/book/addresses/")
        data = {**address("dropoff"), **address("pickup", pickup_zip, line1="22 Oak Ave")}
        if pickup_unknown:
            data = {**address("dropoff"), "pickup-unknown": "on"}
        self.assertRedirects(self.client.post("/book/addresses/", data), "/book/delivery/")
        r = self.client.post("/book/delivery/", {"presence": presence, "time_request": time_request})
        self.assertRedirects(r, "/book/contact/")
        r = self.client.post("/book/contact/", {"name": "Pat Renter", "email": "pat@example.com",
                                                 "phone": "410-555-0100", "access_notes": "Side porch"})
        self.assertRedirects(r, "/book/review/")
        return self.client.post("/book/review/")


class BookingFlowTests(FlowTestCase):
    def test_happy_path(self):
        self.assertRedirects(self.client.get("/book/review/"), "/book/")  # can't skip ahead

        r = self.walk(addons=5, extras={"moving-blanket": 4, "packing-kit": 1})
        booking = Booking.objects.get()
        assert_redirects_to_booking(self, r, booking)

        # Confirmed straight away; owners have 48 hours to decline or ask for a change.
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertAlmostEqual((booking.review_until - timezone.now()).total_seconds(), 48 * 3600, delta=60)
        self.assertEqual(booking.visits.get(kind="dropoff").window, self.drop_window)
        self.assertEqual(booking.visits.get(kind="pickup").window, self.pick_window)

        # 2BR for 3 weeks: 169 + 65; 5 extra totes × (5 + 3); 4 blankets × 5; packing kit 35;
        # pickup address 26 miles away: $25 zone fee.
        lines = [(l.kind, l.quantity, l.amount) for l in booking.price_lines.all()]
        self.assertEqual(lines, [("package", 1, D("234.00")), ("addon", 5, D("40.00")),
                                 ("rental", 4, D("20.00")), ("sale", 1, D("35.00")),
                                 ("delivery", 1, D("25.00"))])
        self.assertEqual(booking.renewal_week_price, D("80.00"))  # 65 + 5 × 3
        items = {l.product.slug: l.quantity for l in booking.lines.all()}
        self.assertEqual(items, {"bin-27gal": 45, "dolly": 2, "moving-blanket": 4, "packing-kit": 1})
        self.assertEqual((booking.dropoff_miles, booking.pickup_miles), (4, 26))

        page = self.client.get(links.booking_url(booking))
        self.assertContains(page, "$354.00")
        self.assertContains(page, "by 1:00 PM")

        self.assertIn("You're booked", mail.outbox[0].subject)
        token = re.search(r"/b/([^/\s]+)/", mail.outbox[0].body).group(1)
        self.assertEqual(links.read_token(token), booking.reference)
        self.assertIn("Total before tax: $354.00", mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].to, ["owner@example.com"])
        self.assertIn("2BR package, 3 weeks", mail.outbox[1].body)

    def test_too_soon(self):
        r = self.step_package(dropoff=timezone.localdate() + timedelta(days=2))
        self.assertContains(r, "at least 3 days")

    def test_rental_length_limits(self):
        self.assertContains(self.step_package(weeks=1), "Select a valid choice")
        self.assertContains(self.step_package(weeks=5), "Select a valid choice")

    def test_not_enough_totes(self):
        r = self.step_package("whole-home")  # 80 totes; we own 60 and have no planned capacity
        self.assertContains(r, "We only have 60")

    def test_date_without_delivery(self):
        r = self.step_package(dropoff=self.dropoff + timedelta(days=1))
        self.assertContains(r, "not delivering that day")

    def test_extended_zone_and_too_far(self):
        self.step_package()
        self.client.post("/book/extras/", {"addon_totes": 0})
        r = self.client.post("/book/addresses/", {**address("dropoff", "21702"), "pickup-unknown": "on"})
        self.assertContains(r, "we serve within 35 miles")
        r = self.client.post("/book/addresses/", {**address("dropoff"), **address("pickup", "20001")})
        self.assertContains(r, "we serve within 35 miles")

    def test_present_needs_a_time(self):
        self.step_package()
        self.client.post("/book/extras/", {"addon_totes": 0})
        self.client.post("/book/addresses/", {**address("dropoff"), "pickup-unknown": "on"})
        r = self.client.post("/book/delivery/", {"presence": "present", "time_request": ""})
        self.assertContains(r, "Let us know when you&#x27;ll be there")

    def test_present_with_time_request(self):
        self.walk(presence="present", time_request="After 10 AM please")
        booking = Booking.objects.get()
        self.assertEqual((booking.presence, booking.time_request), ("present", "After 10 AM please"))
        self.assertIn("After 10 AM please", mail.outbox[1].body)

    def test_pickup_address_later(self):
        self.walk(pickup_unknown=True)
        booking = Booking.objects.get()
        self.assertIsNone(booking.pickup_address)
        url = links.booking_url(booking)
        self.assertContains(self.client.get(url), "Add your moving-in address")

        r = self.client.post(url, address("pickup", "21401", line1="5 Hill Rd"))
        self.assertRedirects(r, url)
        booking.refresh_from_db()
        self.assertEqual((booking.pickup_miles, booking.delivery_fee), (26, D("25.00")))
        self.assertEqual(booking.price_lines.filter(kind="delivery").count(), 1)

    def test_day_fills_up(self):
        self.walk()  # takes the only dropoff stop that day
        self.client.cookies.clear()  # a second renter
        self.assertContains(self.step_package(), "not delivering that day, or it&#x27;s full")

    def test_day_taken_between_steps(self):
        self.step_package()
        self.client.post("/book/extras/", {"addon_totes": 0})
        self.client.post("/book/addresses/", {**address("dropoff"), "pickup-unknown": "on"})
        self.client.post("/book/delivery/", {"presence": "porch"})
        self.client.post("/book/contact/", {"name": "A", "email": "a@example.com", "phone": "1"})
        from rentals.models import Address, Customer
        other = Booking.objects.create(
            customer=Customer.objects.create(name="Other", email="o@example.com"), dropoff_date=self.dropoff,
            dropoff_address=Address.objects.create(line1="1 X St", city="Baltimore", zip_code="21214"),
            status="confirmed")
        Visit.objects.create(booking=other, kind="dropoff", window=self.drop_window, status="confirmed")

        r = self.client.post("/book/review/")
        self.assertRedirects(r, "/book/")
        self.assertEqual(Booking.objects.count(), 1)


class PlannedCapacityFlowTests(FlowTestCase):
    def test_owner_email_says_when_to_buy_more(self):
        bins(planned_capacity=120)  # own 60, book as if 120
        self.walk(package="whole-home")
        self.assertIn("BUY MORE", mail.outbox[1].body)
        self.assertIn("20 more", mail.outbox[1].body)


class ReviewWindowTests(FlowTestCase):
    def test_owner_declines(self):
        self.walk()
        booking = Booking.objects.get()
        self.client.login(username="owner", password="x")
        self.assertContains(self.client.get("/ops/"), "you can still decline")
        r = self.client.post(f"/ops/b/{booking.reference}/decline",
                             {"action": "decline", "reason": "We're fully booked."}, follow=True)
        self.assertContains(r, "declined")
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.decline_reason), ("declined", "We're fully booked."))
        self.assertEqual(inventory.available(bins(), self.dropoff, self.pickup), 60)
        self.assertIn("cancelled", mail.outbox[-1].subject)
        self.assertIn("We're fully booked.", mail.outbox[-1].body)

    def test_owner_asks_for_a_change(self):
        self.walk()
        booking = Booking.objects.get()
        self.client.login(username="owner", password="x")
        self.client.post(f"/ops/b/{booking.reference}/decline",
                         {"action": "change", "reason": "Could we drop off Saturday instead?"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, "confirmed")
        self.assertIn("Saturday instead", mail.outbox[-1].body)
        self.client.logout()
        self.assertContains(self.client.get(links.booking_url(booking)), "We need a change")

    def test_cannot_decline_after_window(self):
        self.walk()
        with self.assertRaisesMessage(services.CannotDecline, "has passed"):
            services.decline_booking(Booking.objects.get(), "late", now=timezone.now() + timedelta(hours=49))

    def test_manual_approval_still_available(self):
        s = BusinessSettings.load()
        s.auto_confirm = False
        s.save()
        self.walk()
        self.assertEqual(Booking.objects.get().status, Booking.Status.REQUESTED)
        self.assertIn("We got your tote request", mail.outbox[0].subject)


class BookingLinkTests(TestCase):
    def test_bad_token(self):
        r = self.client.get("/b/not-a-real-token/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "expired", status_code=404)

    def test_find_booking_doesnt_reveal_who_booked(self):
        r = self.client.post("/my-booking/", {"email": "nobody@example.com"}, follow=True)
        self.assertContains(r, "If there are bookings under nobody@example.com")
        self.assertEqual(len(mail.outbox), 0)


class EmailEscapingTests(TestCase):
    def test_plain_text_emails_are_not_html_escaped(self):
        from django.template.loader import render_to_string

        class B:  # just enough of a booking for the template
            reference = "MB-TEST01"
            decline_reason = "We're fully booked & can't fit you in."
            dropoff_date = timezone.localdate()

            class customer:
                name = "Pat O'Brien"
        body = render_to_string("rentals/email/declined_body.txt", {"booking": B, "site_url": "x"})
        self.assertIn("Pat O'Brien", body)
        self.assertIn("We're fully booked & can't fit you in.", body)
