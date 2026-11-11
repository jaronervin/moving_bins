from datetime import date

from rentals.models import Address, Booking, Customer, Product


def bins():
    return Product.objects.get(slug="bin-27gal")


def make_booking(dropoff=date(2027, 1, 9), weeks=1, **kwargs):
    customer = Customer.objects.create(name="Pat Renter", email="pat@example.com")
    address = Address.objects.create(line1="1 Main St", city="Baltimore", zip_code="21214")
    return Booking(customer=customer, dropoff_address=address, dropoff_date=dropoff,
                   weeks=weeks, **kwargs)
