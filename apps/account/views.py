from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RecipientInfo
from .serializers import RecipientListSerializer, RecipientSerializer
from .services import create_recipient


class RecipientListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Recipients"],
        responses={200: RecipientListSerializer, 401: OpenApiTypes.OBJECT},
        description="List the authenticated customer's saved recipients, default first, then by ID. No pagination or writes.",
    )
    def get(self, request):
        recipients = RecipientInfo.objects.filter(user=request.user).order_by("-is_default", "pk")
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
