"""Preview contract, integrity and provider-boundary regression tests."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from core.test_support import create_customer
from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, PaymentAttempt
from apps.checkout.services.pricing import ConversionUnavailable, checkout_rates, rounded_unit_price
from apps.checkout.services.tokens import PreviewExpired, read_preview
from apps.ecommerce.models import Cart, CartItem, Order, OrderItem, Product, ProductImage
from apps.inventory.models import StockReservation
from apps.store.models import Store
from core.test_support import create_purchase

URL = '/api/shop/checkout/preview/'


class PreviewAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = create_customer(username='buyer', email='buyer@example.com',
            phone_number='+639111111111', full_name='Buyer', country_code='PH')
        self.other = create_customer(username='other', email='other@example.com',
            phone_number='+639222222222', full_name='Other', country_code='PH')
        self.store = Store.objects.create(name='Camera store', country_code='PH')
        self.store2 = Store.objects.create(name='Accessory store', country_code='PH')
        self.product = Product.objects.create(store=self.store, name='Camera', price='12.34', stock=5, status='active')
        self.product2 = Product.objects.create(store=self.store2, name='Lens', price='20.00', stock=3, status='active')
        self.client.force_authenticate(self.user)
        network = patch('apps.checkout.services.pricing.urlopen', side_effect=AssertionError('Unexpected network call'))
        self.network = network.start()
        self.addCleanup(network.stop)

    def preview(self, **updates):
        data = {'source': 'product', 'product_id': self.product.pk, 'quantity': 2}
        data.update(updates)
        return self.client.post(URL, data, format='json')

    def test_product_totals_image_token_and_no_mutations(self):
        ProductImage.objects.create(product=self.product, image='products/camera.jpg', alt_text='Camera front', is_default=True)
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.json()
        self.assertEqual(data['total'], {'amount': '24.68', 'currency': 'PHP'})
        self.assertTrue(data['can_place_order'])
        self.assertEqual(data['issues'], [])
        row = data['stores'][0]['items'][0]
        self.assertEqual(row['unit_price']['amount'], '12.34')
        self.assertIsNone(row['cart_item_id'])
        self.assertTrue(row['product']['image']['image'].endswith('/products/camera.jpg'))
        self.assertEqual(row['product']['image']['alt_text'], 'Camera front')
        payload = read_preview(data['preview_token'], self.user)
        self.assertEqual(payload['user_id'], self.user.pk)
        self.assertEqual(payload['items'][0]['quantity'], 2)
        self.assertEqual(payload['items'][0]['original_unit_price'], '12.34')
        self.assertEqual(payload['items'][0]['exchange_rate'], '1')
        self.assertGreater(payload['expires_at'], timezone.now().timestamp() + 298)
        self.assertLessEqual(payload['expires_at'], timezone.now().timestamp() + 300)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        for model in (Cart, CartItem, Checkout, Order, OrderItem, PaymentAttempt, StockReservation):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.network.assert_not_called()

    def test_cart_only_selected_items_and_saved_quantities(self):
        cart = Cart.objects.create(user=self.user, store=self.store)
        cart2 = Cart.objects.create(user=self.user, store=self.store2)
        a = CartItem.objects.create(cart=cart, product=self.product, quantity=3)
        b = CartItem.objects.create(cart=cart2, product=self.product2, quantity=2)
        extra = Product.objects.create(store=self.store, name='Unselected', price='99.99', stock=1, status='active')
        CartItem.objects.create(cart=cart, product=extra)
        response = self.client.post(URL, {'source': 'cart', 'item_ids': [b.pk, a.pk]}, format='json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([g['store']['id'] for g in data['stores']], [self.store.pk, self.store2.pk])
        self.assertEqual(data['total']['amount'], '77.02')
        self.assertEqual([g['subtotal']['amount'] for g in data['stores']], ['37.02', '40.00'])
        self.assertEqual(CartItem.objects.count(), 3)
        payload = read_preview(data['preview_token'], self.user)
        self.assertEqual({i['cart_item_id'] for i in payload['items']}, {a.pk, b.pk})

    def test_reject_foreign_or_missing_items_without_partial_preview(self):
        own = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store), product=self.product)
        other = CartItem.objects.create(cart=Cart.objects.create(user=self.other, store=self.store), product=self.product)
        for selected in ([own.pk, other.pk], [own.pk, 99999]):
            self.assertEqual(self.client.post(URL, {'source': 'cart', 'item_ids': selected}, format='json').status_code, 404)
        self.assertEqual(self.preview(product_id=99999).status_code, 404)

    def test_request_shapes_authentication_and_untrusted_totals(self):
        for data in [
            {}, {'source': 'product'}, {'source': 'other'}, {'source': 'cart', 'item_ids': []},
            {'source': 'cart', 'item_ids': [1, 1]}, {'source': 'cart', 'item_ids': list(range(1, 502))},
            {'source': 'cart', 'item_ids': [1], 'quantity': 2},
        ]:
            self.assertEqual(self.client.post(URL, data, format='json').status_code, 400, data)
        for quantity in (0, -1, 1.5, True, 'invalid', 2147483648):
            self.assertEqual(self.preview(quantity=quantity).status_code, 400, quantity)
        for extra in ({'price': '0.01'}, {'currency': 'USD'}, {'total': '0.01'}, {'item_ids': [1]}):
            self.assertEqual(self.preview(**extra).status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.preview().status_code, 401)

    def test_stock_and_availability_issues_have_no_token_or_writes(self):
        for status, stock, code in [('active', 1, 'insufficient_stock'), ('active', 0, 'product_unavailable'), ('archived', 5, 'product_unavailable')]:
            Product.objects.filter(pk=self.product.pk).update(status=status, stock=stock)
            data = self.preview().json()
            self.assertFalse(data['can_place_order'])
            self.assertEqual(data['issues'][0]['code'], code)
            self.assertIsNone(data['preview_token'])
            self.assertIsNone(data['expires_at'])
        self.assertFalse(Checkout.objects.exists())

    def test_unpublished_cart_item_is_redacted_and_direct_product_hidden(self):
        item = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store), product=self.product)
        Product.objects.filter(pk=self.product.pk).update(status='draft', name='Secret')
        self.assertEqual(self.preview().status_code, 404)
        data = self.client.post(URL, {'source': 'cart', 'item_ids': [item.pk]}, format='json').json()
        row = data['stores'][0]['items'][0]
        self.assertEqual(row['product']['name'], 'Unavailable product')
        self.assertIsNone(row['unit_price'])
        self.assertIsNone(data['total'])
        self.assertIsNone(data['preview_token'])
        self.assertNotIn('Secret', json.dumps(data))

    def test_unresolved_holds_reduce_stock_even_after_deadline(self):
        recipient = RecipientInfo.objects.create(user=self.user, name='Buyer', phone_number=self.user.phone_number,
            country_code='PH', address_line1='Street', city='Manila')
        _, order_item = create_purchase(user=self.user, recipient=recipient, product=self.product, quantity=4)
        hold = StockReservation.objects.create(order_item=order_item, product=self.product, quantity=4,
            expires_at=timezone.now() + timedelta(minutes=10))
        data = self.preview().json()
        self.assertEqual(data['stores'][0]['items'][0]['available_stock'], 1)
        self.assertFalse(data['can_place_order'])
        StockReservation.objects.filter(pk=hold.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(self.preview().json()['can_place_order'])
        StockReservation.objects.filter(pk=hold.pk).update(status='released')
        self.assertTrue(self.preview().json()['can_place_order'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_token_rejects_tampering_other_customer_and_expiry(self):
        token = self.preview().json()['preview_token']
        with self.assertRaises(ValidationError):
            read_preview(token + 'tampered', self.user)
        with self.assertRaises(ValidationError):
            read_preview(token, self.other)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 301):
            with self.assertRaises(PreviewExpired):
                read_preview(token, self.user)

    def test_refresh_reads_current_price_and_cart_quantity(self):
        item = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store), product=self.product)
        request = {'source': 'cart', 'item_ids': [item.pk]}
        first = self.client.post(URL, request, format='json').json()
        Product.objects.filter(pk=self.product.pk).update(price='15.00')
        CartItem.objects.filter(pk=item.pk).update(quantity=2)
        second = self.client.post(URL, request, format='json').json()
        self.assertEqual(first['total']['amount'], '12.34')
        self.assertEqual(second['total']['amount'], '30.00')
        self.assertNotEqual(first['preview_token'], second['preview_token'])

    @patch('apps.checkout.services.preview.checkout_rates', side_effect=ConversionUnavailable)
    def test_conversion_failure_is_503_without_purchase(self, rates):
        response = self.preview()
        self.assertEqual(response.status_code, 503)
        self.assertIn('detail', response.json())
        self.assertFalse(Checkout.objects.exists())

    @patch('apps.checkout.services.pricing._fetch_json')
    def test_mixed_currency_totals_use_signed_multiplier(self, fetch):
        us = Store.objects.create(name='US Store', country_code='US')
        p = Product.objects.create(store=us, name='US product', price='1.13', stock=5, status='active')
        fetch.side_effect = [[{'iso_code': 'USD'}, {'iso_code': 'PHP'}],
            [{'base': 'PHP', 'quote': 'USD', 'rate': '2', 'date': timezone.now().date().isoformat()}]]
        local = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=self.store), product=self.product, quantity=2)
        foreign = CartItem.objects.create(cart=Cart.objects.create(user=self.user, store=us), product=p, quantity=3)
        data = self.client.post(URL, {'source': 'cart', 'item_ids': [local.pk, foreign.pk]}, format='json').json()
        self.assertEqual(data['stores'][1]['items'][0]['unit_price']['amount'], '0.57')
        self.assertEqual(data['stores'][1]['subtotal']['amount'], '1.71')
        self.assertEqual(data['total']['amount'], '26.39')
        terms = read_preview(data['preview_token'], self.user)['items']
        self.assertEqual(terms[1]['exchange_rate'], '0.500000000000')

    def test_negative_price_and_amount_overflow_block_preview(self):
        Product.objects.filter(pk=self.product.pk).update(price='-1.00')
        self.assertEqual(self.preview().json()['issues'][0]['code'], 'invalid_price')
        Product.objects.filter(pk=self.product.pk).update(price='9999999999.99', stock=2147483647)
        data = self.preview(quantity=2147483647).json()
        self.assertFalse(data['can_place_order'])
        self.assertEqual(data['issues'][0]['code'], 'amount_too_large')
        self.assertIsNone(data['preview_token'])

    def test_schema_documents_protected_preview_contract(self):
        schema = self.client.get('/api/schema/?format=json').json()
        operation = schema['paths'][URL]['post']
        self.assertEqual(set(operation['responses']), {'200', '400', '401', '404', '503'})
        self.assertNotIn({}, operation['security'])
        self.assertEqual(operation['responses']['200']['content']['application/json']['schema'],
                         {'$ref': '#/components/schemas/PreviewResponse'})


class CheckoutPricingTests(SimpleTestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.user = SimpleNamespace(address=SimpleNamespace(country_code='US'))
        cache.clear()
        self.addCleanup(cache.clear)

    def row(self, **updates):
        row = {'base': 'USD', 'quote': 'PHP', 'rate': '56', 'date': timezone.now().date().isoformat()}
        row.update(updates)
        return row

    @patch('apps.checkout.services.pricing.urlopen')
    def test_provider_boundary_cache_and_currency_rounding(self, fetch):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps([self.row()]).encode()
        fetch.return_value = response
        target, rates = checkout_rates(self.user, {'PHP', 'USD'})
        self.assertEqual(target, 'USD')
        self.assertEqual(rounded_unit_price(Decimal('5600'), target, rates['PHP']['rate']), Decimal('100.00'))
        self.assertEqual(checkout_rates(self.user, {'PHP', 'USD'}), (target, rates))
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0].full_url, 'https://api.frankfurter.dev/v2/rates?base=usd')
        for amount, currency, rate, expected in [('301', 'JPY', '.5', '151'), ('.615', 'KWD', '.5', '.308')]:
            self.assertEqual(rounded_unit_price(Decimal(amount), currency, Decimal(rate)), Decimal(expected))

    @patch('apps.checkout.services.pricing._fetch_json')
    def test_unknown_and_unsupported_country_fallback_without_relabelling(self, fetch):
        self.user.address.country_code = 'XX'
        fetch.return_value = [self.row()]
        currency, rates = checkout_rates(self.user, {'PHP'})
        self.assertEqual(currency, 'USD')
        self.assertNotEqual(rates['PHP']['rate'], 1)
        cache.clear()
        self.user.address.country_code = 'PH'
        fetch.side_effect = [[{'iso_code': 'USD'}]]
        self.assertEqual(checkout_rates(self.user, {'USD'}), ('USD', {'USD': {'rate': Decimal(1), 'date': None}}))

    @patch('apps.checkout.services.pricing._fetch_json', side_effect=URLError('offline'))
    def test_outage_never_causes_currency_fallback_and_retries_are_bounded(self, fetch):
        for _ in range(2):
            with self.assertRaises(ConversionUnavailable):
                checkout_rates(self.user, {'PHP'})
        fetch.assert_called_once()
        self.user.address.country_code = 'PH'
        with self.assertRaises(ConversionUnavailable):
            checkout_rates(self.user, {'USD'})

    @patch('apps.checkout.services.pricing._fetch_json', side_effect=URLError('offline'))
    def test_only_valid_cached_rates_survive_refresh_failure(self, fetch):
        now = timezone.now().timestamp()
        row = {'rate': Decimal('.02'), 'date': timezone.now().date().isoformat(), 'expires_at': now + 600}
        cache.set('checkout-fx:v1:rates:USD', {'fetched_at': now - 90000, 'rates': {'PHP': row}})
        self.assertEqual(checkout_rates(self.user, {'PHP'})[1]['PHP']['rate'], Decimal('.02'))
        row['expires_at'] = now + 100
        cache.set('checkout-fx:v1:rates:USD', {'fetched_at': now - 90000, 'rates': {'PHP': row}})
        with self.assertRaises(ConversionUnavailable):
            checkout_rates(self.user, {'PHP'})

    @patch('apps.checkout.services.pricing._fetch_json')
    def test_reject_invalid_missing_future_or_old_rates(self, fetch):
        for update in [{'rate': 'NaN'}, {'rate': 'Infinity'}, {'rate': 0}, {'rate': -1},
                       {'date': '2000-01-01'}, {'date': '2999-01-01'}, {'base': 'EUR'}, {'quote': 'EUR'}]:
            cache.clear()
            fetch.return_value = [self.row(**update)]
            with self.subTest(update=update), self.assertRaises(ConversionUnavailable):
                checkout_rates(self.user, {'PHP'})

    @patch('apps.checkout.services.pricing.urlopen')
    def test_malformed_external_payloads_fail_closed(self, fetch):
        for body in [b'not json', b'{}', b'null', b'[]', b'x' * 131073]:
            cache.clear()
            response = MagicMock()
            response.__enter__.return_value.read.return_value = body
            fetch.return_value = response
            with self.subTest(body=body[:20]), self.assertRaises(ConversionUnavailable):
                checkout_rates(self.user, {'PHP'})

    @patch('apps.checkout.services.pricing._fetch_json')
    def test_target_override_keeps_stripe_usd_independent_of_address(self, fetch):
        self.user.address.country_code = 'PH'
        self.assertEqual(checkout_rates(self.user, {'PHP'}),
                         ('PHP', {'PHP': {'rate': Decimal(1), 'date': None}}))
        self.assertEqual(checkout_rates(self.user, {'USD'}, target_currency='USD'),
                         ('USD', {'USD': {'rate': Decimal(1), 'date': None}}))
        fetch.assert_not_called()

    def test_invalid_source_currency_is_never_relabelled(self):
        with self.assertRaises(ConversionUnavailable):
            checkout_rates(self.user, {'ZZZ'})
