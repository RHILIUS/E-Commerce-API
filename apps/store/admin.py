import phonenumbers
from django import forms
from django.contrib import admin
from phonenumbers import geocoder

from .models import Store, StoreMember


def country_name_for_region(region_code):
    """Return phonenumbers' English country name, or the region code as a fallback."""
    example_number = phonenumbers.example_number(region_code)
    if example_number is None:
        return region_code
    return geocoder.country_name_for_number(example_number, "en") or region_code


COUNTRY_CODE_CHOICES = tuple(sorted(
    (
        (region_code, f"{country_name_for_region(region_code)} ({region_code})")
        for region_code in phonenumbers.SUPPORTED_REGIONS
    ),
    key=lambda choice: choice[1],
))


class StoreAdminForm(forms.ModelForm):
    country_code = forms.ChoiceField(choices=COUNTRY_CODE_CHOICES)

    class Meta:
        model = Store
        fields = "__all__"


class StoreMemberInline(admin.TabularInline):
    model = StoreMember
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    form = StoreAdminForm
    list_display = ("name", "country_code", "currency", "created_at", "updated_at")
    list_filter = ("country_code", "currency")
    search_fields = ("name", "description")
    readonly_fields = ("currency", "created_at", "updated_at")
    inlines = (StoreMemberInline,)


@admin.register(StoreMember)
class StoreMemberAdmin(admin.ModelAdmin):
    list_display = ("user", "store", "role", "created_at")
    list_filter = ("role", "store")
    search_fields = ("user__email", "user__full_name", "store__name")
    autocomplete_fields = ("user", "store")
    readonly_fields = ("created_at", "updated_at")
