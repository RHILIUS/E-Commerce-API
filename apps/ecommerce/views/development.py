"""Existing development-only response handler."""
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

User = get_user_model()


class Test(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["Development"], responses={200: dict})
    def get(self, request):

        users = User.objects.all()
        print(users)

        return Response({"message": "hello"}, status=status.HTTP_200_OK)
