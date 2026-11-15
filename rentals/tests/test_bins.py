"""Individually tracked bins: codes, loading, pickup, check-in, renter notes, owner tools."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import segno
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from rentals import bins, inventory, links, services
from rentals.models import Bin, BinContents, Booking, StockState, Visit

from .helpers import assert_redirects_to_booking, bins as bin_product, make_booking

NOW = timezone.make_aware(datetime(2027, 1, 1, 12, 0))


def rental(qty=3, with_pickup_address=True):
    """A confirmed booking for ``qty`` bins, with dropoff/pickup visits."""
    from rentals.models import Address, PricePoint
    # Small test rentals: allow orders below the real 20-bin minimum.
    PricePoint.objects.get_or_create(product=bin_product(), quantity=1, defaults={"unit_rate": D("5")})
    b = services.place_hold(make_booking(dropoff=date(2027, 1, 9)), [(bin_product(), qty)], now=NOW)
    if with_pickup_address:
        b.pickup_address = Address.objects.create(line1="2 New St", city="Baltimore", zip_code="21218")
    b.status = Booking.Status.CONFIRMED
    b.save()
    Visit.objects.create(booking=b, kind="dropoff", status="confirmed")
    Visit.objects.create(booking=b, kind="pickup", status="confirmed")
    return b


class CodeTests(TestCase):
    def test_codes_are_unique_readable_and_clean(self):
        made = bins.register_bins(bin_product(), 300)
        codes = [b.code for b in made]
        self.assertEqual(len(set(codes)), 300)
        for c in codes:
            self.assertEqual(len(c), 3)
            self.assertFalse(set(c) & set("01OIL"))
            self.assertNotIn(c, bins.BLOCKED_CODES)

    def test_normalize(self):
        self.assertEqual(bins.normalize_code(" k7q "), "K7Q")
        self.assertEqual(bins.normalize_code("HTTPS://SPROUTTOTE.COM/Q/K7Q"), "K7Q")
        self.assertEqual(bins.normalize_code("https://sprouttoteco.com/q/k7q/?x=1"), "K7Q")
        self.assertEqual(bins.normalize_code("not a code!"), "")

    def test_purchase_creates_bins(self):
        inventory.record_purchase(bin_product(), 5)
        self.assertEqual(inventory.counts(bin_product())["ready"], 5)

    def test_label_qr_is_small(self):
        qr = segno.make_qr(settings.QR_BASE_URL + "K7Q", error="m", boost_error=True)
        self.assertEqual(qr.mode, "alphanumeric")
        self.assertLessEqual(qr.version, 2)  # 25x25 modules: easy to scan at 1.2 inches


class LifecycleTests(TestCase):
    def setUp(self):
        self.stock = bins.register_bins(bin_product(), 10)
        self.codes = [b.code for b in self.stock]

    def test_full_cycle_with_damage_and_missing(self):
        b = rental(qty=4)
        a, bb, c, d = self.codes[:4]
        for code in (a, bb, c):
            bins.load_bin(b, code)
        with self.assertRaisesMessage(bins.BinError, "Load isn't complete: 3 of 4"):
            bins.complete_dropoff(b)
        bins.load_bin(b, d.lower())  # typed in lower case
        with self.assertRaisesMessage(bins.BinError, "already has all 4"):
            bins.load_bin(b, self.codes[4])
        bins.complete_dropoff(b, now=NOW)
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.OUT)
        self.assertEqual(inventory.counts(bin_product())["out"], 4)

        for code in (a, bb, c):  # d never comes back
            bins.pickup_bin(b, code)
        self.assertEqual(bins.pickup_progress(b), (3, 4))
        self.assertEqual(bins.complete_pickup(b, now=NOW + timedelta(days=7)), [d])

        bins.check_in_bin(a, "good")
        with self.assertRaisesMessage(bins.BinError, "Still to check in"):
            bins.finish_check_in(b)
        bins.check_in_bin(bb, "broken", lid_salvaged=True)
        bins.check_in_bin(c, "lid")
        [summary] = bins.finish_check_in(b, now=NOW + timedelta(days=8))
        # 1 broken + 1 missing at $10, 1 lid at $5
        self.assertEqual(summary.damage_amount, D("25.00"))
        self.assertIn(d, summary.notes)
        statuses = {x.code: x.status for x in Bin.objects.filter(code__in=[a, bb, c, d])}
        self.assertEqual(statuses, {a: "ready", bb: "retired", c: "out_of_service", d: "missing"})
        self.assertEqual(Bin.objects.get(code=a).moves, 1)

        lids = bin_product().spare_lid_product
        self.assertEqual(inventory.counts(lids)["ready"], 1)
        bins.repair_with_spare_lid(c)
        self.assertEqual(Bin.objects.get(code=c).status, "ready")
        self.assertEqual(inventory.counts(lids)["ready"], 0)

        bins.found_bin(d)
        bins.check_in_bin(d, "good")
        self.assertEqual(Bin.objects.get(code=d).status, "ready")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.CLOSED)

    def test_cant_load_a_bin_thats_out_elsewhere(self):
        first, second = rental(qty=1), rental(qty=1)
        bins.load_bin(first, self.codes[0])
        with self.assertRaisesMessage(bins.BinError, first.reference):
            bins.load_bin(second, self.codes[0])

    def test_pickup_rejects_another_rentals_bin(self):
        first, second = rental(qty=1), rental(qty=1)
        bins.load_bin(first, self.codes[0]); bins.complete_dropoff(first)
        bins.load_bin(second, self.codes[1]); bins.complete_dropoff(second)
        with self.assertRaisesMessage(bins.BinError, f"belongs to {second.reference}"):
            bins.pickup_bin(first, self.codes[1])

    def test_no_dropoff_without_moving_in_address(self):
        b = rental(qty=1, with_pickup_address=False)
        bins.load_bin(b, self.codes[0])
        with self.assertRaisesMessage(bins.BinError, "moving-in address"):
            bins.complete_dropoff(b)

    def test_undo_scans(self):
        b = rental(qty=1)
        bins.load_bin(b, self.codes[0])
        bins.unload_bin(b, self.codes[0])
        self.assertEqual(Bin.objects.get(code=self.codes[0]).status, "ready")

    def test_late_check_in_waives_damage(self):
        b = rental(qty=1)
        bins.load_bin(b, self.codes[0]); bins.complete_dropoff(b)
        bins.complete_pickup(b, now=NOW)  # not scanned → missing
        [summary] = bins.finish_check_in(b, now=NOW + timedelta(days=4))
        self.assertEqual(summary.damage_amount, D("0.00"))
        self.assertIn("deadline", summary.notes)

    def test_availability_counts_bins(self):
        Bin.objects.filter(code__in=self.codes[:3]).update(status=StockState.MISSING)
        self.assertEqual(inventory.available(bin_product(), date(2027, 1, 9), date(2027, 1, 16), now=NOW), 7)

    def test_purge_contents_after_30_days(self):
        b = rental(qty=1)
        bins.load_bin(b, self.codes[0]); bins.complete_dropoff(b)
        BinContents.objects.create(booking=b, bin=Bin.objects.get(code=self.codes[0]), contents="books")
        bins.complete_pickup(b, now=NOW)
        self.assertEqual(bins.purge_old_contents(now=NOW + timedelta(days=29)), 0)
        self.assertEqual(bins.purge_old_contents(now=NOW + timedelta(days=31)), 1)


class RenterToteTests(TestCase):
    def setUp(self):
        self.code = bins.register_bins(bin_product(), 1)[0].code
        self.booking = rental(qty=1)
        bins.load_bin(self.booking, self.code)
        bins.complete_dropoff(self.booking)

    def test_stranger_sees_nothing_private(self):
        r = self.client.get(f"/Q/{self.code}")
        self.assertContains(r, "Sprout Tote Co. moving tote")
        self.assertNotContains(r, "Room at the new place")
        self.assertNotContains(r, self.booking.customer.name)

    def test_renter_labels_and_searches(self):
        self.client.get(links.booking_url(self.booking))  # opening the link remembers this browser
        r = self.client.get(f"/Q/{self.code}")
        self.assertContains(r, "Room at the new place")
        r = self.client.post(f"/q/{self.code}", {"contents": "Pots and pans", "room": "Kitchen",
                                                 "fragile": "on"})
        assert_redirects_to_booking(self, r, self.booking, fragment="#totes")
        note = BinContents.objects.get()
        self.assertEqual((note.contents, note.room, note.fragile, note.open_first),
                         ("Pots and pans", "Kitchen", True, False))
        page = self.client.get(links.booking_url(self.booking))
        self.assertContains(page, "Pots and pans")
        self.assertContains(page, self.code)

    def test_lower_case_and_url_codes_redirect(self):
        r = self.client.get(f"/q/{self.code.lower()}")
        self.assertRedirects(r, f"/q/{self.code}", fetch_redirect_response=False)

    def test_read_only_after_pickup(self):
        self.client.get(links.booking_url(self.booking))
        bins.complete_pickup(self.booking)
        r = self.client.post(f"/q/{self.code}", {"contents": "changed"})
        self.assertContains(r, "can't be changed")
        self.assertFalse(BinContents.objects.exists())

    def test_staff_go_to_bin_page(self):
        User.objects.create_user("owner", password="x", is_staff=True)
        self.client.login(username="owner", password="x")
        self.assertRedirects(self.client.get(f"/Q/{self.code}"), f"/ops/bin/{self.code}/")


class OpsTests(TestCase):
    def setUp(self):
        self.codes = [b.code for b in bins.register_bins(bin_product(), 3)]
        self.booking = rental(qty=2)
        User.objects.create_user("owner", password="x", is_staff=True)

    def scan(self, **body):
        return self.client.post("/ops/api/scan", json.dumps(body), content_type="application/json").json()

    def test_requires_staff(self):
        for url in ("/ops/", "/ops/check-in/", "/ops/labels/", f"/ops/b/{self.booking.reference}/load/"):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        r = self.client.post("/ops/api/scan", "{}", content_type="application/json")
        self.assertEqual(r.status_code, 302)

    def test_scan_load_pickup_check_in(self):
        self.client.login(username="owner", password="x")
        ref = self.booking.reference
        self.assertContains(self.client.get(f"/ops/b/{ref}/load/"), "0</span> <span class=\"muted\">of</span> <span id=\"total\">2")
        r = self.scan(mode="load", reference=ref, code=f"HTTPS://SPROUTTOTE.COM/Q/{self.codes[0]}")
        self.assertEqual((r["ok"], r["done"], r["total"]), (True, 1, 2))
        r = self.scan(mode="load", reference=ref, code=self.codes[0])
        self.assertFalse(r["ok"])
        self.scan(mode="load", reference=ref, code=self.codes[1])
        r = self.client.post(f"/ops/b/{ref}/load/", {"action": "finish"})
        self.assertRedirects(r, "/ops/")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "out")

        for c in self.codes[:2]:
            self.assertTrue(self.scan(mode="pickup", reference=ref, code=c)["ok"])
        self.client.post(f"/ops/b/{ref}/pickup/", {"action": "finish"})
        r = self.scan(mode="check_in", code=self.codes[0], outcome="good")
        self.assertEqual(r["message"], f"{self.codes[0]}: good")
        r = self.scan(mode="check_in", code=self.codes[1], outcome="lid")
        self.assertIn("needs a lid", r["message"])
        r = self.client.post(f"/ops/b/{ref}/finish-check-in", follow=True)
        self.assertContains(r, "Damage to charge: $5.00")

    def test_labels(self):
        self.client.login(username="owner", password="x")
        r = self.client.get("/ops/labels/", {"codes": ",".join(self.codes[:2]), "copies": 2})
        self.assertEqual(r.content.decode().count("<svg"), 4)
        self.assertContains(r, self.codes[0])

    def test_bin_page(self):
        self.client.login(username="owner", password="x")
        r = self.client.get(f"/ops/bin/{self.codes[0]}/")
        self.assertContains(r, "Added to inventory")


class WarehouseScanTests(TestCase):
    """Pickup without scanning at the door; totes are scanned at check-in."""

    def test_scan_only_at_check_in(self):
        codes = [b.code for b in bins.register_bins(bin_product(), 3)]
        b = rental(qty=3)
        for c in codes:
            bins.load_bin(b, c)
        bins.complete_dropoff(b)
        self.assertEqual(bins.complete_pickup(b, now=NOW), sorted(codes))  # none scanned
        self.assertEqual(bins.check_in_progress(b), (0, 0, 3))
        bins.check_in_bin(codes[0], "good")
        bins.check_in_bin(codes[1], "broken")
        self.assertEqual(bins.check_in_progress(b), (2, 0, 1))
        [summary] = bins.finish_check_in(b, now=NOW + timedelta(days=1))
        self.assertEqual((summary.good, summary.broken, summary.missing), (1, 1, 1))
        self.assertEqual(summary.damage_amount, D("20.00"))
        kinds = list(Bin.objects.get(code=codes[0]).events.values_list("kind", flat=True))
        self.assertIn("picked_up", kinds)

    def test_cant_check_in_a_tote_still_at_the_renters(self):
        code = bins.register_bins(bin_product(), 1)[0].code
        b = rental(qty=1)
        bins.load_bin(b, code)
        bins.complete_dropoff(b)
        with self.assertRaisesMessage(bins.BinError, "isn't waiting for check-in"):
            bins.check_in_bin(code)


class ReservedCodeTests(TestCase):
    """Codes printed ahead of time aren't inventory until stuck on a tote."""

    def test_reserved_codes_not_bookable_until_activated(self):
        made = bins.register_bins(bin_product(), 5, reserved=True)
        bins.register_bins(bin_product(), 2)
        self.assertEqual(inventory.available(bin_product(), date(2027, 1, 9), date(2027, 1, 16), now=NOW), 2)
        with self.assertRaisesMessage(bins.BinError, "Good"):
            bins.check_in_bin(made[0].code, "broken")
        bins.check_in_bin(made[0].code, "good")  # scanned in at the check-in page
        bins.activate_bin(made[1].code)            # or from its bin page
        self.assertEqual(inventory.available(bin_product(), date(2027, 1, 9), date(2027, 1, 16), now=NOW), 4)
        self.assertEqual(inventory.counts(bin_product())["reserved"], 3)
        with self.assertRaisesMessage(bins.BinError, "already in service"):
            bins.activate_bin(made[1].code)

    def test_reserved_codes_cant_be_loaded(self):
        bins.register_bins(bin_product(), 1)  # one real tote, so the rental can be booked
        code = bins.register_bins(bin_product(), 1, reserved=True)[0].code
        with self.assertRaisesMessage(bins.BinError, "isn't on the shelf"):
            bins.load_bin(rental(qty=1), code)

    def test_create_codes_command(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("create_codes", "4", stdout=out)
        self.assertIn("Created 4 codes (reserved for printing)", out.getvalue())
        self.assertEqual(Bin.objects.filter(status="reserved").count(), 4)
