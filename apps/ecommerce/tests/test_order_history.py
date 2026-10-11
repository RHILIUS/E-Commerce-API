"""Order milestones remain atomic, private, immutable and accurate on historical data."""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.checkout.tests.test_placement import customer, product_request
from apps.ecommerce.models import Order, OrderEvent, Product
from apps.ecommerce.services.orders import cancel_order
from apps.store.models import Store
from core.test_support import create_purchase


class OrderHistoryTests(APITestCase):
    def setUp(self):
        self.user, self.recipient = customer(1)
        self.other, self.other_recipient = customer(2)
        self.store = Store.objects.create(name='Store', country_code='PH')
        self.product = Product.objects.create(store=self.store, name='Camera', price='12.34', stock=10, status='active')
        self.client.force_authenticate(self.user)

    def purchase(self):
        """Make a complete fixture; ordinary order saves still record their milestones."""
        return create_purchase(user=self.user, recipient=self.recipient, product=self.product)[0]

    def types(self, order):
        return list(order.events.values_list('event_type', flat=True))

    def test_placement_and_cancellation_capture_customer_once(self):
        body = product_request(self.client, self.product, self.recipient)
        first = self.client.post('/api/shop/checkouts/', body, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.client.post('/api/shop/checkouts/', body, format='json')
        order = Order.objects.get(pk=first.data['orders'][0]['id'])
        placed = order.events.get()
        self.assertEqual(placed.event_type, 'placed')
        self.assertEqual(placed.actor_id, self.user.pk)
        self.assertEqual(placed.actor_role, 'customer')
        self.assertEqual(placed.occurred_at, order.created_at)
        for _ in range(2): cancel_order(self.user, order.pk)
        self.assertEqual(self.types(order), ['placed', 'cancelled'])
        order.refresh_from_db()
        self.assertEqual(order.events.get(event_type='cancelled').occurred_at, order.concluded_at)

    def test_every_saved_fulfillment_milestone_has_its_own_time(self):
        order = self.purchase()
        stamps = []
        for status in ['accepted', 'packed', 'shipment_arranged', 'shipped', 'delivered', 'completed']:
            timestamp = timezone.now()
            stamps.append(timestamp)
            order.status = status
            order.save(update_fields=['status'], event_actor=self.other, event_actor_role='seller', event_at=timestamp)
            order.save(update_fields=['status'])  # No new transition.
        self.assertEqual(self.types(order), ['placed', 'accepted', 'packed', 'shipment_arranged', 'shipped', 'delivered', 'completed'])
        events = list(order.events.exclude(event_type='placed'))
        self.assertEqual([e.occurred_at for e in events], stamps)
        self.assertTrue(all(e.actor_id == self.other.pk and e.actor_role == 'seller' for e in events))
        self.assertEqual(order.events.get(event_type='delivered').occurred_at, stamps[-2])
        order.refresh_from_db(); self.assertEqual(order.concluded_at, stamps[-1])

    def test_unsaved_status_and_unrelated_updates_do_not_log(self):
        order = self.purchase()
        order.status = 'accepted'
        order.save(update_fields=['updated_at'])
        self.assertEqual(self.types(order), ['placed'])
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')

    def test_event_failure_rolls_back_status_and_outer_rollback_removes_event(self):
        order = self.purchase()
        order.status = 'accepted'
        with patch('apps.ecommerce.models.OrderEvent.objects.create', side_effect=RuntimeError('insertion failed')):
            with self.assertRaises(RuntimeError): order.save(update_fields=['status'])
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                cancel_order(self.user, order.pk)
                raise RuntimeError('outer rollback')
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')
        self.assertEqual(self.types(order), ['placed'])

    def test_payment_confirmation_is_logged_once(self):
        order = self.purchase()
        order.payment_status = 'paid'
        order.save(update_fields=['payment_status'])
        order.save(update_fields=['payment_status'])
        self.assertEqual(self.types(order), ['placed', 'payment_confirmed'])
        order.refresh_from_db()
        self.assertEqual(order.events.get(event_type='payment_confirmed').occurred_at, order.paid_at)

    def test_history_owner_scope_ordering_and_safe_fields(self):
        order = self.purchase()
        order.status = 'accepted'; order.save(update_fields=['status'])
        url = f'/api/shop/orders/{order.pk}/history/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([e['event_type'] for e in response.data['events']], ['placed', 'accepted'])
        self.assertFalse(response.data['is_partial'])
        self.assertEqual(set(response.data['events'][0]), {'id', 'event_type', 'label', 'occurred_at', 'actor_role', 'is_backfilled'})
        self.client.get(url)
        self.assertEqual(order.events.count(), 2)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_events_cannot_be_edited_or_deleted_through_public_orm(self):
        event = self.purchase().events.get()
        event.event_type = 'completed'
        with self.assertRaises(ValidationError): event.save()
        with self.assertRaises(ValidationError): event.delete()
        with self.assertRaises(ValidationError): OrderEvent.objects.filter(pk=event.pk).update(event_type='completed')
        with self.assertRaises(ValidationError): OrderEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(ValidationError): OrderEvent.objects.bulk_update([event], ['event_type'])

    def test_wrong_customer_attribution_rolls_back_the_status_change(self):
        order = self.purchase(); order.status = 'cancelled'
        with self.assertRaises(ValidationError):
            order.save(event_actor=self.other, event_actor_role='customer')
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')

    def test_backfill_uses_only_known_dates_and_marks_history_partial(self):
        with patch('apps.ecommerce.models.OrderEvent.objects.create'):
            completed = self.purchase()
            accepted = self.purchase()
        known = timezone.now()
        Order.objects.filter(pk=completed.pk).update(status='completed', concluded_at=known, paid_at=known)
        Order.objects.filter(pk=accepted.pk).update(status='accepted')
        apps = MigrationExecutor(connection).loader.project_state().apps
        migrate = import_module('apps.ecommerce.migrations.0004_backfill_order_events').backfill_events
        migrate(apps, SimpleNamespace(connection=connection))
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.types(completed), ['placed', 'payment_confirmed', 'completed'])
        self.assertEqual(self.types(accepted), ['placed'])
        self.assertTrue(all(e.actor_id is None and e.actor_role == 'unknown' and e.is_backfilled for e in completed.events.all()))
        self.assertEqual(completed.events.get(event_type='completed').occurred_at, known)
        response = self.client.get(f'/api/shop/orders/{completed.pk}/history/')
        self.assertTrue(response.data['is_partial'])
