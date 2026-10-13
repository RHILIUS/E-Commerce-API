"""Validate and compress photos, keeping a source for later independent crop edits."""
from io import BytesIO
from uuid import uuid4
import warnings

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MAX_PIXELS = 25_000_000


def _read_image(file):
    """Decode a static supported image with bounded pixel count and normalized orientation."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(file) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or getattr(source, "is_animated", False):
                    raise ValidationError({"image": ["Choose a static JPEG, PNG, or WebP image."]})
                if source.width * source.height > MAX_PIXELS:
                    raise ValidationError({"image": ["Choose an image with at most 25 megapixels."]})
                source.load()
                return ImageOps.exif_transpose(source).convert("RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError({"image": ["This image cannot be read. Choose another image."]})


def _encode(image):
    """Produce metadata-free WebP bytes at the agreed avatar quality."""
    output = BytesIO()
    image.save(output, format="WEBP", quality=85, method=6, exif=b"", icc_profile=None)
    return ContentFile(output.getvalue())


def save_photo(user, data):
    """Lock photo edits, preserve original composition, and clean failed storage writes."""
    written = []
    try:
        with transaction.atomic():
            account = get_user_model().objects.select_for_update().get(pk=user.pk)
            upload = data.get("image")
            source = account.profile_image_original or account.profile_image
            if upload is None:
                if not source or source.name != data["source_id"]:
                    raise ValidationError({"source_id": ["Your photo changed. Reload it before adjusting the crop."]})
                with source.open("rb") as file:
                    image = _read_image(file)
            else:
                image = _read_image(upload)
            crop = data["crop"]
            width, height = image.size
            crop_width, crop_height = crop["width"] * width, crop["height"] * height
            if abs(crop_width - crop_height) > 2 or min(crop_width, crop_height) < 1:
                raise ValidationError({"crop": ["Choose a square crop inside the image."]})
            # Crop before resizing the retained source; coordinates stay proportional.
            left, top = crop["x"] * width, crop["y"] * height
            side = min(crop_width, crop_height)
            avatar = image.crop((left, top, left + side, top + side)).resize((512, 512), Image.Resampling.LANCZOS)
            if upload is not None or not account.profile_image_original:
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                field = account.profile_image_original
                name = field.storage.save(f"users/profile/originals/{uuid4().hex}.webp", _encode(image))
                written.append((field.storage, name))
                field.name = name
            field = account.profile_image
            name = field.storage.save(f"users/profile/{uuid4().hex}.webp", _encode(avatar))
            written.append((field.storage, name))
            field.name = name
            account.profile_image_crop = crop
            account.save(update_fields=["profile_image", "profile_image_original", "profile_image_crop"])
        return account
    except Exception:
        for storage, name in written:
            storage.delete(name)
        raise


@transaction.atomic
def remove_photo(user):
    """Clear both photo files and crop metadata; lifecycle signals clean storage after commit."""
    account = get_user_model().objects.select_for_update().get(pk=user.pk)
    account.profile_image = ""
    account.profile_image_original = ""
    account.profile_image_crop = {}
    account.save(update_fields=["profile_image", "profile_image_original", "profile_image_crop"])
    return account
