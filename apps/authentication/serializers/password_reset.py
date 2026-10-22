"""Public password-reset request, success and validation-error contracts."""
from rest_framework import serializers


class ResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        """Use the same normalized key for requests, resends and verification."""
        return value.strip().lower()


class ResetResendSerializer(ResetRequestSerializer):
    challenge_id = serializers.UUIDField()


class ResetVerifySerializer(ResetResendSerializer):
    otp = serializers.RegexField(r"^[0-9]{6}$", min_length=6, max_length=6, trim_whitespace=False, write_only=True)


class ResetConfirmSerializer(serializers.Serializer):
    new_password = serializers.CharField(min_length=8, max_length=64, trim_whitespace=False, write_only=True)


class ResetChallengeSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    expires_at = serializers.DateTimeField()
    retry_after = serializers.IntegerField()
    message = serializers.CharField()


class ResetVerifiedSerializer(serializers.Serializer):
    expires_at = serializers.DateTimeField()
    message = serializers.CharField()


class ResetCSRFSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class ResetErrorSerializer(serializers.Serializer):
    detail = serializers.CharField(required=False)
    code = serializers.CharField(required=False)
    retry_after = serializers.IntegerField(required=False)
    email = serializers.ListField(child=serializers.CharField(), required=False)
    challenge_id = serializers.ListField(child=serializers.CharField(), required=False)
    otp = serializers.ListField(child=serializers.CharField(), required=False)
    new_password = serializers.ListField(child=serializers.CharField(), required=False)
