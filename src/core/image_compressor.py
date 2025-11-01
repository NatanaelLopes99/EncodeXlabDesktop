from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageFile

from .compression_result import CompressionResult

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

SUPPORTED_IMAGE_EXTENSIONS: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")


def compress_image_file(path: Path, quality: int = 85) -> CompressionResult:
    """
    Re-encodes the given image and keeps the new bytes only when the output is smaller.
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        return CompressionResult(path, False, 0, 0, reason="unsupported-format")

    if not path.exists():
        return CompressionResult(path, False, 0, 0, reason="missing-file")

    original_size = path.stat().st_size
    quality = int(max(1, min(quality, 100)))

    buffer = BytesIO()

    with Image.open(path) as image:
        save_kwargs = {}
        save_format = image.format or ext.lstrip(".").upper()

        if ext in {".jpg", ".jpeg"}:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            save_format = "JPEG"
            save_kwargs.update(
                {
                    "quality": quality,
                    "optimize": True,
                    "progressive": True,
                    "subsampling": "4:2:0",
                }
            )
        elif ext == ".png":
            save_format = "PNG"
            save_kwargs.update({"optimize": True, "compress_level": 9})
        elif ext == ".webp":
            save_format = "WEBP"
            save_kwargs.update({"quality": quality, "method": 6})

        image.save(buffer, format=save_format, **save_kwargs)

    new_bytes = buffer.getvalue()
    new_size = len(new_bytes)

    if new_size == 0:
        return CompressionResult(path, False, original_size, original_size, reason="empty-output")

    if new_size >= original_size:
        return CompressionResult(path, False, original_size, original_size, reason="no-gain")

    path.write_bytes(new_bytes)
    return CompressionResult(path, True, original_size, new_size)
