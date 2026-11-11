"""Turn the weekly window pattern into dated windows renters can pick."""

from datetime import timedelta

from django.utils import timezone

from .models import TimeWindow, WindowTemplate


def generate_windows(start=None, days=60):
    """Create TimeWindows from active WindowTemplates for the next ``days`` days.

    Windows that already exist (including ones you've edited or closed) are
    left alone, so it's safe to run this every day.
    Returns the number of windows created.
    """
    start = start or timezone.localdate()
    templates = list(WindowTemplate.objects.filter(active=True))
    created = 0
    for i in range(days):
        day = start + timedelta(days=i)
        for t in templates:
            if t.weekday != day.weekday():
                continue
            _, was_created = TimeWindow.objects.get_or_create(
                date=day, start=t.start, defaults={"end": t.end, "max_stops": t.max_stops}
            )
            created += was_created
    return created
