"""The renter's booking flow, end to end through the web pages.

Uses the fake maps backend: distances come from a ZIP table in rentals/maps.py
(21218 → 4 mi, 21401 → 35 mi, 21702 → 55 mi, 21801 → 125 mi).
"""

from datetime import time, timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from rentals import inventory, links
from rentals.models import Booking, TimeWindow, Visit

from .helpers import bins


def address(prefix, zip_code="21218", line1="10 Elm St"):
    return {f"{prefix}-line1": line1, f"{prefix}-line2": "", f"{prefix}-city": "Baltimore",
            f"{prefix}-state": "MD", f"{prefix}-zip_code": zip_code}


@override_settings(MAPS_BACKEND="fake", OWNER_EMAILS=["owner@example.com"])
class BookingFlowTests(TestCase):
    def setUp(self):
        inventory.record_purchase(bins(), 100)
        self.dropoff = timezone.localdate() + timedelta(days=10)
        self.pickup = self.dropoff + timedelta(days=7)
        self.drop_window = TimeWindow.objects.create(date=self.dropoff, start=time(8), end=time(10), max_stops=1)
        self.pick_window = TimeWindow.objects.create(date=self.pickup, start=time(13), end=time(15))

    def walk(self, qty=35, pickup_zip="21401", pickup_unknown=False):
        """Fill in every step; returns the response to the final submit."""
        r = self.client.post("/book/", {"quantity": qty, "dropoff_date": self.dropoff.isoformat(), "weeks": 1})
        self.assertRedirects(r, "/book/addresses/")
        data = {**address("dropoff"), **address("pickup", pickup_zip, line1="22 Oak Ave")}
        if pickup_unknown:
            data = {**address("dropoff"), "pickup-unknown": "on"}
        r = self.client.post("/book/addresses/", data)
        self.assertRedirects(r, "/book/times/")
        r = self.client.post("/book/times/", {"dropoff_window": self.drop_window.pk,
                                               "pickup_window": self.pick_window.pk})
        self.assertRedirects(r, "/book/contact/")
        r = self.client.post("/book/contact/", {"name": "Pat Renter", "email": "pat@example.com",
                                                 "phone": "410-555-0100", "access_notes": "Side porch"})
        self.assertRedirects(r, "/book/review/")
        return self.client.post("/book/review/")

    def test_happy_path(self):
        review = self.client.get("/book/review/")
        self.assertRedirects(review, "/book/")  # can't skip ahead

        r = self.walk()
        booking = Booking.objects.get()
        self.assertEqual(booking.status, Booking.Status.REQUESTED)
        self.assertEqual(booking.lines.get().quantity, 35)
        self.assertEqual((booking.dropoff_miles, booking.pickup_miles), (4, 35))
        self.assertEqual(str(booking.pickup_fee), "15.00")  # 5 miles × $3
        self.assertEqual(booking.visits.get(kind="dropoff").requested_window, self.drop_window)
        self.assertEqual(booking.access_notes, "Side porch")
        self.assertRedirects(r, links.booking_url(booking))

        page = self.client.get(links.booking_url(booking))
        self.assertContains(page, booking.reference)
        self.assertContains(page, "$177.05")  # 162.05 + 15.00

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(links.booking_url(booking, absolute=True), mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].to, ["owner@example.com"])

    def test_dropoff_too_far(self):
        self.client.post("/book/", {"quantity": 20, "dropoff_date": self.dropoff.isoformat(), "weeks": 1})
        r = self.client.post("/book/addresses/", {**address("dropoff", "21401"), "pickup-unknown": "on"})
        self.assertContains(r, "we deliver within 30 miles")

    def test_pickup_too_far(self):
        self.client.post("/book/", {"quantity": 20, "dropoff_date": self.dropoff.isoformat(), "weeks": 1})
        r = self.client.post("/book/addresses/", {**address("dropoff"), **address("pickup", "21801")})
        self.assertContains(r, "we pick up within 60 miles")

    def test_pickup_address_later(self):
        self.walk(pickup_unknown=True)
        booking = Booking.objects.get()
        self.assertIsNone(booking.pickup_address)
        url = links.booking_url(booking)
        self.assertContains(self.client.get(url), "Add your moving-in address")

        r = self.client.post(url, address("pickup", "21702", line1="5 Hill Rd"))
        self.assertRedirects(r, url)
        booking.refresh_from_db()
        self.assertEqual((booking.pickup_miles, str(booking.pickup_fee)), (55, "75.00"))

    def test_too_many_bins(self):
        r = self.client.post("/book/", {"quantity": 101, "dropoff_date": self.dropoff.isoformat(), "weeks": 1})
        self.assertContains(r, "Only 100 bins are free")

    def test_date_without_windows(self):
        r = self.client.post("/book/", {"quantity": 20, "weeks": 1,
                                        "dropoff_date": (self.dropoff + timedelta(days=1)).isoformat()})
        self.assertContains(r, "no dropoff times left")

    def test_window_fills_up_while_booking(self):
        self.walk()  # takes the only dropoff stop
        self.client.cookies.clear()  # a second renter
        r = self.client.post("/book/", {"quantity": 20, "dropoff_date": self.dropoff.isoformat(), "weeks": 1})
        self.assertContains(r, "no dropoff times left")

    def test_window_taken_between_steps(self):
        # Renter reaches review, then someone else takes the last dropoff stop.
        self.client.post("/book/", {"quantity": 20, "dropoff_date": self.dropoff.isoformat(), "weeks": 1})
        self.client.post("/book/addresses/", {**address("dropoff"), "pickup-unknown": "on"})
        self.client.post("/book/times/", {"dropoff_window": self.drop_window.pk, "pickup_window": self.pick_window.pk})
        self.client.post("/book/contact/", {"name": "A", "email": "a@example.com", "phone": "1"})
        other = Booking.objects.create(customer_id=None or self._customer(), dropoff_date=self.dropoff,
                                       dropoff_address_id=self._address(), status="confirmed")
        Visit.objects.create(booking=other, kind="dropoff", window=self.drop_window, status="confirmed")

        r = self.client.post("/book/review/")
        self.assertRedirects(r, "/book/times/")
        self.assertEqual(Booking.objects.count(), 1)

    def _customer(self):
        from rentals.models import Customer
        return Customer.objects.create(name="Other", email="o@example.com").pk

    def _address(self):
        from rentals.models import Address
        return Address.objects.create(line1="1 X St", city="Baltimore", zip_code="21214").pk


class BookingLinkTests(TestCase):
    def test_bad_token(self):
        r = self.client.get("/b/not-a-real-token/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "expired", status_code=404)

    def test_find_booking_doesnt_reveal_who_booked(self):
        r = self.client.post("/my-booking/", {"email": "nobody@example.com"}, follow=True)
        self.assertContains(r, "If there are bookings under nobody@example.com")
        self.assertEqual(len(mail.outbox), 0)


class MinimumNoticeTests(TestCase):
    def test_too_soon(self):
        soon = timezone.localdate() + timedelta(days=2)
        r = self.client.post("/book/", {"quantity": 20, "dropoff_date": soon.isoformat(), "weeks": 1})
        self.assertContains(r, "We need a few days")
