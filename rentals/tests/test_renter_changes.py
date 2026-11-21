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
