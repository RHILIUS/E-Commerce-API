from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from authentication.serializers import (
    CustomTokenObtainPairSerializer,
    RegisterSerializer,
    SendOTPSerializer,
    UserPublicSerializer,
    VerifyOTPSerializer,
)
from authentication.services.email_service import (
    OTPDeliveryError,
    send_otp_email,
)
from authentication.services.otp_service import OTPRateLimitExceeded, issue_otp
from authentication.services.registration_token_service import (
    REGISTRATION_TOKEN_COOKIE_NAME,
    issue_registration_token,
)
from ecommerce.models import User

is_secure = not settings.DEBUG


class LogoutView(APIView):
    permission_classes = [AllowAny]

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
            path="/",
            samesite="Lax"
        )
        return response


class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def finalize_response(self, request, response, *args, **kwargs):

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


class RefreshView(TokenRefreshView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(
            "refresh_token")
        if refresh_token is None:
            return Response({"detail": "No refresh token found in cookie."}, status=status.HTTP_401_UNAUTHORIZED)

        # mutable copy of request.data so we can inject the token
        data = request.data.copy()
        data["refresh"] = refresh_token
        serializer = self.get_serializer(data=data)

        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            response = Response({"detail": str(e)},
                                status=status.HTTP_401_UNAUTHORIZED)
            response.delete_cookie(
                key="refresh_token",
                path="/",
                samesite="Lax",
            )
            return response

        # Retrieve access token and user_id
        access_token = serializer.validated_data.get("access")
        user_id = AccessToken(access_token)["user_id"]

        # Query the user object through user_id
        try:
            user = User.objects.only(
                "id", "full_name", "email").get(id=user_id)
        except User.DoesNotExist:
            response = Response({"detail": "User not found."},
                                status=status.HTTP_401_UNAUTHORIZED)
            response.delete_cookie(
                key="refresh_token",
                path="/",
                samesite="Lax",
            )
            return response

        response = Response(
            {"access": access_token,
             "user": UserPublicSerializer(user).data},
            status=status.HTTP_200_OK)

        # if ROTATE_REFRESH_TOKENS is True, simplejwt returns a new refresh token too —
        # set it back as an HttpOnly cookie instead of returning it in the body
        new_refresh = serializer.validated_data.get("refresh")
        if new_refresh:
            response.set_cookie(
                key="refresh_token",
                value=str(new_refresh),
                httponly=True,
                secure=is_secure,
                samesite="Lax",
                path="/",
            )

        return response


class SendOTPView(APIView):

    permission_classes = [AllowAny]

    def post(self, request):

        serializer = SendOTPSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Issue OTP
        email = serializer.validated_data["email"]
        try:
            otp = issue_otp(email)
        except OTPRateLimitExceeded as error:
            return Response(
                {
                    "detail": "Please wait before requesting another OTP.",
                    "retry_after": error.retry_after,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(error.retry_after)},
            )

        # Send OTP to email
        try:
            send_otp_email(email, otp)
        except OTPDeliveryError:
            return Response(
                {
                    "detail": (
                        "Unable to send the verification code. "
                        "Please try again later."
                    ),
                    "retry_after": settings.OTP_RESEND_COOLDOWN_SECONDS,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={
                    "Retry-After": str(settings.OTP_RESEND_COOLDOWN_SECONDS),
                },
            )

        return Response(
            {
                "email": email,
                "message": "OTP sent successfully.",
                "retry_after": settings.OTP_RESEND_COOLDOWN_SECONDS,
            },
            status=status.HTTP_200_OK,
        )


class VerifyOTPView(APIView):

    permission_classes = [AllowAny]

    def post(self, request: Request):

        serializer = VerifyOTPSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        registration_token = issue_registration_token(
            serializer.validated_data["email"]
        )
        response = Response(
            {"message": "Email verified successfully."},
            status=status.HTTP_200_OK,
        )
        response.set_cookie(
            key=REGISTRATION_TOKEN_COOKIE_NAME,
            value=registration_token,
            httponly=True,
            secure=is_secure,
            samesite="Lax",
            max_age=settings.REGISTRATION_TOKEN_EXPIRATION_SECONDS,
            path="/api/auth/register/",
        )
        return response


class RegisterView(APIView):

    permission_classes = [AllowAny]

    def post(self, request: Request):

        serializer = RegisterSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        return Response({"message": "Account successfully created"}, status=status.HTTP_200_OK)
