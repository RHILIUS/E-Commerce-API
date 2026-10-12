from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RecipientInfo
from .serializers import RecipientDeleteSerializer, RecipientListSerializer, RecipientSerializer
from .services import create_recipient, delete_recipient, update_recipient


class RecipientListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Recipients"],
        responses={200: RecipientListSerializer, 401: OpenApiTypes.OBJECT},
        description="List the authenticated customer's saved recipients, default first, then by ID. No pagination or writes.",
    )
    def get(self, request):
        recipients = RecipientInfo.objects.filter(user=request.user, is_deleted=False).order_by("-is_default", "pk")
        return Response({"recipients": RecipientSerializer(recipients, many=True).data})

    @extend_schema(
        tags=["Recipients"],
        request=RecipientSerializer,
        responses={
            201: RecipientSerializer,
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Field names mapped to lists of validation messages."),
            401: OpenApiTypes.OBJECT,
        },
        description=(
            "Add a saved recipient owned by the authenticated customer. The first recipient "
            "is automatically default, even when is_default is false. Later recipients "
            "default to false; explicit true replaces the previous default atomically. "
            "Phone numbers use E.164 and need not be unique. No checkout is created."
        ),
    )
    def post(self, request):
        serializer = RecipientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recipient = create_recipient(request.user, serializer.validated_data)
        return Response(RecipientSerializer(recipient).data, status=status.HTTP_201_CREATED)


class RecipientUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Recipients"],
        request=RecipientSerializer,
        responses={
            200: RecipientSerializer,
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Field names mapped to validation messages."),
            401: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
        description="Partially update an owned saved recipient. Omitted fields are unchanged. Default replacement is atomic; the current default cannot be directly cleared. Existing order snapshots remain unchanged.",
    )
    def patch(self, request, pk):
        # Check ownership before validation so foreign and missing IDs always return 404.
        recipient = get_object_or_404(RecipientInfo, pk=pk, user=request.user, is_deleted=False)
        serializer = RecipientSerializer(recipient, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        recipient = update_recipient(request.user, pk, serializer.validated_data)
        return Response(RecipientSerializer(recipient).data)


    @extend_schema(
        tags=["Recipients"],
        parameters=[RecipientDeleteSerializer],
        request=None,
        responses={
            200: RecipientListSerializer,
            400: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Field validation errors, including required replacement_default_id."),
            401: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
        description="Remove an owned saved recipient from future use, retaining historical orders and pending payments. Supply replacement_default_id as a query parameter when removing a default with other saved recipients. Returns the remaining active recipients. Repeated removal is safe.",
    )
    def delete(self, request, pk):
        get_object_or_404(RecipientInfo, pk=pk, user=request.user)
        serializer = RecipientDeleteSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        recipients = delete_recipient(request.user, pk, **serializer.validated_data)
        return Response({"recipients": RecipientSerializer(recipients, many=True).data})
