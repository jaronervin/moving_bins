"""Emails to renters and owners. Text bodies live in templates/rentals/email/."""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import BusinessSettings

from .links import booking_url


def _send(to, subject_template, body_template, context):
    subject = render_to_string(subject_template, context).strip()
    body = render_to_string(body_template, context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to)


def booking_requested(booking):
    """To the renter right after submitting, and a heads-up to the owners."""
    context = {"booking": booking, "link": booking_url(booking, absolute=True),
               "review_hours": BusinessSettings.load().review_hours}
    _send([booking.customer.email], "rentals/email/requested_subject.txt",
          "rentals/email/requested_body.txt", context)
    if settings.OWNER_EMAILS:
        from . import inventory

        admin_link = f"{settings.SITE_URL}/admin/rentals/booking/{booking.pk}/change/"
        needs = [n for n in (inventory.purchase_needs(line.product)
                             for line in booking.lines.select_related("product")) if n]
        _send(settings.OWNER_EMAILS, "rentals/email/owner_new_subject.txt",
              "rentals/email/owner_new_body.txt",
              {**context, "admin_link": admin_link, "needs": needs,
               "ops_link": f"{settings.SITE_URL}/ops/",
               "lead_days": BusinessSettings.load().purchase_lead_days})


def booking_links(email, bookings):
    """Re-send private links for every live booking under an email address."""
    links = [(b, booking_url(b, absolute=True)) for b in bookings]
    _send([email], "rentals/email/links_subject.txt", "rentals/email/links_body.txt",
          {"links": links})


def booking_declined(booking):
    """To the renter when the owners decline an auto-confirmed booking."""
    _send([booking.customer.email], "rentals/email/declined_subject.txt",
          "rentals/email/declined_body.txt", {"booking": booking, "site_url": settings.SITE_URL})
