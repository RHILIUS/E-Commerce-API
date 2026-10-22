"""Explicit contracts for authenticated email-verified password changes."""
from rest_framework import serializers


class PasswordChangeRequestSerializer(serializers.Serializer):
    pass


class PasswordChangeResendSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()


class PasswordChangeVerifySerializer(PasswordChangeResendSerializer):
    otp = serializers.RegexField(r"\A[0-9]{6}\Z", write_only=True, trim_whitespace=False, min_length=6, max_length=6)


class PasswordChangeConfirmSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8, max_length=64)


class PasswordChangeBootstrapSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()
    masked_email = serializers.CharField()


class PasswordChangeVerifiedSerializer(serializers.Serializer):
    expires_at = serializers.DateTimeField()
    message = serializers.CharField()


class PasswordChangeChallengeSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    masked_email = serializers.CharField()
    expires_at = serializers.DateTimeField()
    retry_after = serializers.IntegerField()


class PasswordChangeErrorSerializer(serializers.Serializer):
    detail = serializers.CharField(required=False)
    code = serializers.CharField(required=False)
    retry_after = serializers.IntegerField(required=False)
    new_password = serializers.ListField(child=serializers.CharField(), required=False)
    otp = serializers.ListField(child=serializers.CharField(), required=False)
    challenge_id = serializers.ListField(child=serializers.CharField(), required=False)
