# Changelog

## 0.2.4

Cache-bust BookingTracker CSS with a deterministic content revision.

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
