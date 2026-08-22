# Implementation plan

## Status

Phase 0 is complete: the repository, public TripWatch reference, public EVCC
Home Assistant add-on conventions, and public Xvfb/VNC/noVNC patterns were
audited. The architecture now supports macOS development and Raspberry Pi 4
aarch64 Home Assistant production. No product implementation has started; the
existing POC is unchanged.

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

## Phase 8 — Home Assistant add-on packaging

1. Add `repository.yaml`, add-on manifest, Dockerfile, run script,
   documentation, aarch64 support, `/data` configuration, and health endpoint.
2. Validate startup, migrations, persistence, clean shutdown, and no runtime
   profile/state in image layers using an arm64 image build.

## Phase 9 — remote Chromium login

1. Add Xvfb, minimal window manager, Chromium, VNC/noVNC/websockify process
   supervision in the add-on layer only.
2. Provide authenticated, Ingress-relative browser access with no public VNC
   port; validate session persistence and manual recovery on a real Pi.

## Phase 10 — Home Assistant notifications and state

1. Add Home Assistant notification/state adapters without coupling core logic
   to HA APIs.
2. Verify deduplicated alerts and safe browser/manual-action status exposure.

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
