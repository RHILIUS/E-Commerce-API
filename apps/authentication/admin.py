from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, UserAddress


class UserAddressInline(admin.StackedInline):
    model = UserAddress
    extra = 0
    max_num = 1


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "email",
        "username",
        "full_name",
        "phone_number",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_active", "address__country_code")
    search_fields = ("email", "username", "full_name", "phone_number")
    ordering = ("email",)
    inlines = (UserAddressInline,)
    fieldsets = UserAdmin.fieldsets + (
        ("Contact details", {"fields": ("full_name", "phone_number")}),
    )
    readonly_fields = ("profile_image", "profile_image_original", "profile_image_crop")
    fieldsets += (("Profile photo (managed through the account API)", {"fields": readonly_fields}),)
    readonly_fields += ("authentication_version",)
    fieldsets += (("Session security", {"fields": ("authentication_version",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Contact details", {"fields": ("email", "full_name", "phone_number")}),
    )


from .models import PasswordResetState


@admin.register(PasswordResetState)
class PasswordResetStateAdmin(admin.ModelAdmin):
    """Allow diagnostics without exposing hashes or permitting manual authorization edits."""
    list_display = ("id", "user", "otp_consumed", "expires_at", "failed_attempts", "updated_at")
    list_filter = ("otp_consumed",)
    fields = ("id", "user", "otp_consumed", "expires_at", "last_sent_at", "failed_attempts",
              "send_count", "request_count", "verification_count", "window_started_at",
              "grant_expires_at", "created_at", "updated_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
