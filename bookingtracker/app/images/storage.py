"""Local, validated property-image storage independent of price tracking."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from secrets import token_hex

from PIL import Image, UnidentifiedImageError


class ImageStorageError(ValueError):
    pass


class ImageStorage:
    MAX_UPLOAD_BYTES = 10 * 1024 * 1024
    MAX_PIXELS = 20_000_000
    DETAIL_MAX = 1600
    THUMBNAIL_MAX = 640
    ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

    def __init__(self, root: Path) -> None:
        self.root = root

    def store(self, data: bytes) -> str:
        if not data or len(data) > self.MAX_UPLOAD_BYTES:
            raise ImageStorageError("Obrázek je příliš velký.")
        identifier: str | None = None
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in self.ALLOWED_FORMATS:
                    raise ImageStorageError("Nepodporovaný typ obrázku.")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                if (
                    image.width < 16
                    or image.height < 16
                    or image.width * image.height > self.MAX_PIXELS
                ):
                    raise ImageStorageError("Obrázek má nepodporované rozměry.")
                image = image.convert("RGB")
                identifier = token_hex(16)
                self.root.mkdir(parents=True, exist_ok=True)
                self._write(image, identifier, "detail", self.DETAIL_MAX)
                self._write(image, identifier, "thumbnail", self.THUMBNAIL_MAX)
        except ImageStorageError:
            self.remove(identifier)
            raise
        except (UnidentifiedImageError, OSError, ValueError) as error:
            self.remove(identifier)
            raise ImageStorageError("Soubor není platný obrázek.") from error
        assert identifier is not None
        return identifier

    def path(self, identifier: str, variant: str) -> Path:
        if not self._valid_id(identifier) or variant not in {"detail", "thumbnail"}:
            raise FileNotFoundError
        return self.root / f"{identifier}-{variant}.webp"

    def remove(self, identifier: str | None) -> None:
        if not identifier or not self._valid_id(identifier):
            return
        for variant in ("detail", "thumbnail"):
            self.path(identifier, variant).unlink(missing_ok=True)

    def _write(self, image: Image.Image, identifier: str, variant: str, limit: int) -> None:
        rendered = image.copy()
        rendered.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        temporary = self.path(identifier, variant).with_suffix(".tmp")
        rendered.save(temporary, format="WEBP", quality=82, method=4)
        temporary.replace(self.path(identifier, variant))

    @staticmethod
    def _valid_id(value: str) -> bool:
        return len(value) == 32 and all(character in "0123456789abcdef" for character in value)
