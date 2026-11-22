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
            except Exception:
                if not self.fail_silently:
                    raise
                log.exception("Postmark send failed: %r", message.subject)
        return sent
