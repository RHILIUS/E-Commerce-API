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
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Contact details", {"fields": ("email", "full_name", "phone_number")}),
    )
