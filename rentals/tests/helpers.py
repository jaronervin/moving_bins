from datetime import date

from decimal import Decimal

from rentals.models import Address, Booking, Customer, PricePoint, Product


def bins():
    return Product.objects.get(slug="bin-27gal")


def make_booking(dropoff=date(2027, 1, 9), weeks=1, **kwargs):
    customer = Customer.objects.create(name="Pat Renter", email="pat@example.com")
    address = Address.objects.create(line1="1 Main St", city="Baltimore", zip_code="21214")
    return Booking(customer=customer, dropoff_address=address, dropoff_date=dropoff,
                   weeks=weeks, **kwargs)


def blankets():
    """A counted (not individually tracked) product, for ledger tests."""
    p, created = Product.objects.get_or_create(
        slug="blanket", defaults={"name": "Moving blanket", "replacement_price": Decimal("10"),
                                  "lid_replacement_price": Decimal("5"),
                                  "spare_lid_product": Product.objects.get(slug="spare-lid-27gal")})
    if created:
        PricePoint.objects.create(product=p, quantity=1, unit_rate=Decimal("2.00"))
    return p
