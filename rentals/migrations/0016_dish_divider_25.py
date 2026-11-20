"""Dish/glass divider is $25 (was a $15 placeholder)."""

from decimal import Decimal

from django.db import migrations


def set_price(price):
    def run(apps, schema_editor):
        apps.get_model("rentals", "Product").objects.filter(slug="dish-divider").update(price=Decimal(price))
    return run


class Migration(migrations.Migration):
    dependencies = [("rentals", "0015_agreement_2026_11_draft")]
    operations = [migrations.RunPython(set_price("25.00"), set_price("15.00"))]
