"""The renter's booking flow, end to end through the web pages.

Uses the fake maps backend: straight-line distances come from a ZIP table in
rentals/maps.py (21218 → 4 mi, 21401 → 26 mi, 20001 → 36 mi, 21702 → 48 mi).
Free within 20 miles, $25 per address from 20 to 35 miles, refused beyond.
"""

import re
import unittest.mock
from datetime import time, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from rentals import inventory, links, services
from rentals.models import Booking, BusinessSettings, Package, Product, TimeWindow, Visit

from .helpers import assert_redirects_to_booking, bins


SIGN = {"agree": "on", "typed_name": "Pat Renter"}


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
        return self.client.post("/book/review/", SIGN)


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
        self.assertContains(r, "check availability for 80 27-gallon totes with lid")
        self.assertContains(r, "Contact us at hello@sprouttoteco.com")

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

        r = self.client.post("/book/review/", SIGN)
        self.assertRedirects(r, "/book/")
        self.assertEqual(Booking.objects.count(), 1)


class ExtrasAvailabilityTests(FlowTestCase):
    """Extras are booked against planning counts (none owned yet, bought the same day)."""

    def to_extras(self, package="2br"):
        self.step_package(package)

    def test_launch_counts(self):
        counts = {p.slug: (p.track_stock, p.planned_capacity, p.lead_business_days)
                  for p in Product.objects.filter(kind="rental")}
        self.assertEqual(counts["moving-blanket"], (True, 20, 0))
        self.assertEqual(counts["dolly"], (True, 10, 0))
        self.assertEqual(counts["wardrobe-box"], (False, None, 0))

    def test_past_the_count_says_contact_us(self):
        self.to_extras()
        blanket = product("moving-blanket")
        r = self.client.post("/book/extras/", {"addon_totes": 0, f"x_{blanket.pk}": 21})
        self.assertContains(r, "check availability for 21 moving blankets")
        r = self.client.post("/book/extras/", {"addon_totes": 0, f"x_{blanket.pk}": 20})
        self.assertRedirects(r, "/book/addresses/")

    def test_buy_when_needed_items_are_never_refused(self):
        self.to_extras()
        r = self.client.post("/book/extras/", {"addon_totes": 0, f"x_{product('wardrobe-box').pk}": 40})
        self.assertRedirects(r, "/book/addresses/")

    def test_contact_note_when_none_left(self):
        p = product("appliance-dolly")
        p.planned_capacity = 0
        p.save()
        self.to_extras()
        page = self.client.get("/book/extras/").content.decode()
        page = page[page.index("Rent (one-time fee)"):]  # the item rows, after the upgrade box
        row = page[page.index("Appliance dolly"):page.index("Moving blanket")]
        self.assertIn("for availability", row)
        row = page[page.index("Moving blanket"):page.index("Wardrobe box")]
        self.assertNotIn("for availability", row)

    def test_owner_told_to_buy_same_day(self):
        self.walk(extras={"moving-blanket": 4, "dish-divider": 2})
        body = mail.outbox[1].body
        self.assertIn("BUY MORE: with this booking, you need 4 more (Moving blanket)", body)
        self.assertIn("(same day is fine)", body)
        self.assertIn("2 more (Dish/glass divider)", body)


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


class WindowTemplateAdminTests(TestCase):
    def test_saving_a_pattern_fills_in_delivery_days(self):
        from rentals.models import WindowTemplate
        User.objects.create_superuser("boss", "b@example.com", "x")
        self.client.login(username="boss", password="x")
        r = self.client.post("/admin/rentals/windowtemplate/add/",
                             {"weekday": 5, "start": "09:00", "end": "13:00", "max_stops": 4, "active": "on"},
                             follow=True)
        self.assertContains(r, "delivery day(s) for the next 60 days")
        self.assertEqual(WindowTemplate.objects.count(), 1)
        self.assertGreaterEqual(TimeWindow.objects.filter(date__week_day=7).count(), 8)  # Saturdays


class AddressSuggestionTests(FlowTestCase):
    def test_suggestions_only_with_a_browser_key(self):
        self.step_package()
        self.client.post("/book/extras/", {"addon_totes": 0})
        self.assertNotContains(self.client.get("/book/addresses/"), "places-config")
        with override_settings(GOOGLE_MAPS_BROWSER_KEY="browser-key"):
            page = self.client.get("/book/addresses/")
        self.assertContains(page, 'data-key="browser-key"')
        self.assertContains(page, "rentals/address.js")
        self.assertIn("places.googleapis.com", page["Content-Security-Policy"])


class SystemStatusTests(TestCase):
    @override_settings(MAPS_BACKEND="fake", GOOGLE_MAPS_BROWSER_KEY="", STRIPE_SECRET_KEY="sk_test_abc")
    def test_shows_what_is_on_without_keys(self):
        User.objects.create_user("owner", password="x", is_staff=True)
        self.client.login(username="owner", password="x")
        page = self.client.get("/ops/")
        self.assertContains(page, "Estimated from ZIP codes")
        self.assertContains(page, "Stripe, test mode")
        self.assertNotContains(page, "sk_test_abc")


@override_settings(MAPS_BACKEND="fake")
class AreaCheckTests(TestCase):
    def check(self, text):
        return self.client.post("/check-area/", {"area-line1": text}, HTTP_X_REQUESTED_WITH="fetch").json()

    def test_free_fee_and_out_of_range(self):
        self.assertEqual(self.check("100 Light St, Baltimore, MD 21218")["status"], "free")
        r = self.check("Annapolis 21401")
        self.assertEqual(r["status"], "fee")
        self.assertIn("adds $25", r["message"])
        r = self.check("Frederick 21702")
        self.assertEqual(r["status"], "out")
        self.assertIn("about 48 miles from us", r["message"])
        self.assertEqual(self.check("x")["status"], "error")

    def test_far_off_match_is_not_found(self):
        from rentals import maps
        far = maps.Place(formatted="Somewhere, TX 75001, USA", lat=33, lng=-97, place_id="p")
        with unittest.mock.patch.object(maps, "lookup", return_value=(far, 1300.0)):
            r = self.check("zzzz qqqq")
        self.assertEqual(r["status"], "error")
        self.assertIn("couldn't find that near Baltimore", r["message"])

    def test_building_names_are_not_used_as_the_street(self):
        from rentals.booking_flow import _split_formatted
        self.assertEqual(_split_formatted("Legg Mason Building, 100 Light St, Baltimore, MD 21202, USA")["line1"],
                         "100 Light St")
        self.assertIsNone(_split_formatted("Towson, MD, USA"))
        self.assertIsNone(_split_formatted("Cape St Claire, MD 21401, USA"))

    def test_checked_address_prefills_the_addresses_step(self):
        from rentals import maps
        place = maps.Place(formatted="100 Light St, Baltimore, MD 21202, USA", lat=39.3, lng=-76.6, place_id="p")
        with self.settings(MAPS_BACKEND="fake"), \
                unittest.mock.patch.object(maps, "lookup", return_value=(place, 6.0)):
            self.assertEqual(self.check("100 Light St")["status"], "free")
        self.assertEqual(self.client.session["area_check"],
                         {"line1": "100 Light St", "line2": "", "city": "Baltimore", "state": "MD", "zip_code": "21202"})

    def test_limit_per_visit(self):
        for _ in range(15):
            self.check("Annapolis 21401")
        self.assertIn("lot of checks", self.check("Annapolis 21401")["message"])

    def test_works_without_javascript(self):
        r = self.client.post("/check-area/", {"area-line1": "Frederick 21702", "next": "/"}, follow=True)
        self.assertContains(r, "we don&#x27;t deliver there yet")

    def test_shown_on_home_and_first_step(self):
        inventory.record_purchase(bins(), 1)
        self.assertContains(self.client.get("/"), "Do we deliver to you?")
        self.assertContains(self.client.get("/book/"), "Do we deliver to you?")


class AdminCancelTests(FlowTestCase):
    def test_bulk_cancel_frees_totes(self):
        self.walk()
        booking = Booking.objects.get()
        self.assertLess(inventory.available(bins(), self.dropoff, self.pickup), 60)
        User.objects.create_superuser("boss", "b@example.com", "x")
        self.client.login(username="boss", password="x")
        r = self.client.post("/admin/rentals/booking/", {"action": "cancel_selected", "_selected_action": [booking.pk]},
                             follow=True)
        self.assertContains(r, "Cancelled 1 booking")
        booking.refresh_from_db()
        self.assertEqual(booking.status, "cancelled")
        self.assertEqual(inventory.available(bins(), self.dropoff, self.pickup), 60)
        self.assertTrue(all(v.status == "cancelled" for v in booking.visits.all()))


class NoDeletingRecordsTests(FlowTestCase):
    def test_admin_offers_cancel_not_delete(self):
        self.walk()
        User.objects.create_superuser("boss", "b@example.com", "x")
        self.client.login(username="boss", password="x")
        page = self.client.get("/admin/rentals/booking/")
        self.assertContains(page, "Cancel selected bookings")
        self.assertNotContains(page, "Delete selected")
        self.assertContains(page, "Bookings are kept (not deleted)")
        b = Booking.objects.get()
        self.assertEqual(self.client.get(f"/admin/rentals/booking/{b.pk}/delete/").status_code, 403)


class EmailFailureTests(FlowTestCase):
    def test_booking_still_succeeds_when_email_fails(self):
        with unittest.mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")), \
                self.assertLogs("rentals.emails", level="ERROR"):
            r = self.walk()
        assert_redirects_to_booking(self, r, Booking.objects.get())


class OwnerEmailRouteTests(FlowTestCase):
    @override_settings(GOOGLE_SMTP_USER="jay@sprouttoteco.com", GOOGLE_SMTP_APP_PASSWORD="app-pw",
                       DEFAULT_FROM_EMAIL="Sprout Tote Co. <hello@sprouttoteco.com>")
    def test_owner_alerts_go_through_google(self):
        from django.core.mail import get_connection as real
        calls = []

        def fake(backend=None, **kwargs):
            calls.append((backend, kwargs))
            return real("django.core.mail.backends.locmem.EmailBackend")
        with unittest.mock.patch("rentals.emails.get_connection", side_effect=fake):
            self.walk()
        renter, owner = mail.outbox[0], mail.outbox[1]
        self.assertEqual(renter.from_email, "Sprout Tote Co. <hello@sprouttoteco.com>")
        self.assertEqual(owner.from_email, "Sprout Tote alerts <jay@sprouttoteco.com>")
        self.assertEqual(owner.to, ["owner@example.com"])
        self.assertEqual(calls[0][1]["host"], "smtp.gmail.com")
        self.assertEqual(len(calls), 1)  # only the owner email used Google

    def test_without_google_alerts_use_the_normal_route(self):
        self.walk()
        self.assertEqual(mail.outbox[1].to, ["owner@example.com"])


class SiteUrlTests(TestCase):
    def test_render_address_used_for_email_links(self):
        import importlib
        import os

        import config.settings as conf
        with unittest.mock.patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://sprout-staging.onrender.com/"}):
            os.environ.pop("SITE_URL", None)
            importlib.reload(conf)
            self.assertEqual(conf.SITE_URL, "https://sprout-staging.onrender.com")
        importlib.reload(conf)


class HonestEmailMessageTests(FlowTestCase):
    def test_page_says_so_when_the_email_fails(self):
        with unittest.mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("rejected")), \
                self.assertLogs("rentals.emails", level="ERROR"):
            r = self.client.get(self.walk()["Location"])
        self.assertContains(r, "We couldn&#x27;t email pat@example.com just now")
        self.assertIsNone(Booking.objects.get().confirmation_sent_at)

    def test_page_confirms_when_it_went_out(self):
        r = self.client.get(self.walk()["Location"])
        self.assertContains(r, "We&#x27;ve emailed a confirmation to pat@example.com")
        self.assertIsNotNone(Booking.objects.get().confirmation_sent_at)
