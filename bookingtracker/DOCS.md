# BookingTracker

State is persisted only in `/data`: SQLite, logs, and the Booking browser profile.
Ingress serves the UI; no ports are published and Phase 8 provides no remote browser.
The browser starts headlessly in this phase. Manual remote login is Phase 9.

Phase 8 is complete. Version `0.1.6` was verified on Raspberry Pi 4 / Home
Assistant OS (aarch64): Ingress dashboard, CSS, and Browser routes returned
HTTP 200; Browser Status was `ready`; and the protected internal smoke action
successfully exercised `/usr/bin/chromium` and the existing persistent context.
Phase 9 is next and has not started.
