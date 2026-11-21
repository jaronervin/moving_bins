"""Reminder emails, sent by the daily job (manage.py daily_tasks).

* Before dropoff (``dropoff_reminder_days``): when we're coming, where, porch or
  present, and a nudge if the moving-in address is still missing.
* Before pickup (``pickup_reminder_days``): have the totes empty and stacked
  by the ready-by time, and a link to keep them another week.

Each is sent once per booking (adding a week re-arms the pickup reminder).
"""

from datetime import timedelta

from django.utils import timezone

from . import emails
from .models import Booking, BusinessSettings


def send_reminders(now=None):
    """Returns (dropoff reminders sent, pickup reminders sent)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    s = BusinessSettings.load()
    sent_drop = sent_pick = 0
    if s.dropoff_reminder_days:
        due = Booking.objects.filter(
            status=Booking.Status.CONFIRMED, dropoff_reminded_at__isnull=True,
            dropoff_date__gt=today, dropoff_date__lte=today + timedelta(days=s.dropoff_reminder_days),
        ).select_related("customer", "dropoff_address", "pickup_address")
        for booking in due:
            emails.dropoff_reminder(booking)
            Booking.objects.filter(pk=booking.pk).update(dropoff_reminded_at=now)
            sent_drop += 1
    if s.pickup_reminder_days:
        due = Booking.objects.filter(
            status=Booking.Status.OUT, pickup_reminded_at__isnull=True,
            pickup_date__gt=today, pickup_date__lte=today + timedelta(days=s.pickup_reminder_days),
        ).select_related("customer", "pickup_address")
        for booking in due:
            emails.pickup_reminder(booking)
            Booking.objects.filter(pk=booking.pk).update(pickup_reminded_at=now)
            sent_pick += 1
    return sent_drop, sent_pick
