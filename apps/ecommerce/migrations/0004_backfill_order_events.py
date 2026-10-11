from django.db import migrations


def backfill_events(apps, schema_editor):
    """Preserve only timestamps already stored before timeline recording existed."""
    Order = apps.get_model('ecommerce', 'Order')
    Event = apps.get_model('ecommerce', 'OrderEvent')
    database = schema_editor.connection.alias
    rows = []
    # Skip orders already logged by the application or a previous backfill run.
    orders = Order.objects.using(database).filter(events__isnull=True).select_related('refund__reversal')
    for order in orders.iterator(chunk_size=500):
        facts = [('placed', order.created_at)]
        if order.paid_at:
            facts.append(('payment_confirmed', order.paid_at))
        if order.status in ('cancelled', 'delivered', 'completed') and order.concluded_at:
            facts.append((order.status, order.concluded_at))
        refund = getattr(order, 'refund', None)
        if refund:
            facts.append(('refund_requested', refund.created_at))
            if refund.status == 'succeeded' and refund.reversal_id:
                facts.append(('refund_succeeded', refund.reversal.posted_at))
        for event_type, occurred_at in facts:
            rows.append(Event(order_id=order.pk, event_type=event_type, occurred_at=occurred_at,
                              actor_role='unknown', is_backfilled=True))
        if len(rows) >= 1000:
            Event.objects.using(database).bulk_create(rows)
            rows = []
    Event.objects.using(database).bulk_create(rows)


class Migration(migrations.Migration):
    dependencies = [
        ('ecommerce', '0003_orderevent'),
        ('checkout', '0004_orderrefund'),
    ]
    operations = [migrations.RunPython(backfill_events, migrations.RunPython.noop)]
