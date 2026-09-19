from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.common.images import delete_file_after_commit

from .models import ProductImage


def _schedule_product_image_deletion(image):
    """Delete a product image after commit when no row still references its name."""
    name = image.name
    storage = image.storage
    delete_file_after_commit(
        storage,
        name,
        lambda: ProductImage.objects.filter(image=name).exists(),
    )


@receiver(pre_save, sender=ProductImage)
def remember_replaced_product_image(sender, instance, **kwargs):
    instance._replaced_product_image = None
    if not instance.pk:
        return

    try:
        previous = sender.objects.only("image").get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    if previous.image.name and previous.image.name != instance.image.name:
        instance._replaced_product_image = previous.image


@receiver(post_save, sender=ProductImage)
def delete_replaced_product_image(sender, instance, **kwargs):
    image = getattr(instance, "_replaced_product_image", None)
    if image is not None:
        _schedule_product_image_deletion(image)
        instance._replaced_product_image = None


@receiver(post_delete, sender=ProductImage)
def delete_product_image(sender, instance, **kwargs):
    if instance.image.name:
        _schedule_product_image_deletion(instance.image)
