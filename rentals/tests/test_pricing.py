"""Pricing rules. Pure functions, so these don't touch the database."""

from decimal import Decimal as D

from django.test import SimpleTestCase

from rentals import pricing

BINS = [(20, D("5.00")), (100, D("3.00"))]


class VolumePricingTests(SimpleTestCase):
    def test_spec_table(self):
        expected = {20: "100.00", 35: "162.05", 40: "180.00", 60: "240.00", 80: "280.00", 100: "300.00"}
        for qty, total in expected.items():
            with self.subTest(qty=qty):
                self.assertEqual(pricing.weekly_total(BINS, qty), D(total))

    def test_rate_is_flat_past_last_point(self):
        self.assertEqual(pricing.unit_rate(BINS, 150), D("3.00"))

    def test_below_minimum_refused(self):
        with self.assertRaises(pricing.PricingError):
            pricing.unit_rate(BINS, 19)

    def test_bigger_order_never_costs_less(self):
        totals = [pricing.weekly_total(BINS, n) for n in range(20, 201)]
        self.assertEqual(totals, sorted(totals))
        self.assertEqual(len(set(totals)), len(totals))

    def test_single_point_is_flat_price(self):
        dolly = [(1, D("15.00"))]
        self.assertEqual(pricing.weekly_total(dolly, 3), D("45.00"))

    def test_points_order_doesnt_matter(self):
        self.assertEqual(pricing.unit_rate(list(reversed(BINS)), 60), D("4.00"))


class DistanceTests(SimpleTestCase):
    def fee(self, miles):
        return pricing.pickup_fee(miles, free_miles=30, max_miles=60, rate_per_mile=D("3.00"))

    def test_free_within_30(self):
        self.assertEqual(self.fee(30), (30, D("0.00")))
        self.assertEqual(self.fee(12.3), (12, D("0.00")))

    def test_per_mile_beyond_30(self):
        self.assertEqual(self.fee(42), (42, D("36.00")))
        self.assertEqual(self.fee(60), (60, D("90.00")))

    def test_rounds_to_nearest_mile(self):
        self.assertEqual(self.fee(30.4), (30, D("0.00")))
        self.assertEqual(self.fee(30.5), (31, D("3.00")))

    def test_pickup_over_max_refused(self):
        with self.assertRaises(pricing.OutOfRange):
            self.fee(60.6)

    def test_dropoff_hard_limit(self):
        self.assertEqual(pricing.check_dropoff_distance(29.6, 30), 30)
        with self.assertRaises(pricing.OutOfRange):
            pricing.check_dropoff_distance(30.5, 30)


class QuoteTests(SimpleTestCase):
    def test_multi_week_with_pickup_fee(self):
        q = pricing.build_quote([("Bins", BINS, 35)], weeks=2, pickup_fee_amount=D("36"))
        self.assertEqual(q.lines[0].unit_rate, D("4.63"))
        self.assertEqual(q.weekly_subtotal, D("162.05"))
        self.assertEqual(q.rental_subtotal, D("324.10"))
        self.assertEqual(q.total_before_tax, D("360.10"))

    def test_zero_weeks_refused(self):
        with self.assertRaises(pricing.PricingError):
            pricing.build_quote([("Bins", BINS, 20)], weeks=0)


class ChargeRuleTests(SimpleTestCase):
    def test_late_cancel_is_half_of_rental(self):
        self.assertEqual(pricing.late_cancel_charge(D("162.05"), 50), D("81.03"))

    def test_damage(self):
        # 2 broken + 1 missing at $10, 3 lids at $5
        self.assertEqual(pricing.damage_charge(2, 1, 3, D("10"), D("5")), D("45.00"))
