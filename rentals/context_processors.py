from .models import BusinessSettings


def site(request):
    """Business settings on every page (for the footer's contact details)."""
    from django.conf import settings

    return {"site_settings": BusinessSettings.load(), "site_banner": settings.SITE_BANNER,
            "places_key": settings.GOOGLE_MAPS_BROWSER_KEY}
