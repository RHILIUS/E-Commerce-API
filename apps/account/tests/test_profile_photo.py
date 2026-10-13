"""Photo contract, processing, independent edits, and local-storage lifecycle."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from core.test_support import create_customer

URL = "/api/account/profile/photo/"
CROP = {"x": .25, "y": 0, "width": .5, "height": 1}


def upload(format="JPEG", size=(3000, 1500), animated=False):
    """Create supported and unsupported real image payloads without fixture files."""
    image = Image.new("RGB", size, "red")
    content = BytesIO()
    options = {"save_all": True, "append_images": [Image.new("RGB", size, "blue")]} if animated else {}
    image.save(content, format=format, **options)
    return SimpleUploadedFile("photo.bin", content.getvalue(), content_type="application/octet-stream")


class ProfilePhotoTests(APITestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name, STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        })
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = create_customer(username="photo", email="photo@example.com", full_name="Buyer",
            phone_number="+639171234567", country_code="PH")
        self.client.force_authenticate(self.user)

    def save(self, image=None, crop=CROP):
        return self.client.put(URL, {"image": image or upload(), "crop": json.dumps(crop)}, format="multipart")

    def test_authentication_and_empty_read_without_writes(self):
        self.assertEqual(self.client.get(URL).data, {
            "profile_image": None, "original_image": None, "source_id": "", "crop": None})
        self.client.force_authenticate(None)
        for method in (self.client.get, self.client.put, self.client.delete):
            self.assertEqual(method(URL).status_code, 401)

    def test_upload_compresses_source_avatar_and_preserves_details(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Buyer")
        self.assertEqual(self.user.address.country_code, "PH")
        self.assertEqual(response.data["crop"], CROP)
        with self.user.profile_image.open("rb") as file, Image.open(file) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.format, "WEBP")
            self.assertFalse(image.getexif())
        with self.user.profile_image_original.open("rb") as file, Image.open(file) as image:
            self.assertEqual(image.size, (2048, 1024))
            self.assertEqual(image.format, "WEBP")

    def test_static_formats_are_accepted_from_content(self):
        for format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(format=format), self.captureOnCommitCallbacks(execute=True):
                response = self.save(upload(format=format))
                self.assertEqual(response.status_code, 200, response.data)

    def test_recrop_preserves_source_and_cleans_old_avatar(self):
        saved = self.save().data
        self.user.refresh_from_db()
        old_avatar = Path(self.user.profile_image.path)
        old_source = Path(self.user.profile_image_original.path)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.put(URL, {"source_id": saved["source_id"],
                "crop": {"x": 0, "y": 0, "width": .5, "height": 1}}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["source_id"], saved["source_id"])
        self.assertFalse(old_avatar.exists())
        self.assertTrue(old_source.exists())

    def test_replace_remove_and_account_deletion_clean_both_files(self):
        self.save()
        self.user.refresh_from_db()
        paths = [Path(self.user.profile_image.path), Path(self.user.profile_image_original.path)]
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.save().status_code, 200)
        self.assertTrue(all(not path.exists() for path in paths))
        self.user.refresh_from_db()
        paths = [Path(self.user.profile_image.path), Path(self.user.profile_image_original.path)]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["crop"], None)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(self.client.delete(URL).status_code, 200)
        self.save()
        self.user.refresh_from_db()
        paths = [Path(self.user.profile_image.path), Path(self.user.profile_image_original.path)]
        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()
        self.assertTrue(all(not path.exists() for path in paths))

    def test_bad_images_and_limits_leave_photo_unchanged(self):
        for image in (SimpleUploadedFile("fake.jpg", b"not an image"), upload("GIF"),
                      upload("WEBP", (20, 10), animated=True),
                      SimpleUploadedFile("large.jpg", b"x" * (5 * 1024 * 1024 + 1))):
            with self.subTest(name=image.name):
                self.assertEqual(self.save(image).status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.profile_image)
        self.assertEqual(list(Path(self.directory.name).rglob("*.webp")), [])

    def test_crop_bounds_square_and_non_finite_validation(self):
        for crop in ({**CROP, "x": .75}, {**CROP, "width": .25}, {**CROP, "width": 0},
                     {**CROP, "x": float("nan")}, {**CROP, "height": float("inf")}):
            with self.subTest(crop=crop):
                self.assertEqual(self.save(crop=crop).status_code, 400)
        response = self.client.put(URL, {"image": upload(), "crop": "invalid"}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_stale_and_other_users_sources_cannot_be_recropped(self):
        saved = self.save().data
        self.save()
        response = self.client.put(URL, {"source_id": saved["source_id"], "crop": CROP}, format="json")
        self.assertEqual(response.status_code, 400)
        other = create_customer(username="otherphoto", email="otherphoto@example.com", full_name="Other",
            phone_number="+12025550123", country_code="US")
        self.client.force_authenticate(other)
        self.assertEqual(self.client.put(URL, {"source_id": saved["source_id"], "crop": CROP}, format="json").status_code, 400)

    def test_failed_database_save_cleans_new_files_and_keeps_saved_photo(self):
        self.save()
        self.user.refresh_from_db()
        saved = self.user.profile_image.name
        before = set(Path(self.directory.name).rglob("*.webp"))
        with patch.object(get_user_model(), "save", side_effect=RuntimeError("save failed")):
            with self.assertRaises(RuntimeError):
                self.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_image.name, saved)
        self.assertEqual(set(Path(self.directory.name).rglob("*.webp")), before)

    def test_legacy_photo_can_be_recropped_and_shared_files_are_preserved(self):
        self.user.profile_image.save("legacy.jpg", upload())
        legacy_name = self.user.profile_image.name
        other = create_customer(username="shared", email="shared@example.com", full_name="Shared",
            phone_number="+12025550123", country_code="US")
        other.profile_image = legacy_name
        other.save(update_fields=["profile_image"])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.put(URL, {"source_id": legacy_name, "crop": CROP}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(self.user.profile_image.storage.exists(legacy_name))
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_image_original)

    def test_orientation_is_normalized_and_metadata_is_removed(self):
        image = Image.new("RGB", (40, 20), "red")
        exif = image.getexif()
        exif[274] = 6
        exif[315] = "Private metadata"
        content = BytesIO()
        image.save(content, "JPEG", exif=exif)
        file = SimpleUploadedFile("rotated.jpg", content.getvalue())
        response = self.save(file, {"x": 0, "y": .25, "width": 1, "height": .5})
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        with self.user.profile_image_original.open("rb") as file, Image.open(file) as result:
            self.assertEqual(result.size, (20, 40))
            self.assertFalse(result.getexif())

    def test_oversized_dimensions_are_rejected_before_decode(self):
        with patch("apps.account.photo_services.MAX_PIXELS", 100):
            self.assertEqual(self.save(upload(size=(20, 10))).status_code, 400)
