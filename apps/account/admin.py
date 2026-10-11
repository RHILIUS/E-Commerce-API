"""Staff recipient management using checkout validation and transactional writes."""

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import RecipientInfo
from .serializers import RecipientSerializer
from .services import create_recipient, update_recipient


class RecipientAdminForm(forms.ModelForm):
    class Meta:
        model = RecipientInfo
        fields = "__all__"
        help_texts = {
            "phone_number": "Use international format, for example +639171234567.",
            "country_code": "Two-letter delivery country, for example PH.",
            "is_default": "To replace a default, make another recipient default instead of clearing this one.",
        }

    def _get_validation_exclusions(self):
        """Defer the default constraint to the service's atomic replacement.

        Pre-save constraint validation would reject a new default before the
        service clears the previous one. The database constraint remains active.
        """
        return super()._get_validation_exclusions() | {"is_default"}

    def clean(self):
        """Normalize API fields and lock the customer through admin's POST transaction."""
        cleaned = super().clean()
        if self.errors:
            return cleaned
        user = self.instance.user if self.instance.pk else cleaned["user"]
        # Admin wraps POST validation, service saves and logging in one transaction.
        get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
        serializer = RecipientSerializer(data={
            name: cleaned[name] for name in RecipientSerializer.Meta.fields if name != "id"
        })
        if not serializer.is_valid():
            raise ValidationError({
                name: [str(message) for message in messages]
                for name, messages in serializer.errors.items()
            })
        if self.instance.pk:
            current = RecipientInfo.objects.get(pk=self.instance.pk, user_id=user.pk)
            if current.is_default and not serializer.validated_data["is_default"]:
                raise ValidationError({"is_default": "Choose another default recipient before replacing this default."})
        cleaned.update(serializer.validated_data)
        return cleaned


@admin.register(RecipientInfo)
class RecipientInfoAdmin(admin.ModelAdmin):
    form = RecipientAdminForm
    list_display = ("name", "user", "phone_number", "city", "country_code", "is_default")
    list_select_related = ("user",)
    search_fields = ("name", "phone_number", "user__email", "user__full_name",
                     "address_line1", "address_line2", "city", "region", "postal_code")
    list_filter = ("country_code", "is_default")
    autocomplete_fields = ("user",)
    ordering = ("user__email", "-is_default", "pk")
    readonly_fields = ("id",)
    fieldsets = (
        ("Customer", {"fields": ("id", "user", "is_default")}),
        ("Contact", {"fields": ("name", "phone_number")}),
        ("Address", {"fields": ("country_code", "address_line1", "address_line2",
                                "city", "region", "postal_code")}),
    )

    def get_readonly_fields(self, request, obj=None):
        """Keep saved recipient ownership fixed after creation."""
        return (*self.readonly_fields, "user") if obj else self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """Save through shared services and retain the saved instance for admin logs."""
        data = {name: form.cleaned_data[name] for name in RecipientSerializer.Meta.fields if name != "id"}
        saved = update_recipient(obj.user, obj.pk, data) if change else create_recipient(obj.user, data)
        # Admin logging and related-form saving use this original form instance.
        for field in obj._meta.concrete_fields:
            setattr(obj, field.attname, getattr(saved, field.attname))
        obj._state.adding = False
        obj._state.db = saved._state.db
