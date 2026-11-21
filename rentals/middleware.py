"""Content-Security-Policy: tells browsers which sources the site's pages may
load scripts, frames and data from, which blunts cross-site scripting.

The Stripe entries follow Stripe's security guide (Stripe.js + Checkout).
cdn.jsdelivr.net is only for the QR reader on the owner scanner pages.
maps.googleapis.com / places.googleapis.com are for address suggestions.
"""

POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://js.stripe.com https://*.js.stripe.com https://checkout.stripe.com "
    "https://cdn.jsdelivr.net https://maps.googleapis.com",
    "frame-src https://js.stripe.com https://*.js.stripe.com https://hooks.stripe.com "
    "https://checkout.stripe.com https://link.com https://*.link.com",
    "connect-src 'self' https://api.stripe.com https://checkout.stripe.com https://link.com https://*.link.com "
    "https://maps.googleapis.com https://places.googleapis.com",
    "img-src 'self' data: blob: https://*.stripe.com https://*.link.com https://maps.gstatic.com "
    "https://maps.googleapis.com",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # The Django admin manages its own pages; leave it alone.
        if not request.path.startswith("/admin/"):
            response.setdefault("Content-Security-Policy", POLICY)
        return response
