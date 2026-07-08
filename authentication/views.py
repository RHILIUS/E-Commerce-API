from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from authentication.serializers import SignupSerializer
from ecommerce_backend.settings import DEBUG


class LogoutView(APIView):

    permission_classes = []

    def post(self, request):
        refresh_token = request.COOKIES.get("refresh_token")

        try:
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
        except Exception:
            pass

        response = Response(
            {"detail": "Logout Successful"},
            status=status.HTTP_200_OK,
        )
        response.delete_cookie(
            "refresh_token",
        )
        return response


class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):

        # serialize
        serializer = SignupSerializer(data=request.data)

        # Validate fields
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Save user
        user = serializer.save()
        return Response(
            {**serializer.data, "message": "Account created successfully"}, status=status.HTTP_201_CREATED
        )


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
