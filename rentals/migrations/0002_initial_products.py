"""Set up the products and price points the business launches with.

Stock itself (the first 100 bins) is recorded as a purchase in admin, since
that's a real-world event with a date.
"""

from decimal import Decimal

from django.db import migrations


def create(apps, schema_editor):
    Product = apps.get_model("rentals", "Product")
    PricePoint = apps.get_model("rentals", "PricePoint")
    BusinessSettings = apps.get_model("rentals", "BusinessSettings")

    lids, _ = Product.objects.get_or_create(
        slug="spare-lid-27gal",
        defaults={
            "name": "Spare lid (27 gal)",
            "description": "Lids saved from broken bins. Not rented on their own.",
            "is_rentable": False,
            "active": False,
            "sort_order": 100,
        },
    )
    bins, created = Product.objects.get_or_create(
        slug="bin-27gal",
        defaults={
            "name": "27-gallon bin with lid",
            "description": "Durable plastic moving bin, lid included.",
            "minimum_quantity": 20,
            "replacement_price": Decimal("10.00"),
            "lid_replacement_price": Decimal("5.00"),
            "spare_lid_product": lids,
            "sort_order": 0,
        },
    )
    if created:
        PricePoint.objects.create(product=bins, quantity=20, unit_rate=Decimal("5.00"))
        PricePoint.objects.create(product=bins, quantity=100, unit_rate=Decimal("3.00"))
    BusinessSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("rentals", "0001_initial")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
