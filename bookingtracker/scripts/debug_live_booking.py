"""Local headed Booking parser laboratory; never uses production profile or database."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.booking.capture import audit_rate_fixture_html, sanitize_rate_fixture_html
from app.booking.live_debug import (
    analyze_html,
    capture_availability_html,
    elapsed_ms,
    load_live_config,
    run_production_check,
)
from app.booking.selectors import BookingSelectors
from app.browser.models import BrowserState, NavigationStatus
from app.browser.service import BookingBrowserService
from app.browser.session import detect_page_state
from app.config import BrowserSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = Path.home() / "Library" / "Application Support" / "BookingTrackerDebug"
DEFAULT_CAPTURE_DIR = DEFAULT_PROFILE.parent / "BookingTrackerDebugCaptures"
BOOKING_HOME = "https://www.booking.com/"


def _json(data: dict[str, object]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_profile(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if _is_within(resolved, PROJECT_ROOT):
        raise ValueError("Browser profile must be outside the Git repository")
    return resolved


def _settings(profile: Path) -> BrowserSettings:
    return BrowserSettings(profile_dir=_validate_profile(profile), channel="chrome", headless=False)


def _manual_action(service: BookingBrowserService) -> None:
    while service.requires_manual_action():
        if service.status() is BrowserState.CAPTCHA_REQUIRED:
            prompt = (
                "Booking.com vyžaduje CAPTCHA. Dokončete ji ručně v otevřeném Chromiu; "
                "nic nekopírujte do terminálu. Potom zde stiskněte Enter."
            )
        else:
            prompt = (
                "Přihlaste se ručně přímo v otevřeném Chromiu. Heslo ani ověřovací údaje "
                "nezadávejte do terminálu. Po dokončení zde stiskněte Enter."
            )
        input(prompt)
        service.refresh_state()


def _open_page(service: BookingBrowserService, url: str) -> tuple[object, dict[str, int]]:
    timings: dict[str, int] = {}
    started = perf_counter()
    health = service.start()
    timings["browser_start_ms"] = elapsed_ms(started)
    if not health.context_running:
        raise RuntimeError("headed Chromium could not be started")

    started = perf_counter()
    navigation = service.navigate(url)
    timings["page_navigation_ms"] = elapsed_ms(started)
    if navigation.status in {
        NavigationStatus.TIMEOUT,
        NavigationStatus.NAVIGATION_ERROR,
        NavigationStatus.BROWSER_CRASH,
        NavigationStatus.PAGE_CLOSED,
    }:
        raise RuntimeError(f"navigation failed: {navigation.status.value}")
    _manual_action(service)
    page = service.current_page()
    if page is None:
        raise RuntimeError("browser page is unavailable")
    return page, timings


def _page_summary(page: object, timings: dict[str, int]) -> dict[str, object]:
    started = perf_counter()
    authentication, state = detect_page_state(page)
    timings["page_state_detection_ms"] = elapsed_ms(started)
    final_url = str(page.url)  # type: ignore[attr-defined]
    path = urlsplit(final_url).path
    if state is BrowserState.CAPTCHA_REQUIRED:
        page_type = "captcha"
    elif state in {BrowserState.LOGIN_REQUIRED, BrowserState.LOGGED_OUT}:
        page_type = "login"
    elif "/hotel/" in path:
        page_type = "hotel"
    else:
        page_type = "other"
    counts = {
        "availability": page.locator(BookingSelectors.AVAILABILITY).count(),  # type: ignore[attr-defined]
        "room": page.locator(BookingSelectors.ROOM).count(),  # type: ignore[attr-defined]
        "rate": page.locator(BookingSelectors.RATE).count(),  # type: ignore[attr-defined]
        "legacy_rate": page.locator(BookingSelectors.LEGACY_RATE).count(),  # type: ignore[attr-defined]
    }
    return {
        "final_url": _safe_final_url(final_url),
        "final_hostname": urlsplit(final_url).hostname,
        "page_type": page_type,
        "authentication": authentication.value,
        "captcha_required": state is BrowserState.CAPTCHA_REQUIRED,
        "selector_counts": counts,
    }


def _safe_final_url(value: str) -> str:
    parsed = urlsplit(value)
    allowed = {
        "age",
        "checkin",
        "checkout",
        "group_adults",
        "group_children",
        "no_rooms",
        "selected_currency",
    }
    query = [(key, item) for key, item in parse_qsl(parsed.query) if key in allowed]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _run_analysis(args: argparse.Namespace) -> int:
    config = load_live_config(args.config)
    service = BookingBrowserService(_settings(args.profile_dir))
    try:
        page, timings = _open_page(service, config.navigation_url())
        page_summary = _page_summary(page, timings)
        html = page.content()  # type: ignore[attr-defined]
        _, _, report = analyze_html(html, config)
        report["page"] = page_summary
        report_timings = report["timings"]
        assert isinstance(report_timings, dict)
        report_timings.update(timings)
        _json(report)
        return 0 if report["outcome"] != "parser_error" else 2
    finally:
        service.stop()


class _ObservedBrowser:
    def __init__(self, service: BookingBrowserService) -> None:
        self.service = service

    def navigate(self, url: str):  # noqa: ANN201
        return self.service.navigate(url)

    def current_page(self) -> object | None:
        return self.service.current_page()


def _run_check(args: argparse.Namespace) -> int:
    config = load_live_config(args.config)
    service = BookingBrowserService(_settings(args.profile_dir))
    timings: dict[str, int] = {}
    try:
        started = perf_counter()
        health = service.start()
        timings["browser_start_ms"] = elapsed_ms(started)
        if not health.context_running:
            raise RuntimeError("headed Chromium could not be started")
        result = run_production_check(_ObservedBrowser(service), config)
        record = result.pop("record")
        page = service.current_page()
        if page is None:
            raise RuntimeError("browser page is unavailable after CheckRunner")
        report = {
            "event": "booking_live_debug_production_check",
            "property": config.property_name,
            "page": _page_summary(page, timings),
            "candidate_count_before_matcher": result.pop("candidate_count"),
            "status": record.status.value,
            "reason_code": record.reason_code.value if record.reason_code else None,
            "diagnostic_phase": (
                record.diagnostic_phase.value if record.diagnostic_phase else None
            ),
            "match_classification": (
                record.match_classification.value if record.match_classification else None
            ),
            "duration_ms": record.duration_ms,
            **result,
            "timings": timings,
        }
        if report["volatile_next_check_at"] is not None:
            report["volatile_next_check_at"] = report["volatile_next_check_at"].isoformat()
        _json(report)
        return 0 if record.status.value == "success" else 2
    finally:
        service.stop()


def _capture(args: argparse.Namespace) -> int:
    config = load_live_config(args.config)
    service = BookingBrowserService(_settings(args.profile_dir))
    try:
        page, timings = _open_page(service, config.navigation_url())
        page_summary = _page_summary(page, timings)
        raw_subtree = capture_availability_html(page)
        sanitized = sanitize_rate_fixture_html(raw_subtree)
        findings = audit_rate_fixture_html(sanitized)
        if findings:
            raise RuntimeError("automatic privacy audit failed: " + ", ".join(findings))
        output = args.output
        if output is None:
            safe_name = re.sub(r"[^a-z0-9]+", "-", config.property_name.casefold()).strip("-")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output = DEFAULT_CAPTURE_DIR / f"{safe_name}-{stamp}.booking-capture.html"
        output = output.expanduser().resolve()
        if _is_within(output, PROJECT_ROOT) and not any(
            part in {"debug-live", ".debug-live"} for part in output.parts
        ):
            raise ValueError(
                "capture inside the repository must be in an ignored debug-live directory"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(sanitized, encoding="utf-8")
        _json(
            {
                "event": "booking_live_debug_capture",
                "capture_file": output.name,
                "bytes": len(sanitized.encode()),
                "automatic_privacy_audit": "passed",
                "manual_privacy_audit_required_before_fixture_commit": True,
                "page": page_summary,
                "timings": timings,
            }
        )
        return 0
    finally:
        service.stop()


def _replay(args: argparse.Namespace) -> int:
    config = load_live_config(args.config)
    html = args.capture.read_text(encoding="utf-8")
    findings = audit_rate_fixture_html(html)
    if findings:
        raise RuntimeError("capture privacy audit failed: " + ", ".join(findings))
    _, match, report = analyze_html(html, config)
    report["mode"] = "offline_replay"
    report["exact_match"] = match.accepted
    report["match_classification"] = match.classification.value
    _json(report)
    return 0 if report["outcome"] != "parser_error" else 2


def _open(args: argparse.Namespace) -> int:
    config = load_live_config(args.config) if args.config else None
    service = BookingBrowserService(_settings(args.profile_dir))
    try:
        page, timings = _open_page(service, config.navigation_url() if config else BOOKING_HOME)
        _json(
            {
                "event": "booking_live_debug_open",
                "page": _page_summary(page, timings),
                "timings": timings,
            }
        )
        input(
            "Chromium zůstává otevřený. Až budete hotovi, stiskněte zde Enter "
            "pro bezpečné zavření."
        )
        return 0
    finally:
        service.stop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE)
    commands = parser.add_subparsers(dest="command", required=True)
    open_parser = commands.add_parser("open", help="open headed Chromium for manual login")
    open_parser.add_argument("--config", type=Path)
    for name in ("inspect", "check", "capture"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "capture":
            command.add_argument("--output", type=Path)
    replay = commands.add_parser("replay")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--capture", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "open":
            return _open(args)
        if args.command == "inspect":
            return _run_analysis(args)
        if args.command == "check":
            return _run_check(args)
        if args.command == "capture":
            return _capture(args)
        return _replay(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"debug_live_booking: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
