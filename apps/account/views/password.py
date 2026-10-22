"""Authenticated password changes with explicit CSRF checks and isolated permission."""
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
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.authentication.openapi import MessageResponseSerializer
from ..serializers.password import (
    PasswordChangeBootstrapSerializer, PasswordChangeChallengeSerializer,
    PasswordChangeConfirmSerializer, PasswordChangeErrorSerializer,
    PasswordChangeRequestSerializer, PasswordChangeResendSerializer,
    PasswordChangeVerifiedSerializer, PasswordChangeVerifySerializer,
)
from ..services.password import (
    COOKIE_NAME, COOKIE_PATH, PasswordChangeUnavailable, change_password,
    confirm_password, masked_email,
)


class PasswordChangeIPThrottle(SimpleRateThrottle):
    scope = "password_change_ip"
    rate = "20/min"

    def get_cache_key(self, request, view):
        """Share the guard across bootstrap and all mutations; ignore untrusted forwarded headers."""
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


@method_decorator(never_cache, name="dispatch")
class PasswordChangeView(APIView):
    throttle_classes = [PasswordChangeIPThrottle]

    def initial(self, request, *args, **kwargs):
        """Require CSRF in addition to the project's default Bearer authentication."""
        super().initial(request, *args, **kwargs)
        if request.method == "POST":
            check = CSRFCheck(lambda req: None)
            check.process_request(request)
            reason = check.process_view(request, None, (), {})
            if reason:
                raise PermissionDenied("Security check failed. Reload this page and try again.", code="csrf_failed")

    def throttled(self, request, wait):
        raise PasswordChangeUnavailable("rate_limited", "Too many requests. Please wait and try again.",
                                        429, max(1, math.ceil(wait or 60)))

    def handle_exception(self, exc):
        """Keep retry timing and stale-cookie cleanup consistent for all workflow errors."""
        response = super().handle_exception(exc)
        if isinstance(exc, PasswordChangeUnavailable):
            if "retry_after" in exc.detail:
                response["Retry-After"] = str(exc.detail["retry_after"])
            if exc.detail["code"] == "change_unavailable":
                response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")
        return response

    def perform(self, request, action, serializer_class):
        """Validate the operation then delegate locked state transitions to the service."""
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if action == "confirm":
                confirm_password(request.user.pk, request.COOKIES.get(COOKIE_NAME, ""), data["new_password"])
                response = Response({"message": "Password changed successfully. Sign in with your new password."})
                response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")
                response.delete_cookie("refresh_token", path="/", samesite="Lax")
                return response
            result = change_password(request.user.pk, action, data)
            if action == "verify":
                cookie, expires_at = result
                response = Response({"expires_at": expires_at, "message": "Email verified. Choose a new password."})
                response.set_cookie(COOKIE_NAME, cookie, max_age=settings.PASSWORD_CHANGE_GRANT_SECONDS,
                                    path=COOKIE_PATH, httponly=True, secure=not settings.DEBUG, samesite="Lax")
                return response
            response = Response(result)
            response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")
            return response
        except DatabaseError:
            return Response({"detail": "We couldn't complete the request. Please try again later.",
                             "code": "operation_unavailable"}, status=503)


ERRORS = {400: PasswordChangeErrorSerializer, 401: PasswordChangeErrorSerializer,
          403: PasswordChangeErrorSerializer, 429: PasswordChangeErrorSerializer, 503: PasswordChangeErrorSerializer}
CSRF_HEADER = OpenApiParameter("X-CSRFToken", str, OpenApiParameter.HEADER, required=True,
                               description="Masked token from authenticated bootstrap; include the CSRF cookie.")


class PasswordChangeBootstrapView(PasswordChangeView):
    @extend_schema(tags=["Password change"], responses={200: PasswordChangeBootstrapSerializer,
                   401: PasswordChangeErrorSerializer, 429: PasswordChangeErrorSerializer},
                   description="Require authentication, set the CSRF cookie and return its masked token and the fixed masked account email. Never issue a code or invalidate permission. No caching.")
    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrf_token": get_token(request), "masked_email": masked_email(request.user.email)})


class PasswordChangeRequestView(PasswordChangeView):
    @extend_schema(tags=["Password change"], parameters=[CSRF_HEADER], request=PasswordChangeRequestSerializer,
                   responses={200: PasswordChangeChallengeSerializer, **ERRORS},
                   description="Require authentication and CSRF. Issue a code to the signed-in account's fixed email, rotating the challenge UUID and invalidating prior permission. Preserve durable attempts and limits. Queue after commit; success is broker acceptance, not delivery. No password input or mutation.")
    def post(self, request):
        return self.perform(request, "request", PasswordChangeRequestSerializer)


class PasswordChangeResendView(PasswordChangeView):
    @extend_schema(tags=["Password change"], parameters=[CSRF_HEADER], request=PasswordChangeResendSerializer,
                   responses={200: PasswordChangeChallengeSerializer, **ERRORS},
                   description="Require authentication, CSRF and the current owned challenge. Rotate code and UUID and invalidate earlier permission. Attempts and budgets survive resend; old authorization stays invalid even if email queuing fails.")
    def post(self, request):
        return self.perform(request, "resend", PasswordChangeResendSerializer)


class PasswordChangeVerifyView(PasswordChangeView):
    @extend_schema(tags=["Password change"], parameters=[CSRF_HEADER], request=PasswordChangeVerifySerializer,
                   responses={200: PasswordChangeVerifiedSerializer, **ERRORS},
                   description="Require authentication and CSRF. Consume the owned OTP and set ten-minute single-use password_change permission in an HttpOnly cookie scoped to /api/account/change-password/. No secret in JSON. Password and sessions do not change.")
    def post(self, request):
        return self.perform(request, "verify", PasswordChangeVerifySerializer)


class PasswordChangeConfirmView(PasswordChangeView):
    @extend_schema(tags=["Password change"], parameters=[CSRF_HEADER,
                   OpenApiParameter(COOKIE_NAME, str, OpenApiParameter.COOKIE, required=True)],
                   request=PasswordChangeConfirmSerializer, responses={200: MessageResponseSerializer, **ERRORS},
                   description="Require authentication, CSRF and this account's separate change permission. Under account/state locks, atomically save password, consume permission and increment authentication version. Clear change/refresh cookies; queue notification after commit. Never automatically replay an uncertain response.")
    def post(self, request):
        return self.perform(request, "confirm", PasswordChangeConfirmSerializer)
