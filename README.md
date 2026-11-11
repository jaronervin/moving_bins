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
- Owner admin: http://127.0.0.1:8000/admin/

Locally it uses a SQLite file. Set `DATABASE_URL` to use Postgres (production
always does).

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
| `rentals/windows.py` | Weekly window pattern → dated delivery windows |
| `rentals/admin.py` | Owner back office |
| `rentals/management/commands/daily_tasks.py` | Run once a day: expire holds, add windows |
| `rentals/tests/` | Tests for all of the above |

## Progress

- [x] 1. Project skeleton, models, admin
- [x] 2. Pricing, availability and fee logic (tested); quote page.
      *Driving-distance lookup (Google Maps) arrives with milestone 3.*
- [ ] 3. Renter booking flow
- [ ] 4. Stripe
- [ ] 5. Agreement and signature evidence
- [ ] 6. Owner tools: inbox, schedule, route, at-the-door, check-in
- [ ] 7. Change/cancel, renewals, damage charges, reminders
- [ ] 8. Deploy
