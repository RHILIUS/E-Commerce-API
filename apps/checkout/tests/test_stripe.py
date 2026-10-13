"""Stripe boundary, atomic multi-store funding and retry/expiry regression coverage."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.test import override_settings, skipUnlessDBFeature
from django.db import close_old_connections, connections
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from core.test_support import create_customer
from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, PaymentAttempt, ProviderEvent
from apps.checkout.services.online import PaymentMismatch, apply_session, reconcile_attempt
from apps.checkout.services.placement import reserve_online
from apps.checkout.services.stripe_provider import PaymentUnavailable
from apps.ecommerce.models import Cart, CartItem, Order, OrderItem, Product, ProductImage
from apps.inventory.models import StockReservation
from apps.store.models import Store
from apps.wallet.models import LedgerAccount, LedgerTransaction
from apps.wallet.services import account_balance

CONFIG = dict(STRIPE_SECRET_KEY='sk_test_example', STRIPE_WEBHOOK_SECRET='whsec_example',
              STRIPE_FRONTEND_ORIGIN='http://localhost:5173')
PREVIEW = '/api/shop/checkout/preview/'
START = '/api/shop/checkouts/stripe/'
WEBHOOK = '/api/shop/stripe/webhook/'


class StripeFixtures:
    def setUp(self):
        self.user = create_customer(username='buyer', email='buyer@example.com',
            full_name='Buyer', phone_number='+12025550123', country_code='US')
        self.recipient = RecipientInfo.objects.create(user=self.user, name='Buyer', phone_number=self.user.phone_number,
            country_code='US', address_line1='10 Main Street', city='Boston')
        self.stores = [Store.objects.create(name=f'Store {i}', country_code='US') for i in range(2)]
        self.products = [Product.objects.create(store=s, name=f'Product {i}', price=price, stock=5, status='active')
                         for i, (s, price) in enumerate(zip(self.stores, ['30.00', '40.00']))]
        self.items = [CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=p.store), product=p, quantity=q)
                      for p,q in zip(self.products, [2,1])]
        self.client.force_authenticate(self.user)
        self.preview_input = {'source':'cart', 'item_ids':[i.pk for i in self.items], 'payment_method':'stripe'}
        response = self.client.post(PREVIEW, self.preview_input, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.body = {'preview_token':response.data['preview_token'], 'recipient_id':self.recipient.pk,
                     'payment_method':'stripe', 'idempotency_key':str(uuid.uuid4())}

    def reserve(self):
        """Persist a provider-free pending checkout using the reviewed API payload."""
        checkout, _ = reserve_online(self.user, self.body)
        return checkout.payment_attempts.get()

    def session(self, attempt, *, paid=False, expired=False):
        """Use the provider fields consumed by validation, including expanded intent."""
        return {'id':'cs_test_' + str(attempt.pk), 'livemode':False, 'mode':'payment',
                'currency':'usd', 'amount_total':10000,
                'client_reference_id':str(attempt.checkout.reference),
                'metadata':{'attempt_reference':str(attempt.reference), 'checkout_reference':str(attempt.checkout.reference)},
                'status':'complete' if paid else 'expired' if expired else 'open',
                'payment_status':'paid' if paid else 'unpaid',
                'payment_intent': {'id':'pi_test_'+str(attempt.pk), 'status':'succeeded', 'currency':'usd',
                                   'amount_received':10000, 'livemode':False} if paid else None,
                'url':None if paid or expired else 'https://checkout.stripe.com/c/pay/demo'}

@override_settings(**CONFIG)
class StripeTests(StripeFixtures, APITestCase):
    def test_recipient_removal_preserves_pending_payment_and_saved_delivery(self):
        from apps.account.services import delete_recipient
        attempt = self.reserve()
        snapshots = list(Order.objects.order_by('pk').values_list('recipient_snapshot', flat=True))
        delete_recipient(self.user, self.recipient.pk)
        replay, created = reserve_online(self.user, self.body)
        self.assertFalse(created)
        self.assertEqual(replay.pk, attempt.checkout_id)
        new_body = {**self.body, 'idempotency_key': str(uuid.uuid4())}
        from django.http import Http404
        with self.assertRaises(Http404):
            reserve_online(self.user, new_body)
        apply_session(attempt.pk, self.session(attempt, paid=True))
        self.assertEqual(Order.objects.filter(payment_status='paid').count(), 2)
        self.assertEqual(list(Order.objects.order_by('pk').values_list('recipient_snapshot', flat=True)), snapshots)

    def test_reservation_and_paid_receipt_credit_two_stores_exactly_once(self):
        attempt = self.reserve()
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)
        self.assertEqual(list(Product.objects.order_by('pk').values_list('stock', flat=True)), [5,5])
        self.assertEqual(CartItem.objects.count(), 2)
        self.assertEqual(Order.objects.filter(payment_status='pending').count(), 2)
        session = self.session(attempt, paid=True)
        apply_session(attempt.pk, session)
        apply_session(attempt.pk, session)
        self.assertEqual(list(Product.objects.order_by('pk').values_list('stock', flat=True)), [3,4])
        self.assertEqual(Order.objects.filter(payment_status='paid').count(), 2)
        self.assertEqual(StockReservation.objects.filter(status='consumed').count(), 2)
        self.assertFalse(CartItem.objects.exists())
        self.assertEqual(LedgerTransaction.objects.count(), 1)
        self.assertEqual([account_balance(s.wallet.accounts.get(bucket='pending')) for s in self.stores], [6000,4000])
        self.assertEqual(account_balance(LedgerAccount.objects.get(bucket='treasury')), 10000)
        self.assertEqual(list(Product.objects.values_list('sold_count', flat=True)), [0,0])
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.receipt_id)

    def test_start_reuses_session_key_and_returns_owner_status(self):
        def create(attempt):
            self.assertEqual(attempt.session_parameters['line_items'][0]['price_data']['unit_amount'], 3000)
            self.assertEqual(attempt.session_parameters['line_items'][0]['quantity'], 2)
            return self.session(attempt)
        with patch('apps.checkout.services.stripe_provider.create_session', side_effect=create) as create_mock, patch(
                'apps.checkout.services.stripe_provider.retrieve_session', side_effect=lambda _: self.session(PaymentAttempt.objects.get())):
            first = self.client.post(START, self.body, format='json')
            second = self.client.post(START, self.body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(create_mock.call_count, 1)
        self.assertEqual(Checkout.objects.count(), 1)

    def test_product_summary_uses_saved_names_prices_quantities_and_default_image(self):
        ProductImage.objects.create(product=self.products[0], image='products/other.jpg', order=0)
        ProductImage.objects.create(product=self.products[0], image='products/chosen.jpg', is_default=True, order=9)
        with patch('django.core.files.storage.FileSystemStorage.url', side_effect=lambda name: f'https://cdn.example.com/{name}'):
            attempt = self.reserve()
        lines = attempt.session_parameters['line_items']
        self.assertEqual(lines, [
            {'price_data': {'currency': 'usd', 'unit_amount': 3000, 'product_data': {
                'name': 'Product 0', 'description': 'Sold by Store 0',
                'images': ['https://cdn.example.com/products/chosen.jpg']}}, 'quantity': 2},
            {'price_data': {'currency': 'usd', 'unit_amount': 4000, 'product_data': {
                'name': 'Product 1', 'description': 'Sold by Store 1'}}, 'quantity': 1},
        ])
        original = deepcopy(attempt.session_parameters)
        Product.objects.filter(pk=self.products[0].pk).update(name='Renamed', price='99.00')
        Store.objects.filter(pk=self.stores[0].pk).update(name='Renamed store')
        ProductImage.objects.filter(product=self.products[0]).update(image='products/new.jpg')
        from apps.checkout.services.stripe_provider import create_session
        with patch('apps.checkout.services.stripe_provider.client') as client:
            create_session(attempt)
            attempt.refresh_from_db()
            create_session(attempt)
        for call in client.return_value.v1.checkout.sessions.create.call_args_list:
            self.assertEqual(call.args[0], original)
            self.assertEqual(call.kwargs['options']['idempotency_key'], f'checkout:{attempt.reference}')

    def test_local_and_unusable_images_do_not_block_product_summary(self):
        from apps.checkout.services.stripe_provider import session_parameters
        ProductImage.objects.create(product=self.products[0], image='products/local.jpg')
        attempt = self.reserve()
        urls = ['/media/products/local.jpg', 'http://example.com/image.jpg', 'https://localhost/image.jpg',
                'https://127.0.0.1/image.jpg', 'https://10.0.0.1/image.jpg', 'https://user:secret@example.com/image.jpg']
        for url in urls:
            with self.subTest(url=url), patch('django.core.files.storage.FileSystemStorage.url', return_value=url):
                line = session_parameters(attempt)['line_items'][0]
            self.assertEqual(line['price_data']['product_data']['name'], 'Product 0')
            self.assertNotIn('images', line['price_data']['product_data'])

    def test_large_cart_summary_stays_within_limit_and_preserves_every_cent(self):
        from apps.checkout.services.stripe_provider import session_parameters
        attempt = self.reserve()
        order = attempt.checkout.orders.order_by('store_id').first()
        current_count = 2
        for count in (100, 101, 500):
            OrderItem.objects.bulk_create([OrderItem(
                order=order, product=self.products[0], product_name_at_purchase=f'Extra {i}',
                quantity=3, price_at_purchase=Decimal('0.01'), original_unit_price=Decimal('0.01'),
            ) for i in range(current_count, count)])
            current_count = count
            attempt.amount = Decimal('100.00') + Decimal('0.03') * (count - 2)
            with self.subTest(count=count), self.assertNumQueries(2):
                lines = session_parameters(attempt)['line_items']
            self.assertEqual(len(lines), 100)
            self.assertEqual(sum(line['price_data']['unit_amount'] * line['quantity'] for line in lines),
                             int(attempt.amount * 100))
            if count > 100:
                self.assertEqual(lines[-1]['price_data']['product_data']['name'], f'{count - 99} additional products')
                self.assertEqual(lines[-1]['quantity'], 1)

    def test_product_summary_refuses_mismatched_total_and_retains_zero_price_items(self):
        from apps.checkout.services.stripe_provider import session_parameters
        attempt = self.reserve()
        first = OrderItem.objects.filter(order__checkout=attempt.checkout).order_by('order__store_id').first()
        OrderItem.objects.filter(pk=first.pk).update(price_at_purchase=0)
        with self.assertRaises(PaymentUnavailable):
            session_parameters(attempt)
        attempt.amount = Decimal('40.00')
        lines = session_parameters(attempt)['line_items']
        self.assertEqual(lines[0]['price_data']['unit_amount'], 0)
        self.assertEqual(lines[0]['quantity'], 2)

    def legacy_attempt(self):
        """Reproduce the immutable request saved before dynamic payment methods."""
        attempt = self.reserve()
        params = {**attempt.session_parameters, 'payment_method_types': ['card']}
        PaymentAttempt.objects.filter(pk=attempt.pk).update(session_parameters=params)
        attempt.refresh_from_db()
        return attempt

    def rejection(self):
        """Use the exact provider failure observed in the test account."""
        return stripe.InvalidRequestError(
            'The `payment_method_types` parameter is no longer supported when creating a Checkout Session.',
            param='payment_method_types', http_status=400)

    def test_new_sessions_use_dashboard_managed_payment_methods(self):
        attempt = self.reserve()
        self.assertNotIn('payment_method_types', attempt.session_parameters)

    def test_legacy_rejection_closes_purchase_and_identical_replays_without_new_session(self):
        attempt = self.legacy_attempt()
        with patch('apps.checkout.services.stripe_provider.create_session', side_effect=self.rejection()) as create:
            response = self.client.post(START, self.body, format='json')
            replay = self.client.post(START, self.body, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.args[0].session_parameters, attempt.session_parameters)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.failure_code, 'session_creation_rejected')
        self.assertEqual(Checkout.objects.get().status, 'cancelled')
        self.assertEqual(Order.objects.filter(status='cancelled', payment_status='cancelled').count(), 2)
        self.assertEqual(StockReservation.objects.filter(status='released').count(), 2)
        self.assertEqual(CartItem.objects.count(), 2)
        self.assertEqual(list(Product.objects.order_by('pk').values_list('stock', flat=True)), [5, 5])
        self.assertFalse(LedgerTransaction.objects.exists())
        preview = self.client.post(PREVIEW, self.preview_input, format='json')
        fresh = {**self.body, 'preview_token': preview.data['preview_token'], 'idempotency_key': str(uuid.uuid4())}
        with patch('apps.checkout.services.stripe_provider.create_session', side_effect=lambda a: self.session(a)), patch(
                'apps.checkout.services.stripe_provider.retrieve_session', side_effect=lambda _: self.session(PaymentAttempt.objects.get(status='pending'))):
            self.assertEqual(self.client.post(START, fresh, format='json').status_code, 200)
        self.assertEqual(PaymentAttempt.objects.count(), 2)

    def test_other_creation_errors_preserve_original_attempt_and_holds(self):
        attempt = self.legacy_attempt()
        errors = [stripe.APIConnectionError('timeout'), stripe.AuthenticationError('invalid key'),
                  stripe.InvalidRequestError('different rejection', param='expires_at', http_status=400),
                  stripe.InvalidRequestError('invalid payment method', param='payment_method_types', http_status=400)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch(
                    'apps.checkout.services.stripe_provider.create_session', side_effect=error), self.assertLogs(
                    'apps.checkout.services.online', level='WARNING') as logs:
                self.assertEqual(self.client.post(START, self.body, format='json').status_code, 503)
                attempt.refresh_from_db()
                self.assertEqual(attempt.status, 'pending')
                self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)
            self.assertEqual([record.getMessage() for record in logs.records], [
                f'Stripe reconciliation failed: attempt={attempt.pk} error_type={type(error).__name__}'])

    def test_retrieval_rejection_cannot_release_an_existing_session(self):
        attempt = self.legacy_attempt()
        apply_session(attempt.pk, self.session(attempt))
        with patch('apps.checkout.services.stripe_provider.retrieve_session', side_effect=self.rejection()), self.assertLogs(
                'apps.checkout.services.online', level='WARNING') as logs:
            self.assertEqual(self.client.post(START, self.body, format='json').status_code, 503)
        self.assertEqual([record.getMessage() for record in logs.records], [
            f'Stripe reconciliation failed: attempt={attempt.pk} error_type=InvalidRequestError'])
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)

    def test_rejection_release_is_atomic_on_local_failure(self):
        self.legacy_attempt()
        with patch('apps.checkout.services.stripe_provider.create_session', side_effect=self.rejection()), patch(
                'apps.checkout.models.PaymentAttempt.save', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(START, self.body, format='json')
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)
        self.assertEqual(Order.objects.filter(payment_status='pending').count(), 2)
        self.assertEqual(Checkout.objects.get().status, 'pending')

    def test_rejection_cannot_release_after_concurrent_session_assignment(self):
        from apps.checkout.services.online import reject_session_creation
        attempt = self.legacy_attempt()
        apply_session(attempt.pk, self.session(attempt))
        with self.assertRaises(PaymentMismatch):
            reject_session_creation(attempt.pk)
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)

    def test_uncertain_create_keeps_holds_and_same_attempt(self):
        with patch('apps.checkout.services.stripe_provider.create_session', side_effect=stripe.APIConnectionError('timeout')), self.assertLogs(
                'apps.checkout.services.online', level='WARNING') as logs:
            response = self.client.post(START, self.body, format='json')
        self.assertEqual(response.status_code, 503)
        attempt = PaymentAttempt.objects.get()
        self.assertEqual([record.getMessage() for record in logs.records], [
            f'Stripe reconciliation failed: attempt={attempt.pk} error_type=APIConnectionError'])
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)
        with patch('apps.checkout.services.stripe_provider.create_session', return_value=self.session(attempt)), patch(
                'apps.checkout.services.stripe_provider.retrieve_session', return_value=self.session(attempt)):
            self.assertEqual(self.client.post(START, self.body, format='json').status_code, 200)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_session_id_survives_failure_of_followup_retrieval(self):
        attempt = self.reserve()
        with patch('apps.checkout.services.stripe_provider.create_session', return_value=self.session(attempt)), patch(
                'apps.checkout.services.stripe_provider.retrieve_session', side_effect=stripe.APIConnectionError('timeout')), self.assertLogs(
                'apps.checkout.services.online', level='WARNING') as logs:
            with self.assertRaises(PaymentUnavailable):
                reconcile_attempt(attempt.pk)
        self.assertEqual([record.getMessage() for record in logs.records], [
            f'Stripe reconciliation failed: attempt={attempt.pk} error_type=APIConnectionError'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.stripe_checkout_session_id, self.session(attempt)['id'])
        with patch('apps.checkout.services.stripe_provider.create_session') as create, patch(
                'apps.checkout.services.stripe_provider.retrieve_session', return_value=self.session(attempt)):
            reconcile_attempt(attempt.pk)
        create.assert_not_called()

    def test_unsettled_intent_does_not_release_even_on_expired_session(self):
        attempt = self.reserve()
        session = self.session(attempt, expired=True)
        session['payment_intent'] = {'id': 'pi_processing', 'status': 'processing'}
        apply_session(attempt.pk, session)
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)
        self.assertEqual(Checkout.objects.get().status, 'pending')
        self.assertFalse(LedgerTransaction.objects.exists())

    def test_product_save_cannot_reduce_inventory_below_pending_holds(self):
        from django.core.exceptions import ValidationError
        self.reserve()
        product = self.products[0]
        product.stock = 1
        with self.assertRaises(ValidationError):
            product.save()
        product.refresh_from_db()
        self.assertEqual(product.stock, 5)

    def test_paid_finalization_failure_rolls_back_then_recovers(self):
        attempt = self.reserve()
        with patch('apps.checkout.services.online.post_transaction', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                apply_session(attempt.pk, self.session(attempt, paid=True))
        self.assertFalse(LedgerTransaction.objects.exists())
        self.assertEqual(Order.objects.filter(payment_status='pending').count(), 2)
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)
        apply_session(attempt.pk, self.session(attempt, paid=True))
        self.assertEqual(Order.objects.filter(payment_status='paid').count(), 2)

    def test_expired_provider_session_releases_holds_but_keeps_cart_and_stock(self):
        attempt = self.reserve()
        apply_session(attempt.pk, self.session(attempt, expired=True))
        self.assertEqual(StockReservation.objects.filter(status='released').count(), 2)
        self.assertEqual(CartItem.objects.count(), 2)
        self.assertEqual(list(Product.objects.order_by('pk').values_list('stock',flat=True)), [5,5])
        self.assertEqual(Checkout.objects.get().status, 'expired')
        self.assertFalse(LedgerTransaction.objects.exists())

    def test_local_deadline_alone_does_not_release_stock(self):
        attempt = self.reserve()
        StockReservation.objects.update(expires_at=timezone.now()-timedelta(days=1))
        Product.objects.filter(pk=self.products[0].pk).update(stock=2)
        response = self.client.post(PREVIEW, self.preview_input, format='json')
        self.assertFalse(response.data['can_place_order'])
        self.assertEqual(StockReservation.objects.filter(status='held').count(), 2)

    def test_cart_edit_and_cod_repurchase_blocked_while_pending(self):
        self.reserve()
        item = self.items[0]
        response = self.client.patch(f'/api/shop/carts/items/{item.pk}/', {'quantity':1}, format='json')
        self.assertEqual(response.status_code, 409)
        response = self.client.delete('/api/shop/carts/items/', {'item_ids':[item.pk]}, format='json')
        self.assertEqual(response.status_code, 409)
        preview = self.client.post(PREVIEW, {**self.preview_input, 'payment_method':'cod'}, format='json').data
        response = self.client.post('/api/shop/checkouts/', {**self.body, 'preview_token':preview['preview_token'],
            'payment_method':'cod','idempotency_key':str(uuid.uuid4())}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_live_configuration_and_wrong_review_method_fail_before_reservation(self):
        with override_settings(STRIPE_SECRET_KEY='sk_live_invalid'):
            self.assertEqual(self.client.post(START, self.body, format='json').status_code, 503)
        preview = self.client.post(PREVIEW, {**self.preview_input, 'payment_method':'cod'},format='json').data
        result = self.client.post(START,{**self.body,'preview_token':preview['preview_token']},format='json')
        self.assertEqual(result.status_code,409)
        self.assertFalse(Checkout.objects.exists())

    def test_mismatched_amount_currency_environment_or_metadata_cannot_finalize(self):
        attempt = self.reserve()
        for change in ({'amount_total':9999}, {'currency':'php'}, {'livemode':True}, {'metadata':{}},
                       {'payment_intent':{'id':'pi_bad','status':'processing'}}):
            with self.subTest(change=change), self.assertRaises(PaymentMismatch):
                apply_session(attempt.pk, {**self.session(attempt, paid=True), **change})
        self.assertFalse(LedgerTransaction.objects.exists())

    def test_cancel_racing_success_completes_payment(self):
        attempt = self.reserve()
        apply_session(attempt.pk, self.session(attempt))
        with patch('apps.checkout.services.stripe_provider.retrieve_session', return_value=self.session(attempt, paid=True)):
            result = self.client.post(f'/api/shop/checkouts/{attempt.checkout.reference}/payment/cancel/')
        self.assertEqual(result.data['status'],'completed')
        self.assertEqual(LedgerTransaction.objects.count(),1)

    def test_cancel_expires_at_provider_before_stock_release(self):
        attempt = self.reserve()
        apply_session(attempt.pk, self.session(attempt))
        with patch('apps.checkout.services.stripe_provider.retrieve_session', side_effect=[self.session(attempt), self.session(attempt,expired=True)]), patch(
                'apps.checkout.services.stripe_provider.expire_session') as expire:
            result = self.client.post(f'/api/shop/checkouts/{attempt.checkout.reference}/payment/cancel/')
        expire.assert_called_once()
        self.assertEqual(result.data['status'],'cancelled')

    def test_owner_only_status_and_cancellation(self):
        attempt = self.reserve()
        other = get_user_model().objects.create_user(username='other',email='other@example.com',phone_number='+12025550124')
        self.client.force_authenticate(other)
        url = f'/api/shop/checkouts/{attempt.checkout.reference}/payment/'
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(url).status_code,404)
        self.assertEqual(self.client.post(url+'cancel/').status_code,404)

    def webhook(self, event, *, valid=True):
        """Exercise the real SDK signature check with local test-only secrets."""
        body = json.dumps(event)
        timestamp = int(time.time())
        signature = hmac.new(b'whsec_example',f'{timestamp}.{body}'.encode(),hashlib.sha256).hexdigest()
        return self.client.post(WEBHOOK, body, content_type='application/json',
                                HTTP_STRIPE_SIGNATURE=f't={timestamp},v1={signature if valid else "invalid"}')

    def test_signed_webhook_duplicate_and_out_of_order_events_post_once(self):
        attempt = self.reserve()
        event = {'id':'evt_1','type':'checkout.session.completed','livemode':False,
                 'data':{'object':self.session(attempt,paid=True)}}
        with patch('apps.checkout.services.stripe_provider.retrieve_session',return_value=self.session(attempt,paid=True)):
            self.assertEqual(self.webhook(event,valid=False).status_code,400)
            self.assertFalse(ProviderEvent.objects.exists())
            self.assertEqual(self.webhook(event).status_code,200)
            self.assertEqual(self.webhook(event).status_code,200)
            self.assertEqual(self.webhook({**event,'id':'evt_2','type':'checkout.session.expired'}).status_code,200)
        self.assertEqual(ProviderEvent.objects.filter(processing_status='processed').count(),2)
        self.assertEqual(LedgerTransaction.objects.count(),1)

    def test_webhook_failure_is_durable_and_recovery_command_retries(self):
        from django.core.management import call_command
        from io import StringIO
        attempt = self.reserve()
        event = {'id':'evt_retry','type':'checkout.session.completed','livemode':False,
                 'data':{'object':self.session(attempt,paid=True)}}
        with patch('apps.checkout.services.stripe_provider.retrieve_session',side_effect=stripe.APIConnectionError('timeout')), self.assertLogs(
                'apps.checkout.stripe_views', level='WARNING') as logs:
            self.assertEqual(self.webhook(event).status_code,200)
        saved_event = ProviderEvent.objects.get()
        self.assertEqual([record.getMessage() for record in logs.records], [
            f'Stripe event {saved_event.pk} queued for recovery'])
        self.assertEqual(saved_event.processing_status,'failed')
        with patch('apps.checkout.services.stripe_provider.retrieve_session',return_value=self.session(attempt,paid=True)):
            call_command('reconcile_stripe',stdout=StringIO())
        self.assertEqual(ProviderEvent.objects.get().processing_status,'processed')
        self.assertEqual(LedgerTransaction.objects.count(),1)

    def test_old_unknown_session_is_not_recreated_after_idempotency_retention(self):
        attempt = self.reserve()
        PaymentAttempt.objects.filter(pk=attempt.pk).update(created_at=timezone.now()-timedelta(days=2))
        with patch('apps.checkout.services.stripe_provider.create_session') as create, self.assertLogs(
                'apps.checkout.services.online', level='WARNING') as logs:
            with self.assertRaises(PaymentUnavailable):
                reconcile_attempt(attempt.pk)
        self.assertEqual([record.getMessage() for record in logs.records], [
            f'Stripe reconciliation failed: attempt={attempt.pk} error_type=PaymentMismatch'])
        create.assert_not_called()
        self.assertEqual(StockReservation.objects.filter(status='held').count(),2)


@override_settings(**CONFIG)
class StripeConcurrencyTests(StripeFixtures, APITransactionTestCase):
    def race(self, jobs):
        """Start independent connections together to verify PostgreSQL row-lock behavior."""
        barrier = Barrier(len(jobs))
        def run(job):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return job()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(run, jobs))

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_paid_events_credit_once(self):
        attempt = self.reserve()
        session = self.session(attempt, paid=True)
        self.race([lambda: apply_session(attempt.pk, session) for _ in range(2)])
        self.assertEqual(LedgerTransaction.objects.count(), 1)
        self.assertEqual(list(Product.objects.order_by('pk').values_list('stock', flat=True)), [3,4])
        self.assertEqual(Order.objects.filter(payment_status='paid').count(), 2)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_reservation_replays_one_purchase(self):
        attempts = self.race([lambda: self.reserve().pk for _ in range(2)])
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(StockReservation.objects.count(), 2)
