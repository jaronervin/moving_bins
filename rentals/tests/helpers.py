from datetime import date

from decimal import Decimal

from rentals.models import Address, Booking, Customer, Product


def bins(planned_capacity=None):
    """The tote product. Tests book against what they own unless they set a planned capacity."""
    p = Product.objects.get(slug="bin-27gal")
    if p.planned_capacity != planned_capacity:
        p.planned_capacity = planned_capacity
        p.save(update_fields=["planned_capacity"])
    return p


def make_booking(dropoff=date(2027, 1, 9), weeks=1, **kwargs):
    customer = Customer.objects.create(name="Pat Renter", email="pat@example.com")
    address = Address.objects.create(line1="1 Main St", city="Baltimore", zip_code="21214")
    return Booking(customer=customer, dropoff_address=address, dropoff_date=dropoff,
                   weeks=weeks, **kwargs)


def blankets():
    """A counted (not individually tracked) product, for ledger tests."""
    p, _ = Product.objects.get_or_create(
        slug="test-blanket", defaults={"name": "Moving blanket", "replacement_price": Decimal("10"),
                                       "lid_replacement_price": Decimal("5"), "track_stock": True,
                                       "spare_lid_product": Product.objects.get(slug="spare-lid-27gal")})
    return p


def assert_redirects_to_booking(test, response, booking, fragment=""):
    """Booking links carry a timestamp, so compare which booking they open, not the exact text."""
    from rentals import links

    test.assertEqual(response.status_code, 302)
    location = response["Location"]
    test.assertTrue(location.startswith("/b/") and location.endswith("/" + fragment), location)
    token = location[len("/b/"):].split("/")[0]
    test.assertEqual(links.read_token(token), booking.reference)
