"""Explicit request and response contracts for account password changes."""
from rest_framework import serializers


class PasswordChangeRequestSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8, max_length=64)


class PasswordChangeResendSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()


class PasswordChangeConfirmSerializer(PasswordChangeRequestSerializer):
    challenge_id = serializers.UUIDField()
    otp = serializers.RegexField(r"^[0-9]{6}$", write_only=True, trim_whitespace=False, min_length=6, max_length=6)


class PasswordChangeChallengeSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    masked_email = serializers.CharField()
    expires_at = serializers.DateTimeField()
    retry_after = serializers.IntegerField()


class PasswordChangeErrorSerializer(serializers.Serializer):
    detail = serializers.CharField(required=False)
    code = serializers.CharField(required=False)
    retry_after = serializers.IntegerField(required=False)
    current_password = serializers.ListField(child=serializers.CharField(), required=False)
    new_password = serializers.ListField(child=serializers.CharField(), required=False)
    otp = serializers.ListField(child=serializers.CharField(), required=False)
    challenge_id = serializers.ListField(child=serializers.CharField(), required=False)
