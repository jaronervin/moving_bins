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


def _visits_using(window, now):
    """Visits currently claiming a stop in ``window``.

    A visit claims the window it's asking for (requested), the one the owner
    offered (proposed), or the agreed one (confirmed/done), as long as its
    booking is still live.
    """
    from django.db.models import Q

    from .models import Booking, Visit

    live = Q(booking__status__in=Booking.COMMITTED_STATUSES) | Q(
        booking__status__in=Booking.HOLD_STATUSES, booking__hold_expires_at__gt=now
    )
    claims = (
        Q(status=Visit.Status.REQUESTED, requested_window=window)
        | Q(status=Visit.Status.PROPOSED, proposed_window=window)
        | Q(status__in=[Visit.Status.CONFIRMED, Visit.Status.DONE], window=window)
    )
    return Visit.objects.filter(live, claims)


def stops_left(window, now=None):
    now = now or timezone.now()
    return max(0, window.max_stops - _visits_using(window, now).count())


def open_windows(day, now=None):
    """Windows on ``day`` that renters can still pick."""
    now = now or timezone.now()
    return [w for w in TimeWindow.objects.filter(date=day, is_open=True) if stops_left(w, now) > 0]


def next_open_dates(start, count=6, now=None, horizon_days=90):
    """The next ``count`` dates from ``start`` that have an open window."""
    now = now or timezone.now()
    dates = (
        TimeWindow.objects.filter(date__gte=start, date__lte=start + timedelta(days=horizon_days),
                                  is_open=True)
        .values_list("date", flat=True).distinct().order_by("date")
    )
    found = []
    for d in dates:
        if open_windows(d, now):
            found.append(d)
            if len(found) == count:
                break
    return found
