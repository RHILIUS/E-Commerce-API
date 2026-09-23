from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def split_store_carts(apps, schema_editor):
    """Preserve item IDs and quantities while splitting legacy carts by store."""
    Cart = apps.get_model('ecommerce', 'Cart')
    Item = apps.get_model('ecommerce', 'CartItem')
    alias = schema_editor.connection.alias
    if Item.objects.using(alias).filter(quantity=0).exists():
        raise ValueError('Resolve zero-quantity cart items before applying store carts.')
    for cart in Cart.objects.using(alias).all().iterator():
        store_ids = list(Item.objects.using(alias).filter(cart_id=cart.pk)
                         .values_list('product__store_id', flat=True).distinct().order_by('product__store_id'))
        if not store_ids:
            cart.delete(using=alias)
            continue
        cart.store_id = store_ids[0]
        cart.save(using=alias, update_fields=['store'])
        for store_id in store_ids[1:]:
            target = Cart.objects.using(alias).create(user_id=cart.user_id, store_id=store_id)
            Item.objects.using(alias).filter(cart_id=cart.pk, product__store_id=store_id).update(cart_id=target.pk)
    # Flush PostgreSQL's deferred foreign-key checks before the following DDL.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def merge_store_carts(apps, schema_editor):
    """Merge store carts back into a single cart per user on rollback."""
    Cart = apps.get_model('ecommerce', 'Cart')
    Item = apps.get_model('ecommerce', 'CartItem')
    alias = schema_editor.connection.alias
    targets = {}
    for cart in Cart.objects.using(alias).order_by('pk').iterator():
        target = targets.setdefault(cart.user_id, cart.pk)
        if target != cart.pk:
            Item.objects.using(alias).filter(cart_id=cart.pk).update(cart_id=target)
            cart.delete(using=alias)

    # Rollback also alters the cart table after moving its referenced items.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):
    dependencies = [
        ('ecommerce', '0012_order_concluded_at_order_recipient_info_and_more'),
        ('store', '0004_store_currency'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(model_name='cart', name='user', field=models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE, related_name='carts')),
        migrations.AddField(model_name='cart', name='store', field=models.ForeignKey(to='store.store', on_delete=django.db.models.deletion.CASCADE, related_name='carts', null=True)),
        migrations.RunPython(split_store_carts, merge_store_carts),
        migrations.AlterField(model_name='cart', name='store', field=models.ForeignKey(to='store.store', on_delete=django.db.models.deletion.CASCADE, related_name='carts')),
        migrations.AddConstraint(model_name='cart', constraint=models.UniqueConstraint(fields=('user', 'store'), name='unique_user_store_cart')),
        migrations.AlterField(model_name='cartitem', name='quantity', field=models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])),
        migrations.AddConstraint(model_name='cartitem', constraint=models.CheckConstraint(condition=models.Q(quantity__gte=1), name='cart_item_quantity_positive')),
    ]
