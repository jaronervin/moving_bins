"""Emails to renters and owners. Text bodies live in templates/rentals/email/."""

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.template.loader import render_to_string

import logging

from .links import booking_url
from .models import BusinessSettings, Signature

log = logging.getLogger(__name__)


def _owner_connection():
    """(connection, from address) for owner alerts: Google Workspace when it's
    set up, otherwise the site's normal email route."""
    if settings.GOOGLE_SMTP_USER and settings.GOOGLE_SMTP_APP_PASSWORD:
        connection = get_connection(
            "django.core.mail.backends.smtp.EmailBackend", host="smtp.gmail.com", port=587, use_tls=True,
            username=settings.GOOGLE_SMTP_USER, password=settings.GOOGLE_SMTP_APP_PASSWORD, timeout=8)
        sender = settings.OWNER_ALERT_FROM or f"Sprout Tote alerts <{settings.GOOGLE_SMTP_USER}>"
        return connection, sender
    return None, settings.DEFAULT_FROM_EMAIL


def _send(to, subject_template, body_template, context, attachments=(), owners=False):
    """Send one email. A failure is logged, never raised: a booking must not
    fail because the email service hiccuped. ``owners=True`` uses the owner route."""
    subject = render_to_string(subject_template, context).strip()
    body = render_to_string(body_template, context)
    connection, sender = _owner_connection() if owners else (None, settings.DEFAULT_FROM_EMAIL)
    msg = EmailMessage(subject, body, sender, to, connection=connection)
    for name, content, mime in attachments:
        msg.attach(name, content, mime)
    _deliver(msg)


def _deliver(msg):
    """Send; if the owner route (Google) fails, fall back to the normal route."""
    try:
        msg.send()
        return
    except Exception:
        log.exception("Email %r to %s failed", msg.subject, msg.to)
    if msg.connection is not None:  # Google route failed: try the site's normal route once
        msg.connection, msg.from_email = None, settings.DEFAULT_FROM_EMAIL
        try:
            msg.send()
        except Exception:
            log.exception("Fallback for %r failed too", msg.subject)


def booking_requested(booking):
    """To the renter right after submitting, and a heads-up to the owners."""
    s = BusinessSettings.load()
    context = {"booking": booking, "link": booking_url(booking, absolute=True),
               "review_hours": s.review_hours, "contact_email": s.contact_email,
               "total": sum((line.amount for line in booking.price_lines.all()), 0)}
    attachments = []
    sig = Signature.objects.filter(booking=booking).first()
    if sig is not None:
        from .signing import pdf_bytes

        attachments.append((f"Sprout-Tote-agreement-{booking.reference}.pdf", pdf_bytes(sig), "application/pdf"))
    _send([booking.customer.email], "rentals/email/requested_subject.txt",
          "rentals/email/requested_body.txt", {**context, "signed": sig is not None}, attachments)
    if settings.OWNER_EMAILS:
        from . import inventory

        admin_link = f"{settings.SITE_URL}/admin/rentals/booking/{booking.pk}/change/"
        needs = [n for n in (inventory.purchase_needs(line.product)
                             for line in booking.lines.select_related("product")) if n]
        _send(settings.OWNER_EMAILS, "rentals/email/owner_new_subject.txt",
              "rentals/email/owner_new_body.txt",
              {**context, "admin_link": admin_link, "needs": needs,
               "ops_link": f"{settings.SITE_URL}/ops/",
               "lead_days": BusinessSettings.load().purchase_lead_business_days}, owners=True)


def booking_links(email, bookings):
    """Re-send private links for every live booking under an email address."""
    links = [(b, booking_url(b, absolute=True)) for b in bookings]
    _send([email], "rentals/email/links_subject.txt", "rentals/email/links_body.txt",
          {"links": links})


def booking_declined(booking):
    """To the renter when the owners decline an auto-confirmed booking."""
    _send([booking.customer.email], "rentals/email/declined_subject.txt",
          "rentals/email/declined_body.txt", {"booking": booking, "site_url": settings.SITE_URL})


def booking_cancelled(booking, late_charge=None):
    """To the renter after they cancel online; heads-up to the owners."""
    s = BusinessSettings.load()
    _send([booking.customer.email], "rentals/email/cancelled_subject.txt", "rentals/email/cancelled_body.txt",
          {"booking": booking, "late_charge": late_charge, "contact_email": s.contact_email,
           "site_url": settings.SITE_URL})
    fee = f" Late-cancellation charge: ${late_charge.total} ({late_charge.get_status_display()})." if late_charge else ""
    owner_alert(f"Cancelled: {booking.reference}",
                f"{booking.customer.name} cancelled {booking.reference} (dropoff {booking.dropoff_date:%a %b %-d})."
                f"{fee} The totes and delivery stops are free again.")


def booking_changed(booking):
    """To the renter after they change their booking online; heads-up to the owners."""
    s = BusinessSettings.load()
    total = sum((line.amount for line in booking.price_lines.all()), 0)
    _send([booking.customer.email], "rentals/email/changed_subject.txt", "rentals/email/changed_body.txt",
          {"booking": booking, "total": total, "link": booking_url(booking, absolute=True),
           "contact_email": s.contact_email})
    owner_alert(f"Changed: {booking.reference}",
                f"{booking.customer.name} changed {booking.reference}: {booking.package} for {booking.weeks} weeks, "
                f"dropoff {booking.dropoff_date:%a %b %-d}, pickup {booking.pickup_date:%a %b %-d}. "
                f"New total before tax: ${total}.")


def booking_extended(booking, charge=None):
    """To the renter after they add a week; heads-up to the owners."""
    _send([booking.customer.email], "rentals/email/extended_subject.txt", "rentals/email/extended_body.txt",
          {"booking": booking, "charge": charge, "link": booking_url(booking, absolute=True)})
    owner_alert(f"Extra week: {booking.reference}",
                f"{booking.customer.name} added a week to {booking.reference}. "
                f"Pickup is now {booking.pickup_date:%a %b %-d}.")


def _visit_window(booking, kind):
    visit = booking.visits.filter(kind=kind).select_related("window", "requested_window").first()
    return visit and (visit.window or visit.requested_window)


def dropoff_reminder(booking):
    s = BusinessSettings.load()
    _send([booking.customer.email], "rentals/email/dropoff_reminder_subject.txt",
          "rentals/email/dropoff_reminder_body.txt",
          {"booking": booking, "window": _visit_window(booking, "dropoff"),
           "link": booking_url(booking, absolute=True), "contact_email": s.contact_email,
           "contact_phone": s.contact_phone})


def pickup_reminder(booking):
    from django.urls import reverse

    from .links import make_token

    s = BusinessSettings.load()
    _send([booking.customer.email], "rentals/email/pickup_reminder_subject.txt",
          "rentals/email/pickup_reminder_body.txt",
          {"booking": booking, "window": _visit_window(booking, "pickup"),
           "link": booking_url(booking, absolute=True),
           "add_week_link": settings.SITE_URL + reverse("renter_add_week", args=[make_token(booking)]),
           "contact_email": s.contact_email, "contact_phone": s.contact_phone})


def delivered(booking):
    """To the renter when the totes are dropped off."""
    s = BusinessSettings.load()
    _send([booking.customer.email], "rentals/email/delivered_subject.txt", "rentals/email/delivered_body.txt",
          {"booking": booking, "link": booking_url(booking, absolute=True), "contact_email": s.contact_email,
           "window": _visit_window(booking, "pickup")})


def not_ready(booking):
    """To the renter when totes weren't ready at pickup and the rental was extended a week."""
    s = BusinessSettings.load()
    _send([booking.customer.email], "rentals/email/not_ready_subject.txt", "rentals/email/not_ready_body.txt",
          {"booking": booking, "link": booking_url(booking, absolute=True), "contact_email": s.contact_email,
           "contact_phone": s.contact_phone, "window": _visit_window(booking, "pickup")})


def change_requested(booking):
    """To the renter when the owners ask for a change during the review window."""
    _send([booking.customer.email], "rentals/email/change_subject.txt", "rentals/email/change_body.txt",
          {"booking": booking, "link": booking_url(booking, absolute=True),
           "contact_email": BusinessSettings.load().contact_email})


def card_problem(booking, charge):
    """The hold before dropoff failed: ask the renter to update their card; tell the owners."""
    link = booking_url(booking, absolute=True)
    _send([booking.customer.email], "rentals/email/card_problem_subject.txt",
          "rentals/email/card_problem_body.txt",
          {"booking": booking, "charge": charge, "link": link,
           "contact_email": BusinessSettings.load().contact_email})
    owner_alert(f"Card problem: {booking.reference}",
                f"The hold before dropoff failed for {booking.reference} ({booking.customer.name}, "
                f"dropoff {booking.dropoff_date:%a %b %-d}): {charge.failure_message or 'declined'}. "
                "They've been emailed a link to update their card.")


def owner_alert(subject, body):
    if settings.OWNER_EMAILS:
        connection, sender = _owner_connection()
        _deliver(EmailMessage(subject, body, sender, settings.OWNER_EMAILS, connection=connection))
