# Notes for working on this repo

Sprout Tote Co.'s booking site (Django). See SPEC.md for the business rules
and README.md for running it.

## Keep it SaaS-ready (owners' request, Nov 2026)

The owners may later offer this as software to other rental companies (one
site serving many businesses). That's on the back burner: build for Sprout
Tote first, never slow launch work for it, but keep these habits so the
change stays cheap:

- Business-specific values (prices, fees, radius, hours, contact details,
  agreement text, lead times, stock counts) live in the database
  (BusinessSettings, Product, Package, AgreementVersion), never in code.
- New code shouldn't add more hard-coded "Sprout Tote" names, emails, phone
  numbers or URLs; take them from settings. Existing ones get moved over when
  a file is touched anyway.
- Keep Stripe calls inside rentals/payments.py, so switching to Stripe Connect
  (each business's own account) is a change in one place.
- Keep business logic in plain functions (services, pricing, inventory,
  signing) that take what they need as arguments, not in views or templates.
