"""Katie's launch catalog: packages, extras to rent, items for sale, and the
updated business rules. Every price here is editable in admin.

Placeholders to confirm before launch (also listed in README):
  * dish/glass divider: $15 (quoted as $15-30)
  * extended-zone fee (20-35 miles): $25 per address
  * contact phone: 703-703-2511
"""

from decimal import Decimal as D

from django.db import migrations

EXTRAS = [
    # slug, name, kind, price, sort
    ("dolly", "Dolly", "rental", "10.00", 10),
    ("hand-truck", "Hand truck", "rental", "15.00", 11),
    ("appliance-dolly", "Appliance dolly", "rental", "25.00", 12),
    ("moving-blanket", "Moving blanket", "rental", "5.00", 13),
    ("wardrobe-box", "Wardrobe box", "rental", "15.00", 14),
    ("dish-divider", "Dish/glass divider", "rental", "15.00", 15),
    ("floor-door-protection", "Floor runner & door jamb guards", "rental", "25.00", 16),
    ("mattress-bag-qk", "Mattress bag (Queen/King)", "sale", "10.00", 30),
    ("mattress-bag-tf", "Mattress bag (Twin/Full)", "sale", "8.00", 31),
    ("packing-kit", "Packing kit (kraft paper, honeycomb wrap, paper tape)", "sale", "35.00", 32),
    ("foam-pouch", "Foam pouch", "sale", "1.00", 33),
]

PACKAGES = [
    # slug, name, description, totes, price, extra week, included [(slug, qty)], sort
    ("studio", "Studio/1BR", "For studios and one-bedrooms", 20, "119.00", "45.00", [("dolly", 1)], 1),
    ("2br", "2BR", "For two-bedroom homes", 40, "169.00", "65.00", [("dolly", 2)], 2),
    ("3br", "3BR", "For three-bedroom homes", 60, "229.00", "85.00", [("dolly", 2)], 3),
    ("whole-home", "Whole Home", "For larger homes", 80, "249.00", "99.00",
     [("dolly", 3), ("hand-truck", 1)], 4),
]


def forwards(apps, schema_editor):
    Product = apps.get_model("rentals", "Product")
    Package = apps.get_model("rentals", "Package")
    PackageItem = apps.get_model("rentals", "PackageItem")
    BusinessSettings = apps.get_model("rentals", "BusinessSettings")

    tote = Product.objects.get(slug="bin-27gal")
    tote.name = "27-gallon tote with lid"
    tote.kind = "tote"
    tote.addon_price = D("5.00")
    tote.addon_extra_week_price = D("3.00")
    tote.planned_capacity = 120
    tote.save()
    Product.objects.filter(slug="spare-lid-27gal").update(kind="part", name="Spare tote lid (27 gal)")

    products = {}
    for slug, name, kind, price, sort in EXTRAS:
        products[slug], _ = Product.objects.update_or_create(
            slug=slug,
            defaults={"name": name, "kind": kind, "price": D(price), "sort_order": sort,
                      "is_rentable": kind == "rental", "track_stock": False, "active": True},
        )

    for slug, name, desc, totes, price, week, items, sort in PACKAGES:
        pkg, _ = Package.objects.update_or_create(
            slug=slug,
            defaults={"name": name, "description": desc, "tote_product": tote, "totes": totes,
                      "price": D(price), "extra_week_price": D(week), "sort_order": sort},
        )
        pkg.items.all().delete()
        for item_slug, qty in items:
            PackageItem.objects.create(package=pkg, product=products[item_slug], quantity=qty)

    s, _ = BusinessSettings.objects.get_or_create(pk=1)
    s.cancel_cutoff_hours = 72
    s.review_hours = 48
    s.min_notice_days = 3
    s.save()


class Migration(migrations.Migration):
    dependencies = [("rentals", "0009_packages")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
