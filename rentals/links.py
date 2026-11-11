"""Private booking links: no accounts or passwords.

The link carries the booking reference signed with the site's secret key, so
it can't be guessed or altered. It stops working after
BOOKING_LINK_MAX_AGE_DAYS; the renter can ask for a fresh one by email.
"""

from django.conf import settings
from django.core import signing
from django.urls import reverse

SALT = "rentals.booking-link"


def make_token(booking):
    return signing.dumps({"ref": booking.reference}, salt=SALT, compress=True)


def read_token(token):
    """Booking reference from a token, or None if it's invalid or expired."""
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.BOOKING_LINK_MAX_AGE_DAYS * 86400)
    except signing.BadSignature:  # includes SignatureExpired
        return None
    return data.get("ref")


def booking_url(booking, absolute=False):
    path = reverse("booking_detail", args=[make_token(booking)])
    return settings.SITE_URL + path if absolute else path
