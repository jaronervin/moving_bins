"""Whole Home is $299 (proposed to Katie; the original $449 let 3BR + extra
totes undercut it)."""

from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("rentals", "Package").objects.filter(slug="whole-home").update(price=Decimal("299.00"))


def backwards(apps, schema_editor):
    apps.get_model("rentals", "Package").objects.filter(slug="whole-home").update(price=Decimal("249.00"))


class Migration(migrations.Migration):
    dependencies = [("rentals", "0010_launch_catalog")]
    operations = [migrations.RunPython(forwards, backwards)]
