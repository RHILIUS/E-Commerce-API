"""Invalidate every JWT session when the account authentication version changes."""
from django.contrib.auth import get_user_model
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer, TokenVerifySerializer
from rest_framework_simplejwt.tokens import UntypedToken


def check_authentication_version(user, token):
    """Accept legacy versionless tokens only while the account is still version zero."""
    version = token.get("auth_version", 0)
    if type(version) is not int or version != user.authentication_version or not user.is_active:
        raise InvalidToken("Your session has expired. Please sign in again.")


class VersionedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        check_authentication_version(user, validated_token)
        return user


class VersionedTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = self.token_class(attrs["refresh"])
        try:
            user = get_user_model().objects.get(pk=token["user_id"])
        except (get_user_model().DoesNotExist, KeyError) as error:
            raise InvalidToken("Account unavailable.") from error
        check_authentication_version(user, token)
        # Preserve the checked version when a legacy token is rotated.
        token["auth_version"] = user.authentication_version
        return super().validate({"refresh": str(token)})


class VersionedTokenVerifySerializer(TokenVerifySerializer):
    def validate(self, attrs):
        token = UntypedToken(attrs["token"])
        try:
            user = get_user_model().objects.get(pk=token["user_id"])
        except (get_user_model().DoesNotExist, KeyError) as error:
            raise InvalidToken("Account unavailable.") from error
        check_authentication_version(user, token)
        return super().validate(attrs)


class VersionedJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.authentication.token_authentication.VersionedJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
