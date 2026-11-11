from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from rentals import inventory

from .helpers import bins


class HomePageTests(TestCase):
    def setUp(self):
        inventory.record_purchase(bins(), 100)

    def test_shows_rate_table(self):
        r = self.client.get("/")
        self.assertContains(r, "$4.50")
        self.assertContains(r, "$300.00")

    def test_quote(self):
        d = (timezone.localdate() + timedelta(days=10)).isoformat()
        r = self.client.get("/", {"quantity": 35, "dropoff_date": d, "weeks": 2})
        self.assertContains(r, "$324.10")
        self.assertContains(r, "100 bins free")

    def test_below_minimum(self):
        d = (timezone.localdate() + timedelta(days=10)).isoformat()
        r = self.client.get("/", {"quantity": 5, "dropoff_date": d, "weeks": 1})
        self.assertContains(r, "minimum order is 20")
