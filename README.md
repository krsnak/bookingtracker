# BookingTracker

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

Phases 1–8 are complete: reservation import, persistent browser service,
fixture-backed rate parsing, exact reservation matching, SQLite history,
local scheduling with alerts, the local web UI, and Home Assistant add-on
packaging.
The local SQLite layer uses migrations, foreign keys, decimal-text money, and
immutable price-check/offer snapshots. A comparable price requires an accepted
match, identical currency, and explicit current taxes/fees inclusion;
`delta = current - booked`, so negative is cheaper. The complete
implementation sequence is in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
and the deployment/design details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Home Assistant production verification

Add-on version `0.1.6` was verified on a Raspberry Pi 4 running Home Assistant
OS (aarch64). The Ingress dashboard, `static/app.css`, and Browser page each
returned HTTP 200; Browser navigation remained within the session-specific
Ingress path. Browser Status reported `ready`.

The protected internal browser smoke action verified the existing persistent
Playwright context with `success: true`, `architecture: aarch64`, Chromium at
`/usr/bin/chromium`, an active context, a loaded and closed temporary page, and
no error. An add-on restart shut down the application/server process cleanly
and started a healthy replacement process. Phase 9 is the next planned phase;
it has not started.

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
price check intact even if delivery fails. Phase 9 remote-browser recovery is
implemented but has not completed its real Raspberry Pi / Home Assistant
Ingress validation gate. macOS development does not enable Xvfb, VNC, or noVNC.

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

## Security

The browser profile is session state. Do not inspect or commit it, and never
put it in an image layer. `.env`, SQLite databases, and logs are ignored by
default. The eventual remote browser is available only through authenticated
Home Assistant Ingress, never as a public VNC/noVNC port.
