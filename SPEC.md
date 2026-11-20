# Moving Bins — Product & Technical Spec (v1 draft)

Status: **approved, in progress** (milestones 1–3 built; see README). Items marked **OPEN** need a decision.

---

## 1. What we're building

A small rental site for reusable plastic moving bins, run by two owners.
Renters book bins for whole weeks; the owners drop them off at the house
being moved **out of** and pick them up at the house being moved **into**.

**Why:** sustainability first, savings second. Durable bins replace cardboard
boxes that fall apart and get thrown away; each bin can serve hundreds of
moves before being recycled. The site should say so, and show the running
impact (see §5.1).

**v1 scope:** one product (a standard bin **with its lid**, rented as a set).
Launch plan: **own 60, take bookings as if 120** (planned capacity), and buy
more from Costco (about 3 business days) when bookings call for it. 150
unique codes are printed up front; unused ones stay reserved.
**Designed for later:** more bin sizes, hand trucks, dollies, blankets.
The data model treats every rentable thing as a *product* with its own
stock and pricing, so adding one is data entry, not new code.

Launch target: **beginning of 2027**.

---

## 2. Business rules

### 2.1 Rental period
- Rentals are **2, 3 or 4 weeks** online (settings: min 2, max 4). Shorter or
  longer: renters contact us. Pickup date = dropoff date + 7 × weeks.
- If totes aren't ready at pickup (with an any-time-that-day grace period),
  the rental **auto-renews for one week** at the renewal price locked in at
  booking: the package's extra-week price plus $3 per extra tote. Repeats.

### 2.2 Packages and prices
Every rental starts from a package (prices for 2 weeks, from Katie's sheet):

| Package | Totes | Included | 2 weeks | Extra week |
|---|---:|---|---:|---:|
| Studio/1BR | 20 | 1 dolly | $119 | $45 |
| 2BR | 40 | 2 dollies | $169 | $65 |
| 3BR | 60 | 2 dollies | $229 | $85 |
| Whole Home | 80 | 3 dollies, 1 hand truck | $299 | $99 |

- **Extra totes:** $5 each for the first 2 weeks, then $3 per tote per extra
  week. Priced so a package plus extra totes never undercuts the next
  package up, for 2, 3 or 4 weeks (tested).
- **Every rental includes** color-coded labels, zip ties and a marker.
- **Rental extras**, one-time fee per rental (priced near cost, so two uses
  pay for them): dolly $10, hand truck $15, appliance dolly $25, moving
  blanket $5, wardrobe box $15, dish/glass divider $15 (**placeholder**;
  quoted as $15–30), floor runner & door jamb guards $25.
- **For sale:** mattress bag Queen/King $10, Twin/Full $8, packing kit
  (kraft paper, honeycomb wrap, paper tape) $35, foam pouch $1.
- All prices, packages and contents are editable in admin. Prices are
  **locked at booking** as price lines.
- Stock for extras isn't checked until counts are entered (each product has
  a "track stock" switch).

### 2.3 Service area & delivery fees
Straight-line distance ("radius") from base (approx. center of ZIP 21214;
replace with the real address in settings):

| Distance | Dropoff and pickup |
|---|---|
| Up to 20 mi | Free |
| 20–35 mi | **$25 per address** (**placeholder**; real amount to come) |
| Over 35 mi | Not served online |

A city list may be added later. Addresses are looked up with Google
Geocoding; no driving-distance service is needed.

### 2.4 Sales tax
Collected via **Stripe Tax**. Whether the delivery fee is taxable in Maryland
is a question for an accountant; Stripe handles it once the right tax code is
set per line item.

### 2.5 Cancellation & changes
- Cancel **72+ hours** before dropoff → nothing charged.
- Cancel **inside 72 hours** → **50% of the rental** (package, extra totes,
  rental extras) plus tax. No delivery fee or sale items.
- Nothing is charged before dropoff (§6.1), so no refunds are needed.
- Changes allowed until the same 72-hour cutoff. Minimum notice to book is
  also 3 days (72 hours), matching the ~3 business days to buy more totes.
- **Early pickup:** allowed at the renter's convenience, no refund.

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
1. **Package**: pick a package, 2/3/4 weeks, and a dropoff date at least 3
   days out on a day we deliver. Tote availability is checked here.
2. **Extras**: extra totes, rental extras, items for sale. A running order
   summary shows on every step from here.
3. **Addresses**: moving-out (dropoff) and moving-in (pickup). Out of range is
   refused; outer-zone fees are added.
   - The moving-in address can be left blank and added later from the
     booking page. **No dropoff happens without it.**
4. **Delivery**: each delivery day has a "delivered by" time (e.g. by 1 PM);
   pickups say "have totes ready by" the start time. Renter chooses **leave
   at the door/porch (their risk)** or **I'll be there**, with an optional
   specific-time request (required if they'll be there).
5. **Contact**: name, email, phone, notes.
6. **Card** and **agreement** (coming): card saved, nothing charged; typed
   signature must match the card name.
7. **Booked**: confirmed right away (§3.2). Renter gets a "You're booked"
   email with a private link to view, change or cancel.

### 3.2 Owner response

**Auto-confirm (default):** a submitted booking is **confirmed immediately**
for the days the renter chose, so renters never wait on "pending approval".
The owners get an email and have **48 hours** (setting) on the owner page to
**decline** it or **ask for a change** (e.g. a different day), with a
message emailed to the renter and shown on their booking page.
Nothing has been charged yet, so a decline costs the renter nothing. After 48
hours, changes happen by agreement with the renter like any confirmed booking.

**Manual approval (setting off):** the original flow. The request holds the
bins for 48 hours while the owners accept, or propose other windows for the
renter to accept. No answer in time → the hold expires.

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
- **Available(product, start, end)** = bookable capacity − the *peak* daily
  quantity booked on any day in that range. Bookable capacity = what you own,
  or the product's **planned capacity** if higher.
- **Buy-more warning:** when bookings on any upcoming day exceed what you
  actually own, the owner page and the new-request email say how many to buy
  and the date to order by (first short day − purchase lead time, default 5
  days: 3 business days delivery plus time to sticker). For future dates this *assumes* bins
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

Each bin also counts its own rentals ("this tote has helped 23 moves"), see §5.2.

### 5.2 QR-labelled totes

Every bin has a permanent 3-character code (e.g. `K7Q`: no 0/O/1/I/L, rude
combinations skipped, never reused) and a waterproof label on two sides with
a QR code and the code in large print. The QR points to
`HTTPS://SPROUTTOTE.COM/Q/K7Q` (upper case keeps the code small), which
redirects to the main site with the code kept. Lids aren't labelled.

**Owners (staff login, phone camera, no app):**
- **Load at base:** scan each bin onto the booking; the page shows "19 of 20".
  Dropoff can only be marked done when the count matches and the renter has
  given their moving-in address. Tap a code to undo a scan.
- **Pickup:** mark done; scanning at the door is optional, since totes are
  usually stacked and waiting. Totes not scanned there are scanned at check-in
  back at the warehouse, which also counts as their return.
- **Check-in after cleaning:** pick Good / Lid missing / Broken (optionally
  "lid still good" → spare), scan. Closing the rental marks any bin never
  scanned at pickup as **missing** and works out the damage charge from the
  actual bins: $10 per broken or missing bin, $5 per lid issue.
- **Bin page:** full history; fit a spare lid; mark a missing bin found.
- **Labels:** printable sheet, 2" square labels, 15 per Letter page, 1–4
  copies per bin. Professional waterproof stickers are planned: two on each
  tote plus a spare of the same code for a damaged sticker.
- **Codes printed ahead of time:** codes can be created as *reserved*
  (`python manage.py create_codes 100`) for a print run before all the totes
  exist. Reserved codes aren't inventory and can't be booked or loaded. When a
  label goes on a tote, scanning it at check-in with Good (or "Put in
  service" on its page) puts it on the shelf. A replacement for a destroyed
  tote gets a new code from this pool, not the old tote's.

**Renters:** scanning a tote opens a short form: what's inside, room at the
new place (suggestions + rooms they've used), Fragile, Open first. Their
booking page lists every tote with search ("pans" → `K7Q`) and a print view.
Notes can be edited only while the totes are with them and are **deleted 30
days after pickup** (setting).

**Privacy:** a stranger who scans a tote sees only "this is a Sprout Tote Co.
tote". Contents show only on a browser that has opened the renter's private
booking link (so family or movers they share the link with can help).

Bins are the source of truth for bin counts; the stock ledger now covers
counted items only (spare lids, and later blankets etc.).

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

### 6.2 How it's built (Stripe)

Built following the Stripe plugin's best-practices guidance (API version
`2026-09-30.endive`, `stripe` Python SDK 16, a `StripeClient` instance, no
`payment_method_types`):

| Step | Stripe API |
|---|---|
| Save card at booking | Checkout Session, `mode=setup`, `ui_mode=embedded_page`, mounted with Stripe.js `createEmbeddedCheckoutPage` on our card page. Totes are held 30 min (`awaiting_card`) while the renter enters it. |
| Hold before dropoff | PaymentIntent, `capture_method=manual`, `off_session`, saved card (daily task). Failure → renter emailed to update card, owners alerted. |
| Charge at dropoff | Capture the hold (less if the total dropped; capture + extra charge if it grew). No hold → charge directly. |
| Decline / cancel in time | Cancel the hold. |
| Late cancel | Capture 50% of the rental from the hold (or charge it). |
| Damage, renewal weeks | Off-session PaymentIntent on the saved card. |
| Records | Every attempt is a `Charge` row; webhooks keep them in step. |

**Webhooks** (`/stripe/webhook/`, signature-verified): `checkout.session.completed`,
`payment_intent.*`, `charge.dispute.created` (owners alerted).

**Security:** keys only from the environment (never in code); a
Content-Security-Policy on every page (no inline scripts); Stripe recommends a
**restricted key** (`rk_`) in production instead of the full secret key.

**Sales tax: not on yet.** Stripe Tax needs, first: Maryland registration
recorded in Stripe (Tax → Locations), a head-office address in Tax settings,
and product tax codes chosen with your accountant (rentals, delivery, sale
items). Until then no tax is collected, by design rather than silently.

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
| `Product` | name, kind (tote/rental/sale/part), price, addon_price, addon_extra_week_price, track_stock, replacement prices |
| `Package` / `PackageItem` | name, totes, price, extra_week_price; included items (dollies, hand truck) |
| `PriceLine` | booking, kind, description, quantity, unit_price, amount (locked at booking) |
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
| `Bin` | product, code, status, booking (current), moves, created_at |
| `BinEvent` | bin, kind (loaded, picked up, checked in…), booking, at, by |
| `BinContents` | booking, bin, contents, room, fragile, open_first (deleted 30 days after pickup) |
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

- [x] Business name + domain (Sprout Tote Company, sprouttoteco.com)
- [ ] Stripe account (enable 2FA); Stripe Tax registration
- [ ] Maryland sales tax registration (confirm with an accountant)
- [ ] Google Cloud account with billing enabled (for the Geocoding API key)
- [ ] Email sending provider; `hello@sprouttoteco.com`
- [ ] Hosting account (Render or Railway)
- [ ] Exact base address (replaces ZIP 21214 center)
- [ ] Liability insurance / business entity (worth asking about)

**Placeholders to replace before launch:** phone 703-703-2511; 20–35 mile
fee $25; dish/glass divider $15; Whole Home $299 (pending Katie); stock
counts for extras.

---

## 13. Open questions

1. Whole Home at $299 (proposed; was $449): pending Katie.
2. Fee for the 20–35 mile zone.
3. Dish/glass divider: one price or sizes?
4. How many of each rental extra you own (to turn on stock checks).
5. City list for the service area: replace or supplement the radius?
6. Real phone number.
