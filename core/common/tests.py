from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from core.common.images import MAX_IMAGE_SIZE, compress_uploaded_image


class CompressUploadedImageTests(SimpleTestCase):
    def test_resizes_and_compresses_jpeg_upload(self):
        source = BytesIO()
        Image.new("RGB", (3000, 2400), "red").save(source, format="JPEG", quality=100)
        upload = SimpleUploadedFile(
            "product.jpg",
            source.getvalue(),
            content_type="image/jpeg",
        )

        compressed = compress_uploaded_image(upload)

        with Image.open(compressed) as result:
            self.assertLessEqual(result.width, MAX_IMAGE_SIZE[0])
            self.assertLessEqual(result.height, MAX_IMAGE_SIZE[1])
        self.assertEqual(compressed.name, "product.jpg")
        self.assertLess(compressed.size, upload.size)
