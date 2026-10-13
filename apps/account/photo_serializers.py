"""Independent photo contract; crop coordinates refer to the oriented source image."""
import math

from drf_spectacular.utils import extend_schema_field, OpenApiTypes
from rest_framework import serializers


class PhotoCropSerializer(serializers.Serializer):
    x = serializers.FloatField(min_value=0, max_value=1)
    y = serializers.FloatField(min_value=0, max_value=1)
    width = serializers.FloatField(min_value=0, max_value=1)
    height = serializers.FloatField(min_value=0, max_value=1)

    def validate(self, attrs):
        if not all(math.isfinite(value) for value in attrs.values()):
            raise serializers.ValidationError("Crop coordinates must be finite.")
        if (attrs["width"] <= 0 or attrs["height"] <= 0 or
                attrs["x"] + attrs["width"] > 1.000001 or
                attrs["y"] + attrs["height"] > 1.000001):
            raise serializers.ValidationError("Crop must fit inside the image.")
        return attrs


class PhotoSaveSerializer(serializers.Serializer):
    image = serializers.FileField(required=False)
    crop = PhotoCropSerializer()
    source_id = serializers.CharField(required=False)

    def validate(self, attrs):
        if "image" not in attrs and not attrs.get("source_id"):
            raise serializers.ValidationError({"source_id": "The existing photo is required for re-cropping."})
        if "image" in attrs and attrs["image"].size > 5 * 1024 * 1024:
            raise serializers.ValidationError({"image": "Choose an image no larger than 5 MB."})
        return attrs


class PhotoSerializer(serializers.Serializer):
    profile_image = serializers.URLField(allow_null=True)
    original_image = serializers.URLField(allow_null=True)
    source_id = serializers.CharField(allow_blank=True)
    crop = PhotoCropSerializer(allow_null=True)


def photo_data(user, request):
    """Expose saved avatar and re-croppable source, including legacy seeded photos."""
    source = user.profile_image_original or user.profile_image
    crop = user.profile_image_crop
    return {
        "profile_image": request.build_absolute_uri(user.profile_image.url) if user.profile_image else None,
        "original_image": request.build_absolute_uri(source.url) if source else None,
        "source_id": source.name if source else "",
        "crop": crop if all(key in crop for key in ("x", "y", "width", "height")) else None,
    }


@extend_schema_field(OpenApiTypes.BINARY)
class PhotoUploadField(serializers.FileField):
    """Document multipart uploads as binary rather than response-style file URLs."""


class PhotoUploadSerializer(serializers.Serializer):
    image = PhotoUploadField()
    crop = serializers.CharField(help_text='JSON-encoded object with x, y, width, height fractions.')


class PhotoRecropSerializer(serializers.Serializer):
    crop = PhotoCropSerializer()
    source_id = serializers.CharField()
