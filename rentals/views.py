from datetime import timedelta

from django.shortcuts import get_object_or_404, render

from . import inventory, pricing, services
from .forms import QuoteForm
from .models import BusinessSettings, Product


def home(request):
    """Landing page with the quote calculator and impact numbers."""
    bins = get_object_or_404(Product, slug="bin-27gal", active=True)
    form = QuoteForm(request.GET or None, minimum_quantity=bins.minimum_quantity)
    quote = available = pickup_date = error = None

    if form.is_valid():
        qty = form.cleaned_data["quantity"]
        dropoff = form.cleaned_data["dropoff_date"]
        weeks = form.cleaned_data["weeks"]
        pickup_date = dropoff + timedelta(days=7 * weeks)
        available = inventory.available(bins, dropoff, pickup_date)
        try:
            quote = services.quote([(bins, qty)], weeks)
        except pricing.PricingError as exc:
            error = str(exc)
        if quote and qty > available:
            error = f"Only {available} bins are free for those dates."

    return render(request, "rentals/home.html", {
        "form": form,
        "product": bins,
        "quote": quote,
        "available": available,
        "pickup_date": pickup_date,
        "error": error,
        "settings": BusinessSettings.load(),
        "impact": inventory.impact_stats(),
        "rate_table": [
            (n, pricing.to_cents(pricing.unit_rate(services.price_points(bins), n)),
             pricing.weekly_total(services.price_points(bins), n))
            for n in (20, 40, 60, 80, 100)
        ],
    })
