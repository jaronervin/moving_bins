"""Renters cancelling, changing and extending from their booking link."""

from datetime import time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from rentals import inventory, links, payments, services
from rentals.models import Booking, Charge, TimeWindow

from .helpers import bins
from .test_booking_flow import FlowTestCase, pkg, product


class RenterCancelTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.walk(addons=5, extras={"moving-blanket": 4})
        self.booking = Booking.objects.get()
        self.token = links.make_token(self.booking)
        mail.outbox.clear()

    def test_free_cancel_before_cutoff(self):
        page = self.client.get(f"/b/{self.token}/")
        self.assertContains(page, "Cancel booking")
        self.assertContains(page, "Change booking")
        self.assertContains(self.client.get(f"/b/{self.token}/cancel/"), "Cancelling now is free")
        r = self.client.post(f"/b/{self.token}/cancel/", follow=True)
        self.assertContains(r, "Your booking is cancelled")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertEqual(inventory.available(bins(), self.dropoff, self.pickup), 60)
        self.assertIn("is cancelled", mail.outbox[0].subject)
        self.assertIn("Nothing was charged", mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].to, ["owner@example.com"])
        self.assertNotContains(self.client.get(f"/b/{self.token}/"), "Cancel booking")

    def test_late_cancel_fee(self):
        # 2BR 3 weeks 234 + 5 extra totes 40 + 4 blankets 20 = 294 rental; 50% = 147
        cutoff = services.free_cancel_until(self.booking)
        self.assertEqual(services.cancel_fee(self.booking, cutoff - timedelta(minutes=1)), 0)
        self.assertEqual(services.cancel_fee(self.booking, cutoff), D("147.00"))
        with mock.patch.object(services.timezone, "now", return_value=cutoff + timedelta(hours=1)):
            self.assertContains(self.client.get(f"/b/{self.token}/cancel/"), "late-cancellation charge of $147.00")

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_late_cancel_charges_the_card(self):
        self.booking.stripe_payment_method_id = "pm_1"
        self.booking.save()
        late = Charge(booking=self.booking, kind=Charge.Kind.LATE_CANCEL, amount=D("147.00"),
                      status=Charge.Status.CAPTURED)
        with mock.patch.object(payments, "charge_late_cancel", return_value=late) as charge, \
                mock.patch.object(payments, "release") as release:
            services.cancel_booking(self.booking, now=services.free_cancel_until(self.booking) + timedelta(hours=1))
        charge.assert_called_once()
        release.assert_not_called()

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_owner_cancel_never_charges(self):
        self.booking.stripe_payment_method_id = "pm_1"
        self.booking.save()
        with mock.patch.object(payments, "charge_late_cancel") as charge, mock.patch.object(payments, "release"):
            services.cancel_booking(self.booking, by_owner=True,
                                    now=services.free_cancel_until(self.booking) + timedelta(hours=1))
        charge.assert_not_called()


class RenterChangeTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.walk(addons=5, extras={"moving-blanket": 4})
        self.booking = Booking.objects.get()
        self.url = f"/b/{links.make_token(self.booking)}/change/"
        mail.outbox.clear()

    def form(self, package="2br", weeks=3, dropoff=None, addons=5, extras=None, confirm=False):
        data = {"package": pkg(package).pk, "weeks": weeks, "dropoff_date": (dropoff or self.dropoff).isoformat(),
                "addon_totes": addons}
        for slug, q in (extras if extras is not None else {"moving-blanket": 4}).items():
            data[f"x_{product(slug).pk}"] = q
        if confirm:
            data["confirm"] = "1"
        return data

    def test_order_from_booking(self):
        order = services.order_from_booking(self.booking)
        self.assertEqual((order.package.slug, order.weeks, order.addon_totes), ("2br", 3, 5))
        self.assertEqual({p.slug: q for p, q in order.extras.items()}, {"moving-blanket": 4})

    def test_preview_then_confirm(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'value="5"')  # extra totes prefilled
        r = self.client.post(self.url, self.form(package="3br", addons=0, extras={}))
        self.assertContains(r, "New total before tax")
        self.assertContains(r, "Confirm changes")
        self.assertEqual(Booking.objects.get().package.slug, "2br")  # not saved yet
        r = self.client.post(self.url, self.form(package="3br", addons=0, extras={}, confirm=True), follow=True)
        self.assertContains(r, "Your booking is updated")
        b = Booking.objects.get()
        self.assertEqual(b.package.slug, "3br")
        self.assertEqual({l.product.slug: l.quantity for l in b.lines.all()}, {"bin-27gal": 60, "dolly": 2})
        # 3BR 3 weeks = 229 + 85; pickup is 26 miles away: $25
        self.assertEqual(sum(l.amount for l in b.price_lines.all()), D("339.00"))
        self.assertIn("Updated", mail.outbox[0].subject)
        self.assertIn("Total before tax: $339.00", mail.outbox[0].body)

    def test_new_dates_move_the_visits(self):
        new_drop = self.dropoff + timedelta(days=1)
        new_pick = new_drop + timedelta(days=14)
        TimeWindow.objects.create(date=new_drop, start=time(9), end=time(13))
        w = TimeWindow.objects.create(date=new_pick, start=time(9), end=time(13))
        self.client.post(self.url, self.form(weeks=2, dropoff=new_drop, confirm=True))
        b = Booking.objects.get()
        self.assertEqual((b.dropoff_date, b.pickup_date), (new_drop, new_pick))
        self.assertEqual(b.visits.get(kind="pickup").window, w)
        # the old dropoff day has its stop back
        from rentals import windows
        self.assertEqual(windows.stops_left(self.drop_window), 1)

    def test_no_delivery_on_new_date(self):
        r = self.client.post(self.url, self.form(dropoff=self.dropoff + timedelta(days=2), confirm=True))
        self.assertContains(r, "not delivering on")
        self.assertEqual(Booking.objects.get().dropoff_date, self.dropoff)

    def test_too_many_totes(self):
        r = self.client.post(self.url, self.form(package="whole-home", confirm=True))
        self.assertContains(r, "check availability for 85 27-gallon totes")
        self.assertEqual(Booking.objects.get().package.slug, "2br")

    def test_closed_after_cutoff(self):
        with mock.patch.object(timezone, "now", return_value=services.free_cancel_until(self.booking)):
            r = self.client.post(self.url, self.form(package="3br", confirm=True))
            self.assertContains(r, "Changes close 72 hours before dropoff")
            self.assertNotContains(self.client.get(f"/b/{links.make_token(self.booking)}/"), "Change booking")


class AddWeekTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.walk()
        self.booking = Booking.objects.get()
        Booking.objects.filter(pk=self.booking.pk).update(status=Booking.Status.OUT)
        self.token = links.make_token(self.booking)
        self.next_window = TimeWindow.objects.create(date=self.pickup + timedelta(days=7), start=time(9), end=time(13))
        mail.outbox.clear()

    def test_add_week(self):
        self.assertContains(self.client.get(f"/b/{self.token}/"), "Keep them another week")
        self.assertContains(self.client.get(f"/b/{self.token}/add-week/"), "$65.00 for the extra week")
        r = self.client.post(f"/b/{self.token}/add-week/", follow=True)
        self.assertContains(r, "Your pickup is now")
        b = Booking.objects.get()
        self.assertEqual((b.renewals, b.pickup_date), (1, self.pickup + timedelta(days=7)))
        self.assertEqual(b.visits.get(kind="pickup").window, self.next_window)
        self.assertIn("Extra week added", mail.outbox[0].subject)

    def test_no_pickup_day_a_week_later(self):
        self.next_window.delete()
        r = self.client.post(f"/b/{self.token}/add-week/", follow=True)
        self.assertContains(r, "not delivering on")
        self.assertEqual(Booking.objects.get().renewals, 0)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_declined_card_changes_nothing(self):
        failed = NS(status=Charge.Status.FAILED, failure_message="Your card was declined.")
        with mock.patch.object(payments, "charge", return_value=failed):
            r = self.client.post(f"/b/{self.token}/add-week/", follow=True)
        self.assertContains(r, "couldn&#x27;t charge your card (Your card was declined.)")
        b = Booking.objects.get()
        self.assertEqual((b.renewals, b.pickup_date), (0, self.pickup))
        self.assertEqual(b.visits.get(kind="pickup").window, self.pick_window)

    @override_settings(STRIPE_SECRET_KEY="sk_test_fake")
    def test_charges_the_weekly_price(self):
        ok = NS(status=Charge.Status.CAPTURED, total=D("65.00"))
        with mock.patch.object(payments, "charge", return_value=ok) as charge:
            self.client.post(f"/b/{self.token}/add-week/")
        self.assertEqual(charge.call_args.args[1:3], (Charge.Kind.RENEWAL, D("65.00")))
        self.assertIn("We charged $65.00", mail.outbox[0].body)


class ReminderTests(FlowTestCase):
    def test_dropoff_and_pickup_reminders_once(self):
        from rentals.reminders import send_reminders
        self.walk(pickup_unknown=True)
        booking = Booking.objects.get()
        mail.outbox.clear()
        now = timezone.now()
        self.assertEqual(send_reminders(now), (0, 0))  # dropoff is 10 days out
        three_before = now + timedelta(days=(self.dropoff - timezone.localdate(now)).days - 3)
        self.assertEqual(send_reminders(three_before), (1, 0))
        body = mail.outbox[0].body
        self.assertIn("by 1:00 PM", body)
        self.assertIn("We still need your moving-in address", body)
        self.assertEqual(send_reminders(three_before), (0, 0))  # only once

        Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.OUT)
        two_before = now + timedelta(days=(self.pickup - timezone.localdate(now)).days - 2)
        self.assertEqual(send_reminders(two_before), (0, 1))
        self.assertIn("ready by 9:00 AM", mail.outbox[-1].body)
        self.assertIn("/add-week/", mail.outbox[-1].body)

        # adding a week re-arms the pickup reminder
        TimeWindow.objects.create(date=self.pickup + timedelta(days=7), start=time(9), end=time(13))
        with mock.patch.object(services.timezone, "now", return_value=two_before):
            services.add_week(booking)
        self.assertEqual(send_reminders(two_before + timedelta(days=7)), (0, 1))


class RouteViewTests(FlowTestCase):
    def test_day_view_orders_stops_and_links_to_maps(self):
        from rentals import routes
        self.drop_window.max_stops = 3
        self.drop_window.save()
        self.walk(package="studio", pickup_zip="21401")  # dropoff 21218 (4 mi)
        self.client.cookies.clear()
        self.walk(package="studio")
        far, near = Booking.objects.order_by("pk")
        from rentals.models import Address
        Address.objects.filter(pk=far.dropoff_address_id).update(lat=D("39.60"), lng=D("-76.563"))  # ~17 mi north
        stops = routes.stops_for(self.dropoff)
        self.assertEqual([s.booking for s in stops], [near, far])
        links = routes.maps_links(stops)
        self.assertEqual(len(links), 1)
        self.assertIn("waypoints=10+Elm+St", links[0])

        self.client.login(username="owner", password="x")
        page = self.client.get(f"/ops/day/?date={self.dropoff.isoformat()}")
        self.assertContains(page, "2 dropoffs · 0 pickups")
        self.assertContains(page, "Open route in Google Maps")
        self.assertContains(page, "Drop off by 1:00 PM")
        self.assertEqual(self.client.get("/ops/day/?date=nonsense").status_code, 200)

    def test_long_days_split_into_legs(self):
        from rentals import routes
        stops = [NS(where=f"{i} Main St, Baltimore, MD", kind="dropoff") for i in range(1, 15)]
        links = routes.maps_links(stops)
        self.assertEqual(len(links), 2)
        self.assertIn("destination=10+Main+St", links[0])
        self.assertIn("origin=10+Main+St", links[1])


class DoorTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.walk(package="studio")
        inventory.record_purchase(product("dolly"), 1)  # bought the day of the move
        self.booking = Booking.objects.get()
        self.client.login(username="owner", password="x")
        mail.outbox.clear()

    def visit(self, kind):
        return self.booking.visits.get(kind=kind)

    def door(self, kind, action, **extra):
        return self.client.post(f"/ops/visit/{self.visit(kind).pk}/door",
                                {"action": action, "date": self.dropoff.isoformat(), **extra}, follow=True)

    def load_all(self):
        from rentals import bins as binlib
        from rentals.models import Bin, StockState
        for b in Bin.objects.filter(status=StockState.READY)[:20]:
            binlib.load_bin(self.booking, b.code)

    def test_route_shows_door_buttons(self):
        page = self.client.get(f"/ops/day/?date={self.dropoff.isoformat()}")
        self.assertContains(page, ">Dropped off</button>")

    def test_dropoff_needs_a_full_load(self):
        r = self.door("dropoff", "dropped_off")
        self.assertContains(r, "Load isn")
        self.assertEqual(Booking.objects.get().status, "confirmed")

    def test_dropped_off_with_photo_and_note(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        self.load_all()
        buf = io.BytesIO()
        Image.new("RGB", (3000, 2000), "green").save(buf, "JPEG")
        photo = SimpleUploadedFile("IMG_1.jpg", buf.getvalue(), content_type="image/jpeg")
        with self.settings(MEDIA_ROOT=self._tmp()):
            r = self.door("dropoff", "dropped_off", photo=photo, note="Side porch")
            self.assertContains(r, "dropped off.")
            v = self.visit("dropoff")
            self.assertEqual(v.status, "done")
            self.assertIn("Side porch", v.notes)
            with Image.open(v.photo.path) as saved:
                self.assertEqual(max(saved.size), 1600)  # shrunk from 3000 px
            self.assertEqual(self.client.get(f"/ops/visit/{v.pk}/photo").status_code, 200)
            self.client.logout()
            self.assertEqual(self.client.get(f"/ops/visit/{v.pk}/photo").status_code, 302)  # owners only
        self.assertEqual(Booking.objects.get().status, "out")
        self.assertIn("Your totes are here", mail.outbox[0].subject)

    def test_picked_up_and_not_ready(self):
        self.load_all()
        self.door("dropoff", "dropped_off")
        TimeWindow.objects.create(date=self.pickup + timedelta(days=7), start=time(9), end=time(13))
        mail.outbox.clear()
        r = self.door("pickup", "not_ready", note="Still packing")
        self.assertContains(r, "not ready, extended to")
        b = Booking.objects.get()
        self.assertEqual((b.status, b.renewals, b.pickup_date), ("out", 1, self.pickup + timedelta(days=7)))
        self.assertIn("Not ready at pickup", self.visit("pickup").notes)
        self.assertIn("back next week", mail.outbox[0].subject)
        r = self.door("pickup", "picked_up")
        self.assertContains(r, "picked up.")
        self.assertEqual(Booking.objects.get().status, "picked_up")

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d


class DoorShortStockTests(DoorTests):
    """Not bought yet: a clear message, not an error page."""

    def setUp(self):
        FlowTestCase.setUp(self)
        self.walk(package="studio")
        self.booking = Booking.objects.get()
        self.client.login(username="owner", password="x")

    def test_dropoff_without_the_dolly_on_record(self):
        self.load_all()
        r = self.door("dropoff", "dropped_off")
        self.assertContains(r, "record the purchase first")
        self.assertEqual(Booking.objects.get().status, "confirmed")

    test_route_shows_door_buttons = test_dropoff_needs_a_full_load = None
    test_dropped_off_with_photo_and_note = test_picked_up_and_not_ready = None


class AddressChangeTests(FlowTestCase):
    """Fake maps: 21218 → 4 mi, 21401 → 26 mi ($25), 20001 → 36 mi (refused)."""

    def setUp(self):
        super().setUp()
        self.walk()  # dropoff 21218, pickup 21401 ($25)
        self.booking = Booking.objects.get()
        self.token = links.make_token(self.booking)
        mail.outbox.clear()

    def post(self, kind, zip_code, line1="9 New St"):
        return self.client.post(f"/b/{self.token}/address/{kind}/", {
            f"{kind}-line1": line1, f"{kind}-line2": "", f"{kind}-city": "Baltimore",
            f"{kind}-state": "MD", f"{kind}-zip_code": zip_code}, follow=True)

    def test_change_links_on_booking_page(self):
        page = self.client.get(f"/b/{self.token}/")
        self.assertContains(page, f"/b/{self.token}/address/dropoff/")
        self.assertContains(page, f"/b/{self.token}/address/pickup/")

    def test_pickup_moves_into_free_zone(self):
        r = self.post("pickup", "21218")
        self.assertContains(r, "Address updated")
        b = Booking.objects.get()
        self.assertEqual((b.pickup_address.line1, b.pickup_miles, b.delivery_fee), ("9 New St", 4, D("0")))
        self.assertFalse(b.price_lines.filter(kind="delivery").exists())
        self.assertIn("Address updated", mail.outbox[0].subject)
        self.assertIn("New pickup address", mail.outbox[1].subject)
        self.assertEqual(mail.outbox[1].to, ["owner@example.com"])

    def test_too_far_is_refused(self):
        r = self.post("dropoff", "20001")
        self.assertContains(r, "we serve within 35 miles")
        self.assertEqual(Booking.objects.get().dropoff_address.line1, "10 Elm St")

    def test_dropoff_locked_after_cutoff(self):
        with mock.patch.object(timezone, "now", return_value=services.free_cancel_until(self.booking)):
            ok, reason = services.address_change_allowed(self.booking, "dropoff")
        self.assertFalse(ok)
        self.assertIn("72 hours", reason)

    def test_after_dropoff_same_fee_ok_but_fee_change_needs_us(self):
        Booking.objects.filter(pk=self.booking.pk).update(status=Booking.Status.OUT)
        self.assertContains(self.post("pickup", "21403"), "Address updated")  # 28 mi: still $25
        r = self.post("pickup", "21218")  # free zone would change the fee after the card was charged
        self.assertContains(r, "changes the delivery fee")
        self.assertEqual(Booking.objects.get().pickup_address.zip_code, "21403")
        self.assertContains(self.client.get(f"/b/{self.token}/address/dropoff/", follow=True),
                            "already delivered")
