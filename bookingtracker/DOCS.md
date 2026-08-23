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

Phase 10 remains pending real-Pi HA notify verification. It adds a default 5%
Decimal price-drop threshold, optional per-reservation override, and persisted
percentage-band deduplication. Only accepted/comparable exact or explicitly
better matches can notify. Home Assistant owns Telegram secrets; BookingTracker
uses a general configured `notify.*` entity through the supported internal Core
API proxy. Delivery failures preserve alerts and checks with sanitized errors
for retry.
