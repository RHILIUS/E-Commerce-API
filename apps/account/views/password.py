"""Authenticated password-change endpoints with bounded IP traffic."""
import math

from django.db import DatabaseError
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.authentication.openapi import MessageResponseSerializer
from ..serializers.password import (
    PasswordChangeChallengeSerializer, PasswordChangeConfirmSerializer,
    PasswordChangeErrorSerializer, PasswordChangeRequestSerializer, PasswordChangeResendSerializer,
)
from ..services.password import PasswordChangeUnavailable, change_password


class PasswordChangeIPThrottle(SimpleRateThrottle):
    scope = "password_change_ip"
    rate = "20/min"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PasswordChangeView(APIView):
    throttle_classes = [PasswordChangeIPThrottle]

    def throttled(self, request, wait):
        raise PasswordChangeUnavailable("rate_limited", "Too many requests. Please wait and try again.", 429, max(1, math.ceil(wait or 60)))

    def perform(self, request, action, serializer_class):
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data = change_password(request.user.pk, action, serializer.validated_data)
        except DatabaseError:
            return Response({"detail": "We couldn't complete the request. Please try again later.",
                             "code": "operation_unavailable"}, status=503)
        except PasswordChangeUnavailable as error:
            response = Response(error.detail, status=error.status_code)
            if "retry_after" in error.detail:
                response["Retry-After"] = str(error.detail["retry_after"])
            return response
        response = Response(data)
        if action == "confirm":
            response.delete_cookie("refresh_token", path="/", samesite="Lax")
        return response


ERRORS = {400: PasswordChangeErrorSerializer, 401: PasswordChangeErrorSerializer,
          429: PasswordChangeErrorSerializer, 503: PasswordChangeErrorSerializer}


class PasswordChangeRequestView(PasswordChangeView):
    @extend_schema(tags=["Password change"], request=PasswordChangeRequestSerializer,
                   responses={200: PasswordChangeChallengeSerializer, **ERRORS},
                   description="Validate current and new passwords, issue a user-bound OTP and email it to the registered account. Account limits persist across restarts. No password is changed.")
    def post(self, request):
        return self.perform(request, "request", PasswordChangeRequestSerializer)


class PasswordChangeResendView(PasswordChangeView):
    @extend_schema(tags=["Password change"], request=PasswordChangeResendSerializer,
                   responses={200: PasswordChangeChallengeSerializer, **ERRORS},
                   description="Replace the owned challenge's code. The previous code is invalidated even if delivery fails. Preserve failed attempts and account limits.")
    def post(self, request):
        return self.perform(request, "resend", PasswordChangeResendSerializer)


class PasswordChangeConfirmView(PasswordChangeView):
    @extend_schema(tags=["Password change"], request=PasswordChangeConfirmSerializer,
                   responses={200: MessageResponseSerializer, **ERRORS},
                   description="Consume an owned, valid OTP and atomically change the password and authentication version. Old access and refresh tokens fail. Clear the HttpOnly refresh_token cookie and attempt a post-commit notification; notification failure does not undo success.")
    def post(self, request):
        return self.perform(request, "confirm", PasswordChangeConfirmSerializer)
