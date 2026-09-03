from rest_framework import status
from rest_framework.exceptions import APIException


class ExpiredOTPError(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = {"email": ["Expired or invalid OTP"]}
    default_code = "otp_expired"


class MaxAttemptError(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = {
        "otp": ["You have exceeded the maximum attempts. Please request a new OTP"]}
    default_code = "max_attempts_exceeded"
