from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from apps.ecommerce.models import Cart, CartItem, Product
from apps.ecommerce.services.carts import CartConflict, add_item
from apps.store.models import Store


class CartTests(APITestCase):
    def setUp(self):
        rates = patch('apps.ecommerce.services.carts.get_display_rates', return_value={'PHP': ('PHP', Decimal(1))})
        rates.start()
        self.addCleanup(rates.stop)
        self.user = get_user_model().objects.create_user(username='buyer', email='buyer@example.com', phone_number='+639111111111', full_name='Buyer', country_code='PH')
        self.other = get_user_model().objects.create_user(username='other', email='other@example.com', phone_number='+639222222222', full_name='Other', country_code='PH')
        self.store = Store.objects.create(name='First store', country_code='PH')
        self.store2 = Store.objects.create(name='Second store', country_code='US')
        self.product = Product.objects.create(store=self.store, name='Camera', price='12.34', stock=5, status='active')
        self.product2 = Product.objects.create(store=self.store2, name='Lens', price='20.00', stock=3, status='active')
        self.client.force_authenticate(self.user)

    def add(self, product=None, quantity=1):
        return self.client.post('/api/shop/carts/items/', {'product_id': (product or self.product).pk, 'quantity': quantity}, format='json')

    def test_authentication_and_empty_read_does_not_create_cart(self):
        self.assertEqual(self.client.get('/api/shop/carts/').json(), {'carts': []})
        self.assertFalse(Cart.objects.exists())
        self.client.force_authenticate(None)
        for method, path in [('get', '/api/shop/carts/'), ('post', '/api/shop/carts/items/'), ('delete', '/api/shop/carts/items/'), ('patch', '/api/shop/carts/items/1/')]:
            self.assertEqual(getattr(self.client, method)(path, {}, format='json').status_code, 401)

    def test_separate_store_carts_increment_and_exact_subtotals(self):
        self.assertEqual(self.add(quantity=2).status_code, 200)
        response = self.add(quantity=1)
        row = response.json()['carts'][0]['items'][0]
        self.assertEqual(row['quantity'], 3)
        self.assertEqual(row['subtotal'], {'amount': '37.02', 'currency': 'PHP'})
        self.assertEqual(self.add(self.product2).status_code, 200)
        self.assertEqual(Cart.objects.filter(user=self.user).count(), 2)
        self.assertEqual(CartItem.objects.count(), 2)
        carts = self.client.get('/api/shop/carts/').json()['carts']
        self.assertEqual([cart['store']['id'] for cart in carts], [self.store.pk, self.store2.pk])
        self.assertEqual(carts[1]['items'][0]['subtotal']['currency'], 'USD')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_stock_checks_are_atomic_and_quantity_validation(self):
        self.add(quantity=4)
        self.assertEqual(self.add(quantity=2).status_code, 409)
        item = CartItem.objects.get()
        self.assertEqual(item.quantity, 4)
        for quantity in [0, -1, 1.5, 'invalid', 2147483648]:
            self.assertEqual(self.add(quantity=quantity).status_code, 400)
        Product.objects.filter(pk=self.product.pk).update(stock=2)
        path = f'/api/shop/carts/items/{item.pk}/'
        self.assertEqual(self.client.patch(path, {'quantity': 3}).status_code, 409)
        self.assertEqual(self.client.patch(path, {'quantity': 2}).status_code, 200)
        self.assertEqual(self.client.get('/api/shop/carts/').json()['carts'][0]['items'][0]['quantity'], 2)

    def test_owner_scoping_and_atomic_bulk_removal(self):
        self.add()
        own = CartItem.objects.get()
        other_cart = Cart.objects.create(user=self.other, store=self.store)
        foreign = CartItem.objects.create(cart=other_cart, product=self.product)
        self.assertEqual(len(self.client.get('/api/shop/carts/').json()['carts']), 1)
        self.assertEqual(self.client.patch(f'/api/shop/carts/items/{foreign.pk}/', {'quantity': 2}).status_code, 404)
        self.assertEqual(self.client.delete('/api/shop/carts/items/', {'item_ids': [own.pk, foreign.pk]}, format='json').status_code, 404)
        self.assertTrue(CartItem.objects.filter(pk=own.pk).exists())
        self.add(self.product2)
        ids = list(CartItem.objects.filter(cart__user=self.user).values_list('pk', flat=True))
        response = self.client.delete('/api/shop/carts/items/', {'item_ids': ids}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'carts': []})
        self.assertFalse(Cart.objects.filter(user=self.user).exists())
        self.assertTrue(CartItem.objects.filter(pk=foreign.pk).exists())

    def test_current_prices_stock_and_unpublished_redaction(self):
        self.add()
        item = CartItem.objects.get()
        Product.objects.filter(pk=self.product.pk).update(stock=0, price='15.00')
        row = self.client.get('/api/shop/carts/').json()['carts'][0]['items'][0]
        self.assertFalse(row['product']['can_purchase'])
        self.assertEqual(row['subtotal']['amount'], '15.00')
        self.assertEqual(self.add().status_code, 409)
        Product.objects.filter(pk=self.product.pk).update(status='archived')
        self.assertEqual(self.add().status_code, 404)
        self.assertEqual(self.client.patch(f'/api/shop/carts/items/{item.pk}/', {'quantity': 1}).status_code, 409)
        Product.objects.filter(pk=self.product.pk).update(status='draft', name='Private draft')
        row = self.client.get('/api/shop/carts/').json()['carts'][0]['items'][0]
        self.assertEqual(row['product']['name'], 'Unavailable product')
        self.assertIsNone(row['product']['display_price'])
        self.assertIsNone(row['subtotal'])
        self.assertEqual(self.client.delete('/api/shop/carts/items/', {'item_ids': [item.pk]}, format='json').status_code, 200)

    def test_model_store_membership_and_positive_quantity(self):
        cart = Cart.objects.create(user=self.user, store=self.store)
        with self.assertRaises(ValidationError):
            CartItem.objects.create(cart=cart, product=self.product2)
        with self.assertRaises(ValidationError):
            CartItem.objects.create(cart=cart, product=self.product, quantity=0)
        CartItem.objects.create(cart=cart, product=self.product)
        self.product.store = self.store2
        with self.assertRaises(ValidationError):
            self.product.save()


class ConcurrentCartTests(TransactionTestCase):
    def test_concurrent_additions_do_not_lose_updates_or_exceed_stock(self):
        user = get_user_model().objects.create_user(username='parallel', email='parallel@example.com', phone_number='+639333333333', full_name='Buyer', country_code='PH')
        store = Store.objects.create(name='Concurrent store', country_code='PH')
        product = Product.objects.create(store=store, name='Camera', price='10', stock=3, status='active')
        barrier = Barrier(2)

        def add():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                add_item(user, product.pk, 2)
                return 'ok'
            except CartConflict:
                return 'conflict'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: add(), range(2)))
        self.assertCountEqual(outcomes, ['ok', 'conflict'])
        self.assertEqual(Cart.objects.filter(user=user).count(), 1)
        self.assertEqual(CartItem.objects.get().quantity, 2)


class StoreCartMigrationTests(TransactionTestCase):
    def test_split_and_rollback_preserve_item_ids_and_quantities(self):
        executor = MigrationExecutor(connection)
        new = executor.loader.graph.leaf_nodes()
        old = [node for node in new if node[0] != 'ecommerce'] + [('ecommerce', '0012_order_concluded_at_order_recipient_info_and_more')]
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            user = apps.get_model('authentication', 'User').objects.create(username='legacy', email='legacy@example.com', phone_number='+639444444444', full_name='Legacy', country_code='PH')
            stores = [apps.get_model('store', 'Store').objects.create(name=name, country_code='PH', currency='PHP') for name in ['One', 'Two']]
            products = [apps.get_model('ecommerce', 'Product').objects.create(store_id=store.pk, name=store.name, price='10', stock=5) for store in stores]
            cart = apps.get_model('ecommerce', 'Cart').objects.create(user_id=user.pk)
            rows = [apps.get_model('ecommerce', 'CartItem').objects.create(cart_id=cart.pk, product_id=product.pk, quantity=index + 2) for index, product in enumerate(products)]
            MigrationExecutor(connection).migrate(new)
            self.assertEqual(Cart.objects.filter(user_id=user.pk).count(), 2)
            self.assertEqual(list(CartItem.objects.order_by('pk').values_list('pk', 'quantity')), [(row.pk, row.quantity) for row in rows])
            for item in CartItem.objects.select_related('cart', 'product'):
                self.assertEqual(item.cart.store_id, item.product.store_id)
            MigrationExecutor(connection).migrate(old)
            self.assertEqual(apps.get_model('ecommerce', 'Cart').objects.filter(user_id=user.pk).count(), 1)
            self.assertEqual(list(apps.get_model('ecommerce', 'CartItem').objects.order_by('pk').values_list('pk', 'quantity')), [(row.pk, row.quantity) for row in rows])
        finally:
            MigrationExecutor(connection).migrate(new)
