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


class ReservationImportDocument(BaseModel):
    """Only safe, normalized evidence crosses the upload/domain boundary."""

    text: str = Field(min_length=1)
    uris: list[str] = Field(default_factory=list)
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
    if hostname not in {"booking.com", "www.booking.com"} and not hostname.endswith(".booking.com"):
        return None
    if parsed.scheme.casefold() != "https" or not parsed.path.startswith("/hotel/"):
        return None
    return urlunsplit(("https", "www.booking.com", parsed.path, "", ""))


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


def _annotation_uris(page: object) -> list[str]:
    uris: list[str] = []
    annotations = page.get("/Annots", [])  # type: ignore[union-attr]
    for annotation_ref in annotations:
        try:
            annotation = annotation_ref.get_object()
            action = annotation.get("/A")
            uri = action.get("/URI") if action and action.get("/S") == "/URI" else None
            if isinstance(uri, str) and (canonical := canonical_booking_hotel_url(uri)):
                uris.append(canonical)
        except Exception:  # Malformed annotations are untrusted, not exceptional to the user.
            continue
    return uris


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
        uris: list[str] = []
        metadata_title = str((reader.metadata or {}).get("/Title") or "").strip()
        if metadata_title:
            # Metadata is only evidence for the reviewable parser, never persisted raw.
            extracted.append(f"PDF title: {metadata_title}")
        for page in reader.pages:
            try:
                page_text = page.extract_text(extraction_mode="layout")
            except TypeError:
                page_text = page.extract_text()
            extracted.append(page_text or "")
            uris.extend(_annotation_uris(page))
    except ImportDocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise ImportDocumentError(PDF_SAFE_ERROR) from None
    text = normalize_confirmation_text("\n".join(extracted))
    if not text:
        raise ImportDocumentError(PDF_TEXT_ERROR)
    return ReservationImportDocument(
        text=text,
        uris=list(dict.fromkeys(uris)),
        source=ImportDocumentSource.PDF,
    )
