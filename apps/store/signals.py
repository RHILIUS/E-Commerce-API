from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.common.images import delete_file_after_commit

from .models import Store


def _schedule_store_image_deletion(image):
    """Delete a store image after commit when no store still references its name."""
    name = image.name
    storage = image.storage
    delete_file_after_commit(
        storage,
        name,
        lambda: Store.objects.filter(
            Q(profile_image=name) | Q(banner_image=name)
        ).exists(),
    )


@receiver(pre_save, sender=Store)
def remember_replaced_store_images(sender, instance, **kwargs):
    if not instance.pk:
        return

    try:
        previous = sender.objects.only("profile_image", "banner_image").get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    instance._replaced_store_images = [
        previous_image
        for field_name in ("profile_image", "banner_image")
        if (previous_image := getattr(previous, field_name)).name
        and previous_image.name != getattr(instance, field_name).name
    ]


@receiver(post_save, sender=Store)
def delete_replaced_store_images(sender, instance, **kwargs):
    for image in getattr(instance, "_replaced_store_images", ()):
        _schedule_store_image_deletion(image)
    instance._replaced_store_images = []


@receiver(post_delete, sender=Store)
def delete_store_images(sender, instance, **kwargs):
    for image in (instance.profile_image, instance.banner_image):
        if image.name:
            _schedule_store_image_deletion(image)
