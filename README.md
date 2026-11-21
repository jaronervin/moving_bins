# moving_bins

Weekly rental site for reusable moving bins (and later hand trucks, dollies
and blankets): online booking, inventory tracking, distance-based pickup
fees, Stripe payments, online rental agreement, and owner scheduling/routing.

See [SPEC.md](SPEC.md) for the design and build plan.

## Run it locally

Needs Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate        # creates db.sqlite3 with the bin product and prices
python manage.py seed_dev       # 60 totes in service, 90 reserved codes, a delivery window every day
python manage.py createsuperuser
python manage.py runserver
```

- Owner tools (log in first): http://127.0.0.1:8000/ops/ (phones only allow the camera on
  `https://` sites or `localhost`, so the scanner's camera works once deployed; typing codes works anywhere)
- Home page with packages: http://127.0.0.1:8000/
- Book: http://127.0.0.1:8000/book/
- Owner admin: http://127.0.0.1:8000/admin/

Locally:
- the database is a SQLite file (set `DATABASE_URL` to use Postgres, as production does);
- emails are printed in the terminal running `runserver`, including the renter's private link;
- distances are made up from a small ZIP table in `rentals/maps.py` (e.g. 21218 = 4 mi free,
  21401 = 26 mi outer zone, 20001 = 36 mi refused; other ZIPs = 10 mi).

### Real addresses (Google Maps)

The service area is a straight-line radius, so only address lookup is needed.

1. Create a Google Cloud project with billing enabled.
2. Enable the **Geocoding API**.
3. Create an API key, restricted to that API.
4. Run with `MAPS_BACKEND=google GOOGLE_MAPS_API_KEY=...`.

## Payments (Stripe)

Without `STRIPE_SECRET_KEY` the site skips the card step (handy for local work).
With it, bookings go through Stripe's embedded Checkout to save a card.

| Setting (environment variable) | What |
|---|---|
| `STRIPE_SECRET_KEY` | Secret or, better, **restricted** key (`rk_…`). Never in code; in production use the host's secrets settings. |
| `STRIPE_PUBLISHABLE_KEY` | Public key for the card page (defaults to the test key). |
| `STRIPE_WEBHOOK_SECRET` | Signing secret of the webhook endpoint (`whsec_…`). |
| `STRIPE_PAYMENT_METHOD_CONFIGURATION` | Optional: a payment-method configuration limited to cards (+ Link). |
| `STRIPE_CA_BUNDLE` | Only for sandboxes behind a TLS-inspecting proxy. Leave unset in production. |

**In the Stripe Dashboard before launch:**
1. **Webhook:** Developers → Webhooks → add endpoint `https://sprouttoteco.com/stripe/webhook/` with events
   `checkout.session.completed`, `payment_intent.succeeded`, `payment_intent.payment_failed`,
   `payment_intent.canceled`, `payment_intent.amount_capturable_updated`, `charge.dispute.created`.
2. **Payment methods:** keep to **cards** (and Link). Holds and later charges need a reusable card.
3. **Restricted key** for the site with write access to Customers, Checkout Sessions, SetupIntents,
   PaymentIntents and PaymentMethods (read).
4. **Daily task:** `python manage.py daily_tasks` places the holds for upcoming dropoffs.

Test cards: `4242 4242 4242 4242` (any future date, any CVC) works; see Stripe's test-card list for declines.

## Deploying (Render)

`render.yaml` describes the test copy of the site (staging): a web service and
a Postgres database, both on Render's free plan. `build.sh` runs on every
deploy (install, static files, database migrations, first owner login).

1. In Render: **New > Blueprint**, connect GitHub, pick this repository and the branch to deploy.
2. Fill in the values Render asks for: Stripe test secret key and webhook secret, the first owner
   login (username, email, password), and `OWNER_EMAILS`.
3. Every push to that branch redeploys automatically.

Emails go to Render's logs until an email provider is set up (`EMAIL_BACKEND` and `EMAIL_HOST*`).
The free plan has no scheduled jobs, so `daily_tasks` (expire holds, add delivery days, card holds)
doesn't run on staging by itself; production needs a Render Cron Job running
`python manage.py daily_tasks` each morning.

## Tests

```bash
python manage.py test rentals
```

Three tests call your **Stripe test-mode** account for real (hold, partial capture, decline,
release, damage charge). They only run when asked:

```bash
STRIPE_LIVE_TESTS=1 STRIPE_SECRET_KEY=sk_test_... python manage.py test rentals.tests.test_payments
```

One test (two renters grabbing the last bins at the same instant) needs
Postgres and is skipped on SQLite:

```bash
DATABASE_URL=postgres://user@localhost:5432/moving_bins python manage.py test rentals
```

## Where things live

| File | What's in it |
|---|---|
| `rentals/models.py` | All the tables, with notes on each |
| `rentals/pricing.py` | Packages, extra totes, extras, delivery zone, late-cancel and damage charges (pure functions) |
| `rentals/inventory.py` | Stock counts from the ledger, availability by date, dropoff/pickup/check-in |
| `rentals/services.py` | Placing a hold without overbooking; expiring holds |
| `rentals/windows.py` | Weekly window pattern → dated delivery windows; open windows with room |
| `rentals/bins.py` | Tracked totes: codes, loading, pickup, check-in, missing, spare lids |
| `rentals/totes.py` | Renter tote pages: scan a QR to note contents, search their totes |
| `rentals/ops.py` | Owner tools: camera scanner pages, bin history, printable QR labels |
| `rentals/payments.py` | Everything that talks to Stripe: save card, holds, captures, charges, webhooks |
| `rentals/payment_views.py` | Card page, return from Stripe, webhook endpoint |
| `rentals/middleware.py` | Content-Security-Policy header |
| `rentals/signing.py` | Rental agreement: showing it, recording signatures, the name check, the signed PDF |
| `rentals/agreements/` | Agreement text, one Markdown file per version |
| `rentals/booking_flow.py` | The renter's booking steps (dates → addresses → times → contact → review) |
| `rentals/maps.py` | Address lookup and driving distance (Google, or a fake for development) |
| `rentals/links.py` | Private, signed booking links (no renter accounts) |
| `rentals/emails.py` | Emails; text is in `templates/rentals/email/` |
| `rentals/admin.py` | Owner back office |
| `rentals/management/commands/daily_tasks.py` | Run once a day: expire holds, add windows, delete old tote notes |
| `rentals/management/commands/create_codes.py` | Create tote codes ahead of time for sticker printing |
| `rentals/management/commands/export_stickers.py` | Print-ready sticker PDF (2", 1/8" bleed, one per page) + CSV for a label printer |
| `rentals/tests/` | Tests for all of the above |

## Before launch: placeholders to replace

All in the admin (Business settings, Products, Packages):

- [ ] **Premium package** above Whole Home (Katie's $449 tier: adds moving blankets, door jamb
      protectors, etc.); waiting on the details.
- [ ] **Base location:** approx. center of ZIP 21214; set the real coordinates.
- [ ] **Extras stock:** planning counts are rough (none owned yet; see SPEC §2.2). Adjust "Planned capacity"
      in admin as you buy.
- [ ] **Stripe live mode:** activate the account, then set live `STRIPE_SECRET_KEY` (restricted key),
      `STRIPE_PUBLISHABLE_KEY` and `STRIPE_WEBHOOK_SECRET` in the host's secrets settings.
- [ ] **When the LLC is formed:** update Stripe's business details (legal name, business type, EIN),
      the rental agreement's party name if it changes (e.g. an "LLC" ending), and the website footer.
- [ ] **Rental agreement:** lawyer review of `rentals/agreements/2026-11-draft.md` (open questions are in the
      Claude Doc); publish the reviewed text as a new version.
- [ ] **Hosting:** set `TRUST_X_FORWARDED_FOR=1` so signatures record the renter's real IP address.
- [ ] **Stripe payment methods (live mode):** Settings › Payment methods: turn off Link, Amazon Pay and Affirm
      (they hide card details, so signatures lose the card brand, last 4 and bank address checks). Keep cards,
      Apple Pay, Google Pay. Already done in test mode.
- [ ] **Stripe Tax:** Maryland registration + head office address + tax codes (see SPEC §6.2), then switch on.

## Progress

- [x] 1. Project skeleton, models, admin
- [x] 2. Pricing, availability and fee logic (tested); quote page
- [x] 3. Renter booking flow, private booking link, add moving-in address later.
- [x] QR-labelled totes: per-bin tracking, scanner pages, labels, renter contents notes
- [x] 4. Stripe: card saved at booking, hold before dropoff, charge at dropoff, late-cancel/damage/renewal
      charges, webhooks (tax not yet switched on)
- [x] 5. Agreement signed online at Review; card name check; signed PDF emailed and on the booking page
- [ ] 6. Owner tools: inbox, schedule, route, at-the-door, check-in
- [ ] 7. Change/cancel, renewals, damage charges, reminders
- [ ] 8. Deploy
