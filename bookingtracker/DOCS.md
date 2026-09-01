# BookingTracker

State is persisted only in `/data`: SQLite, logs, and the Booking browser profile.

Phases 0–10 are **COMPLETE**. Phase 11 — Czech frontend and reservation dashboard
— is **IN PROGRESS**. Phase 11A / 0.5.0 is **COMPLETE** after production validation.
The diagnostic 0.5.1 intermediate release is **COMPLETE AND PRODUCTION-VALIDATED**.
Parser reliability 0.5.2 is **IMPLEMENTATION COMPLETE, PRODUCTION VALIDATION PENDING**.
Phase 11B is planned for 0.5.3, Phase 11C for 0.5.4, and Phase 11D for 0.5.5.

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
library detail is hidden outside closed technical diagnostics. Production validation of 0.5.2 is
pending with the STORHAUGEN procedure documented in the root README.

Local live validation later confirmed that supported legacy offers and an exact match continue
through the production pipeline. The remaining failure was navigation nondeterminism: production
passed the stored bare canonical URL directly, so dates and occupancy depended on browser cookies
and previous-search state, and Booking could also temporarily return no availability surface after
`domcontentloaded`. The unreleased fix builds one search URL from stored reservation facts for every
manual or scheduled check, keeps the canonical database value unchanged, waits boundedly for
availability, and permits one bounded repeat navigation. The safe local
`inspect → capture → replay → check` workflow is documented in the root README; its profile, real
config, and captures never enter the add-on or Git.

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
