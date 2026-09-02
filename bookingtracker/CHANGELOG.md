# Changelog

## 0.5.5

- Keep `exact` separate while accepting a differently named room only as explicit
  `equivalent` or objectively `better`; a lower price never relaxes a room or rate condition.
- Persist structured public room facts with offer snapshots and safe matcher evidence: private
  room/dorm bed, bathroom, balcony/terrace, area, view, air conditioning, kitchen,
  accessibility, bed type and capacity. Marketing labels are not upgrade evidence.
- Require confirmed property, requested occupancy, final tax-inclusive total/currency, meal,
  cancellation, payment and every known booked room feature. Missing or worse evidence is
  non-comparable; no benefit compensates for a downgrade.
- Select the lowest total among all same-currency, tax-inclusive, individually safe candidates;
  exact/equivalent/better only break an equal-price tie, otherwise retain an ambiguous result for
  non-orderable terms. Alerts and detail UI name the category and
  show safe objective evidence. No schema migration or historical rewrite is required.
- Papaya validation remains conservative: a dorm bed is rejected and Economy/Classic wording
  alone cannot replace the booked balcony room.
- Preserve tri-state true/false/unknown room facts: an absent DOM fact is unknown, never a
  negative fact; a known booked balcony, view, bathroom, food, cancellation, payment, occupancy,
  currency, and tax basis cannot be lost.
- Production/fixture validation: Papaya Hostel remains `no_comparable_offer` because its other
  rooms lack mandatory evidence and its dorm bed is rejected. The STORHAUGEN exact fixture remains
  exact. Neither validation creates a false price or `PRICE_DROP`.

## 0.5.4

- Treat a reliably recognized adults-only confirmation block as zero children while retaining
  unknown children for missing, conflicting, or unrecognized guest evidence in both text and PDF
  imports.
- Classify missing availability-search facts as `incomplete_reservation` in
  `reservation_validation` before browser navigation, with approved Czech edit guidance.
- Make diagnostic redaction idempotent and keep raw/internal details out of ordinary and closed
  technical UI presentation.
- Treat `no_comparable_offer` and normal no-availability as technically healthy: reset failure
  state, use the normal interval, and hide a superseded `CHECK_FAILED` only on the current detail.
  Its history, delivery status, and manual acknowledgement are unchanged. Exact-match and
  `PRICE_DROP` protections are unchanged.
- Production validation: after Papaya Hostel was set to `children=0`, navigation and offer
  collection completed and the safe result was `no_match`/`no_comparable_offer` in exact matching.
  It created neither a price nor a `PRICE_DROP`; only the former failure-series/alert
  classification required this bugfix.

## 0.5.3

- Build every manual and scheduled Booking navigation URL deterministically from the stored
  canonical hotel URL, dates, occupancy, rooms, known child ages, and optional currency without
  mutating the canonical database value.
- Wait boundedly for asynchronous availability content and retry navigation once when Booking
  temporarily returns neither an availability nor no-availability surface.
- Recognize the explicit Czech final-price evidence `Zahrnuje daně a poplatky` and add a local
  production-`CheckRunner` dry-run laboratory with sanitized capture/replay and no persistent or
  alert side effects.
- Skip scheduler checks when the shared runner is busy, then revalidate `next_check_at` after
  lock acquisition so a completed manual check cannot create a second stale history row.
- Emit privacy-safe `trigger` and exact `started_at` fields in completed-check logs without a
  property name or reservation identifier.
- Preserve exact-match requirements and the rule that an unaccepted or non-comparable offer
  cannot produce a `PRICE_DROP`.
- Live validation: STORHAUGEN completed `success`/`exact_match` at 1250 NOK against 1138.39 NOK
  without `PRICE_DROP`; Papaya Hostel, Atlas Haven, and Dar Dikrayat each safely ended as
  `no_comparable_offer`. No live test created a false comparison or alert.

## 0.5.2

- Fixed the 0.5.1 production root cause where an optional one-second Playwright
  `body.inner_text` timeout escaped page-state detection and became a false navigation timeout.
- Added one bounded optional-locator reader, partial-snapshot candidate continuation, stable safe
  diagnostic phases, and Czech-only ordinary diagnostic presentation.
- Added migration 6 and sanitized STORHAUGEN regressions for missing optional evidence, later exact
  candidates, parser/no-comparable outcomes, actual locator timeout, and bounded runtime.
- Production validation on Raspberry Pi remains pending.

## 0.5.1

- Added a prominent CSRF-protected, Ingress-aware manual reservation check through the existing
  serialized runner and persistent browser context.
- Added non-blocking busy/manual-lease outcomes, Czech result flashes, persisted diagnostics, and
  one sanitized `booking_check_completed` stdout event for every completed manual or scheduled run.
- Preserved exact-match pricing, failure/backoff state, alert thresholds and deduplication.
- Production validation with STORHAUGEN GARD identified the escaped optional
  `Locator.inner_text` timeout fixed by 0.5.2.

## 0.5.0

- Added Czech request-aware global navigation, active-page semantics, logical back links, and PRG confirmation on reservation save.
- Added explicit Czech presentation helpers for statuses, dates, times, money, and boolean values.
- Added compact, content-hashed `ui.css`; Phase 11A is complete after Raspberry Pi production validation.

## 0.4.3

- Made Booking waiting sentences an authoritative, split-line-safe property identity anchor.
- Added a compact, namespaced review card and a real in-memory PDF upload regression with Unicode and a canonical URI annotation.
- Conflicting Czech/English property anchors now require manual review instead of falling back.

## 0.4.2

- Fixed split-line Czech cancellation evidence and consolidated strong PDF property aliases into the final property.
- Widened the responsive Czech review layout with a desktop grid and mobile single-column fallback.

## 0.4.1

- Fixed PDF property evidence scoring so generic Gmail/Booking calls to action cannot become a hotel name.
- Added resilient Czech PDF cancellation/payment parsing and exact city-tax fee derivation.
- Reworked reservation review into Czech sections: recognized values are read-only by default with an explicit correction control; missing values open directly for completion.

## 0.4.0

- Added a shared, deterministic text/PDF reservation-import pipeline with a Czech/English Booking lexicon.
- Added in-memory PDF confirmation upload with signature, 10 MB and 20-page limits, active-content rejection, and canonical hotel URI extraction.
- Made the review form editable for all critical reservation and price facts.

## 0.3.6

Reject ambiguous, reversed, or equal imported stay dates before Pydantic model
construction. The import UI now presents a safe validation message instead of
an internal error, while preserving labelled arrival/departure priority and
never treating cancellation dates as stay dates.

## 0.3.5

Fix deterministic import of long Czech Gmail Markdown confirmations: canonical
hotel links and property evidence now outrank mail chrome, explicitly labelled
arrival/departure dates outrank cancellation dates, and repeated section facts
retain their price/payment meanings. Pasted mail metadata and non-hotel links
are sanitized before a confirmation is retained.

## 0.3.4

Parse sanitized Czech Markdown Booking confirmations deterministically,
including safe Booking property links, labelled tables, Czech dates, sectioned
price/payment totals, cancellation facts, and room-specific breakfast evidence.

## 0.3.3

Complete Phase 10 production verification and add tag-gated GitHub Actions
publication of a prebuilt public ARM64 GHCR add-on image.

Production validation completed: the `v0.3.3` workflow published the public
GHCR image, Home Assistant pulled it without local build, and Pi runtime,
Ingress, noVNC, persistent authenticated profile, ARM64 smoke, and restart
succeeded.

## 0.3.2

Fix Home Assistant Core REST `notify.send_message` payloads to use top-level
`entity_id`, `title`, and `message`, rather than action/YAML-style nesting.

## 0.3.1

Add an Ingress-safe, CSRF-protected Settings diagnostic that sends a clearly
labelled test message through the same configured Home Assistant notification
adapter used for real alerts. It creates no reservation, check, alert, or
deduplication state.

## 0.3.0

Begin Phase 10 with a 5% Decimal price-drop threshold, per-reservation
override, persistent percentage-band deduplication, generic Home Assistant
`notify.*` delivery through the Core API proxy, and safe delivery retry.

## 0.2.4

Cache-bust BookingTracker CSS with a deterministic content revision.

Production verification completed on Raspberry Pi 4 / Home Assistant OS:
Phase 9 remote browser recovery worked through authenticated dynamic Ingress,
with no public VNC/noVNC port. Manual Booking.com login persisted across an
add-on restart; remote-session completion restored `authenticated`, released
the manual lease, and returned the runtime to `ready`. The aarch64 persistent
Chromium smoke, responsive 16:9 noVNC iframe, and CSS cache-busting succeeded.

## 0.2.3

Make the Ingress noVNC frame responsive at the Xvfb 16:9 aspect ratio.

## 0.2.2

Pass the full dynamic Ingress WebSocket path in noVNC 1.3's client-side URL
fragment so Bookworm noVNC does not escape the Ingress prefix.

## 0.2.1

Keep noVNC's WebSocket endpoint below its asset path so Home Assistant Ingress
never receives a parent-path traversal segment.

## 0.2.0

Implement Phase 9 remote/manual Booking session recovery through Home Assistant
Ingress. Production validation on Raspberry Pi remains pending.

## 0.1.6

Generate URLs from Home Assistant's request-specific Ingress prefix.

## 0.1.5

Add an Ingress-safe internal Chromium smoke action.

## 0.1.4

Include the browser session detection module in the self-contained add-on source tree.

## 0.1.3

Use POSIX `sh` startup options on Debian slim.

## 0.1.2

Use Debian 12 glibc ARM64 runtime so Playwright's manylinux ARM64 wheel can install.

## 0.1.1

Fix Home Assistant Supervisor build context by making this add-on directory self-contained.

## 0.1.0

Initial aarch64 Home Assistant add-on packaging.
