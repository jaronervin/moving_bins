from django.test import TestCase

from rentals import inventory

from .helpers import bins


class HomePageTests(TestCase):
    def setUp(self):
        inventory.record_purchase(bins(), 60)

    def test_shows_packages_and_add_ons(self):
        r = self.client.get("/")
        for text in ("Studio/1BR", "$119", "2BR", "$169", "3BR", "$229", "Whole Home", "$249",
                     "80 totes", "3 dollies", "1 hand truck", "1 dolly", "Appliance dolly", "Packing kit",
                     "$5 each for 2 weeks, then $3/week"):
            self.assertContains(r, text)

    def test_footer_contact(self):
        r = self.client.get("/")
        self.assertContains(r, "hello@sprouttoteco.com")
        self.assertContains(r, "703-703-2511")


class CountedTests(TestCase):
    def test_plurals(self):
        from rentals.models import counted

        cases = {(1, "Dolly"): "1 dolly", (3, "Dolly"): "3 dollies",
                 (45, "27-gallon tote with lid"): "45 27-gallon totes with lid",
                 (2, "Mattress bag (Queen/King)"): "2 mattress bags (Queen/King)",
                 (2, "Floor runner & door jamb guards"): "2 floor runner & door jamb guards",
                 (2, "Wardrobe box"): "2 wardrobe boxes", (1, "Hand truck"): "1 hand truck"}
        for (q, name), expected in cases.items():
            self.assertEqual(counted(q, name), expected)
