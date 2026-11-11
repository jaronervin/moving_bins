"""Inventory ledger, availability and holds."""

from datetime import date, datetime, timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone

from rentals import inventory, services
from rentals.models import Booking, StockState, Visit

from .helpers import bins, make_booking

NOW = timezone.make_aware(datetime(2027, 1, 1, 12, 0))


class LedgerTests(TestCase):
    def setUp(self):
        self.bins = bins()
        inventory.record_purchase(self.bins, 100)

    def test_purchase_goes_to_ready(self):
        self.assertEqual(inventory.counts(self.bins)[StockState.READY], 100)

    def test_full_rental_cycle(self):
        booking = services.place_hold(make_booking(), [(self.bins, 40)], now=NOW)
        booking.status = Booking.Status.CONFIRMED
        booking.save()

        inventory.record_dropoff(booking)
        c = inventory.counts(self.bins)
        self.assertEqual((c["ready"], c["out"]), (60, 40))

        inventory.record_pickup(booking)
        Visit.objects.create(booking=booking, kind="pickup", status="done", completed_at=NOW)
        self.assertEqual(inventory.counts(self.bins)["awaiting_check_in"], 40)

        check = inventory.record_check_in(
            booking, self.bins, good=35, broken=2, lids_salvaged=2, lid_issues=2, missing=1, now=NOW
        )
        c = inventory.counts(self.bins)
        self.assertEqual(c, {"ready": 95, "out": 0, "awaiting_check_in": 0,
                             "out_of_service": 2, "retired": 2})
        # 3 bins (2 broken + 1 missing) × $10 + 2 lids × $5
        self.assertEqual(check.damage_amount, D("40.00"))

        lids = self.bins.spare_lid_product
        self.assertEqual(inventory.counts(lids)["ready"], 2)
        inventory.repair_with_spare_lids(self.bins, 2)
        self.assertEqual(inventory.counts(self.bins)["ready"], 97)
        self.assertEqual(inventory.counts(lids)["ready"], 0)

    def test_check_in_must_add_up(self):
        booking = services.place_hold(make_booking(), [(self.bins, 20)], now=NOW)
        with self.assertRaises(inventory.InventoryError):
            inventory.record_check_in(booking, self.bins, good=19)

    def test_late_check_in_waives_damage(self):
        booking = services.place_hold(make_booking(), [(self.bins, 20)], now=NOW)
        booking.status = Booking.Status.CONFIRMED
        booking.save()
        inventory.record_dropoff(booking)
        inventory.record_pickup(booking)
        Visit.objects.create(booking=booking, kind="pickup", status="done", completed_at=NOW)
        check = inventory.record_check_in(booking, self.bins, good=19, missing=1,
                                          now=NOW + timedelta(days=4))
        self.assertEqual(check.damage_amount, D("0.00"))
        self.assertIn("deadline", check.notes)

    def test_cant_drop_off_more_than_ready(self):
        booking = services.place_hold(make_booking(), [(self.bins, 20)], now=NOW)
        inventory._move(self.bins, 90, StockState.READY, StockState.OUT_OF_SERVICE, "adjustment")
        with self.assertRaises(inventory.InventoryError):
            inventory.record_dropoff(booking)


class AvailabilityTests(TestCase):
    def setUp(self):
        self.bins = bins()
        inventory.record_purchase(self.bins, 100)

    def hold(self, qty, dropoff, weeks=1):
        return services.place_hold(make_booking(dropoff=dropoff, weeks=weeks), [(self.bins, qty)], now=NOW)

    def test_pickup_date_is_whole_weeks(self):
        b = self.hold(20, date(2027, 1, 9), weeks=2)
        self.assertEqual(b.pickup_date, date(2027, 1, 23))

    def test_overlapping_bookings_share_the_fleet(self):
        self.hold(60, date(2027, 1, 9))
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 12), date(2027, 1, 19), now=NOW), 40)

    def test_pickup_day_and_dropoff_day_overlap(self):
        # Booking 1 is picked up Jan 16; a booking dropping off Jan 16 can't use those bins.
        self.hold(60, date(2027, 1, 9))
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 16), date(2027, 1, 23), now=NOW), 40)
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 17), date(2027, 1, 24), now=NOW), 100)

    def test_overbooking_refused(self):
        self.hold(60, date(2027, 1, 9))
        with self.assertRaises(services.NotEnoughStock) as ctx:
            self.hold(41, date(2027, 1, 12))
        self.assertEqual(ctx.exception.free, 40)
        self.assertEqual(Booking.objects.filter(status="requested").count(), 1)

    def test_expired_holds_free_the_bins(self):
        self.hold(100, date(2027, 1, 9))
        later = NOW + timedelta(hours=49)
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 9), date(2027, 1, 16), now=later), 100)
        self.assertEqual(services.expire_holds(now=later), 1)
        self.assertEqual(Booking.objects.get().status, Booking.Status.EXPIRED)

    def test_confirmed_bookings_dont_expire(self):
        b = self.hold(100, date(2027, 1, 9))
        b.status = Booking.Status.CONFIRMED
        b.save()
        later = NOW + timedelta(days=5)
        self.assertEqual(services.expire_holds(now=later), 0)
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 9), date(2027, 1, 16), now=later), 0)

    def test_renewal_extends_occupancy(self):
        b = self.hold(100, date(2027, 1, 9))
        b.status = Booking.Status.OUT
        b.renewals = 1
        b.save()
        self.assertEqual(b.pickup_date, date(2027, 1, 23))
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 20), date(2027, 1, 27), now=NOW), 0)

    def test_out_of_service_bins_not_bookable(self):
        inventory._move(self.bins, 10, StockState.READY, StockState.OUT_OF_SERVICE, "adjustment")
        self.assertEqual(inventory.available(self.bins, date(2027, 1, 9), date(2027, 1, 16), now=NOW), 90)

    def test_rebooking_same_booking_excludes_itself(self):
        b = self.hold(100, date(2027, 1, 9))
        services.place_hold(b, [(self.bins, 90)], now=NOW)  # change quantity
        self.assertEqual(b.lines.get().quantity, 90)

    def test_locked_rate(self):
        b = self.hold(35, date(2027, 1, 9))
        line = b.lines.get()
        self.assertEqual(line.locked_weekly_total, D("162.05"))

    def test_ready_shortfall_warning(self):
        a = self.hold(70, date(2027, 1, 2))
        b = self.hold(30, date(2027, 1, 4))
        for bk in (a, b):
            bk.status = Booking.Status.CONFIRMED
            bk.save()
        inventory._move(self.bins, 10, StockState.READY, StockState.OUT_OF_SERVICE, "adjustment")
        warnings = inventory.ready_shortfalls(self.bins, today=date(2027, 1, 1))
        self.assertEqual(warnings, [(date(2027, 1, 4), 100, 90)])
