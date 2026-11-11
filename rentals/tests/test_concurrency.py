"""Two renters submitting at the same moment must not both get the last bins.

Needs a real database with row locking, so it only runs on Postgres:

    DATABASE_URL=postgres://... python manage.py test rentals
"""

import threading
import unittest
from datetime import date

from django.db import connection
from django.test import TransactionTestCase

from rentals import inventory, services
from rentals.models import Booking

from .helpers import bins, make_booking


@unittest.skipUnless(connection.vendor == "postgresql", "needs Postgres row locks")
class SimultaneousHoldTests(TransactionTestCase):
    serialized_rollback = True  # keep the products created by migrations

    def test_only_one_of_two_simultaneous_holds_succeeds(self):
        product = bins()
        inventory.record_purchase(product, 100)
        drafts = [make_booking(dropoff=date(2027, 1, 9)) for _ in range(2)]
        start = threading.Barrier(2)
        results = []

        def submit(draft):
            start.wait()  # release both threads at once
            try:
                services.place_hold(draft, [(product, 60)])
                results.append("ok")
            except services.NotEnoughStock:
                results.append("refused")
            finally:
                connection.close()

        threads = [threading.Thread(target=submit, args=(d,)) for d in drafts]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), ["ok", "refused"])
        self.assertEqual(Booking.objects.filter(status="requested").count(), 1)
