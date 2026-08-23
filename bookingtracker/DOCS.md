# BookingTracker

State is persisted only in `/data`: SQLite, logs, and the Booking browser profile.
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

Version `0.3.5` accepts both short and long Czech Gmail Markdown confirmations
without retaining Gmail chrome or non-hotel links. A canonical
`https://www.booking.com/hotel/...` link is preferred even when it appears
after generic Booking, confirmation, payment, image, or mail links. Explicit
`Příjezd`/`Odjezd` facts outrank cancellation dates. Repeated equal price facts
are not treated as a conflict; section labels keep stay total, planned Booking
payment, and city tax distinct. Explicit room-specific breakfast evidence is
retained, while an absent meal statement remains unknown. The model has no
persisted `paid` field, so a zero-paid display is not substituted for the
booked total.

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
