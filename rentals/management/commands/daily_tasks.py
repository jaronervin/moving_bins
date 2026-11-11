"""Run once a day (e.g. a scheduled job on the host): expire holds, add windows.

    python manage.py daily_tasks
"""

from django.core.management.base import BaseCommand

from rentals.services import expire_holds
from rentals.windows import generate_windows


class Command(BaseCommand):
    help = "Expire unanswered holds and create upcoming delivery windows."

    def handle(self, *args, **options):
        expired = expire_holds()
        created = generate_windows()
        self.stdout.write(f"Expired {expired} hold(s); created {created} window(s).")
