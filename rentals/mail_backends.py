"""Send email through Postmark's HTTP API (https, port 443).

Used instead of SMTP because some hosts (Render's free plan among them) block
outgoing email ports. Same Django interface: EmailMessage(...).send().
"""

import base64
import json
import logging
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger(__name__)
API_URL = "https://api.postmarkapp.com/email"


def _payload(message):
    data = {
        "From": message.from_email,
        "To": ", ".join(message.to),
        "Subject": message.subject,
        "TextBody": message.body,
        "MessageStream": getattr(settings, "POSTMARK_MESSAGE_STREAM", "outbound"),
    }
    if message.cc:
        data["Cc"] = ", ".join(message.cc)
    if message.bcc:
        data["Bcc"] = ", ".join(message.bcc)
    if message.reply_to:
        data["ReplyTo"] = ", ".join(message.reply_to)
    attachments = []
    for item in message.attachments:
        name, content, mime = item if isinstance(item, tuple) else (item.get_filename(), item.get_payload(decode=True),
                                                                     item.get_content_type())
        if isinstance(content, str):
            content = content.encode()
        attachments.append({"Name": name, "Content": base64.b64encode(content).decode(),
                            "ContentType": mime or "application/octet-stream"})
    if attachments:
        data["Attachments"] = attachments
    return data


class PostmarkBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            req = urllib.request.Request(
                API_URL, data=json.dumps(_payload(message)).encode(), method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/json",
                         "X-Postmark-Server-Token": settings.POSTMARK_SERVER_TOKEN})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    json.load(resp)
                sent += 1
                count_sent(len(message.to) + len(message.cc) + len(message.bcc))
            except Exception:
                if not self.fail_silently:
                    raise
                log.exception("Postmark send failed: %r", message.subject)
        return sent


WARN_AT = (70, 90)


def count_sent(recipients):
    """Add to this month's count; warn the owners once at each level."""
    from django.db.models import F
    from django.utils import timezone

    from .models import EmailUsage

    try:
        month = timezone.localdate().replace(day=1)
        usage, _ = EmailUsage.objects.get_or_create(month=month)
        EmailUsage.objects.filter(pk=usage.pk).update(sent=F("sent") + recipients)
        usage.refresh_from_db()
        limit = settings.POSTMARK_MONTHLY_LIMIT
        level = max((p for p in WARN_AT if usage.sent * 100 >= p * limit), default=0)
        # Claim the warning first (only one request wins), then send it.
        if level > usage.warned and EmailUsage.objects.filter(pk=usage.pk, warned__lt=level).update(warned=level):
            _warn(usage, level, limit)
    except Exception:  # counting must never stop an email
        log.exception("Couldn't count Postmark usage")


def _warn(usage, level, limit):
    from .emails import owner_alert
    from .models import Booking

    bookings = Booking.objects.filter(created_at__date__gte=usage.month).exclude(
        status__in=[Booking.Status.DRAFT, Booking.Status.EXPIRED]).count()
    owner_alert(
        f"Postmark: {usage.sent} of {limit} emails used this month",
        f"The booking site has sent {usage.sent} of the {limit} emails in Postmark's plan this month "
        f"({level}%), from {bookings} booking(s) so far. When the limit is reached, Postmark stops "
        f"sending until next month, so renters would miss confirmations and reminders.\n\n"
        f"To avoid that, upgrade the plan in Postmark (Account > Billing), then set POSTMARK_MONTHLY_LIMIT "
        f"in Render to the new limit.")
