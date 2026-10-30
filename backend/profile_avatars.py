"""Bound and normalize account photos before private storage."""
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 2_000_000


def normalize_avatar(raw):
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in ("JPEG", "PNG", "WEBP") or source.width * source.height > 16_000_000:
                raise ValueError("Choose a smaller JPEG, PNG, or WebP photo")
            photo = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGBA"), (512, 512))
            clean = Image.new("RGB", (512, 512), "white")
            clean.paste(photo, mask=photo.getchannel("A"))
            result = BytesIO()
            clean.save(result, format="PNG")
            return result.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Choose a readable JPEG, PNG, or WebP photo") from exc
