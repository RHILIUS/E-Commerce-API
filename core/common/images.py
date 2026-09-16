from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


MAX_IMAGE_SIZE = (2048, 2048)
IMAGE_QUALITY = 85
COMPRESSIBLE_FORMATS = {"JPEG", "PNG", "WEBP"}


def compress_uploaded_image(uploaded_file):
    """Resize and optimize a new upload before Django sends it to storage."""
    uploaded_file.seek(0)
    original_content = uploaded_file.read()
    uploaded_file.seek(0)

    with Image.open(BytesIO(original_content)) as source:
        image_format = source.format
        if image_format not in COMPRESSIBLE_FORMATS or getattr(source, "is_animated", False):
            return uploaded_file

        image = ImageOps.exif_transpose(source)
        original_size = image.size
        image.thumbnail(MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)

        save_options = {}
        if image_format == "JPEG":
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            save_options = {
                "quality": IMAGE_QUALITY,
                "optimize": True,
                "progressive": True,
            }
        elif image_format == "PNG":
            save_options = {"optimize": True, "compress_level": 9}
        elif image_format == "WEBP":
            save_options = {"quality": IMAGE_QUALITY, "method": 6}

        output = BytesIO()
        image.save(output, format=image_format, **save_options)

    compressed_content = output.getvalue()
    was_resized = original_size != image.size
    if not was_resized and len(compressed_content) >= len(original_content):
        return uploaded_file

    return ContentFile(compressed_content, name=uploaded_file.name)
