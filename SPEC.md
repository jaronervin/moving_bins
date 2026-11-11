# Moving Bins — Product & Technical Spec (v1 draft)

Status: **approved, in progress** (milestones 1–2 built; see README). Items marked **OPEN** need a decision.

---

## 1. What we're building

A small rental site for reusable plastic moving bins, run by two owners.
Renters book bins for whole weeks; the owners drop them off at the house
being moved **out of** and pick them up at the house being moved **into**.

**Why:** sustainability first, savings second. Durable bins replace cardboard
boxes that fall apart and get thrown away; each bin can serve hundreds of
moves before being recycled. The site should say so, and show the running
impact (see §5.1).

**v1 scope:** one product (a standard bin **with its lid**, rented as a set),
100 in stock to start.
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

  `rate(n) = 5.00 − 0.025 × (n − 20)` for 20 ≤ n ≤ 100, rounded to the cent

  `weekly total = n × rate(n)` (so the receipt math always checks out)

| Bins | Per bin | Weekly total |
|---:|---:|---:|
| 20 | $5.00 | $100.00 |
| 35 | $4.63 | $162.05 |
| 40 | $4.50 | $180.00 |
| 60 | $4.00 | $240.00 |
| 80 | $3.50 | $280.00 |
| 100 | $3.00 | $300.00 |

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
- Cancel **48+ hours** before the dropoff window starts → nothing charged.
- Cancel **inside 48 hours** → the card is charged **50% of the rental**
  (plus tax on that amount). No pickup fee, since that trip never happens.
- Nothing is charged before dropoff (§6.1), so cancelling earlier just
  releases the hold. No refunds needed.
- Changes (quantity, dates, windows, addresses) are allowed until the same
  48-hour cutoff. Changes to dates/windows go back to the owner for approval.
  Since nothing is charged until dropoff, the new price simply replaces the
  old one.
- **Early pickup:** renters may ask for an earlier pickup if they finish
  early (owner approves the window). No refund for unused days.

### 2.6 Damage & loss
- Damage is assessed at **check-in** (§4.2), not at the door, so it's done
  carefully after cleaning. Check-in must be done within **3 days of
  pickup**; after that, no damage charge for that booking. (Keeps renters
  from being surprised weeks later.)
- Charges, set in settings and stated in the agreement:
  - **Missing or broken bin (with or without its lid): $10** — the retail
    replacement cost.
  - **Missing or broken lid only: $5.** Lids aren't sold separately, but
    broken bins leave good spare lids behind (§4.1).
  - Scuffs, scratches and normal wear: no charge.
- Charged to the saved card with an itemized email.

> Why $10 and not $6–7: the weekly rent paid for the *use* of that bin for
> the week. Once it's lost, you also lose every future rental it would have
> earned, plus the time to buy and prep a replacement. Charging the full
> replacement cost is standard and easy to defend. The bins quickly cover
> their cost: at $3–5 a week, a $10 bin pays for itself in 2–4 rentals.

---

## 3. Booking lifecycle

```
 draft ──► requested ──► confirmed ──► out (dropped off) ──► picked_up ──► checked_in ──► closed
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
   - The **moving-in address can be left blank** at booking ("I don't know
     yet"). The renter is reminded by email until they add it. **No dropoff
     happens without it**. The owner sees a "missing pickup address" flag,
     and the dropoff can't be marked done until the address is in.
   - The pickup fee is quoted once the address is added, and charged with
     the rental at dropoff.
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
- **Accept** both windows → status `confirmed`. Nothing is charged yet
  (§6.1). Renter is emailed a confirmation.
- **Propose** different window(s) → status `times_proposed` → renter gets an
  email to accept or decline. Accepting confirms.
- No owner action within 48h, or no renter answer within 48h of a proposal
  → hold expires, renter is emailed, nothing is charged.
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
  booked on any day in that range. For future dates this *assumes* bins
  come back on their pickup date and pass check-in (no buffer day).

### 4.1 Unit states
Every bin set is in one of these states (counts, not individual bins):

| State | Meaning | Rentable? |
|---|---|---|
| `ready` | Clean, inspected, on the shelf | Yes |
| `out` | With a renter | No |
| `awaiting_check_in` | Picked up, not yet cleaned/inspected | Not yet |
| `out_of_service` | Bin is fine but needs a lid | No |
| `retired` | Broken beyond use → recycling | No (leaves stock) |

**Spare lids** are tracked as their own non-rentable item. When a bin breaks
but its lid is fine, the lid goes into spares. A spare lid can return an
`out_of_service` bin to `ready` in one step.

Moves between states are recorded in the ledger, so the counts always
add up and you can see history (e.g. how many bins were retired this year).

### 4.2 Check-in
After pickup the bins move to `awaiting_check_in`. Once cleaned and
inspected, the owner records for that booking:
- **good** → back to `ready`
- **missing** bins / lids
- **broken** bins → `retired` (recycled)
- **lid missing/broken** → `out_of_service` until a lid is replaced

This closes the booking and triggers any damage charge (§2.6).

**Safety check:** the schedule warns when an upcoming dropoff needs more
`ready` bins than are on the shelf (for example, a big job the morning after
another big pickup that hasn't been checked in yet).
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
- **Availability windows** — owners publish windows per date with a max
  number of stops each. Renters can only pick published windows with room.
  Since demand is unknown, windows are fully configurable: set up a weekly
  pattern (e.g. Sat/Sun 8–10, 10–12, 1–3) and add, remove or close
  individual dates on top of it. Tune it as demand shows up.
- **Schedule** — day and week views of all dropoffs and pickups, grouped by
  window, with bin counts, addresses, access notes and status.
- **Route** — for any day + window: stops in an efficient order starting
  and ending at base, total miles and drive time, and a one-tap "Open in
  Google Maps" link with all stops. At this volume (a handful of stops), the
  best order can simply be computed exhaustively from a distance matrix.
- **At the door** — from a phone: mark dropped off / picked up / missed
  (renews), count of bin sets picked up, optional porch photo.
- **Check-in** — list of bookings awaiting check-in; enter good / missing /
  broken / lid issues per booking (§4.2).
- **Inventory** — counts by state (ready, out, awaiting check-in, out of
  service, retired), the ledger, and a calendar of bins out per day.

Vehicle capacity: assumed to fit all 100 sets in one trip, so no load
warnings for now. Kept as a setting in case that changes.

### 5.1 Impact stats
Shown on the public homepage and in the owner dashboard, all derived from
bookings (no extra work at the door):
- moves served
- bin-weeks rented
- estimated cardboard boxes avoided (bin-sets rented × **2.5 boxes** per
  27-gallon bin; settable)
- bins retired and recycled

Per-bin lifetime counts ("this bin has helped 47 moves") would need a
label/QR code on every bin and a scan at each dropoff and pickup. Not in v1,
but the ledger design leaves room for it.

---

## 6. Payments & card security

- **Stripe Checkout / Elements** collects card details. Card numbers go
  straight from the renter's browser to Stripe and never touch our server or
  database. We store only Stripe IDs (`cus_…`, `pm_…`, `pi_…`).
- This keeps us at the simplest PCI level (SAQ A, a short yearly
  self-assessment).
- The card is saved with the renter's consent for later off-session charges
  (dropoff, late cancellation, renewals, damage). The agreement includes that
  authorization.

### 6.1 When money moves

| Moment | What happens |
|---|---|
| Booking | Card verified and saved. **$0 charged.** |
| ~24h before dropoff | **Authorization** (a pending hold, not a charge) for rental + pickup fee + tax. If it fails, the renter is emailed to update the card and the owner sees a flag *before* loading the truck. |
| Dropoff marked done | Authorization **captured**, so this is when the charge actually happens. Receipt emailed. |
| Cancel ≥ 48h out | Nothing. Any authorization is released. |
| Cancel < 48h out | 50% of rental + tax charged (taken from the authorization if there is one; the rest is released). |
| Missed pickup (renewal) | One week at the locked rate, charged right away. |
| Check-in damage | Damage total charged. |

The authorization step is optional (a setting). Without it, the card is
simply charged when dropoff is marked done, and a declined card is only
discovered at the door.
- Refunds are rare, since nothing is charged before dropoff. If one is
  needed (e.g. an owner-side mistake) it goes back through Stripe.
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
cancelled, hold expired.

---

## 9. Data model (first cut)

| Table | Key fields |
|---|---|
| `Product` | name, slug, active, replacement_price, part_replacement_price (lid), stripe_tax_code |
| `PricePoint` | product, min_quantity, unit_rate |
| `StockMovement` | product, quantity, from_state, to_state, booking (optional), reason, note, created_at |
| `Customer` | name, email, phone, stripe_customer_id |
| `Address` | line1, line2, city, state, zip, lat, lng, place_id |
| `Booking` | customer, status, dropoff_date, weeks, pickup_date, dropoff_address, pickup_address, pickup_miles, pickup_fee, hold_expires_at, stripe_payment_method_id, access_notes |
| `BookingLine` | booking, product, quantity, locked_unit_rate |
| `TimeWindow` | date, start, end, max_stops |
| `Visit` | booking, kind (dropoff/pickup), window, proposed_window, status, completed_at, photo |
| `Charge` | booking, kind (rental/late_cancel/renewal/damage/refund), amount, tax, stripe_id, status (authorized/captured/released/failed/refunded) |
| `AgreementVersion` | version, text, sha256, effective_at |
| `Signature` | booking, agreement_version, typed_name, card_name, avs_line1, avs_zip, ip, user_agent, signed_at |
| `CheckIn` | booking, product, good, missing, broken, lid_issues, checked_in_at, by |
| `Settings` | base address, radii, $/mile, hold hours, cancel cutoff, late-cancel charge %, check-in deadline, vehicle capacity, boxes-per-bin |

---

## 10. Stack & running costs

| Piece | Choice | Est. monthly cost |
|---|---|---|
| App | Python 3.12+ + Django 5.2 LTS, plain server-rendered pages + a little JS | — |
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
| 4 | Stripe: save card, authorize, charge at dropoff | Works end-to-end in Stripe test mode |
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

None right now.

Decided:
- Late cancellation → 50% of rental charged (§2.5).
- Missing/broken bin → $10; lid only → $5 (§2.6).
- Nothing charged until dropoff; late cancel charges 50% (§6.1).
- 2.5 boxes per bin for impact stats (§5.1).
- Vehicle fits all 100 (§5).
- Windows: configurable, tuned to demand (§5).
- Booking allowed without moving-in address; no dropoff without it (§3.1).
- Early pickup allowed, no refund (§2.5).
