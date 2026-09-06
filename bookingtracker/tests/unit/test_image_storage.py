from __future__ import annotations

from io import BytesIO

import pytest
from app.images.storage import ImageStorage, ImageStorageError
from PIL import Image


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (800, 600)) -> bytes:
    image = Image.new("RGB", size, "red")
    target = BytesIO()
    image.save(target, format=fmt)
    return target.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_supported_images_create_safe_webp_variants(tmp_path, fmt: str) -> None:  # noqa: ANN001
    storage = ImageStorage(tmp_path)
    identifier = storage.store(image_bytes(fmt))

    assert len(identifier) == 32
    assert storage.path(identifier, "detail").is_file()
    assert storage.path(identifier, "thumbnail").is_file()
    with Image.open(storage.path(identifier, "detail")) as rendered:
        assert rendered.format == "WEBP"
        assert rendered.width <= storage.DETAIL_MAX


@pytest.mark.parametrize("payload", [b"not an image", b""])
def test_invalid_images_are_rejected_without_files(tmp_path, payload: bytes) -> None:  # noqa: ANN001
    storage = ImageStorage(tmp_path)
    with pytest.raises(ImageStorageError):
        storage.store(payload)
    assert not tmp_path.exists() or not list(tmp_path.iterdir())


def test_limits_path_traversal_and_delete_are_safe(tmp_path) -> None:  # noqa: ANN001
    storage = ImageStorage(tmp_path)
    identifier = storage.store(image_bytes())
    with pytest.raises(FileNotFoundError):
        storage.path("../unsafe", "detail")
    with pytest.raises(ImageStorageError):
        storage.store(b"x" * (storage.MAX_UPLOAD_BYTES + 1))
    storage.remove(identifier)
    assert not storage.path(identifier, "detail").exists()
    assert not storage.path(identifier, "thumbnail").exists()


def test_absurd_dimensions_are_rejected(tmp_path) -> None:  # noqa: ANN001
    storage = ImageStorage(tmp_path)
    with pytest.raises(ImageStorageError, match="rozměry"):
        storage.store(image_bytes(size=(5000, 5000)))


def test_transcode_strips_uploaded_metadata(tmp_path) -> None:  # noqa: ANN001
    source = Image.new("RGB", (80, 80), "blue")
    metadata = source.getexif()
    metadata[270] = "private source text"
    payload = BytesIO()
    source.save(payload, format="JPEG", exif=metadata)

    identifier = ImageStorage(tmp_path).store(payload.getvalue())

    with Image.open(tmp_path / f"{identifier}-detail.webp") as rendered:
        assert not rendered.getexif()
