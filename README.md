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
python manage.py seed_dev       # 100 bins on the shelf + weekend delivery windows
python manage.py createsuperuser
python manage.py runserver
```

- Quote page: http://127.0.0.1:8000/
- Book: http://127.0.0.1:8000/book/
- Owner admin: http://127.0.0.1:8000/admin/

Locally:
- the database is a SQLite file (set `DATABASE_URL` to use Postgres, as production does);
- emails are printed in the terminal running `runserver`, including the renter's private link;
- distances are made up from a small ZIP table in `rentals/maps.py` (e.g. 21218 = 4 mi,
  21401 = 35 mi, 21702 = 55 mi, 21801 = 125 mi; other ZIPs = 10 mi).

### Real distances (Google Maps)

1. Create a Google Cloud project with billing enabled.
2. Enable the **Geocoding API** and the **Routes API**.
3. Create an API key, restricted to those two APIs.
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
| `rentals/pricing.py` | Volume rates, pickup fee, late-cancel and damage charges (pure functions) |
| `rentals/inventory.py` | Stock counts from the ledger, availability by date, dropoff/pickup/check-in |
| `rentals/services.py` | Placing a hold without overbooking; expiring holds |
| `rentals/windows.py` | Weekly window pattern → dated delivery windows; open windows with room |
| `rentals/booking_flow.py` | The renter's booking steps (dates → addresses → times → contact → review) |
| `rentals/maps.py` | Address lookup and driving distance (Google, or a fake for development) |
| `rentals/links.py` | Private, signed booking links (no renter accounts) |
| `rentals/emails.py` | Emails; text is in `templates/rentals/email/` |
| `rentals/admin.py` | Owner back office |
| `rentals/management/commands/daily_tasks.py` | Run once a day: expire holds, add windows |
| `rentals/tests/` | Tests for all of the above |

## Progress

- [x] 1. Project skeleton, models, admin
- [x] 2. Pricing, availability and fee logic (tested); quote page
- [x] 3. Renter booking flow, private booking link, add moving-in address later.
      *Card and agreement steps slot into Review in milestones 4–5.*
- [ ] 4. Stripe
- [ ] 5. Agreement and signature evidence
- [ ] 6. Owner tools: inbox, schedule, route, at-the-door, check-in
- [ ] 7. Change/cancel, renewals, damage charges, reminders
- [ ] 8. Deploy
