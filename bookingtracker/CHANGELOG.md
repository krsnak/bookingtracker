# Changelog

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
