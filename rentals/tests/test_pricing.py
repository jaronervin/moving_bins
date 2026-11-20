"""Pricing rules. Pure functions, so these don't touch the database."""

from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from rentals import pricing
from rentals.pricing import PackageTerms

STUDIO = PackageTerms("Studio/1BR", 20, D("119"), D("45"))
TWO_BR = PackageTerms("2BR", 40, D("169"), D("65"))
THREE_BR = PackageTerms("3BR", 60, D("229"), D("85"))
WHOLE = PackageTerms("Whole Home", 80, D("299"), D("99"))
PACKAGES = [STUDIO, TWO_BR, THREE_BR, WHOLE]
ADDON, ADDON_WEEK = D("5"), D("3")


def price(pkg, weeks=2, addons=0, **kw):
    return pricing.price_order(pkg, weeks, min_weeks=2, max_weeks=4, addon_totes=addons,
                               addon_price=ADDON, addon_extra_week_price=ADDON_WEEK, **kw)


class PackageTests(SimpleTestCase):
    def test_package_prices(self):
        self.assertEqual([price(p).total_before_tax for p in PACKAGES],
                         [D("119.00"), D("169.00"), D("229.00"), D("299.00")])

    def test_extra_weeks(self):
        self.assertEqual(price(TWO_BR, weeks=3).total_before_tax, D("234.00"))  # 169 + 65
        self.assertEqual(price(TWO_BR, weeks=4).total_before_tax, D("299.00"))  # 169 + 2 × 65

    def test_extra_totes(self):
        q = price(STUDIO, weeks=2, addons=5)
        self.assertEqual(q.total_before_tax, D("144.00"))  # 119 + 5 × $5
        q = price(STUDIO, weeks=4, addons=5)
        # package 119 + 2×45 = 209; totes 5 × (5 + 2×3) = 55
        self.assertEqual(q.total_before_tax, D("264.00"))
        self.assertEqual(q.lines[1].unit_price, D("11.00"))

    def test_renewal_week_price(self):
        self.assertEqual(price(TWO_BR).renewal_week_price, D("65.00"))
        self.assertEqual(price(TWO_BR, addons=10).renewal_week_price, D("95.00"))  # 65 + 10 × $3

    def test_length_limits(self):
        with self.assertRaisesMessage(pricing.PricingError, "at least 2 weeks"):
            price(STUDIO, weeks=1)
        with self.assertRaisesMessage(pricing.PricingError, "contact us"):
            price(STUDIO, weeks=5)

    def test_never_cheaper_to_build_the_next_package(self):
        """Package + extra totes should never undercut the next package up (Katie's Whole Home fix)."""
        for weeks in (2, 3, 4):
            for small, big in zip(PACKAGES, PACKAGES[1:]):
                build = price(small, weeks, addons=big.totes - small.totes).total_before_tax
                with self.subTest(weeks=weeks, small=small.name, big=big.name):
                    self.assertGreater(build, price(big, weeks).total_before_tax)


class ExtrasTests(SimpleTestCase):
    def test_rentals_are_one_time_and_sales_separate(self):
        extras = [("rental", "Moving blanket", 4, D("5")), ("sale", "Packing kit", 1, D("35")),
                  ("rental", "Hand truck", 0, D("15"))]
        q2 = price(STUDIO, weeks=2, extras=extras)
        q4 = price(STUDIO, weeks=4, extras=extras)
        self.assertEqual(q2.rental_subtotal, D("139.00"))  # 119 + 20
        self.assertEqual(q2.sales_subtotal, D("35.00"))
        self.assertEqual(q4.rental_subtotal - q2.rental_subtotal, D("90.00"))  # only the package grows
        self.assertEqual(len(q2.lines), 3)  # zero-quantity hand truck left out

    def test_late_cancel_excludes_sales_and_delivery(self):
        q = price(STUDIO, extras=[("sale", "Packing kit", 1, D("35"))],
                  delivery=[("Dropoff beyond 20 miles", D("25"))])
        self.assertEqual(q.total_before_tax, D("179.00"))
        self.assertEqual(pricing.late_cancel_charge(q.rental_subtotal, 50), D("59.50"))


class ZoneTests(SimpleTestCase):
    def test_zones(self):
        self.assertEqual(pricing.zone_fee(19.9, 20, 35, D("25")), D("0.00"))
        self.assertEqual(pricing.zone_fee(20.0, 20, 35, D("25")), D("0.00"))
        self.assertEqual(pricing.zone_fee(20.1, 20, 35, D("25")), D("25.00"))
        with self.assertRaisesMessage(pricing.OutOfRange, "within 35 miles"):
            pricing.zone_fee(35.2, 20, 35, D("25"))

    def test_straight_line(self):
        # Baltimore (21214) to Annapolis is roughly 26 miles as the crow flies.
        miles = pricing.straight_line_miles(39.352, -76.563, 38.978, -76.492)
        self.assertAlmostEqual(miles, 26, delta=1.5)


class OtherRuleTests(SimpleTestCase):
    def test_damage(self):
        self.assertEqual(pricing.damage_charge(2, 1, 3, D("10"), D("5")), D("45.00"))

    def test_business_days(self):
        monday = date(2027, 1, 11)
        self.assertEqual(pricing.business_days_before(monday, 3), date(2027, 1, 6))  # Wednesday
        friday = date(2027, 1, 15)
        self.assertEqual(pricing.business_days_before(friday, 3), date(2027, 1, 12))
