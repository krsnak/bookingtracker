# BookingTracker

## Reservation card presentation

The Reservation overview uses a presentation-only card view model. It groups active
reservations by arrival month, formats Czech stay/cancellation/check labels, and exposes
only an accepted exact/equivalent/better price as current. A newer price-less check can
show a dated “Poslední známá cena”, never as a current result. The templates contain no
price arithmetic or check-status interpretation.

Property image storage is not implemented in this phase. The view model deliberately
exposes `image_url`, `image_alt`, and `has_image`, but currently supplies a deterministic
local fallback initial only. A future upload design must validate actual MIME content,
size and dimensions; strip metadata; transcode to JPEG/WebP under a random local name;
and remove the local file when its reservation is deleted under an explicit retention
policy. Booking CDN hotlinks and automatic image downloading remain out of scope.

State is persisted only in `/data`: SQLite, logs, and the Booking browser profile.

Phases 0–10 are **COMPLETE**. Phase 11 — Czech frontend and reservation dashboard
— is **IN PROGRESS**. Phase 11A / 0.5.0 is **COMPLETE** after production validation.
The diagnostic 0.5.1 intermediate release is **COMPLETE AND PRODUCTION-VALIDATED**.
Parser/navigation reliability 0.5.3 is **PRODUCTION-VALIDATED**. The 0.5.8 PDF-import
reliability release is **IMPLEMENTATION COMPLETE, RELEASE VALIDATION PENDING**. Version 0.5.9
adds conservative availability-detection reliability.
Phase 11B and 11D are **IN PROGRESS**; Phase 11C remains planned for a later local-image upload
step. Version 0.5.10 implements overview cards, the price presentation gate, and a local SVG
history chart. Its Home Assistant Ingress verification remains pending after release.

The planned UI remains server-rendered, local, single-user, and fully
Ingress-aware. It will translate internal states for normal Czech presentation,
prepare price/comparability decisions in view-models rather than templates,
and retain the accepted exact-match gate before showing any price direction.
Images will be validated, optimized, and stored only below `/data`; missing
images use a local placeholder. CSRF, the persistent browser lifecycle,
scheduler, and Home Assistant/Telegram notifications remain unchanged.

Version `0.5.2` fixes the production root cause observed through the 0.5.1
`Zkontrolovat nyní` action on STORHAUGEN GARD. The call path reached
`detect_page_state`, where optional `body.inner_text(timeout=1000)` raised a Playwright timeout;
the surrounding browser navigation handler incorrectly classified it as a global timeout before
the snapshot parser ran. Optional locator reads now use a shared 250 ms budget and at most 100 ms
per text read, return no evidence when absent, and catch only expected Playwright locator
timeouts. Real navigation timeouts remain distinct. Partial snapshots continue through later
candidates; mandatory missing offer structure is `parser_error`, and matcher rejection is
`no_comparable_offer`. Migration 6 persists only a stable safe diagnostic phase. Raw English
library detail is hidden outside closed technical diagnostics. The subsequent 0.5.3 release
validation procedure is documented in the root README.

Local live validation later confirmed that supported legacy offers and an exact match continue
through the production pipeline. The remaining failure was navigation nondeterminism: production
passed the stored bare canonical URL directly, so dates and occupancy depended on browser cookies
and previous-search state, and Booking could also temporarily return no availability surface after
`domcontentloaded`. Version `0.5.3` builds one search URL from stored reservation facts for every
manual or scheduled check, keeps the canonical database value unchanged, waits boundedly for
availability, and permits one bounded repeat navigation. It also skips a busy scheduler run and
revalidates its due state after lock acquisition, so a manual check cannot create a stale second
history row. Completed logs contain only safe `trigger` and `started_at` source evidence. The safe
local `inspect → capture → replay → check` workflow is documented in the root README; its profile,
real config, and captures never enter the add-on or Git. Live validation yielded STORHAUGEN
`success`/`exact_match` at 1250 NOK against 1138.39 NOK with no `PRICE_DROP`; Papaya Hostel,
Atlas Haven, and Dar Dikrayat safely returned `no_comparable_offer` without a false comparison or
alert.

Version 0.5.4 records `children=0` only from a reliable adults-only
occupancy block, never merely because a document does not mention children. Missing required
search facts are classified as `incomplete_reservation`/`reservation_validation` before browser
navigation and render a Czech edit instruction. A valid `no_comparable_offer` is not a technical
failure: it resets the failure count, keeps the normal interval, creates no failure alert, and
hides an older superseded technical-failure alert only on the reservation detail. Exact matching and
comparable-price requirements remain unchanged.
Papaya's existing production record already has `children=0`; no re-import is needed after this
change. Atlas Haven and Dar Dikrayat remain untouched without their original import evidence.

Version 0.5.5 keeps the exact-reservation guarantee while preserving the
existing `exact` category separately from `equivalent` and objectively `better` rooms. Different
room wording is not enough: public scoped evidence must prove property, requested occupancy,
currency, final tax-inclusive total, food, cancellation, payment and every known booked room fact.
Private room/dorm bed, bathroom, balcony/terrace, area, view, air conditioning, kitchen,
accessibility and bed type are structured facts; Economy/Classic/Deluxe-style labels prove nothing.
The lowest total among all individually safe exact/equivalent/better offers wins; exact,
equivalent, better only break an equal-price tie, followed by stable diagnostic order.
Non-orderable terms are ambiguous. Price-drop alerts name the category and a
safe terse objective improvement. No raw DOM is stored and existing immutable snapshot JSON needs
no migration. Papaya's dorm offer and unproven Economy/Classic alternatives remain non-comparable.

Version 0.5.8 derives PDF property identity from a strict safe Booking hotel hyperlink. Visible
property text must geometrically overlap the annotation after composition of PDF text and graphics
transforms, including Gmail layouts. Query and fragments are removed from canonical URLs;
confirmation, help, payment, homepage, and external links are rejected. Duplicate annotations for
the same hotel are deduplicated, different hotel URLs require manual review, and the generic
arbitrary-line fallback is removed. The safe confirmation anchor remains a fallback. Guest House
names, stay dates, cancellation, adults-only `children=0`, the conservative price matcher, and
stored reservations remain unchanged; no migration is needed. A real Gmail PDF passed the public
local upload E2E path.

Version 0.5.9 distinguishes an inconclusive empty Booking shell from explicit unavailability,
non-comparable offers, and an unsupported offer structure. After `goto`, the browser waits only a
bounded time, scrolls, clicks the single allowlisted availability CTA at most once, and waits
again. It never activates a booking, payment, or confirmation control and no longer repeats the
whole navigation. CAPTCHA/login and navigation failures win over lower-priority states; recognized
offer or explicit no-availability evidence wins too. Concrete room/rate/current-price hints remain
parser input, so an unrecognized mandatory offer structure is still `parser_error`. The empty
shell message is “Dostupnost se nepodařilo ověřit. Booking.com pro zadaný termín nezobrazil
nabídky ani potvrzení, že je ubytování vyprodané.” This status records no price, delta,
`PRICE_DROP`, or `CHECK_FAILED`, retains the existing technical failure count, and retries in two
hours. A live CTA validation covered Comfortable and Downtown; PDF import, matcher, scheduler
lock, and price logic are unchanged.

Version 0.5.7 fixes the second English PDF layout without weakening conservative import. `Guest
House` and other ordinary accommodation words remain valid property identity; only complete,
normalized section headings are excluded. The confirmation anchor supports one safe wrapped line
and cannot absorb a following section, date, address, reservation number, or payment text.
`Cancellation policy` distinguishes free cancellation with or without a safe deadline,
non-refundable, and unknown conditions; the Czech review shows known free cancellation without
inventing a deadline. Cancellation dates remain non-stay evidence. The exact/equivalent/better
matcher remains conservative, a real local public PDF-upload E2E passed, and no DB migration or
stored-reservation rewrite is needed.

Version 0.5.6 makes PDF confirmation import conservative again. It removes the unanchored generic
property fallback, recognizes the authoritative English `Your booking is confirmed at …` property
anchor even with a harmless layout column, and excludes payment-card lists and generic payment,
cancellation, amenity, tax, guest, and contact sections from property identity. English
`DD Month YYYY` is supported for explicitly labelled stay dates only; payment, cancellation,
issuance, and confirmation dates cannot become arrival or departure. Conflicting or unproven facts
remain for manual review. Adults-only `children=0` stays intact. The real local public PDF upload
route was validated; no migration or change to already stored reservations was needed, and the
0.5.5 exact/equivalent/better matcher remains unchanged.

Version `0.5.1` adds the CSRF-protected, Ingress-aware `Zkontrolovat nyní` action. It uses the
same non-overlapping shared runner, persistent browser context, exact matcher, schedule state,
history, alert rules, and deduplication as the scheduler. Completed manual and scheduled checks
emit one sanitized `booking_check_completed` JSON event to stdout. Busy and active remote-lease
states return safe Czech messages without creating a check. Production validation is complete:
the STORHAUGEN GARD attempt persisted the locator-timeout evidence that motivated 0.5.2.
The diagnostic command remains:

```bash
ha apps logs 96d726fc_bookingtracker | tail -n 200 | grep -Ei 'STORHAUGEN|check_result|reason_code'
```

Expect exactly one safe event and no false price delta or PRICE_DROP on failure.

Version `0.5.0` adds request-aware Czech navigation, logical back links, an explicit internal-status
presentation mapping, Czech date/money/boolean formatting, and content-hashed `ui.css` compact
typography. The legacy `app.css` and page-specific `review.css` remain separate. Version `0.4.3`
gives an anchored Booking waiting sentence absolute priority through the public
PDF upload path. Conflicting language anchors require review. The compact review card loads its
own content-hashed, Ingress-safe stylesheet. General multilingual block separation and a
structured Booking price-adjustment field remain backlog items.

Version `0.4.1` ranks property evidence rather than accepting generic CTA text. The review UI
shows recognized facts as read-only Czech summaries with an explicit **Upravit** control; missing
or ambiguous facts are opened for completion. Server-side typed validation remains mandatory.

Version `0.4.2` consolidates the strongest named property evidence and accepts PDF line breaks in
Czech cancellation clauses. The wider two-column review collapses to one column on mobile; real
PDF files and personal data remain outside the repository.

Version `0.4.0` introduces the preferred in-memory PDF confirmation import. PDF and text
use one deterministic Czech/English pipeline; PDFs are limited to 10 MB and 20 pages and
are never persisted. Text paste remains the fallback. The review screen now permits
explicit correction of every critical fact before activation.
Ingress serves the UI; no ports are published. Version `0.2.4` completed Phase
9 real-Pi production validation. On Home Assistant, Chromium starts headful in
private Xvfb; noVNC is available only through authenticated Ingress and only
while a manual session is active.

Phase 8 is complete. Version `0.1.6` was verified on Raspberry Pi 4 / Home
Assistant OS (aarch64): Ingress dashboard, CSS, and Browser routes returned
HTTP 200; Browser Status was `ready`; and the protected internal smoke action
successfully exercised `/usr/bin/chromium` and the existing persistent context.
Phase 9 production verification on Raspberry Pi 4 / Home Assistant OS (aarch64)
confirmed Browser State and remote desktop `ready`, functional noVNC HTTP
assets and WebSocket forwarding through the dynamic Ingress prefix, no public
VNC/noVNC port, manual Booking.com login, and an `authenticated` state after
ending the remote session. The manual lease released, the remote runtime
returned to `ready`, and the login persisted across an add-on restart. The
subsequent protected smoke succeeded in the same persistent context using
`/usr/bin/chromium`; the 16:9 noVNC iframe and CSS content-hash cache-busting
also worked.

Phase 10 is complete following real-Pi HA notify verification. It adds a default 5%
Decimal price-drop threshold, optional per-reservation override, and persisted
percentage-band deduplication. Only accepted/comparable exact or explicitly
better matches can notify. Home Assistant owns Telegram secrets; BookingTracker
uses a general configured `notify.*` entity through the supported internal Core
API proxy. Delivery failures preserve alerts and checks with sanitized errors
for retry.

On a Home Assistant deployment, save the desired `notify.*` entity in
Notification settings, then use **Send test notification**. The diagnostic uses
the configured production HA adapter but creates no price check, alert, or
deduplication state. Its one-time result is shown after redirecting back to
Settings.

That diagnostic was verified on Raspberry Pi 4 / Home Assistant OS with
`notify.roman` and Telegram Broadcast. Home Assistant owns the Telegram bot
token and chat configuration; BookingTracker does not retain them.

The Home Assistant REST service call uses top-level `entity_id`, `title`, and
`message` fields for `notify.send_message`.

Version `0.3.6` accepts both short and long Czech Gmail Markdown confirmations
without retaining Gmail chrome or non-hotel links. A canonical
`https://www.booking.com/hotel/...` link is preferred even when it appears
after generic Booking, confirmation, payment, image, or mail links. Explicit
`Příjezd`/`Odjezd` facts outrank cancellation dates. Repeated equal price facts
are not treated as a conflict; section labels keep stay total, planned Booking
payment, and city tax distinct. Explicit room-specific breakfast evidence is
retained, while an absent meal statement remains unknown. The model has no
persisted `paid` field, so a zero-paid display is not substituted for the
booked total. Ambiguous, equal, or reversed stay dates are shown as unknown
with a safe validation message and cannot be saved or activated; they are never
silently reordered.

## Prebuilt ARM64 updates

From version `0.3.3`, Home Assistant pulls the public release image
`ghcr.io/krsnak/bookingtracker-addon:<version>` for normal one-click updates;
it does not build the add-on on the Pi. Releases are created only by a matching
`v<version>` Git tag after GitHub Actions verifies tests, lint, imports,
packaging, and version consistency. Before the first release, make the GHCR
package public in GitHub Packages. If an update fails to resolve, `ha store
repair` and a Supervisor restart are emergency diagnostics; roll back by using
the repository revision pointing to the previous known-good image.

Release `0.3.3` was validated end to end on Raspberry Pi 4 / Home Assistant
OS. The `v0.3.3` Actions workflow succeeded in 9m 13s and published public
`ghcr.io/krsnak/bookingtracker-addon:0.3.3`; Supervisor pulled it rather than
locally building with `buildx`. Ingress and remote noVNC worked, the persistent
`/data/booking_profile` preserved the logged-in Booking session, remote-session
completion returned `authenticated`, and the ARM64 `/usr/bin/chromium` smoke
succeeded with the same persistent context. Restart cleanly reinitialized the
browser and remote runtime.
