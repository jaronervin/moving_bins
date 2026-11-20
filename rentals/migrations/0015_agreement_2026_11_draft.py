"""Load the first rental agreement text (rentals/agreements/2026-11-draft.md).

Later versions are added in the admin (Agreement versions). A signed version
is never edited; each signature keeps the exact text and its SHA-256 hash.
"""

import hashlib
from pathlib import Path

from django.db import migrations

VERSION = "2026-11-draft"
TEXT_FILE = Path(__file__).resolve().parent.parent / "agreements" / f"{VERSION}.md"


def forwards(apps, schema_editor):
    AgreementVersion = apps.get_model("rentals", "AgreementVersion")
    text = TEXT_FILE.read_text(encoding="utf-8")
    AgreementVersion.objects.get_or_create(
        version=VERSION,
        defaults={"text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
    )


class Migration(migrations.Migration):
    dependencies = [("rentals", "0014_signature_evidence")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
