# BookingTracker

State is persisted only in `/data`: SQLite, logs, and the Booking browser profile.
Ingress serves the UI; no ports are published. Version `0.2.4` implements Phase
9 remote recovery but has not completed its real-Pi production gate. On Home
Assistant, Chromium starts headful in private Xvfb; noVNC is available only
through authenticated Ingress and only while a manual session is active.

Phase 8 is complete. Version `0.1.6` was verified on Raspberry Pi 4 / Home
Assistant OS (aarch64): Ingress dashboard, CSS, and Browser routes returned
HTTP 200; Browser Status was `ready`; and the protected internal smoke action
successfully exercised `/usr/bin/chromium` and the existing persistent context.
Phase 9 must still be verified through a real Home Assistant Ingress/noVNC
manual-login and clean-shutdown smoke test before it is marked complete.
