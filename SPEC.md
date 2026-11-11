# Moving Bins — Product & Technical Spec (v1 draft)

Status: **draft for review**. Nothing is built yet. Items marked **OPEN** need a decision.

---

## 1. What we're building

A small rental site for reusable plastic moving bins, run by two owners.
Renters book bins for whole weeks; the owners drop them off at the house
being moved **out of** and pick them up at the house being moved **into**.

**v1 scope:** one product (standard bins), 100 in stock to start.
**Designed for later:** more bin sizes, hand trucks, dollies, blankets.
The data model treats every rentable thing as a *product* with its own
stock and pricing, so adding one is data entry, not new code.

Launch target: **beginning of 2027**.

---

## 2. Business rules

### 2.1 Rental period
- Rentals run in **7-day blocks**. Renter picks a dropoff date and a number
  of weeks; pickup date = dropoff date + 7 × weeks.
- If bins aren't ready at pickup, the owner marks the pickup **missed** and
  the rental **auto-renews for one more week** at the rate locked in at
  booking (no second pickup fee). This can repeat.

### 2.2 Bin pricing (per week)
- Minimum order: **20 bins**. Any whole number above 20 is allowed, up to
  what's available.
- **Whole-order volume pricing**: the per-bin rate depends on the total count
  and applies to every bin in the order.
- Rate slides in a straight line from **$5.00 at 20 bins** to **$3.00 at 100
  bins**, and stays at $3.00 above 100:

  `rate(n) = 5.00 − 0.025 × (n − 20)` for 20 ≤ n ≤ 100

  `weekly total = n × rate(n)`, rounded to the cent.

| Bins | Per bin | Weekly total |
|---:|---:|---:|
| 20 | $5.000 | $100.00 |
| 35 | $4.625 | $161.88 |
| 40 | $4.500 | $180.00 |
| 60 | $4.000 | $240.00 |
| 80 | $3.500 | $280.00 |
| 100 | $3.000 | $300.00 |

  The total always increases with quantity, so a bigger order never costs
  less than a smaller one.

- **How it's stored:** each product has a list of price points
  `(quantity, per-unit rate)`; the rate is interpolated linearly between
  points and held flat past the last one. Bins = `[(20, 5.00), (100, 3.00)]`.
  A flat-priced item (e.g. a dolly at $15/week) is a single point.
- The per-bin rate is **locked at booking** and reused for renewals.

### 2.3 Service area & delivery fees
All distances are **driving miles** from the business base (for now, the
center of ZIP **21214**; replace with the real address in settings).

| | Within 30 mi | 30–60 mi | Over 60 mi |
|---|---|---|---|
| **Dropoff** (moving-out address) | Free | Not accepted | Not accepted |
| **Pickup** (moving-in address) | Free | **$3 per mile over 30** | Not accepted |

- Miles are rounded to the nearest whole mile. Example: pickup 42 mi away
  → 12 × $3 = **$36**.
- Fee is charged once, with the rental. Renewals don't add it again.
- All of these (base, radius, $/mile, max) are settings, not code.

### 2.4 Sales tax
Collected via **Stripe Tax**. Whether the delivery fee is taxable in Maryland
is a question for an accountant; Stripe handles it once the right tax code is
set per line item.

### 2.5 Cancellation & changes
- Cancel **48+ hours** before the dropoff window starts → full refund.
- **OPEN:** what happens inside 48 hours (no refund? partial? a flat fee?).
- Changes (quantity, dates, windows, addresses) are allowed until the same
  48-hour cutoff. Changes to dates/windows go back to the owner for approval.
  Price differences are charged or refunded automatically.

### 2.6 Damage & loss
- At pickup the owner records bins returned and bins damaged.
- Missing/damaged bins are charged to the saved card at a per-bin
  replacement price stated in the agreement. Renter gets an itemized email.
- **OPEN:** replacement price per bin.

---

## 3. Booking lifecycle

```
 draft ──► requested ──► confirmed ──► out (dropped off) ──► returned ──► closed
              │  ▲            │              │
              │  │ renter     │              └─► renewed (+1 week, repeatable)
              ▼  │ accepts    ▼
          times_proposed   cancelled
              │
              └─► expired (no response in time)
```

### 3.1 Renter flow (no accounts / passwords)
1. **Quote** — pick quantity, dropoff date, number of weeks. Availability and
   price update live.
2. **Addresses** — moving-out (dropoff) and moving-in (pickup), with address
   autocomplete. Distances checked immediately; out-of-range is rejected with
   a clear message; pickup fee shown.
3. **Time windows** — choose a preferred dropoff window and pickup window
   from the windows the owners have published for those dates.
4. **Contact** — name, email, phone, porch/access notes.
5. **Card** — Stripe-hosted form in *setup* mode: the card is verified and
   saved, **nothing is charged yet**. Billing address collected for address
   verification (AVS).
6. **Sign** — read the agreement, tick the box, type full name. Typed name
   must match the name on the payment method (ignoring case/spacing).
7. **Submitted** — status `requested`. Bins are **held for 48 hours** so
   nobody else can book them. Renter gets a confirmation email with a
   private link to view, change or cancel the booking.

### 3.2 Owner response
- **Accept** both windows → status `confirmed` → the saved card is
  **charged** (rental + pickup fee + tax). Renter is emailed a receipt.
- **Propose** different window(s) → status `times_proposed` → renter gets an
  email to accept or decline. Accepting confirms and charges.
- No owner action within 48h, or no renter answer within 48h of a proposal
  → hold expires, renter is emailed, nothing is charged.
- If the charge fails at confirmation, renter is emailed a link to update the
  card; the booking stays held for 48h.
- Confirmed times and addresses can still be adjusted later by either side.

### 3.3 Renter's private link
Emailed links carry a signed, expiring token tied to the booking — no
accounts. A "resend my link" form takes an email address and re-sends links
for that address's bookings.

---

## 4. Inventory & availability

- Bins are interchangeable, so they're **counted, not individually tracked**.
- **Stock** for a product = sum of an adjustments ledger (purchase +100,
  lost −2, damaged −1, …). Nothing is ever overwritten, so history is
  auditable.
- A booking occupies its quantity **from dropoff date through pickup date,
  inclusive**. Statuses that occupy stock: `requested`, `times_proposed`
  (while the hold is live), `confirmed`, `out`, `renewed`.
- **Available(product, start, end)** = stock − the *peak* daily quantity
  booked on any day in that range.
- To prevent two people grabbing the last bins at the same moment, the
  availability check and the hold are done inside one database transaction
  that locks the product row.
- **Renewal conflicts:** if a missed pickup would push the bins into a date
  where they're needed for another booking, the owner sees a warning on the
  schedule.

---

## 5. Owner tools

Built on Django's admin plus a few custom pages.

- **Requests inbox** — pending requests with requested windows; one click to
  accept, or pick alternatives to propose.
- **Availability windows** — owners publish windows per date (e.g. Sat
  8–10am, 10–12pm) with a max number of stops each. Renters can only pick
  published windows with room.
- **Schedule** — day and week views of all dropoffs and pickups, grouped by
  window, with bin counts, addresses, access notes and status.
- **Route** — for any day + window: stops in an efficient order starting
  and ending at base, total miles and drive time, and a one-tap "Open in
  Google Maps" link with all stops. At this volume (a handful of stops), the
  best order can simply be computed exhaustively from a distance matrix.
- **At the door** — from a phone: mark dropped off / picked up / missed
  (renews), record returned & damaged counts, optional porch photo.
- **Inventory** — current stock, the adjustments ledger, and a calendar of
  bins out per day.

**OPEN:** how many bins fit in the vehicle per trip? (Lets the route view
warn when a window's dropoffs exceed one load.)

---

## 6. Payments & card security

- **Stripe Checkout / Elements** collects card details. Card numbers go
  straight from the renter's browser to Stripe and never touch our server or
  database. We store only Stripe IDs (`cus_…`, `pm_…`, `pi_…`).
- This keeps us at the simplest PCI level (SAQ A, a short yearly
  self-assessment).
- The card is saved with the renter's consent for later off-session charges
  (confirmation, renewals, damage) — the agreement includes that
  authorization.
- Refunds (cancellation, reduced quantity) go back through Stripe.
- Saved cards are **detached 30 days after a booking closes** if no damage
  claim is open.
- Owners must enable two-factor auth on the Stripe account.

---

## 7. Rental agreement & signature evidence

- Agreement text is **versioned**; each signature points to the exact
  version signed.
- At signing we record:
  - typed name, and the name on the payment method (must match)
  - billing ZIP/street **AVS results** from the card's bank
  - email address (signed copy emailed immediately as a PDF)
  - timestamp, IP address, browser
  - SHA-256 hash of the agreement text shown
- Combined with the delivery to their address and the porch photos, this
  gives solid evidence for any dispute or chargeback.
- Agreement text to be drafted together (separate task).

---

## 8. Emails

Transactional only: request received, times proposed, confirmed + receipt,
reminder the day before each visit, renewed, returned/closed, damage charge,
cancelled/refunded, hold expired.

---

## 9. Data model (first cut)

| Table | Key fields |
|---|---|
| `Product` | name, slug, active, replacement_price, stripe_tax_code |
| `PricePoint` | product, min_quantity, unit_rate |
| `StockAdjustment` | product, delta, reason, note, created_at |
| `Customer` | name, email, phone, stripe_customer_id |
| `Address` | line1, line2, city, state, zip, lat, lng, place_id |
| `Booking` | customer, status, dropoff_date, weeks, pickup_date, dropoff_address, pickup_address, pickup_miles, pickup_fee, hold_expires_at, stripe_payment_method_id, access_notes |
| `BookingLine` | booking, product, quantity, locked_unit_rate |
| `TimeWindow` | date, start, end, max_stops |
| `Visit` | booking, kind (dropoff/pickup), window, proposed_window, status, completed_at, photo |
| `Charge` | booking, kind (rental/renewal/damage/refund), amount, tax, stripe_id, status |
| `AgreementVersion` | version, text, sha256, effective_at |
| `Signature` | booking, agreement_version, typed_name, card_name, avs_line1, avs_zip, ip, user_agent, signed_at |
| `ReturnReport` | booking, product, returned, damaged, missing |
| `Settings` | base address, radii, $/mile, hold hours, cancel cutoff |

---

## 10. Stack & running costs

| Piece | Choice | Est. monthly cost |
|---|---|---|
| App | Python 3.12 + Django 5, plain server-rendered pages + a little JS | — |
| Database | Postgres | — |
| Hosting | Render or Railway (web app + Postgres + daily scheduled job) | ~$15–20 |
| Payments | Stripe (~2.9% + 30¢ per charge) + Stripe Tax (~0.5%) | per transaction |
| Maps | Google Maps Platform: address autocomplete, geocoding, driving distances | $0 at this volume (free monthly allowance) |
| Email | Postmark or Resend | $0 on free tier |
| Domain | — | ~$1 (≈$12/yr) |

Comfortably under $50/month before transaction fees.

Code will be kept plain and well-commented, with tests around pricing,
availability and fees.

---

## 11. Build plan

| # | Milestone | Done when |
|---|---|---|
| 1 | Project skeleton, models, admin | Products, stock ledger and bookings editable in admin |
| 2 | Pricing, availability, distance fee logic | Fully tested; quote page works |
| 3 | Renter booking flow | Quote → addresses → windows → contact |
| 4 | Stripe: save card, charge on confirm, refunds | Works end-to-end in Stripe test mode |
| 5 | Agreement + signature evidence + PDF email | Signed copy arrives by email |
| 6 | Owner tools: inbox, propose times, schedule, route, at-the-door | Owners can run a week from their phones |
| 7 | Change/cancel link, renewals, damage charges, reminders | All lifecycle paths covered |
| 8 | Deploy, domain, live Stripe, dry run with friends | Ready for January 2027 |

---

## 12. Things the owners need to set up

- [ ] Business name + domain
- [ ] Stripe account (enable 2FA); Stripe Tax registration
- [ ] Maryland sales tax registration (confirm with an accountant)
- [ ] Google Cloud account with billing enabled (for the Maps API key)
- [ ] Email sending provider (needs the domain)
- [ ] Hosting account (Render or Railway)
- [ ] Exact base address (replaces ZIP 21214 center)
- [ ] Liability insurance / business entity (worth asking about)

---

## 13. Open questions

1. Cancellation inside 48 hours: no refund, partial, or a flat fee?
2. Replacement price per lost/damaged bin.
3. Vehicle capacity in bins per trip.
4. Typical windows you'll publish (e.g. 2-hour blocks, which days).
5. Can a renter book before they know the moving-in address (fill it in
   later, say up to 3 days before pickup)?
6. Should the pickup be allowed earlier than scheduled if they finish early
   (no refund), or always on the booked date?
