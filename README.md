# BookingTracker

Version 0.5.10 adds the server-rendered reservation dashboard, local detail price history, and a
presentation-only defence-in-depth gate: current and historical prices are displayed only for
accepted `exact`, `equivalent`, or objectively `better` matches in the reservation currency.
Version 0.5.9 adds `availability_unknown` for an exact Booking search that yields neither offers
nor an explicit unavailable surface. It is not `no_availability` (explicitly unavailable),
`no_comparable_offer` (a healthy completed check whose offers are unsafe to compare), or
`parser_error` (concrete room/rate/current-price evidence whose required structure is unknown).
The safe browser path is `goto` → bounded wait → scroll → one allowlisted availability CTA →
bounded wait; the former second `goto` is removed. The CTA is clicked at most once and never comes
from booking, payment, or confirmation controls. CAPTCHA/login and navigation errors take
priority, as do recognized offers and explicit no-availability; concrete offer hints still go to
the parser. An empty shell safely says: “Dostupnost se nepodařilo ověřit. Booking.com pro zadaný
termín nezobrazil nabídky ani potvrzení, že je ubytování vyprodané.” It creates no price, delta,
`PRICE_DROP`, or `CHECK_FAILED`, leaves the failure series unchanged, and retries in two hours.
The Comfortable and Downtown live CTA test validated this path. PDF import, matcher, scheduler
lock, and comparable-price logic are unchanged.

Version 0.5.8 derives PDF hotel identity from a strict safe Booking hotel hyperlink. It reads a
visible property name only when its text geometrically overlaps the annotation, composing both PDF
text and graphics transforms so translated, scaled, rotated, and Gmail layouts remain correct.
Canonical hotel URLs drop query and fragments; confirmation, help, payment, homepage, and external
links are rejected. Duplicate annotations for one hotel are harmless, but different hotel URLs
require manual review. The generic arbitrary-line fallback is removed; the safe confirmation anchor
remains the fallback. Guest House names, stay dates, cancellation, adults-only `children=0`, and
the conservative price matcher are unchanged. A real Gmail confirmation passed the public local
PDF-upload E2E path; no migration or rewrite of stored reservations is needed.

Version 0.5.7 fixes a second English Booking PDF layout safely. Legitimate property names such as
`Guest House`, `Hotel`, `Hostel`, `Riad`, and `Apartment` remain valid; only complete normalized
section headings such as Payment methods, Cancellation policy, Booking details, Price information,
and Guest details are rejected. The authoritative `Your booking is confirmed at …` anchor supports
one safe wrapped continuation but never absorbs a following section, date, address, reservation
number, or payment text. `Cancellation policy` is recognized; a confirmed free cancellation with no
safe deadline renders as `Bezplatné zrušení`, not an invented date. Cancellation dates remain
separate from the stay. The conservative matcher, browser navigation, and exact/equivalent/better
price safeguards are unchanged. A real local public PDF-upload E2E validation passed; no migration
or stored-reservation rewrite is needed.

Version 0.5.6 fixes conservative Booking confirmation PDF import. The unsafe unanchored
property-name fallback is gone: payment-card lists and generic payment, cancellation, amenity,
tax, guest, and contact sections cannot become accommodation identity. The authoritative English
`Your booking is confirmed at …` anchor also tolerates a harmless PDF layout column. English
`DD Month YYYY` stay dates are recognized only from stay evidence; payment, cancellation,
issuance, and confirmation dates remain non-stay evidence. Conflicting or unproven facts stay
empty for review. The adults-only `children=0` rule remains intact. A real local public PDF-upload
E2E validation passed; no migration was needed and existing reservations are unchanged. The
0.5.5 exact/equivalent/better matcher and price safeguards are unchanged.

Version 0.5.5 adds automatic `exact`, `equivalent`, and objectively `better` room comparison
without weakening the booked-reservation invariant. A different room name and marketing labels
(Economy, Classic, Superior, Deluxe and similar) prove nothing. Room facts are tri-state:
explicit true, explicit false, or unknown; missing DOM text is never false. Every known booked
fact remains protected, including private room versus dorm bed, balcony, view, bathroom, food,
cancellation, payment, occupancy, currency, and tax-inclusive price basis. A better fact never
compensates for a worse or unknown one. Among independently safe candidates the lowest
tax-inclusive full-stay total in the same currency wins; exact, equivalent, better, then stable
diagnostic index break only a price tie. Non-orderable terms are ambiguous without a price. Safe
evidence and objective differences are persisted without raw DOM or PII; old snapshots remain
readable without a migration. The detail and `PRICE_DROP` alert name the selected category.

Version 0.5.4 fixes post-release reservation validation and failure classification. A reliable
adults-only confirmation block stores `children=0`, while conflicting or unrecognized occupancy
remains unknown. Missing search facts are a repairable `incomplete_reservation`, never a Booking
navigation error: the browser does not open, and there is no technical backoff or failure alert.
`no_comparable_offer` is technically healthy, uses the normal interval, and resets the technical
failure series. A historical `CHECK_FAILED` is hidden from the current detail after a newer healthy
check, but retains its history, delivery state, and manual-only `acknowledged_at`. Sanitization is
idempotent and ordinary UI remains Czech-only. Exact matcher, offer parser, URL builder, and
browser navigation are unchanged.

Version 0.5.3 fixes deterministic Booking navigation and scheduler reliability after 0.5.2
navigated only a canonical hotel URL: dates and occupancy could then depend on cookies and profile
state, and the scheduler could wait behind a manual check before running a stale second attempt.
Version 0.5.1 added a safe, serialized manual price check with persisted diagnostics,
Czech results, and one sanitized structured stdout event. Version 0.5.0 completed Czech
navigation, presentation helpers, and compact typography after production validation. Version
0.4.3 makes Booking's Czech/English accommodation waiting sentence the authoritative property
anchor, including PDF line breaks and Unicode names. Review uses a compact, isolated
stylesheet. Synthetic PDFs are created only in memory during tests; no real confirmation or
personal data is committed.

The next unreleased matcher follow-up retains the exact-room invariant while allowing a differently
named room only when explicit Booking evidence proves it objectively equivalent or better. It
never treats Economy, Classic, Superior, Deluxe, Premium, Executive, or Suite wording as quality
evidence. A candidate must confirm property, requested occupancy, one-room availability, currency,
tax-inclusive full-stay total, food, cancellation and payment protections; missing evidence is
non-comparable. Explicit room facts (private room versus dorm bed, bathroom, balcony/terrace,
area, view, air conditioning, kitchen, accessibility and bed type) cannot be lost. The deterministic
lowest safe total across exact/equivalent/better wins; category order breaks an equal-price tie,
while non-orderable terms are ambiguous. No migration is needed: structured match evidence is
already retained in immutable match-result and offer-snapshot JSON. Papaya's dorm bed is rejected,
and Economy/Classic names alone remain non-comparable unless the DOM proves all booked facts.

The preceding corrective change distinguishes a technically failed Booking check from a
valid completed check without a safely comparable offer. A reliably recognized guest block such
as `2 adults` or `2 dospělí` now records zero children; an unclear or conflicting guest block
remains unknown for review. Missing search facts stop before browser navigation as
`incomplete_reservation` with a Czech correction message. `no_comparable_offer` is a healthy,
price-less outcome: it resets the technical failure series, uses the normal schedule interval,
and hides a superseded technical-failure alert only on the current detail. Exact-match rules and
the no-false-`PRICE_DROP` guard remain unchanged.

Version 0.4.1 fixes property evidence scoring for Gmail-exported PDFs and presents recognized
reservation facts as Czech read-only summaries until the user chooses to correct them.

Version 0.4.2 consolidates strong PDF property evidence after extraction and supports Czech
split-line cancellation text. The responsive review remains local-only; no real confirmation PDF
or personal data is stored in the repository.

Version 0.4.0 adds a preferred Booking confirmation PDF import. The local service extracts
text and safe canonical Booking hotel links only in memory; copy-paste confirmation text
remains available as a fallback and every critical extracted field is reviewable before save.

BookingTracker is a personal, local Booking.com reservation price tracker. It
tracks the exact reservation you already hold and compares only equivalent—or
explicitly labelled better—Booking.com offers. It does not look for the
cheapest unrelated hotel room.

It never books, cancels, changes reservations, enters payment data, stores
Booking credentials, or bypasses CAPTCHA. Login and CAPTCHA resolution are
always manual.

## Runtime targets

- Development: macOS, Python virtual environment, local SQLite, and manual
  Playwright smoke checks.
- Production: Raspberry Pi 4 (aarch64), Home Assistant OS, and a custom
  Home Assistant add-on accessed from the sidebar through Ingress.

Production state persists in add-on `/data`:

- `/data/bookingtracker.db`
- `/data/booking_profile/`
- `/data/logs/`

Phases 0–10 are complete: reservation import, persistent browser service,
fixture-backed rate parsing, exact reservation matching, SQLite history,
local scheduling with alerts, the local web UI, and Home Assistant add-on
packaging.
The local SQLite layer uses migrations, foreign keys, decimal-text money, and
immutable price-check/offer snapshots. A comparable price requires an accepted
match, identical currency, and explicit current taxes/fees inclusion;
`delta = current - booked`, so negative is cheaper. The complete
implementation sequence is in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
and the deployment/design details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Phase 11 roadmap

Phase 11 — Czech frontend and reservation dashboard — is in progress. It will make
the local single-user Home Assistant interface fully Czech, compact, and
logically navigable, taking inspiration only from TripWatch's information
density without copying its brand or source code. Phase 11A / 0.5.0 is COMPLETE after
production validation. The 0.5.1 diagnostic intermediate step is production-validated. The
0.5.3 parser/navigation reliability was production-validated. The 0.5.8 PDF-import reliability
release is validated locally. Phases 11B and 11D are **IN PROGRESS**; 11C remains planned.

- **11A / 0.5.0 — Navigation, Czech language, and typography:** a `Rezervace`
  home page, Czech global navigation and presentation mappings, reliable back
  links, active navigation state, and shared compact accessible design tokens. COMPLETE.
- **Diagnostic / 0.5.1 — Manual check and safe diagnostics:** one prominent,
  CSRF-protected and Ingress-aware manual check through the shared serialized runner,
  persisted Czech diagnostics, alerts, and one sanitized stdout JSON event. Implementation
  complete and production-validated on STORHAUGEN GARD.
- **Reliability / 0.5.3 — Deterministic availability checks:** bounded optional locator reads,
  deterministic URL construction from stored reservation facts, availability waiting with one
  retry, safe diagnostics, and scheduler revalidation without duplicate history. Production
  validation confirmed the expected Papaya `no_comparable_offer` after `children=0` was supplied.
  Its existing production record was already manually corrected, so this follow-up applies only to
  future or safe repeat imports; it does not alter Atlas Haven or Dar Dikrayat automatically.
- **11B / 0.5.10 — Reservation overview:** **IN PROGRESS** — month-grouped responsive cards with
  exact-match-safe comparable prices, Czech status/date presentation, and
  clear add/check-all actions.
- **11C / 0.5.11 — Property images:** validated manual uploads, optimized local
  thumbnails under `/data`, safe relative database references, and a local
  placeholder; any Booking-derived image remains a later optional step.
- **11D / 0.5.12 — Reservation detail and price history:** **IN PROGRESS** — compact facts,
  accepted comparable-price deltas, local history chart, reservation actions,
  and separately collapsed diagnostics.

The phase retains CSRF, arbitrary HA Ingress prefixes, the browser lifecycle,
scheduler, Home Assistant/Telegram notification boundary, and the rule that a
price is comparable only after an accepted exact, equivalent, or objectively better match.

## Production validation for 0.5.10

After installing 0.5.10 on the Raspberry Pi, open BookingTracker through Home Assistant Ingress
on desktop and mobile. Verify the reservation dashboard, a detail with price history, a reservation
without a comparable price, `availability_unknown`, a technical failure, Check Now, and CSRF/Ingress
navigation. No price or delta may appear for an unsafe result.

## Earlier production validation for 0.5.9

After installing 0.5.9 on the Raspberry Pi, upload a current Booking confirmation PDF and verify
that the visible hotel hyperlink provides the property name and a canonical `/hotel/...html` URL
without query or fragment. Confirmation, payment, help, homepage, and external links must not
become identity; different hotel links must remain for manual review. Only actual
arrival/departure dates may become the stay; a card-method list, payment, cancellation, issuance,
or confirmation date must not replace them. Do not save an unclear review: correct it manually.
Then close any remote browser lease and
run one manual check for each active reservation. Confirm the Czech flash result and refreshed
`Poslední kontrola` fields. The STORHAUGEN sanitized fixture must remain `success`/`exact_match`
at 1250 NOK against 1138.39 NOK without `PRICE_DROP`; Papaya Hostel, Atlas Haven, and Dar
Dikrayat must safely remain non-comparable when required evidence is absent. Papaya's
`children=0` record remains valid;
its healthy `no_match`/`no_comparable_offer` result must not show a price, delta, `PRICE_DROP`,
technical failure series, or an active stale `CHECK_FAILED` detail alert. For an empty Booking
shell, confirm the new Czech availability-unknown message, no price/delta/alert, unchanged failure
count, and a next attempt two hours later. Do not treat that result as hotel unavailability.

```bash
ha apps logs 96d726fc_bookingtracker | tail -n 200 | grep 'booking_check_completed'
```

There must be exactly one `booking_check_completed` JSON record per completed click, containing
the safe `trigger`, exact `started_at`, status, reason code, duration, failure count, next check
time, and stable `diagnostic_phase`. It must contain no Booking URL, reservation identifier,
property name, confirmation number, PIN, e-mail, cookie, token, HTML, traceback, or local path.
Reload the detail to confirm persistence. A failed or non-comparable result must not show a price
delta or create `PRICE_DROP`; login/CAPTCHA must request manual recovery through the existing
protected remote session before retrying.

### Local live parser laboratory

The ignored `scripts/debug_live_booking.local.json` contains only non-secret reservation search
facts; copy its schema from `scripts/debug_live_booking.example.json`. The headed Mac profile is
stored outside Git under `~/Library/Application Support/BookingTrackerDebug`, and sanitized
captures default to a separate directory beside it. Run from `bookingtracker/`:

```bash
../.venv/bin/python scripts/debug_live_booking.py inspect --config scripts/debug_live_booking.local.json
../.venv/bin/python scripts/debug_live_booking.py capture --config scripts/debug_live_booking.local.json
../.venv/bin/python scripts/debug_live_booking.py replay --config scripts/debug_live_booking.local.json --capture /path/to/sanitized.booking-capture.html
../.venv/bin/python scripts/debug_live_booking.py check --config scripts/debug_live_booking.local.json
```

`check` invokes the real `CheckRunner`, `PriceCheckService`, matcher, and price comparison with
volatile repositories and a no-delivery alert boundary. It never opens the production database or
changes the scheduler. A capture must pass the automatic privacy audit and manual review before it
can be considered for a committed fixture.

## Home Assistant production verification

Add-on versions `0.1.6` and `0.2.4` were verified on a Raspberry Pi 4 running
Home Assistant OS (aarch64). Version `0.1.6` verified the Ingress dashboard,
`static/app.css`, and Browser page each returned HTTP 200; Browser navigation
remained within the session-specific Ingress path. Browser Status reported
`ready`.

The protected internal browser smoke action verified the existing persistent
Playwright context with `success: true`, `architecture: aarch64`, Chromium at
`/usr/bin/chromium`, an active context, a loaded and closed temporary page, and
no error. An add-on restart shut down the application/server process cleanly
and started a healthy replacement process.

Version `0.2.4` completed Phase 9 production verification: Browser State and
remote desktop were `ready`; responsive 16:9 noVNC HTTP assets and WebSocket
worked below the dynamic Ingress prefix; and no VNC/noVNC port was public. A
user manually logged into Booking.com, then ending the remote session detected
`Authentication: authenticated`, released the manual lease, and returned the
remote runtime to `ready`. The login survived an add-on restart and Booking.com
still recognized it. A subsequent remote-session end again returned
`authenticated`; the protected browser smoke succeeded in the same persistent
context on `aarch64` using `/usr/bin/chromium`, with its temporary test page
loaded and closed and no error. CSS content-hash cache-busting also worked.

## Local web UI

The self-contained application/add-on build context is `bookingtracker/`. Run the local interface with:

```bash
cd bookingtracker
../.venv/bin/uvicorn app.web.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Add a reservation by pasting a confirmation,
review and correct known fields, and supply the Booking URL before checking.
The Browser page can start the existing headed persistent-profile workflow for
manual login; credentials are never entered into BookingTracker. The app uses
a configurable base path for direct development and derives its per-request
Home Assistant prefix from `X-Ingress-Path` for route, form, redirect, and
static-asset URLs.

The scheduler defaults to three checks per day, persists due/backoff state in
SQLite, skips inactive or checked-in reservations, and serializes the shared
browser. Login/CAPTCHA pause automated retries for seven days; transient
infrastructure failures exponentially back off to 24 hours. Alerts are
deduplicated and delivered through a local console adapter, leaving a persisted
price check intact even if delivery fails. Phase 9 remote-browser recovery has
completed its real Raspberry Pi / Home Assistant Ingress validation gate. macOS
development does not enable Xvfb, VNC, or noVNC.

## Phase 10 notification policy

Phase 10 is production-complete. Its default minimum
comparable price drop is 5%; an individual reservation may set a Decimal
override from greater than 0 through 100%. A price-drop notification requires
the existing accepted/comparable exact-match gate, same currency, and final
tax-inclusive basis—cheaper rejected rooms or rate plans never alert.

Alerts deduplicate by persisted percentage bands (5%, 10%, 15%, and so on at
the default threshold). A return to a band that was already sent stays quiet;
only a higher unseen band notifies. Changing a threshold silently establishes a
new historical high-water baseline, avoiding historical notification floods.
Home Assistant owns Telegram configuration and secrets. BookingTracker uses a
generic configured `notify.*` entity through its internal API proxy, so another
HA notifier can be used later. Delivery failures retain the price check and
internal alert, store only a sanitized error, and allow a safe retry.

The `0.3.2` Pi verification delivered BookingTracker's diagnostic through the
configured `notify.roman` entity. Home Assistant owns the Telegram bot token
and chat configuration; BookingTracker does not store either.

## Add-on releases

Normal updates are one click in Home Assistant: version `0.3.3` and later use
the public prebuilt ARM64 image `ghcr.io/krsnak/bookingtracker-addon:<version>`.
Supervisor resolves the tag from `config.yaml` and downloads it rather than
building on the Pi. Before the first release, make the linked GHCR package
public in GitHub Packages; otherwise Supervisor cannot pull it anonymously.

The first prebuilt release was production-validated on Raspberry Pi 4 / Home
Assistant OS. The `v0.3.3` workflow completed in 9m 13s and published
`ghcr.io/krsnak/bookingtracker-addon:0.3.3`; Supervisor pulled it without a
local `buildx` build. The add-on, Ingress, remote noVNC, persistent Booking
profile/session, authenticated remote-session completion, ARM64 browser smoke,
and clean restart all succeeded.

Release procedure: update both project versions, update the changelog, push the
commit, and create a matching protected tag such as `v0.3.3`. The GitHub Actions
workflow tests, lints, verifies version consistency, and then publishes only
the ARM64 image. Confirm the workflow is green before selecting **Update** in
Home Assistant. To roll back, select/reinstall the prior repository revision
whose version points to the prior known-good image. `ha store repair` and a
Supervisor restart are emergency diagnostics only—use them for a stale store or
failed pull, not normal updates. The old local-build timeout is expected only
for a repository revision without `image:`.

## Remote/manual browser recovery

In the Home Assistant add-on, version `0.2.4` starts one headful Chromium in a
private Xvfb display and retains its one persistent Playwright context/profile.
The Browser Status page can temporarily open a protected noVNC session through
Home Assistant Ingress for manual Booking login or CAPTCHA recovery. x11vnc and
websockify bind only to loopback and stop when the user ends the session; the
browser, context, Xvfb, and profile remain running for later automatic checks.
While this manual lease is active, scheduling and Check Now navigation are
safely blocked. No credential, cookie, token, or profile data is exported.

## Intended workflow

1. Open BookingTracker from Home Assistant.
2. Use its protected remote browser only to manually log in or resolve a
   session/CAPTCHA condition.
3. Paste a Booking confirmation, review/correct the extracted reservation, and
   confirm it.
4. Check now or use the conservative scheduler.
5. Review history and Home Assistant alerts for comparable price changes.

## Development browser smoke test

After manually closing any other Chrome process using the dedicated Booking
profile, run the opt-in smoke test with a second Booking hotel URL:

```bash
.venv/bin/python scripts/browser_smoke.py --second-url 'https://www.booking.com/hotel/...'
```

It launches the configured visible Chrome channel, uses the ignored persistent
profile, navigates the Grand Hotel Hønefoss URL and the supplied second URL in
one context, prints only high-level health/navigation metadata, then stops.

## Parser fixtures

Rate parsing is fixture-first. `tests/fixtures/booking_*_rates.html` are
sanitized, narrow availability subtrees; they do not contain cookies or profile
data. To capture a new candidate fixture manually, use
`scripts/capture_rate_fixture.py --url URL --output PATH`, then inspect and
sanitize the generated file before committing it. Live parser smoke checks use
`scripts/rate_parser_smoke.py` and are never part of normal pytest.

Confirmation import also accepts short and long Czech Gmail Markdown: labelled
tables, Czech dates, safe Booking property links, separated price/payment
sections, cancellation facts, and explicit room-specific breakfast evidence.
Explicit arrival/departure labels outrank cancellation dates, and repeated
equal section facts are not treated as a conflict. Only a canonical Booking
hotel link is retained; Gmail, mailto, image, confirmation, and payment links
are removed before the pasted text is persisted. Unknown meal facts remain
unknown. Ambiguous, equal, or reversed stay dates remain unknown and block
activation rather than being silently reordered.

## Security

The browser profile is session state. Do not inspect or commit it, and never
put it in an image layer. `.env`, SQLite databases, and logs are ignored by
default. The eventual remote browser is available only through authenticated
Home Assistant Ingress, never as a public VNC/noVNC port.
