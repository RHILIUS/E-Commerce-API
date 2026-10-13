"""Photo updates are separate from personal-detail saves."""
import json

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .photo_serializers import PhotoSaveSerializer, PhotoSerializer, PhotoUploadSerializer, PhotoRecropSerializer, photo_data
from .photo_services import remove_photo, save_photo

RESPONSES = {200: PhotoSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT}


class ProfilePhotoView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser]

    @extend_schema(tags=["Profile"], responses={200: PhotoSerializer, 401: OpenApiTypes.OBJECT})
    def get(self, request):
        user = get_user_model().objects.get(pk=request.user.pk)
        return Response(photo_data(user, request))

    @extend_schema(tags=["Profile"], request={"application/json": PhotoRecropSerializer, "multipart/form-data": PhotoUploadSerializer}, responses=RESPONSES,
        description="Upload image plus JSON-encoded crop via multipart; re-crop via JSON crop and source_id. Crop coordinates are normalized fractions of the oriented source. Static JPEG/PNG/WebP, at most 5 MiB and 25 megapixels. Square crops only. Source is optimized to at most 2048px; avatar is 512px WebP at quality 85.")
    def put(self, request):
        data = request.data.dict() if hasattr(request.data, "dict") else dict(request.data)
        if isinstance(data.get("crop"), str):
            try:
                data["crop"] = json.loads(data["crop"])
            except (ValueError, TypeError):
                raise ValidationError({"crop": ["Provide valid JSON crop coordinates."]})
        serializer = PhotoSaveSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        user = save_photo(request.user, serializer.validated_data)
        return Response(photo_data(user, request))

    @extend_schema(tags=["Profile"], responses={200: PhotoSerializer, 401: OpenApiTypes.OBJECT},
        description="Remove avatar, retained source, and crop metadata. Repeated removal is safe.")
    def delete(self, request):
        return Response(photo_data(remove_photo(request.user), request))
