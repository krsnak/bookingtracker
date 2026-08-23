# Implementation plan

## Corrective import release 0.4.1

Added CTA-resistant property evidence scoring, Czech split-line cancellation/payment parsing,
and a Czech progressive review UI. Real Raspberry Pi validation of an actual Gmail PDF remains
required before declaring this correction production-complete.

## Import phase 0.4.0

Implemented the shared PDF/text reservation import boundary, Czech/English deterministic
extraction, canonical hotel annotation selection, and complete review editing. This phase still
requires one real Gmail-exported Booking PDF test on the Raspberry Pi before it can be called
production-complete.

## Status

Phases 0–10 are complete. The architecture supports macOS development and
Raspberry Pi 4 aarch64 Home Assistant production. Prebuilt add-on distribution
is the next deployment milestone.

## Phase 0 — foundation audit (complete)

- [x] Inspect all project source files.
- [x] Identify the persistent Chrome-profile POC as reusable knowledge.
- [x] Review applicable public TripWatch patterns.
- [x] Document target architecture, data flow, POC disposition, project tree,
  and technical risks.
- [x] Add repository security ignores and future-agent invariants.
- [x] Define the Home Assistant add-on boundary, Ingress-safe UI rule,
  `/data` persistence model, and remote-login architecture.

## Phase 1 — domain and text import (complete)

1. [x] Add Python packaging, Ruff, pytest, and Pydantic.
2. [x] Define reservation, candidate, money, cancellation, and validation models.
3. [x] Build deterministic pasted-confirmation extraction and optional LLM provider
   interface (no provider implementation or secrets required).
4. [x] Add review-safe validation and fixture-based unit tests.

Acceptance: representative confirmation text returns a reviewable candidate
with correct property, dates, guests, room, cancellation facts, separate price
amounts, and currency.

Completed with sanitized Papaya and Grand Hotel Hønefoss fixtures. The parser
also flags conflicting totals in one semantic section and blocks activation
when any critical field is absent. Browser, rate-parser, matcher, SQLite, UI,
and Home Assistant phases have not been started.

## Phase 2 — persistent browser service (implementation complete; live smoke pending)

1. [x] Refactor the POC into a configured service with explicit lifecycle/status.
2. [x] Add direct URL navigation, page recovery, and serialized navigation.
3. [x] Detect logged-out, CAPTCHA/challenge, timeouts, and navigation failures.
4. [x] Retain an opt-in manual login/smoke command; never automate credentials.

Acceptance: one manually authenticated Chrome context handles multiple direct
Booking navigations without relaunching for every check.

Unit coverage is complete. Live smoke validation remains pending because the
existing persistent Chrome profile was locked by an existing Chrome session;
the service correctly returned a sanitized `ERROR` health result without
touching the profile. Do not close a user's active browser session
automatically; rerun the manual smoke command after the profile is free.

## Phase 3 — rate parser (complete)

1. [x] Define `RateOffer`, parser results, and centralized selectors.
2. [x] Parse separate offers within a room and preserve evidence/warnings.
3. [x] Extract current/original prices, Genius and Genius breakfast, meal,
   cancellation, payment, and tax facts.
4. [x] Add sanitized DOM fixtures and integration tests.

Acceptance: stored Papaya/Morocco fixture yields distinct offers including
Genius breakfast and cancellation facts.

Authenticated live smoke completed through the Phase 2 browser service. Grand
Hotel Hønefoss returned three legacy-table rate offers; Papaya Hostel returned
one offer for the tested dates. The current Papaya page did not display the
historic multiple-rate Genius-breakfast variant, so that regression behavior is
covered by the sanitized fixture and must be revalidated when live availability
changes.

## Phase 4 — exact matcher (complete)

1. [x] Implement hard rejection rules and conservative normalization.
2. [x] Implement scoring, candidate explanations, and upgrade classification.
3. [x] Add matcher tests for exact, wording, meal, cancellation, Genius, and
   upgrade cases.

Acceptance: cheaper wrong-room, no-breakfast, and non-refundable offers are
rejected; an equivalent rate is explainably selected.

Phase 4 has no price-selection logic. The Papaya fixture regression confirms
the refundable Genius-breakfast offer is accepted while the non-refundable
alternative is rejected; all candidates are evaluated before a result is
selected.

## Phase 5 — SQLite and price history (complete)

1. [x] Add ordered SQLite migrations, foreign keys, repositories, and
   append-only check/offer-snapshot storage.
2. [x] Implement final-total comparison with currency and tax-basis guardrails.
3. [x] Join one explicit browser/parser/matcher/pricing/persistence operation.
4. [x] Add isolated persistence, comparison, and orchestration outcome tests.

Acceptance: decimal money round-trips exactly; every success and failure is
historical; `delta = current - booked`; and the Papaya fixture yields
EUR 18.88 -> EUR 16.88 = EUR -2.00 only for the accepted refundable offer.

## Phase 6 — scheduler and alerts (complete)

1. [x] Add persistent due-state scheduling, shared manual/scheduled runner,
   serialized browser access, and conservative backoff.
2. [x] Add persisted typed alerts with price, historical-low, session/CAPTCHA,
   and repeated-failure policies.
3. [x] Add deduplication and console notification delivery boundary.
4. [x] Add deterministic scheduler/alert tests with injected clock and jitter.

Acceptance: the Papaya comparable EUR 16.88 rate creates one `PRICE_DROP`; a
repeat is suppressed, a lower rate creates a new alert, and non-comparable or
rejected alternatives create none.

## Phase 7 — Ingress-safe local UI (complete)

1. [x] Add FastAPI/Jinja dashboard, import/review, detail/history, alerts, and
   browser-status screens with mobile-first local CSS.
2. [x] Wire manual check and enable/disable actions to the existing runner and
   repositories.
3. [x] Add lifecycle-owned scheduler polling and test non-root generated URLs,
   forms, redirects, and static assets.

## Phase 8 — Home Assistant add-on packaging (complete)

1. [x] Add `repository.yaml`, self-contained `bookingtracker/` add-on manifest, Dockerfile, run script,
   documentation, aarch64 support, `/data` configuration, and health endpoint.
2. [x] Validate startup, migrations, persistence, clean shutdown, and no runtime
   profile/state in image layers using an arm64 image build.

Production validation completed on Raspberry Pi 4 / Home Assistant OS with
add-on version `0.1.6`: the Ingress dashboard, `/static/app.css`, and Browser
page returned HTTP 200; Browser navigation stayed under the dynamic Ingress
prefix; and Browser Status was `ready`. The protected internal ARM64 smoke
action returned success with `aarch64`, `/usr/bin/chromium`, an active persistent
context, a loaded temporary test page, a closed temporary test page, and no
error. Restarting the add-on logged clean application/server shutdown followed
by a healthy new process.

## Phase 9 — remote Chromium login (complete)

1. [x] Add Xvfb, minimal window manager, Chromium, VNC/noVNC/websockify process
   supervision in the add-on layer only.
2. [x] Provide authenticated, Ingress-relative browser access with no public VNC
   port and a manual lease that blocks automatic navigation.
3. [x] Validate session persistence, WebSocket forwarding, manual recovery,
   and clean shutdown on a real Pi.

Production validation completed on Raspberry Pi 4 / Home Assistant OS with
add-on version `0.2.4`. Browser State and remote desktop were `ready`; noVNC
HTTP assets and its WebSocket both worked through the dynamic Home Assistant
Ingress prefix, with no public VNC/noVNC port. The user manually opened
Booking.com and logged in, and ending the remote session detected
`Authentication: authenticated`, released the manual lease (`False`), and
returned the remote runtime to `ready`.

The authenticated Booking session persisted through an add-on restart and was
recognized again by Booking.com. After ending a subsequent remote session, the
state returned to `authenticated`. The protected browser smoke then succeeded
in the same persistent context: `architecture: aarch64`,
`chromium_executable: /usr/bin/chromium`,
`persistent_context_active: True`, `test_page_loaded: True`,
`test_page_closed: True`, and `error: None`. The responsive 16:9 noVNC iframe
and CSS content-hash cache-busting were also verified.

## Phase 10 — Home Assistant notifications and state (complete)

1. [x] Add a platform-independent notification boundary and Home Assistant
   adapter without coupling core logic to HA APIs. The adapter calls the Core
   API proxy with `SUPERVISOR_TOKEN`; it does not access Telegram directly.
2. [x] Add a global 5% minimum price-drop threshold with an optional per-
   reservation Decimal override (valid range >0 through 100).
3. [x] Create PRICE_DROP alerts only from accepted, comparable exact or
   explicitly-better matches with matching currency and final-tax basis.
4. [x] Persist percentage-band deduplication (5%, 10%, 15%, ... by default).
   A threshold change establishes a silent historical high-water baseline, so
   changing configuration cannot immediately replay historical notifications.
5. [x] Add Ingress-safe web controls for the global threshold, HA notify entity,
   per-reservation override, delivery status, and safe retry.
6. [x] Verify real delivery through a Home Assistant notify entity on the Pi.

Production verification completed on Raspberry Pi 4 / Home Assistant OS with
version `0.3.2`: the configured `notify.roman` entity delivered the
BookingTracker diagnostic through Home Assistant's Telegram Broadcast
integration. Home Assistant owns the bot token and chat configuration;
BookingTracker stores neither. The existing exact-match/comparable gate, band
deduplication, CSRF, and persistent browser lifecycle remained unchanged.

## Deployment backlog

- [x] Build and publish a prebuilt ARM64 add-on image through GitHub Actions
  and GHCR so Home Assistant downloads the release image instead of performing
  a long local build for every update.

Production validation completed on Raspberry Pi 4 / Home Assistant OS with
release `0.3.3`. The `v0.3.3` GitHub Actions workflow finished successfully in
9m 13s and published public `ghcr.io/krsnak/bookingtracker-addon:0.3.3`.
Supervisor pulled it without a local `buildx` build; the add-on, Ingress, and
remote noVNC started successfully. The persistent `/data/booking_profile`
retained the authenticated Booking session; ending remote control returned
`Authentication: authenticated`. The ARM64 persistent-context smoke succeeded
using `/usr/bin/chromium`, with its temporary page loaded and closed and no
error. Add-on restart was clean and reinitialized browser and remote runtime.

## Risks and mitigations

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Booking DOM changes | Parser failure or incomplete offers | Centralized selectors, narrow parser adapters, sanitized fixtures, explicit `PARSER_ERROR`. |
| Login expiry or challenge | No trustworthy availability result | Explicit `LOGGED_OUT`/`CAPTCHA`, manual user action, no bypass logic. |
| Ambiguous confirmation totals | False savings | Preserve labelled amounts, require a chosen comparable total during review, warn instead of guessing. |
| Room wording varies by locale | False mismatch or false match | Conservative normalization plus hard constraints and explainable candidates. |
| Rate terms are incomplete in DOM | False equivalence | Reject when known reservation protections are missing; retain evidence and warnings. |
| Browser profile exposure | Account/session compromise | Ignore profile/DB/logs, redact structured logs, never inspect or print cookies. |
| Live-site instability | Fragile automated testing | Fixture-first unit/integration tests; manual opt-in smoke tests only. |
| aarch64 Chromium dependencies | Add-on cannot start or browser crashes | Gate Phase 8 on native arm64 build and real-Pi browser startup. |
| Pi memory pressure | Browser/noVNC instability | One browser/context, serialized checks, bounded logs, Pi 4 validation. |
| Ingress base path/WebSockets | Broken links, assets, redirects, or remote login | Relative URL generation and non-root Ingress integration test. |
| Remote-browser exposure | Session compromise | Authenticated HA Ingress only; no published VNC/noVNC port or profile export. |

## Verification for every implementation phase

Run formatting/lint and tests, add regression fixtures for discovered parser
changes, and record any unverified live Booking behavior as a remaining risk.
