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

## Tests

```bash
python manage.py test rentals
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

- [ ] **Phone number:** 703-703-2511 is a placeholder and shows in every page's footer.
- [ ] **20–35 mile fee:** $25 per address is a placeholder.
- [ ] **Dish/glass divider:** $15 is a placeholder (quoted as $15–30).
- [ ] **Whole Home:** $299 proposed (was $449); waiting on Katie.
- [ ] **Base location:** approx. center of ZIP 21214; set the real coordinates.
- [ ] **Extras stock:** turn on "track stock" for each extra once counted.

## Progress

- [x] 1. Project skeleton, models, admin
- [x] 2. Pricing, availability and fee logic (tested); quote page
- [x] 3. Renter booking flow, private booking link, add moving-in address later.
      *Card and agreement steps slot into Review in milestones 4–5.*
- [x] QR-labelled totes: per-bin tracking, scanner pages, labels, renter contents notes
- [ ] 4. Stripe
- [ ] 5. Agreement and signature evidence
- [ ] 6. Owner tools: inbox, schedule, route, at-the-door, check-in
- [ ] 7. Change/cancel, renewals, damage charges, reminders
- [ ] 8. Deploy
