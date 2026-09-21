from rest_framework import serializers

from apps.authentication.serializers import UserPublicSerializer


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    user = UserPublicSerializer()


class RefreshResponseSerializer(LoginResponseSerializer):
    pass


class VerifyTokenRequestSerializer(serializers.Serializer):
    token = serializers.CharField()


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class DetailResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class SendOTPResponseSerializer(MessageResponseSerializer):
    email = serializers.EmailField()
    retry_after = serializers.IntegerField()


class RetryErrorResponseSerializer(DetailResponseSerializer):
    retry_after = serializers.IntegerField()
