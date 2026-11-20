from .models import BusinessSettings


def site(request):
    """Business settings on every page (for the footer's contact details)."""
    return {"site_settings": BusinessSettings.load()}
