# BookingTracker agent instructions

## Non-negotiable invariant

Track the exact existing reservation. Never optimize for the cheapest hotel
room and never compare a price until the matcher has accepted an equivalent or
explicitly-labelled better offer.

Do not silently substitute room type, room count, dates, occupancy, meal plan,
breakfast, cancellation protection, or relevant payment conditions. A lower
price for a materially worse rate is not an alert.

## Safety and privacy

- This is a local, single-user application. Do not add Supabase, cloud hosting,
  multi-tenancy, Gmail/IMAP, inbound email, or automated booking/cancellation.
- Production is a Raspberry Pi 4 aarch64 Home Assistant add-on. Keep core
  business logic platform-independent; Docker, Ingress, `/data`, and remote
  browser components must stay in adapters/deployment layers.
- Never automate Booking credentials, store credentials, bypass CAPTCHA, or
  add anti-bot evasion. A challenge means stop and request manual action.
- `data/booking_profile` contains session state: do not inspect, print, log,
  commit, copy, or expose cookies/tokens/session storage.
- Do not commit `.env`, SQLite data, logs, sanitized fixtures containing PII,
  or real Booking confirmation data.
- noVNC/VNC is manual recovery only and must be protected by Home Assistant
  Ingress. Never expose a public remote-browser port or profile export.

## Engineering workflow

1. Inspect the relevant module and tests before editing.
2. Keep Booking DOM selectors in the adapter/selectors layer.
3. Use typed Pydantic models, explicit enums/statuses, `Decimal` for money,
   foreign keys, and append-only price history.
4. Prefer deterministic parsing/matching. LLM output must be typed, validated,
   reviewable, and never saved directly.
5. Add or update fixture-based tests for behavior changes. Do not run live
   Booking tests in normal CI.
6. Run lint and tests before handoff; explicitly report unverified live-site
   risks.
7. Assume Home Assistant mounts the app below an arbitrary base path: do not
   hard-code root-relative links, redirects, static assets, or API URLs.
