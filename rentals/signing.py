"""
The rental agreement: showing it, signing it, and the evidence we keep.

At booking the renter reads the current agreement version, ticks the box and
types their full name. We record a Signature with the typed name, time, IP
address, browser, the version and the SHA-256 hash of its exact text.

When their card is saved (rentals.payments.finish_card_setup) we add the
card's evidence: the cardholder name, brand, last 4 digits, and the bank's
address checks (AVS). The typed name must match the cardholder name; if it
doesn't, the booking waits until they re-sign with the cardholder's name or
use a card in their own name.

A PDF copy (agreement text + signature record) is attached to the
confirmation email and can be downloaded from the booking page.
"""

import html
import io
import re
import unicodedata

import markdown
from django.conf import settings
from django.utils import timezone
from django.utils.safestring import mark_safe

from .models import AgreementVersion, Signature

# ---------------------------------------------------------------------------
# Showing the agreement
# ---------------------------------------------------------------------------


def current_version():
    return AgreementVersion.current()


def render_html(version):
    """The agreement as HTML. Text comes only from owners (admin), never renters."""
    return mark_safe(markdown.markdown(version.text, extensions=["tables", "sane_lists"]))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _name_tokens(name):
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    name = re.sub(r"['.]", "", name.lower())  # O'Brien → obrien, J. → j
    name = re.sub(r"[^a-z]+", " ", name)
    tokens = [t for t in name.split() if t]
    # Drop common titles and suffixes so "Dr. Pat O'Brien Jr." matches "PAT OBRIEN".
    drop = {"mr", "mrs", "ms", "dr", "jr", "sr", "ii", "iii", "iv"}
    return [t for t in tokens if t not in drop]


def names_match(typed, card):
    """Same first and last name, ignoring case, punctuation, accents, titles and
    middle names or initials. "Pat O'Brien" matches "PAT OBRIEN" and "Pat J. O'Brien";
    "Patricia O'Brien" does not match "Pat O'Brien".
    """
    a, b = _name_tokens(typed), _name_tokens(card)
    if not a or not b:
        return False
    if a == b:
        return True
    return a[0] == b[0] and a[-1] == b[-1]


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def client_ip(request):
    """The visitor's IP address. Behind the production host's proxy, set
    TRUST_X_FORWARDED_FOR so the real address is used, not the proxy's."""
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def evidence_from_request(request):
    return {"ip_address": client_ip(request), "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500]}


def record_signature(booking, typed_name, version, ip_address=None, user_agent="", now=None):
    """Create or replace the booking's signature (re-signing replaces it)."""
    now = now or timezone.now()
    sig, _ = Signature.objects.update_or_create(booking=booking, defaults={
        "agreement_version": version,
        "agreement_sha256": version.sha256,
        "typed_name": typed_name.strip(),
        "email": booking.customer.email,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "signed_at": now,
    })
    return sig


def record_card_evidence(booking, payment_method):
    """Add the card's name and bank checks to the signature. Returns the Signature or None."""
    sig = Signature.objects.filter(booking=booking).first()
    if sig is None:
        return None
    card = getattr(payment_method, "card", None)
    checks = getattr(card, "checks", None) if card else None
    details = getattr(payment_method, "billing_details", None)
    sig.card_name = ((details.name if details else "") or "")[:150]
    sig.card_brand = (getattr(card, "brand", "") or "")[:20]
    sig.card_last4 = (getattr(card, "last4", "") or "")[:4]
    sig.avs_line1_check = (getattr(checks, "address_line1_check", "") or "")[:20]
    sig.avs_postal_code_check = (getattr(checks, "address_postal_code_check", "") or "")[:20]
    sig.save()
    return sig


# ---------------------------------------------------------------------------
# PDF copy
# ---------------------------------------------------------------------------


def _inline(text):
    """Markdown bold → ReportLab markup, everything else escaped."""
    text = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def pdf_bytes(signature):
    """A PDF with the agreement text and the signature record."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body, h1, h2 = styles["BodyText"], styles["Title"], styles["Heading2"]
    booking = signature.booking
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=f"Rental agreement {booking.reference}", author="Sprout Tote Company")
    flow = [Paragraph("Sprout Tote Co. Rental Agreement", h1),
            Paragraph(f"Booking {booking.reference} · version {signature.agreement_version.version}", body),
            Spacer(1, 8)]

    bullets, table_rows = [], []

    def flush():
        if bullets:
            items = []  # [parent paragraph, its sub-items]
            for nested, text in bullets:
                para = Paragraph(_inline(text), body)
                if nested and items:
                    items[-1][1].append(ListItem(para, leftIndent=12))
                else:
                    items.append((para, []))
            flow.append(ListFlowable(
                [ListItem([para, ListFlowable(sub, bulletType="bullet", start="–", leftIndent=12)] if sub else para,
                          leftIndent=12) for para, sub in items],
                bulletType="bullet", leftIndent=12))
            bullets.clear()
        if table_rows:
            t = Table(table_rows, colWidths=[3.9 * inch, 2.6 * inch])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            flow.append(t)
            table_rows.clear()

    for raw in signature.agreement_version.text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):
                table_rows.append([Paragraph(_inline(c), body) for c in cells])
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullets.append((line.startswith("  "), stripped[2:]))
            continue
        flush()
        if not stripped:
            continue
        if stripped.startswith("## "):
            flow.append(Paragraph(_inline(stripped[3:]), h2))
        else:
            flow.append(Paragraph(_inline(stripped), body))
    flush()

    flow += [Spacer(1, 14), Paragraph("Signature record", h2)]
    when = timezone.localtime(signature.signed_at).strftime("%B %-d, %Y at %-I:%M %p %Z")
    record = [
        ("Signed by (typed name)", signature.typed_name),
        ("Email", signature.email),
        ("Date and time", when),
        ("IP address", signature.ip_address or "not recorded"),
        ("Agreement version", signature.agreement_version.version),
        ("Text fingerprint (SHA-256)", signature.agreement_sha256),
    ]
    if signature.card_last4:
        record.append(("Card", f"{signature.card_brand.title()} ending {signature.card_last4}, "
                               f"name on card: {signature.card_name or 'not given'}"))
    t = Table([[Paragraph(_inline(k), body), Paragraph(_inline(str(v)), body)] for k, v in record],
              colWidths=[2.0 * inch, 4.5 * inch])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow.append(t)
    doc.build(flow)
    return out.getvalue()
