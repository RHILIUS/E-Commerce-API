"""Customer isolation, tab semantics, cancellation stock and refund recovery."""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.checkout.models import OrderRefund, PaymentAttempt
from apps.checkout.services.refunds import reconcile_refund
from apps.checkout.tests.test_placement import customer, product_request
from apps.ecommerce.models import Order, Product
from apps.store.models import Store
from apps.wallet.models import LedgerTransaction
from apps.wallet.services import Posting, account_balance, create_wallet, get_treasury_account, post_transaction
from core.test_support import create_purchase

URL = '/api/shop/orders/'


class UserOrdersTests(APITestCase):
    def setUp(self):
        self.user, self.recipient = customer(1)
        self.other, self.other_recipient = customer(2)
        self.store = Store.objects.create(name='Snapshot store', country_code='US', currency='USD')
        self.product = Product.objects.create(store=self.store, name='Snapshot camera', price='12.34', stock=10, status='active')
        self.client.force_authenticate(self.user)

    def purchase(self, status='pending'):
        """Create saved purchases for read tests without pretending to move inventory."""
        return create_purchase(user=self.user, recipient=self.recipient, product=self.product, quantity=2, status=status)[0]

    def paid(self):
        """Model a verified test receipt with the same pending-store ledger allocation."""
        order = self.purchase()
        checkout = order.checkout
        # Fixture-only conversion happens before creating the payment attempt.
        type(checkout).objects.filter(pk=checkout.pk).update(payment_method='stripe')
        checkout.refresh_from_db()
        account = create_wallet(store_id=self.store.pk).accounts.get(bucket='pending')
        receipt = post_transaction(operation_key=f'receipt:{order.pk}', operation_type='payment_receipt', postings=[
            Posting(get_treasury_account().pk, 'debit', 2468), Posting(account.pk, 'credit', 2468)])
        attempt = PaymentAttempt.objects.create(checkout=checkout, status='succeeded', amount='24.68', currency='USD',
            stripe_payment_intent_id=f'pi_{order.pk}', receipt=receipt)
        Order.objects.filter(pk=order.pk).update(payment_status='paid')
        Product.objects.filter(pk=self.product.pk).update(stock=8)
        return order, attempt, account

    def provider_result(self, refund, status='succeeded'):
        """Return only provider facts required to bind the refund to this exact request."""
        return {'id': f're_{refund.pk}', 'amount': refund.amount_cents, 'currency': 'usd',
                'payment_intent': refund.payment_attempt.stripe_payment_intent_id, 'status': status,
                'metadata': {'order_refund_reference': str(refund.reference)}}

    def test_tabs_isolate_customer_and_preserve_snapshots(self):
        orders = [self.purchase(status) for status in Order.Status.values]
        create_purchase(user=self.other, recipient=self.other_recipient, product=self.product)
        Product.objects.filter(pk=self.product.pk).update(name='Changed', price='99.00')
        expected = {'all': Order.Status.values, 'to_ship': ['pending', 'accepted', 'packed', 'shipment_arranged'],
                    'to_receive': ['shipped'], 'cancelled': ['cancelled'], 'delivered': ['delivered'], 'completed': ['completed']}
        for tab, statuses in expected.items():
            response = self.client.get(URL, {'tab': tab})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([o['id'] for o in response.data['results']], [o.pk for o in reversed(orders) if o.status in statuses])
            for order in response.data['results']:
                self.assertEqual(order['items'][0]['name'], 'Snapshot camera')
                self.assertEqual(order['subtotal']['amount'], '24.68')
        self.assertEqual(self.client.get(URL, {'tab': 'bad'}).status_code, 400)

    def test_pagination_and_auth(self):
        for _ in range(21): self.purchase()
        result = self.client.get(URL).data
        self.assertEqual(result['count'], 21)
        self.assertEqual(len(result['results']), 20)
        self.assertIsNotNone(result['next'])
        self.assertEqual(len(self.client.get(URL, {'page': 2}).data['results']), 1)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_cod_cancel_replays_restore_inventory_once(self):
        # Exercise real COD placement so the original stock reduction is verified too.
        self.user.country_code = 'US'; self.user.save()
        response = self.client.post('/api/shop/checkouts/', product_request(self.client, self.product, self.recipient), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['orders'][0]['id']
        for _ in range(2):
            result = self.client.post(f'{URL}{pk}/cancel/')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['status'], 'cancelled')
            self.assertIsNotNone(result.data['concluded_at'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(OrderRefund.objects.exists())
        self.assertFalse(LedgerTransaction.objects.exists())

    def test_reject_foreign_and_ineligible_without_stock_changes(self):
        for status in ['shipment_arranged', 'shipped', 'delivered', 'completed']:
            order = self.purchase(status)
            self.assertEqual(self.client.post(f'{URL}{order.pk}/cancel/').status_code, 409)
        order = create_purchase(user=self.other, recipient=self.other_recipient, product=self.product)[0]
        self.assertEqual(self.client.post(f'{URL}{order.pk}/cancel/').status_code, 404)
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 10)

    def test_pending_online_payment_requires_existing_payment_flow(self):
        order = self.purchase()
        type(order.checkout).objects.filter(pk=order.checkout_id).update(status='pending', payment_method='stripe')
        result = self.client.get(URL).data['results'][0]
        self.assertTrue(result['payment_pending']); self.assertFalse(result['can_cancel'])
        self.assertEqual(self.client.post(f'{URL}{order.pk}/cancel/').status_code, 409)

    @patch('apps.checkout.services.refunds.provider.create_refund')
    def test_paid_cancellation_and_retry_refund_once(self, create):
        order, attempt, account = self.paid()
        create.side_effect = lambda refund: self.provider_result(refund)
        for _ in range(2):
            result = self.client.post(f'{URL}{order.pk}/cancel/')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['refund']['status'], 'succeeded')
        self.assertEqual(create.call_count, 1)
        self.assertEqual(OrderRefund.objects.count(), 1)
        self.assertEqual(account_balance(account), 0)
        self.assertEqual(account_balance(get_treasury_account()), 0)
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 10)
        attempt.refresh_from_db(); self.assertEqual(attempt.status, 'succeeded')
        self.assertEqual(LedgerTransaction.objects.count(), 2)
        self.assertEqual(list(order.events.values_list('event_type', flat=True)),
                         ['placed', 'cancelled', 'refund_requested', 'refund_succeeded'])

    @patch('apps.checkout.services.refunds.provider.create_refund')
    def test_unknown_outcome_is_durable_and_retry_recovers(self, create):
        order, _, account = self.paid()
        create.side_effect = TimeoutError
        result = self.client.post(f'{URL}{order.pk}/cancel/')
        self.assertEqual(result.data['status'], 'cancelled')
        self.assertTrue(result.data['refund']['needs_review'])
        self.assertEqual(account_balance(account), 2468)
        create.side_effect = lambda refund: self.provider_result(refund)
        result = self.client.post(f'{URL}{order.pk}/cancel/')
        self.assertEqual(result.data['refund']['status'], 'succeeded')
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 10)

    @patch('apps.checkout.services.refunds.provider.retrieve_refund')
    @patch('apps.checkout.services.refunds.provider.create_refund')
    def test_pending_refund_uses_retrieve_and_reverses_only_after_success(self, create, retrieve):
        order, _, account = self.paid()
        create.side_effect = lambda refund: self.provider_result(refund, 'pending')
        self.client.post(f'{URL}{order.pk}/cancel/')
        refund = OrderRefund.objects.get(order=order)
        self.assertEqual(account_balance(account), 2468)
        retrieve.return_value = self.provider_result(refund)
        self.client.post(f'{URL}{order.pk}/cancel/')
        self.assertEqual(create.call_count, 1); retrieve.assert_called_once_with(refund.stripe_refund_id)
        self.assertEqual(account_balance(account), 0)
        self.assertEqual(order.events.filter(event_type='refund_succeeded').count(), 1)

    @patch('apps.checkout.services.refunds.provider.find_refund', return_value=None)
    @patch('apps.checkout.services.refunds.provider.create_refund', side_effect=TimeoutError)
    def test_old_ambiguous_refund_never_recreates_after_key_expiry(self, create, find):
        order, _, _ = self.paid()
        self.client.post(f'{URL}{order.pk}/cancel/')
        refund = OrderRefund.objects.get(order=order)
        OrderRefund.objects.filter(pk=refund.pk).update(created_at=timezone.now()-timedelta(days=2))
        reconcile_refund(refund.pk)
        self.assertEqual(create.call_count, 1); find.assert_called_once()
        refund.refresh_from_db(); self.assertEqual(refund.failure_code, 'manual_review_required')

    @patch('apps.checkout.services.refunds.provider.create_refund')
    def test_mismatched_and_failed_refunds_do_not_reverse_ledger(self, create):
        order, _, account = self.paid()
        create.side_effect = lambda refund: {**self.provider_result(refund), 'amount': 9999}
        self.client.post(f'{URL}{order.pk}/cancel/')
        self.assertEqual(account_balance(account), 2468)
        create.side_effect = lambda refund: self.provider_result(refund, 'failed')
        result = self.client.post(f'{URL}{order.pk}/cancel/')
        self.assertEqual(result.data['refund']['status'], 'failed')
        self.assertEqual(account_balance(account), 2468)

    @patch('apps.checkout.services.refunds.provider.create_refund')
    def test_multi_store_charge_refunds_only_selected_store(self, create):
        first = self.purchase()
        second_store = Store.objects.create(name='Other store', country_code='US', currency='USD')
        second_product = Product.objects.create(store=second_store, name='Lens', price='10.00', stock=4, status='active')
        second, _ = create_purchase(user=self.user, recipient=self.recipient, product=second_product, checkout=first.checkout)
        checkout = first.checkout
        type(checkout).objects.filter(pk=checkout.pk).update(payment_method='stripe', total_amount='34.68')
        checkout.refresh_from_db()
        first_account = create_wallet(store_id=self.store.pk).accounts.get(bucket='pending')
        second_account = create_wallet(store_id=second_store.pk).accounts.get(bucket='pending')
        receipt = post_transaction(operation_key='multi-store-receipt', operation_type='payment_receipt', postings=[
            Posting(get_treasury_account().pk, 'debit', 3468), Posting(first_account.pk, 'credit', 2468), Posting(second_account.pk, 'credit', 1000)])
        PaymentAttempt.objects.create(checkout=checkout, status='succeeded', amount='34.68', currency='USD',
            stripe_payment_intent_id='pi_multi', receipt=receipt)
        Order.objects.filter(checkout=checkout).update(payment_status='paid')
        Product.objects.filter(pk=self.product.pk).update(stock=8)
        create.side_effect = lambda refund: self.provider_result(refund)
        result = self.client.post(f'{URL}{first.pk}/cancel/')
        self.assertEqual(result.data['refund']['amount']['amount'], '24.68')
        self.assertEqual(account_balance(first_account), 0)
        self.assertEqual(account_balance(second_account), 1000)
        self.assertEqual(account_balance(get_treasury_account()), 1000)
        second.refresh_from_db(); self.assertEqual(second.status, 'pending')
        second_product.refresh_from_db(); self.assertEqual(second_product.stock, 4)

    def test_accepted_and_packed_orders_can_cancel(self):
        for status in ['accepted', 'packed']:
            order = self.purchase(status)
            self.assertEqual(self.client.post(f'{URL}{order.pk}/cancel/').status_code, 200)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from apps.ecommerce.services.orders import cancel_order


class CancellationConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_duplicate_cancel_restores_inventory_once(self):
        user, recipient = customer(1)
        store = Store.objects.create(name='Store', country_code='PH')
        product = Product.objects.create(store=store, name='Camera', price='12.34', stock=8, status='active')
        order, _ = create_purchase(user=user, recipient=recipient, product=product, quantity=2)
        barrier = Barrier(2)
        def cancel():
            """Use independent DB sessions to force competing cancellation requests."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                cancel_order(user, order.pk)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: cancel(), range(2)))
        product.refresh_from_db(); self.assertEqual(product.stock, 10)
        order.refresh_from_db(); self.assertEqual(order.status, 'cancelled')
        self.assertEqual(order.events.filter(event_type='cancelled').count(), 1)
