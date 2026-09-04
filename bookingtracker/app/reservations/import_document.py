"""Untrusted Booking confirmation inputs normalized before domain extraction."""

from __future__ import annotations

import re
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.reservations.models import ImportDocumentSource

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20
PDF_TEXT_ERROR = "PDF neobsahuje čitelný text. V Gmailu použijte Tisk → Uložit jako PDF."
PDF_SAFE_ERROR = "PDF potvrzení nelze bezpečně zpracovat. Použijte prosím jiný export z Gmailu."


class PdfHotelLink(BaseModel):
    """Safe, local evidence from one PDF hotel-link annotation."""

    canonical_url: str
    visible_text: str | None = Field(default=None, max_length=160)
    page_number: int = Field(ge=0)
    annotation_index: int = Field(ge=0)


class ReservationImportDocument(BaseModel):
    """Only safe, normalized evidence crosses the upload/domain boundary."""

    text: str = Field(min_length=1)
    uris: list[str] = Field(default_factory=list)
    hotel_links: list[PdfHotelLink] = Field(default_factory=list)
    document_title: str | None = Field(default=None, max_length=160)
    source: ImportDocumentSource
    warnings: list[str] = Field(default_factory=list)


class ImportDocumentError(ValueError):
    """A user-safe import failure; raw PDF details are intentionally discarded."""


def canonical_booking_hotel_url(value: str) -> str | None:
    """Allow only canonical HTTPS Booking hotel pages, without tracking data."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").casefold()
    allowed_host = hostname in {"booking.com", "www.booking.com"} or bool(
        re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?\.booking\.com", hostname)
    )
    if not allowed_host or parsed.username or parsed.password or parsed.port:
        return None
    hotel_path = re.fullmatch(
        r"/hotel/([a-z]{2,16})/([^/]+?)(?:\.[a-z]{2}(?:-[a-z]{2})?)?\.html",
        parsed.path,
        re.I,
    )
    if parsed.scheme.casefold() != "https" or hotel_path is None:
        return None
    country, property_slug = hotel_path.groups()
    canonical_path = f"/hotel/{country.casefold()}/{property_slug}.html"
    return urlunsplit(("https", "www.booking.com", canonical_path, "", ""))


def normalize_confirmation_text(value: str) -> str:
    """Remove mail identity/transaction metadata while retaining reservation facts."""
    value = value.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")
    safe: list[str] = []
    sensitive = re.compile(
        r"\b(?:pin|confirmation number|reservation confirmation number|číslo rezervace|"
        r"purchase id|payment (?:id|uuid)|from:|to:|odesílatel|příjemce)\b",
        re.I,
    )
    for raw in value.splitlines(keepends=True):
        ending = "\n" if raw.endswith(("\n", "\r")) else ""
        line = re.sub(r"[\t ]+", " ", raw.strip())
        if not line:
            safe.append(ending)
            continue
        if sensitive.search(line) or re.search(r"[\w.+-]+@[\w.-]+", line):
            continue
        # Phone numbers must include an actual phone separator; do not eat ISO dates.
        line = re.sub(r"\+?\d{1,3}[ ()]\d[\d ()-]{6,}\d", "", line)
        if line:
            safe.append(line + ending)
        elif ending:
            safe.append(ending)
    return "".join(safe)


def text_document(source_text: str) -> ReservationImportDocument:
    text = normalize_confirmation_text(source_text)
    if not text:
        raise ImportDocumentError("Potvrzení neobsahuje čitelný text.")
    return ReservationImportDocument(text=text, source=ImportDocumentSource.TEXT)


def _page_text_fragments(page: object) -> list[tuple[str, float, float]]:
    fragments: list[tuple[str, float, float]] = []

    def visitor(text, current_matrix, text_matrix, _font, _size):  # noqa: ANN001
        value = str(text).strip()
        if value:
            # Text matrices are local to the active graphics state. Gmail
            # exports apply a page-scale/flip matrix, so use the composed PDF
            # point before comparing it to an annotation /Rect.
            x = float(text_matrix[4]) * float(current_matrix[0]) + float(text_matrix[5]) * float(
                current_matrix[2]
            ) + float(current_matrix[4])
            y = float(text_matrix[4]) * float(current_matrix[1]) + float(text_matrix[5]) * float(
                current_matrix[3]
            ) + float(current_matrix[5])
            fragments.append((value, x, y))

    try:
        page.extract_text(visitor_text=visitor)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return []
    return fragments


def _visual_point(
    x: float, y: float, width: float, height: float, rotation: int
) -> tuple[float, float]:
    if rotation == 90:
        return y, width - x
    if rotation == 180:
        return width - x, height - y
    if rotation == 270:
        return height - y, x
    return x, y


def _visual_rect(page: object, rect: object) -> tuple[float, float, float, float] | None:
    try:
        values = [float(value) for value in rect]  # type: ignore[union-attr]
        if len(values) != 4:
            return None
        lower_x, lower_y, upper_x, upper_y = values
        media_box = page.mediabox  # type: ignore[union-attr]
        left, bottom = float(media_box.left), float(media_box.bottom)
        width = float(media_box.width)
        height = float(media_box.height)
        rotation = int(page.get("/Rotate", 0)) % 360  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return None
    points = [
        _visual_point(x - left, y - bottom, width, height, rotation)
        for x in (lower_x, upper_x)
        for y in (lower_y, upper_y)
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _link_visible_text(page: object, rect: object) -> str | None:
    visual_rect = _visual_rect(page, rect)
    if visual_rect is None:
        return None
    left, bottom, right, top = visual_rect
    try:
        media_box = page.mediabox  # type: ignore[union-attr]
        width, height = float(media_box.width), float(media_box.height)
        rotation = int(page.get("/Rotate", 0)) % 360  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return None
    matched: list[tuple[float, float, str]] = []
    for text, x, y in _page_text_fragments(page):
        visual_x, visual_y = _visual_point(x, y, width, height, rotation)
        if left - 24 <= visual_x <= right + 24 and bottom - 24 <= visual_y <= top + 24:
            matched.append((visual_y, visual_x, text))
    if not matched:
        return None
    matched.sort(key=lambda item: (-item[0], item[1]))
    value = normalize_confirmation_text(" ".join(item[2] for item in matched)).strip()
    return value or None


def _annotation_hotel_links(page: object, page_number: int) -> list[PdfHotelLink]:
    links: list[PdfHotelLink] = []
    annotations = page.get("/Annots", [])  # type: ignore[union-attr]
    for annotation_index, annotation_ref in enumerate(annotations):
        try:
            annotation = annotation_ref.get_object()
            action = annotation.get("/A")
            uri = action.get("/URI") if action and action.get("/S") == "/URI" else None
            canonical = canonical_booking_hotel_url(uri) if isinstance(uri, str) else None
            rect = annotation.get("/Rect")
            if canonical and rect:
                links.append(
                    PdfHotelLink(
                        canonical_url=canonical,
                        visible_text=_link_visible_text(page, rect),
                        page_number=page_number,
                        annotation_index=annotation_index,
                    )
                )
        except Exception:  # Malformed annotations are untrusted, not exceptional to the user.
            continue
    return links


def pdf_document(pdf_bytes: bytes) -> ReservationImportDocument:
    if len(pdf_bytes) > MAX_PDF_BYTES or not pdf_bytes.startswith(b"%PDF-"):
        raise ImportDocumentError(PDF_SAFE_ERROR)
    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=True)
        if reader.is_encrypted or len(reader.pages) > MAX_PDF_PAGES:
            raise ImportDocumentError(PDF_SAFE_ERROR)
        root = reader.trailer.get("/Root", {})
        names = root.get("/Names", {}) if root else {}
        if any(key in root for key in ("/OpenAction", "/AA")) or any(
            key in names for key in ("/JavaScript", "/EmbeddedFiles")
        ):
            raise ImportDocumentError(PDF_SAFE_ERROR)
        extracted: list[str] = []
        hotel_links: list[PdfHotelLink] = []
        metadata_title = str((reader.metadata or {}).get("/Title") or "").strip()
        safe_title = normalize_confirmation_text(metadata_title).strip() or None
        for page_number, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text(extraction_mode="layout")
            except TypeError:
                page_text = page.extract_text()
            extracted.append(page_text or "")
            hotel_links.extend(_annotation_hotel_links(page, page_number))
    except ImportDocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise ImportDocumentError(PDF_SAFE_ERROR) from None
    text = normalize_confirmation_text("\n".join(extracted))
    if not text:
        raise ImportDocumentError(PDF_TEXT_ERROR)
    deduplicated_links: list[PdfHotelLink] = []
    seen_links: set[tuple[str, str | None]] = set()
    for link in hotel_links:
        key = (link.canonical_url, link.visible_text.casefold() if link.visible_text else None)
        if key not in seen_links:
            seen_links.add(key)
            deduplicated_links.append(link)
    return ReservationImportDocument(
        text=text,
        uris=list(dict.fromkeys(link.canonical_url for link in deduplicated_links)),
        hotel_links=deduplicated_links,
        document_title=safe_title,
        source=ImportDocumentSource.PDF,
    )
