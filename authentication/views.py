from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from authentication.serializers import RegisterSerializer
from ecommerce_backend.settings import DEBUG


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data['refresh']
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({'detail': 'Logged out successfully.'}, status=status.HTTP_205_RESET_CONTENT)
        except Exception:
            return Response({'detail': 'Invalid token.'}, status=status.HTTP_400_BAD_REQUEST)


class RegisterView(APIView):
    permission_classes = []
    authentication_classes = []

    def post(self, request):

        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(TokenObtainPairView):

    def finalize_response(self, request, response, *args, **kwargs):
        is_secure = False if DEBUG else True

        # Refresh token cookie
        if response.data.get("refresh"):
            response.set_cookie(
                key="refresh_token",
                value=response.data["refresh"],
                httponly=True,
                secure=is_secure,        # False only if testing over plain http on localhost
                samesite="Lax",
            )
            del response.data["refresh"]
        return super().finalize_response(request, response, *args, **kwargs)
