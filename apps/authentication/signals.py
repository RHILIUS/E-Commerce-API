"""Delete replaced/removed user photos only after successful database commits."""
from django.db.models import Q
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver

from core.common.images import delete_file_after_commit
from .models import User

PHOTO_FIELDS = ("profile_image", "profile_image_original")


def _schedule_deletion(image):
    """Preserve files still referenced by another account or by its other photo field."""
    name = image.name
    delete_file_after_commit(image.storage, name, lambda: User.objects.filter(
        Q(profile_image=name) | Q(profile_image_original=name)).exists())


@receiver(pre_save, sender=User)
def remember_replaced_photos(sender, instance, update_fields=None, **kwargs):
    instance._replaced_photos = []
    if not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).first()
    if previous:
        instance._replaced_photos = [getattr(previous, field) for field in PHOTO_FIELDS
            if (update_fields is None or field in update_fields) and getattr(previous, field).name
            and getattr(previous, field).name != getattr(instance, field).name]


@receiver(post_save, sender=User)
def delete_replaced_photos(sender, instance, **kwargs):
    for image in getattr(instance, "_replaced_photos", ()):
        _schedule_deletion(image)
    instance._replaced_photos = []


@receiver(post_delete, sender=User)
def delete_user_photos(sender, instance, **kwargs):
    for field in PHOTO_FIELDS:
        image = getattr(instance, field)
        if image.name:
            _schedule_deletion(image)
