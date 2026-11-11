"""Emails to renters and owners. Text bodies live in templates/rentals/email/."""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .links import booking_url


def _send(to, subject_template, body_template, context):
    subject = render_to_string(subject_template, context).strip()
    body = render_to_string(body_template, context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to)


def booking_requested(booking):
    """To the renter right after submitting, and a heads-up to the owners."""
    context = {"booking": booking, "link": booking_url(booking, absolute=True)}
    _send([booking.customer.email], "rentals/email/requested_subject.txt",
          "rentals/email/requested_body.txt", context)
    if settings.OWNER_EMAILS:
        admin_link = f"{settings.SITE_URL}/admin/rentals/booking/{booking.pk}/change/"
        _send(settings.OWNER_EMAILS, "rentals/email/owner_new_subject.txt",
              "rentals/email/owner_new_body.txt", {**context, "admin_link": admin_link})


def booking_links(email, bookings):
    """Re-send private links for every live booking under an email address."""
    links = [(b, booking_url(b, absolute=True)) for b in bookings]
    _send([email], "rentals/email/links_subject.txt", "rentals/email/links_body.txt",
          {"links": links})
