"""Anonymous password reset with explicit CSRF checks even without a login session."""
import math

from django.conf import settings
from django.db import DatabaseError
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.authentication.openapi import MessageResponseSerializer
from apps.authentication.serializers.password_reset import (
    ResetChallengeSerializer, ResetConfirmSerializer, ResetCSRFSerializer,
    ResetErrorSerializer, ResetRequestSerializer, ResetResendSerializer,
    ResetVerifiedSerializer, ResetVerifySerializer,
)
from apps.authentication.services.password_reset import (
    COOKIE_NAME, COOKIE_PATH, ResetError, confirm_reset, request_code, verify_code,
)


class ResetIPThrottle(SimpleRateThrottle):
    scope = "password_reset_ip"
    rate = "20/min"

    def get_cache_key(self, request, view):
        """Share the IP budget across all recovery operations."""
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


@method_decorator(never_cache, name="dispatch")
class ResetView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ResetIPThrottle]

    def initial(self, request, *args, **kwargs):
        """Do not rely on DRF's anonymous/session authentication to enforce CSRF."""
        super().initial(request, *args, **kwargs)
        if request.method == "POST":
            check = CSRFCheck(lambda req: None)
            check.process_request(request)
            reason = check.process_view(request, None, (), {})
            if reason:
                raise PermissionDenied("Security check failed. Reload this page and try again.", code="csrf_failed")

    def throttled(self, request, wait):
        raise ResetError("rate_limited", "Too many requests. Please wait and try again.", 429, max(1, math.ceil(wait or 60)))

    def handle_exception(self, exc):
        """Keep reset errors and Retry-After consistent, including throttle failures."""
        response = super().handle_exception(exc)
        if isinstance(exc, ResetError) and "retry_after" in exc.detail:
            response["Retry-After"] = str(exc.detail["retry_after"])
        return response

    def perform(self, request, serializer_class, action):
        """Validate one exact contract and keep database failures out of public responses."""
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if action in ("request", "resend"):
                return Response(request_code(**data))
            if action == "verify":
                cookie, expires_at = verify_code(**data)
                response = Response({"expires_at": expires_at, "message": "Email verified. Choose a new password."})
                response.set_cookie(COOKIE_NAME, cookie, max_age=settings.PASSWORD_RESET_GRANT_SECONDS,
                                    path=COOKIE_PATH, httponly=True, secure=not settings.DEBUG, samesite="Lax")
                return response
            confirm_reset(request.COOKIES.get(COOKIE_NAME, ""), data["new_password"])
            response = Response({"message": "Password reset successfully. Sign in with your new password."})
            response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")
            response.delete_cookie("refresh_token", path="/", samesite="Lax")
            return response
        except DatabaseError:
            return Response({"detail": "We couldn't complete the request. Please try again later.",
                             "code": "operation_unavailable"}, status=503)


ERRORS = {400: ResetErrorSerializer, 403: ResetErrorSerializer, 429: ResetErrorSerializer, 503: ResetErrorSerializer}
CSRF_HEADER = OpenApiParameter("X-CSRFToken", str, OpenApiParameter.HEADER, required=True,
                               description="Masked token from the recovery bootstrap response; browser must also send its CSRF cookie.")


class ResetBootstrapView(ResetView):
    @extend_schema(tags=["Password recovery"], auth=[], responses={200: ResetCSRFSerializer, 429: ResetErrorSerializer},
                   description="Initialize the CSRF cookie and return a masked CSRF token. Does not authorize password reset. Responses must not be cached.")
    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class ResetRequestView(ResetView):
    @extend_schema(tags=["Password recovery"], auth=[], parameters=[CSRF_HEADER], request=ResetRequestSerializer,
                   responses={200: ResetChallengeSerializer, **ERRORS},
                   description="Request a reset code. Known, unknown and ineligible emails receive equivalent challenges, cooldowns and errors. Queue the same task shape after commit; the worker sends only to eligible accounts. Invalidates an earlier grant for this email.")
    def post(self, request):
        return self.perform(request, ResetRequestSerializer, "request")


class ResetResendView(ResetView):
    @extend_schema(tags=["Password recovery"], auth=[], parameters=[CSRF_HEADER], request=ResetResendSerializer,
                   responses={200: ResetChallengeSerializer, **ERRORS},
                   description="Replace the identified code, rotate the challenge UUID and invalidate prior reset permission. Preserve the email's attempts and durable budgets. Old codes remain invalid if queuing fails.")
    def post(self, request):
        return self.perform(request, ResetResendSerializer, "resend")


class ResetVerifyView(ResetView):
    @extend_schema(tags=["Password recovery"], auth=[], parameters=[CSRF_HEADER], request=ResetVerifySerializer,
                   responses={200: ResetVerifiedSerializer, **ERRORS},
                   description="Consume a valid OTP and set a ten-minute, single-use password_reset HttpOnly cookie scoped to /api/auth/forgot-password/. No secret is returned in JSON. No password or login session changes yet.")
    def post(self, request):
        return self.perform(request, ResetVerifySerializer, "verify")


class ResetConfirmView(ResetView):
    @extend_schema(tags=["Password recovery"], auth=[], parameters=[CSRF_HEADER,
                   OpenApiParameter(COOKIE_NAME, str, OpenApiParameter.COOKIE, required=True)],
                   request=ResetConfirmSerializer, responses={200: MessageResponseSerializer, **ERRORS},
                   description="Require CSRF and the reset cookie. Under account/state locks, atomically save password, consume permission and increment authentication version. Clear reset/refresh cookies and queue notification after commit. Never automatically replay an uncertain response.")
    def post(self, request):
        return self.perform(request, ResetConfirmSerializer, "confirm")
