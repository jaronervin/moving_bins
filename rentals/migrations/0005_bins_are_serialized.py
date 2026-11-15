from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("rentals", "Product").objects.filter(slug="bin-27gal").update(is_serialized=True)


def backwards(apps, schema_editor):
    apps.get_model("rentals", "Product").objects.filter(slug="bin-27gal").update(is_serialized=False)


class Migration(migrations.Migration):
    dependencies = [("rentals", "0004_bins")]
    operations = [migrations.RunPython(forwards, backwards)]
