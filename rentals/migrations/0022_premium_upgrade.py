"""The Premium upgrade: $79 for the first 2 weeks, any package can add it.

PLACEHOLDERS until Katie's list: 10 moving blankets + 1 floor runner & door
jamb guard set, $20 per extra week. All editable in admin (Upgrades).
"""

from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Upgrade = apps.get_model("rentals", "Upgrade")
    UpgradeItem = apps.get_model("rentals", "UpgradeItem")
    Product = apps.get_model("rentals", "Product")
    up = Upgrade.objects.create(
        name="Premium upgrade",
        description="Moving blankets plus floor and door-frame protection, so nothing gets scuffed.",
        price=Decimal("79.00"), extra_week_price=Decimal("20.00"),
    )
    for slug, qty in [("moving-blanket", 10), ("floor-door-protection", 1)]:
        product = Product.objects.filter(slug=slug).first()
        if product:
            UpgradeItem.objects.create(upgrade=up, product=product, quantity=qty)


def backwards(apps, schema_editor):
    apps.get_model("rentals", "Upgrade").objects.filter(name="Premium upgrade").delete()


class Migration(migrations.Migration):
    dependencies = [("rentals", "0021_upgrades")]
    operations = [migrations.RunPython(forwards, backwards)]
