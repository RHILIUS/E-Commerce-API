"""COD integrity, confirmation isolation and PostgreSQL race regression tests."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.test_support import create_customer
from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, PaymentAttempt
from apps.checkout.services.placement import PlacementConflict, place_cod
from apps.ecommerce.models import Cart, CartItem, Order, OrderItem, Product, ProductImage
from apps.inventory.models import StockReservation
from apps.store.models import Store
from core.test_support import create_purchase

PREVIEW = '/api/shop/checkout/preview/'
PLACE = '/api/shop/checkouts/'


# Use actual preview responses so placement tests exercise the approved signing contract.
def product_request(client, product, recipient, quantity=2, key=None):
    result = client.post(PREVIEW, {'source': 'product', 'product_id': product.pk, 'quantity': quantity}, format='json')
    assert result.status_code == 200 and result.data['can_place_order'], result.data
    return {'preview_token': result.data['preview_token'], 'recipient_id': recipient.pk,
            'payment_method': 'cod', 'idempotency_key': str(key or uuid.uuid4())}


# Shared fixtures keep the API and independent-connection concurrency scenarios equivalent.
def customer(number):
    user = create_customer(username=f'buyer{number}', email=f'buyer{number}@example.com',
        phone_number=f'+63911111111{number}', full_name='Buyer', country_code='PH')
    recipient = RecipientInfo.objects.create(user=user, name='Original recipient', phone_number=user.phone_number,
        country_code='PH', address_line1='Original address', city='Manila')
    return user, recipient


class CODPlacementTests(APITestCase):
    def test_confirmation_current_images_leave_purchase_snapshots_unchanged(self):
        self.store.profile_image = 'stores/receipt.jpg'
        self.store.save()
        ProductImage.objects.create(product=self.product, image='products/first.jpg', order=0)
        selected = ProductImage.objects.create(product=self.product, image='products/default.jpg',
                                               is_default=True, order=5, alt_text='Camera front')
        body = self.body()
        response = self.place(body)
        self.assertEqual(response.status_code, 201)
        order = response.data['orders'][0]
        self.assertIn('stores/receipt.jpg', order['store']['profile_image'])
        self.assertIn('products/default.jpg', order['items'][0]['image']['image'])
        self.assertEqual(order['items'][0]['image']['alt_text'], 'Camera front')
        self.assertEqual(self.place(body).data, response.data)
        url = f"{PLACE}{response.data['reference']}/"
        self.assertEqual(self.client.get(url).data, response.data)
        selected.delete()
        self.assertIn('products/first.jpg', self.client.get(url).data['orders'][0]['items'][0]['image']['image'])
        self.product.delete()
        item = self.client.get(url).data['orders'][0]['items'][0]
        self.assertIsNone(item['image'])
        self.assertIsNone(item['product_id'])
        self.assertEqual(item['name'], 'Camera')
        self.assertEqual(item['unit_price'], order['items'][0]['unit_price'])

    def setUp(self):
        self.user, self.recipient = customer(1)
        self.other, self.other_recipient = customer(2)
        self.store = Store.objects.create(name='Original store', country_code='PH')
        self.store2 = Store.objects.create(name='Second store', country_code='PH')
        self.product = Product.objects.create(store=self.store, name='Camera', price='12.34', stock=5, status='active')
        self.product2 = Product.objects.create(store=self.store2, name='Lens', price='20.00', stock=3, status='active')
        self.client.force_authenticate(self.user)

    def body(self, **kwargs):
        return product_request(self.client, self.product, self.recipient, **kwargs)

    def place(self, data):
        return self.client.post(PLACE, data, format='json')

    def cart_body(self):
        a = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store), product=self.product, quantity=2)
        b = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store2), product=self.product2, quantity=1)
        extra = Product.objects.create(store=self.store, name='Unselected', price='2', stock=5, status='active')
        untouched = CartItem.objects.create(cart=a.cart, product=extra, quantity=3)
        preview = self.client.post(PREVIEW, {'source': 'cart', 'item_ids': [a.pk, b.pk]}, format='json').data
        return {'preview_token': preview['preview_token'], 'recipient_id': self.recipient.pk,
                'payment_method': 'cod', 'idempotency_key': str(uuid.uuid4())}, [a, b], untouched

    def test_removed_recipient_blocks_new_order_but_preserves_retry_and_confirmation(self):
        from apps.account.services import delete_recipient
        body = self.body()
        response = self.place(body)
        self.assertEqual(response.status_code, 201)
        delete_recipient(self.user, self.recipient.pk)
        self.assertEqual(self.place(body).data, response.data)
        self.assertEqual(self.client.get(f"{PLACE}{response.data['reference']}/").data, response.data)
        new_body = self.body()
        self.assertEqual(self.place(new_body).status_code, 404)

    def test_direct_cod_creates_completed_checkout_unpaid_order_and_keeps_cart(self):
        cart = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store), product=self.product, quantity=1)
        with patch('apps.checkout.services.pricing.urlopen', side_effect=AssertionError('No FX needed')):
            response = self.place(self.body())
        self.assertEqual(response.status_code, 201, response.data)
        data = response.json()
        self.assertEqual(data['total'], {'amount': '24.68', 'currency': 'PHP'})
        self.assertEqual(data['status'], 'completed')
        self.assertIsNotNone(data['completed_at'])
        self.assertEqual(data['orders'][0]['status'], 'pending')
        self.assertEqual(data['orders'][0]['payment_status'], 'unpaid')
        self.assertEqual(data['orders'][0]['recipient']['name'], 'Original recipient')
        self.assertEqual(data['orders'][0]['items'][0]['original_unit_price']['amount'], '12.34')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        self.assertEqual(CartItem.objects.get(pk=cart.pk).quantity, 1)
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.get().quantity, 2)
        self.assertIsNone(OrderItem.objects.get().source_cart_item_id)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertFalse(StockReservation.objects.exists())
        checkout = Checkout.objects.get()
        self.assertEqual(len(checkout.request_fingerprint), 64)
        self.assertNotIn('request_fingerprint', data)
        self.assertEqual(self.client.get(f"{PLACE}{data['reference']}/").json(), data)

    def test_multi_store_cart_purchase_only_removes_selected_lines(self):
        body, selected, untouched = self.cart_body()
        result = self.place(body)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['total']['amount'], '44.68')
        self.assertEqual(Order.objects.count(), 2)
        self.assertFalse(CartItem.objects.filter(pk__in=[item.pk for item in selected]).exists())
        self.assertEqual(CartItem.objects.get(pk=untouched.pk).quantity, 3)
        self.assertFalse(Cart.objects.filter(pk=selected[1].cart_id).exists())
        self.assertTrue(Cart.objects.filter(pk=selected[0].cart_id).exists())
        self.assertTrue(all(item.source_cart_item_id is None for item in OrderItem.objects.all()))
        self.assertEqual(list(Product.objects.filter(pk__in=[self.product.pk,self.product2.pk]).order_by('pk').values_list('stock', flat=True)), [3, 2])

    def test_replay_precedes_expiry_recipient_validation_and_cart_cleanup(self):
        body, _, _ = self.cart_body()
        first = self.place(body)
        RecipientInfo.objects.filter(pk=self.recipient.pk).update(phone_number='invalid')
        Product.objects.filter(pk=self.product.pk).update(stock=0, price='99.00')
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 301):
            replay = self.place(body)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)

    def test_reused_key_different_payload_is_conflict(self):
        body = self.body()
        self.assertEqual(self.place(body).status_code, 201)
        for updates in ({'preview_token': body['preview_token'] + 'changed'}, {'recipient_id': self.other_recipient.pk}):
            self.assertEqual(self.place({**body, **updates}).status_code, 409)
        self.assertEqual(Checkout.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_new_request_expired_tampered_or_foreign_token_creates_nothing(self):
        body = self.body()
        self.assertEqual(self.place({**body, 'preview_token': body['preview_token'] + 'changed'}).status_code, 400)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 301):
            self.assertEqual(self.place(body).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.place({**body, 'recipient_id': self.other_recipient.pk}).status_code, 400)
        self.assertFalse(Checkout.objects.exists())

    def test_recipient_must_be_owned_complete_and_valid(self):
        body = self.body()
        for pk in (self.other_recipient.pk, 99999):
            self.assertEqual(self.place({**body, 'recipient_id': pk}).status_code, 404)
        for values in ({'name': ''}, {'name': 'Buyer', 'phone_number': 'bad'}, {'phone_number': self.user.phone_number, 'address_line1': ' '}):
            RecipientInfo.objects.filter(pk=self.recipient.pk).update(**values)
            self.assertEqual(self.place(body).status_code, 400)
        self.assertFalse(Checkout.objects.exists())

    def test_stock_price_status_names_and_store_changes_require_review(self):
        body = self.body()
        for values in ({'stock': 1}, {'price': '13.00'}, {'status': 'archived'}, {'name': 'Renamed'}, {'store_id': self.store2.pk}):
            Product.objects.filter(pk=self.product.pk).update(**values)
            self.assertEqual(self.place(body).status_code, 409, values)
            Product.objects.filter(pk=self.product.pk).update(stock=5, price='12.34', status='active', name='Camera', store=self.store)
        Store.objects.filter(pk=self.store.pk).update(name='Renamed store')
        self.assertEqual(self.place(body).status_code, 409)
        self.assertFalse(Checkout.objects.exists())

    def test_deleted_product_requires_review(self):
        body = self.body()
        self.product.delete()
        self.assertEqual(self.place(body).status_code, 409)
        self.assertFalse(Checkout.objects.exists())

    def test_cart_quantity_change_or_removal_requires_review(self):
        body, selected, _ = self.cart_body()
        CartItem.objects.filter(pk=selected[0].pk).update(quantity=3)
        self.assertEqual(self.place(body).status_code, 409)
        CartItem.objects.filter(pk=selected[0].pk).delete()
        self.assertEqual(self.place(body).status_code, 409)
        self.assertFalse(Checkout.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)

    def test_unresolved_hold_blocks_placement_even_after_deadline(self):
        body = self.body()
        _, item = create_purchase(user=self.other, recipient=self.other_recipient, product=self.product, quantity=4)
        hold = StockReservation.objects.create(order_item=item, product=self.product, quantity=4, expires_at=timezone.now()+timedelta(minutes=5))
        self.assertEqual(self.place(body).status_code, 409)
        hold.expires_at = timezone.now()-timedelta(seconds=1)
        hold.save()
        self.assertEqual(self.place(body).status_code, 409)
        hold.status = 'released'
        hold.save()
        self.assertEqual(self.place(body).status_code, 201)
        self.assertEqual(StockReservation.objects.count(), 1)

    def test_mid_transaction_failure_rolls_back_every_store_stock_and_cart(self):
        body, selected, untouched = self.cart_body()
        create = OrderItem.objects.create
        calls = 0
        def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Simulated second-store failure')
            return create(**kwargs)
        with patch('apps.checkout.services.placement.OrderItem.objects.create', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.place(body)
        self.assertFalse(Checkout.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderItem.objects.exists())
        self.assertEqual(CartItem.objects.count(), 3)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        self.assertEqual(Product.objects.get(pk=self.product2.pk).stock, 3)
        self.assertEqual(self.place(body).status_code, 201)

    def test_failure_during_cart_cleanup_also_rolls_back(self):
        body, _, _ = self.cart_body()
        with patch('django.db.models.query.QuerySet.delete', side_effect=RuntimeError('Cleanup failure')):
            with self.assertRaises(RuntimeError):
                self.place(body)
        self.assertFalse(Checkout.objects.exists())
        self.assertEqual(CartItem.objects.count(), 3)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)

    def test_confirmation_uses_snapshots_after_edits_and_product_deletion(self):
        first = self.place(self.body()).json()
        RecipientInfo.objects.filter(pk=self.recipient.pk).update(name='Changed', address_line1='Changed')
        Store.objects.filter(pk=self.store.pk).update(name='Changed')
        Product.objects.filter(pk=self.product.pk).update(name='Changed', price='999.00')
        path = f"{PLACE}{first['reference']}/"
        with patch('apps.checkout.services.pricing.urlopen', side_effect=AssertionError('No live FX')):
            self.assertEqual(self.client.get(path).json(), first)
            self.product.delete()
            data = self.client.get(path).json()
        self.assertIsNone(data['orders'][0]['items'][0]['product_id'])
        self.assertEqual(data['orders'][0]['items'][0]['name'], 'Camera')
        self.assertEqual(data['orders'][0]['store']['name'], 'Original store')
        self.assertEqual(data['orders'][0]['recipient']['name'], 'Original recipient')
        self.assertEqual(data['total']['amount'], '24.68')

    def test_confirmation_authorization_and_request_validation(self):
        body = self.body()
        ref = self.place(body).data['reference']
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'{PLACE}{ref}/').status_code, 404)
        self.assertEqual(self.client.get(f'{PLACE}{uuid.uuid4()}/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'{PLACE}{ref}/').status_code, 401)
        self.assertEqual(self.place(body).status_code, 401)
        self.client.force_authenticate(self.user)
        for updates in ({'payment_method': 'stripe'}, {'payment_method': ''}, {'recipient_id': 0},
                        {'idempotency_key': 'invalid'}, {'total': '0.01'}, {'preview_token': ''}):
            self.assertEqual(self.place({**body, **updates}).status_code, 400)
        for field in body:
            self.assertEqual(self.place({key: value for key,value in body.items() if key != field}).status_code, 400)

    def test_keys_are_scoped_to_customer_and_legacy_keys_fail_closed(self):
        body = self.body(quantity=1)
        self.assertEqual(self.place(body).status_code, 201)
        self.client.force_authenticate(self.other)
        other = product_request(self.client, self.product, self.other_recipient, quantity=1, key=body['idempotency_key'])
        self.assertEqual(self.place(other).status_code, 201)
        self.assertEqual(Checkout.objects.count(), 2)
        self.client.force_authenticate(self.user)
        Checkout.objects.filter(user=self.user).update(request_fingerprint='')
        self.assertEqual(self.place(body).status_code, 409)

    def test_request_identity_is_immutable_after_purchase(self):
        self.place(self.body())
        for field, value in [('request_fingerprint', 'a'*64), ('idempotency_key', uuid.uuid4())]:
            checkout = Checkout.objects.get()
            setattr(checkout, field, value)
            with self.assertRaises(ModelValidationError):
                checkout.save()

    def test_converted_prices_and_rates_survive_placement_without_provider_calls(self):
        body = None
        us = Store.objects.create(name='US store', country_code='US')
        product = Product.objects.create(store=us, name='Import', price='1.13', stock=5, status='active')
        with patch('apps.checkout.services.preview.checkout_rates', return_value=('PHP', {'USD': {'rate': Decimal('.5'), 'date': timezone.now().date().isoformat()}})):
            body = product_request(self.client, product, self.recipient, quantity=3)
        with patch('apps.checkout.services.pricing.urlopen', side_effect=AssertionError('Use the reviewed rate')):
            result = self.place(body)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['total']['amount'], '1.71')
        self.assertEqual(result.data['orders'][0]['items'][0]['unit_price']['amount'], '0.57')
        self.assertEqual(result.data['orders'][0]['items'][0]['original_unit_price']['currency'], 'USD')
        self.assertEqual(Order.objects.get().exchange_rate, Decimal('.5'))

    def test_schema_documents_placement_and_confirmation(self):
        schema = self.client.get('/api/schema/?format=json').json()
        self.assertEqual(set(schema['paths'][PLACE]['post']['responses']), {'200','201','400','401','404','409'})
        self.assertNotIn({}, schema['paths'][PLACE]['post']['security'])
        get = schema['paths'][PLACE+'{reference}/']['get']
        self.assertEqual(set(get['responses']), {'200','401','404'})


class ConcurrentCODTests(TransactionTestCase):
    def setUp(self):
        self.user, self.recipient = customer(1)
        self.other, self.other_recipient = customer(2)
        store = Store.objects.create(name='Concurrent store', country_code='PH')
        self.product = Product.objects.create(store=store, name='Last units', price='10.00', stock=2, status='active')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def race(self, jobs):
        """Start independent PostgreSQL transactions together and surface thread failures."""
        barrier = Barrier(len(jobs))
        def submit(job):
            user, data = job
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                checkout, created = place_cod(user, data)
                return (201 if created else 200, checkout.reference)
            except PlacementConflict:
                return (409, None)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(submit, jobs))

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_identical_requests_create_one_purchase(self):
        body = product_request(self.client, self.product, self.recipient)
        results = self.race([(self.user, body), (self.user, body)])
        self.assertCountEqual([status for status,_ in results], [201, 200])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 0)

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_customers_cannot_buy_the_same_last_units(self):
        first = product_request(self.client, self.product, self.recipient)
        self.client.force_authenticate(self.other)
        second = product_request(self.client, self.product, self.other_recipient)
        results = self.race([(self.user, first), (self.other, second)])
        self.assertCountEqual([status for status,_ in results], [201, 409])
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 0)

    @skipUnlessDBFeature('has_select_for_update')
    def test_same_key_different_request_conflicts_after_first_commit(self):
        first = product_request(self.client, self.product, self.recipient, quantity=1)
        # Both signed requests are valid, so whichever wins must persist its fingerprint.
        second = product_request(self.client, self.product, self.recipient, quantity=2, key=first['idempotency_key'])
        results = self.race([(self.user, first), (self.user, second)])
        self.assertCountEqual([status for status,_ in results], [201, 409])
        self.assertEqual(Checkout.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_different_keys_cannot_purchase_the_same_cart_twice(self):
        item = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.product.store), product=self.product, quantity=1)
        token = self.client.post(PREVIEW, {'source': 'cart', 'item_ids': [item.pk]}, format='json').data['preview_token']
        first = {'preview_token': token, 'recipient_id': self.recipient.pk, 'payment_method': 'cod', 'idempotency_key': str(uuid.uuid4())}
        second = {**first, 'idempotency_key': str(uuid.uuid4())}
        results = self.race([(self.user, first), (self.user, second)])
        self.assertCountEqual([status for status,_ in results], [201,409])
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 1)
