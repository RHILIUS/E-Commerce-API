from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from apps.authentication.token_authentication import VersionedTokenRefreshSerializer, VersionedTokenVerifySerializer

from apps.authentication.openapi import (
    DetailResponseSerializer,
    LoginRequestSerializer,
    LoginResponseSerializer,
    MessageResponseSerializer,
    RefreshResponseSerializer,
    RetryErrorResponseSerializer,
    SendOTPResponseSerializer,
    VerifyTokenRequestSerializer,
)
from apps.authentication.serializers import (
    CustomTokenObtainPairSerializer,
    RegisterSerializer,
    SendOTPSerializer,
    UserPublicSerializer,
    VerifyOTPSerializer,
)
from apps.authentication.services.email_service import (
    OTPDeliveryError,
    send_otp_email,
)
from apps.authentication.services.otp_service import OTPRateLimitExceeded, issue_otp
from apps.authentication.services.registration_token_service import (
    REGISTRATION_TOKEN_COOKIE_NAME,
    issue_registration_token,
)
from apps.ecommerce.models import User

is_secure = not settings.DEBUG


class LogoutView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"], request=None, auth=[],
        parameters=[OpenApiParameter(
            "refresh_token", str, location=OpenApiParameter.COOKIE,
            description="Optional HttpOnly refresh-token cookie to revoke.",
        )],
        description="Clear the HttpOnly refresh-token cookie and blacklist its token when present.",
        responses={200: DetailResponseSerializer},
    )
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

    @extend_schema(
        tags=["Authentication"], request=LoginRequestSerializer, auth=[],
        description="Return a Bearer access token and public user data. The refresh token is set in an HttpOnly cookie and is never returned in the body.",
        responses={200: LoginResponseSerializer,
                   401: DetailResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

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
    serializer_class = VersionedTokenRefreshSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"], request=None, auth=[],
        parameters=[OpenApiParameter(
            "refresh_token", str, location=OpenApiParameter.COOKIE, required=True,
            description="HttpOnly refresh-token cookie set by login or the previous refresh.",
        )],
        description="Read the refresh_token HttpOnly cookie, validate the active user and authentication version, rotate it when configured, and return a new access token and public user data. No refresh token is accepted in the request body or returned in the response body.",
        responses={200: RefreshResponseSerializer,
                   401: DetailResponseSerializer},
    )
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
        except (TokenError, InvalidToken) as e:
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
            user = User.objects.get(id=user_id)
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
             "user": UserPublicSerializer(user, context={"request": request}).data},
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

    @extend_schema(
        tags=["Authentication"], request=SendOTPSerializer, auth=[],
        description="Send a registration OTP to the supplied email. The OTP is never returned in the response.",
        responses={200: SendOTPResponseSerializer, 400: OpenApiTypes.OBJECT,
                   429: RetryErrorResponseSerializer, 503: RetryErrorResponseSerializer},
    )
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

    @extend_schema(
        tags=["Authentication"], request=VerifyOTPSerializer, auth=[],
        description="Verify the OTP and set an HttpOnly registration_token cookie scoped to /api/auth/register/. The cookie is not returned in the response body.",
        responses={200: MessageResponseSerializer, 400: OpenApiTypes.OBJECT,
                   410: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT},
    )
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

    @extend_schema(
        tags=["Authentication"], request=RegisterSerializer, auth=[],
        parameters=[OpenApiParameter(
            "registration_token", str, location=OpenApiParameter.COOKIE, required=True,
            description="HttpOnly registration cookie set by OTP verification.",
        )],
        description="Create a user, personal address and default recipient atomically using the verified registration_token HttpOnly cookie. Email comes from that cookie, not the request body. The cookie is cleared on success.",
        responses={201: MessageResponseSerializer, 400: OpenApiTypes.OBJECT,
                   401: DetailResponseSerializer},
    )
    def post(self, request: Request):

        serializer = RegisterSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        response = Response(
            {"message": "Account successfully created"},
            status=status.HTTP_201_CREATED,
        )
        # Clean-up registration_token
        response.delete_cookie(
            key=REGISTRATION_TOKEN_COOKIE_NAME,
            path="/api/auth/register/",
        )
        return response


class VerifyAccessTokenView(TokenVerifyView):
    serializer_class = VersionedTokenVerifySerializer
    @extend_schema(
        tags=["Authentication"], request=VerifyTokenRequestSerializer, auth=[],
        description="Validate a JWT signature, expiry, active user and authentication version. Legacy versionless tokens are accepted only for accounts at version zero. Returns an empty object on success.",
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT,
                   401: DetailResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
