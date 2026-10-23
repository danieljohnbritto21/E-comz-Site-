import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError

from apps.accounts.models import User, SellerProfile, BuyerProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord, StockMovement
from apps.inventory.services import (
    validate_purchase_quantity,
    deduct_stock_for_order,
    restore_stock_for_order,
    restore_stock_for_sub_order,
    InventoryError
)
from apps.cart.models import Cart, CartItem
from apps.orders.models import Order, OrderItem, SellerSubOrder, Address


class BikeDeliveryPurchaseLimitsAndInventoryTests(TestCase):
    """
    Automated test suite verifying Fresh Mart's unit-based maximum quantity
    per order for bike delivery and inventory controls across all 12 required areas:
    
    1. A customer can order up to 5 kg when at least 5 kg is available.
    2. A customer cannot order more than 5 kg of potatoes in one order.
    3. A customer can order up to 5 litres, but not 6 litres.
    4. A customer can order up to 5 bunches, but not 6 bunches.
    5. Products with fewer than 5 units in stock are limited to their actual available stock.
    6. Out-of-stock products cannot be added to the cart.
    7. Duplicate cart lines cannot bypass the per-product limit.
    8. Checkout rejects quantities above the configured maximum.
    9. Direct API or manually altered requests cannot bypass the backend checks.
    10. Different units receive the correct limits.
    11. Sellers cannot change another seller's products or stock.
    12. Existing login, product listing, cart, checkout, and order features continue to work.
    """

    def setUp(self):
        # 1. Admin
        self.admin = User.objects.create_superuser(
            username='admin_test',
            email='admin@freshmart.test',
            password='password123',
            role=User.Role.ADMIN
        )

        # 2. Seller 1 (Snacks & Vegetables)
        self.seller_1 = User.objects.create_user(
            username='seller_snack',
            email='seller1@freshmart.test',
            password='password123',
            role=User.Role.SELLER
        )
        self.seller_profile_1 = SellerProfile.objects.create(
            user=self.seller_1,
            store_name='Green Agro & Snacks',
            store_slug='green-agro-snacks',
            is_approved=True
        )

        # 3. Seller 2 (Dairy & Produce)
        self.seller_2 = User.objects.create_user(
            username='seller_dairy',
            email='seller2@freshmart.test',
            password='password123',
            role=User.Role.SELLER
        )
        self.seller_profile_2 = SellerProfile.objects.create(
            user=self.seller_2,
            store_name='Pure Dairy & Greens',
            store_slug='pure-dairy-greens',
            is_approved=True
        )

        # 4. Customers
        self.buyer_1 = User.objects.create_user(
            username='buyer_alice',
            email='alice@freshmart.test',
            password='password123',
            role=User.Role.BUYER
        )
        BuyerProfile.objects.create(user=self.buyer_1)
        self.address_1 = Address.objects.create(
            user=self.buyer_1,
            full_name='Alice Buyer',
            phone_number='9876543210',
            house_number='12B',
            street='Market Road',
            area='Central',
            city='Bangalore',
            state='Karnataka',
            pin_code='560001',
            is_default=True
        )

        self.buyer_2 = User.objects.create_user(
            username='buyer_bob',
            email='bob@freshmart.test',
            password='password123',
            role=User.Role.BUYER
        )
        BuyerProfile.objects.create(user=self.buyer_2)
        self.address_2 = Address.objects.create(
            user=self.buyer_2,
            full_name='Bob Buyer',
            phone_number='9876543211',
            house_number='14C',
            street='Green Lane',
            area='North',
            city='Bangalore',
            state='Karnataka',
            pin_code='560001',
            is_default=True
        )

        # 5. Categories
        self.category_snacks = Category.objects.create(name='Snacks', slug='snacks', is_active=True)
        self.category_veg = Category.objects.create(name='Vegetables', slug='vegetables', is_active=True)
        self.category_dairy = Category.objects.create(name='Dairy', slug='dairy', is_active=True)

        # 6. Products with specific units & inventory
        # Potatoes: 200 kg stock, unit 'kg' -> default bike limit 5 kg
        self.potatoes = Product.objects.create(
            seller=self.seller_1,
            category=self.category_veg,
            name='Farm Fresh Potatoes',
            slug='farm-fresh-potatoes',
            sku='SKU-POTATO-200',
            unit='kg',
            unit_value='1 kg',
            selling_price=Decimal('30.00'),
            mrp=Decimal('40.00'),
            product_cost=Decimal('20.00'),
            is_active=True
        )
        self.stock_potatoes = StockRecord.objects.create(
            product=self.potatoes,
            current_stock=200,
            low_stock_threshold=20
        )

        # Milk: 50 litres stock, unit 'litres' -> default bike limit 5 litres
        self.milk = Product.objects.create(
            seller=self.seller_2,
            category=self.category_dairy,
            name='Fresh Cow Milk',
            slug='fresh-cow-milk',
            sku='SKU-MILK-50',
            unit='litres',
            unit_value='1 Litre Pouch',
            selling_price=Decimal('60.00'),
            mrp=Decimal('65.00'),
            product_cost=Decimal('50.00'),
            is_active=True
        )
        self.stock_milk = StockRecord.objects.create(
            product=self.milk,
            current_stock=50,
            low_stock_threshold=10
        )

        # Bananas: 100 bunches stock, unit 'bunch' -> default bike limit 5 bunches
        self.bananas = Product.objects.create(
            seller=self.seller_1,
            category=self.category_veg,
            name='Robusta Bananas',
            slug='robusta-bananas',
            sku='SKU-BANANA-100',
            unit='bunch',
            unit_value='1 bunch (6-8 pcs)',
            selling_price=Decimal('45.00'),
            mrp=Decimal('50.00'),
            product_cost=Decimal('35.00'),
            is_active=True
        )
        self.stock_bananas = StockRecord.objects.create(
            product=self.bananas,
            current_stock=100,
            low_stock_threshold=15
        )

        # Lay's: 4 packets in stock, unit 'packet' -> default bike limit 5 packets, actual max 4
        self.lays_low_stock = Product.objects.create(
            seller=self.seller_1,
            category=self.category_snacks,
            name="Lay's Magic Masala",
            slug='lays-magic-masala',
            sku='SKU-LAYS-4',
            unit='packets',
            unit_value='52 g packet',
            selling_price=Decimal('20.00'),
            mrp=Decimal('25.00'),
            product_cost=Decimal('15.00'),
            is_active=True
        )
        self.stock_lays_low = StockRecord.objects.create(
            product=self.lays_low_stock,
            current_stock=4,
            low_stock_threshold=5
        )

        # Potatoes Low Stock: 3 kg in stock, unit 'kg' -> actual max 3 kg
        self.potatoes_3kg = Product.objects.create(
            seller=self.seller_1,
            category=self.category_veg,
            name='Baby Potatoes',
            slug='baby-potatoes',
            sku='SKU-BABY-POTATO-3',
            unit='kg',
            unit_value='1 kg',
            selling_price=Decimal('35.00'),
            mrp=Decimal('45.00'),
            is_active=True
        )
        self.stock_potatoes_3kg = StockRecord.objects.create(
            product=self.potatoes_3kg,
            current_stock=3,
            low_stock_threshold=5
        )

        # Out of stock Apples - 0 kg
        self.apples_out_of_stock = Product.objects.create(
            seller=self.seller_1,
            category=self.category_veg,
            name='Kashmiri Apples',
            slug='kashmiri-apples',
            sku='SKU-APPLES-0',
            unit='kg',
            unit_value='1 kg',
            selling_price=Decimal('180.00'),
            mrp=Decimal('220.00'),
            product_cost=Decimal('150.00'),
            is_active=True
        )
        self.stock_apples = StockRecord.objects.create(
            product=self.apples_out_of_stock,
            current_stock=0,
            low_stock_threshold=10
        )

        # Lay's Normal: 100 packets
        self.lays = Product.objects.create(
            seller=self.seller_1,
            category=self.category_snacks,
            name="Lay's Classic Salted",
            slug='lays-classic-salted',
            sku='SKU-LAYS-100',
            unit='packets',
            unit_value='52 g packet',
            selling_price=Decimal('20.00'),
            mrp=Decimal('25.00'),
            product_cost=Decimal('15.00'),
            is_active=True
        )
        self.stock_lays = StockRecord.objects.create(
            product=self.lays,
            current_stock=100,
            low_stock_threshold=20
        )

        # Eggs: 25 pieces
        self.eggs = Product.objects.create(
            seller=self.seller_2,
            category=self.category_dairy,
            name='Country Brown Eggs',
            slug='country-brown-eggs',
            sku='SKU-EGGS-25',
            unit='pieces',
            unit_value='6 pieces pack',
            selling_price=Decimal('55.00'),
            mrp=Decimal('60.00'),
            product_cost=Decimal('45.00'),
            is_active=True
        )
        self.stock_eggs = StockRecord.objects.create(
            product=self.eggs,
            current_stock=25,
            low_stock_threshold=10
        )
        # Mark setUp products as approved for storefront browsing
        Product.objects.filter(is_active=True).update(is_approved=True, approval_status=Product.ApprovalStatus.APPROVED)

        # HTTP Clients
        self.client_alice = Client()
        self.client_alice.force_login(self.buyer_1)

        self.client_bob = Client()
        self.client_bob.force_login(self.buyer_2)

        self.client_seller1 = Client()
        self.client_seller1.force_login(self.seller_1)

        self.client_seller2 = Client()
        self.client_seller2.force_login(self.seller_2)

        self.client_admin = Client()
        self.client_admin.force_login(self.admin)

    # --------------------------------------------------------------------------
    # 1. A customer can order up to 5 kg when at least 5 kg is available
    # --------------------------------------------------------------------------
    def test_01_customer_can_order_up_to_5kg_when_available(self):
        """Potatoes has 200 kg in stock. Ordering 5 kg must succeed."""
        self.assertEqual(self.potatoes.current_stock_count, 200)
        self.assertEqual(self.potatoes.per_order_limit, 5)
        self.assertEqual(self.potatoes.actual_max_order_quantity, 5)

        url = reverse('cart:add_to_cart')
        resp = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('item_quantity'), 5)
        self.assertEqual(data.get('per_order_limit'), 5)
        self.assertEqual(data.get('actual_max_order_quantity'), 5)

    # --------------------------------------------------------------------------
    # 2. A customer cannot order more than 5 kg of potatoes in one order
    # --------------------------------------------------------------------------
    def test_02_customer_cannot_order_more_than_5kg_potatoes(self):
        """Potatoes has 200 kg in stock, but customer cannot add 6 kg (exceeds bike delivery limit)."""
        url = reverse('cart:add_to_cart')
        resp = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': 6}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data.get('success'))
        self.assertIn('maximum of 5 kg', data.get('error', '').lower())
        self.assertIn('per order', data.get('error', '').lower())

    # --------------------------------------------------------------------------
    # 3. A customer can order up to 5 litres, but not 6 litres
    # --------------------------------------------------------------------------
    def test_03_customer_can_order_up_to_5_litres_not_6(self):
        """Milk has 50 litres in stock. 5 litres is allowed, 6 litres is rejected."""
        url = reverse('cart:add_to_cart')
        
        # 6 litres rejected
        resp_fail = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.milk.id, 'quantity': 6}),
            content_type='application/json'
        )
        self.assertEqual(resp_fail.status_code, 400)
        self.assertIn('maximum of 5 litres', resp_fail.json().get('error', '').lower())

        # 5 litres accepted
        resp_ok = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.milk.id, 'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertEqual(resp_ok.json().get('item_quantity'), 5)

    # --------------------------------------------------------------------------
    # 4. A customer can order up to 5 bunches, but not 6 bunches
    # --------------------------------------------------------------------------
    def test_04_customer_can_order_up_to_5_bunches_not_6(self):
        """Bananas has 100 bunches in stock. 5 bunches is allowed, 6 bunches is rejected."""
        url = reverse('cart:add_to_cart')

        # 6 bunches rejected
        resp_fail = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.bananas.id, 'quantity': 6}),
            content_type='application/json'
        )
        self.assertEqual(resp_fail.status_code, 400)
        self.assertIn('maximum of 5 bunches', resp_fail.json().get('error', '').lower())

        # 5 bunches accepted
        resp_ok = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.bananas.id, 'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertEqual(resp_ok.json().get('item_quantity'), 5)

    # --------------------------------------------------------------------------
    # 5. Products with fewer than 5 units in stock are limited to their actual available stock
    # --------------------------------------------------------------------------
    def test_05_fewer_than_5_units_limited_to_available_stock(self):
        """
        - Baby potatoes has 3 kg in stock -> max order is 3 kg.
        - Lay's Magic Masala has 4 packets in stock -> max order is 4 packets.
        """
        self.assertEqual(self.potatoes_3kg.actual_max_order_quantity, 3)
        self.assertEqual(self.lays_low_stock.actual_max_order_quantity, 4)

        url = reverse('cart:add_to_cart')

        # Attempting 5 kg of baby potatoes when only 3 kg in stock -> rejected with available stock explanation
        resp_p5 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes_3kg.id, 'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(resp_p5.status_code, 400)
        self.assertIn('only 3 kg', resp_p5.json().get('error', '').lower())
        self.assertIn('available in stock', resp_p5.json().get('error', '').lower())

        # Adding 3 kg succeeds
        resp_p3 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes_3kg.id, 'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(resp_p3.status_code, 200)
        self.assertEqual(resp_p3.json().get('item_quantity'), 3)

        # Attempting 5 packets of Lay's when only 4 packets in stock -> rejected with available stock explanation
        resp_l5 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.lays_low_stock.id, 'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(resp_l5.status_code, 400)
        self.assertIn('only 4 packets', resp_l5.json().get('error', '').lower())

        # Adding 4 packets succeeds
        resp_l4 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.lays_low_stock.id, 'quantity': 4}),
            content_type='application/json'
        )
        self.assertEqual(resp_l4.status_code, 200)
        self.assertEqual(resp_l4.json().get('item_quantity'), 4)

    # --------------------------------------------------------------------------
    # 6. Out-of-stock products cannot be added to the cart
    # --------------------------------------------------------------------------
    def test_06_out_of_stock_products_cannot_be_added_to_cart(self):
        """Apples have 0 stock. Attempting to add 1 unit to cart must fail with Out of Stock message and button disabled."""
        self.assertEqual(self.apples_out_of_stock.actual_max_order_quantity, 0)

        url = reverse('cart:add_to_cart')
        resp = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.apples_out_of_stock.id, 'quantity': 1}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('out of stock', resp.json().get('error', '').lower())

        # Check storefront detail page renders disabled button
        detail_url = reverse('catalog:product_detail', kwargs={'slug': self.apples_out_of_stock.slug})
        resp_detail = self.client_alice.get(detail_url)
        self.assertEqual(resp_detail.status_code, 200)
        self.assertIn('Out of Stock', resp_detail.content.decode('utf-8'))
        self.assertIn('disabled', resp_detail.content.decode('utf-8'))

    # --------------------------------------------------------------------------
    # 7. Duplicate cart lines cannot bypass the per-product limit
    # --------------------------------------------------------------------------
    def test_07_duplicate_cart_lines_cannot_bypass_limit(self):
        """Customer adds 3 kg of potatoes, then attempts to add 3 kg more. Total is 6 kg > 5 kg limit, must be blocked."""
        url = reverse('cart:add_to_cart')

        # Add 3 kg first
        resp1 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json().get('item_quantity'), 3)

        # Attempt to add 3 kg more (cumulative would be 6 kg) -> Rejected!
        resp2 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('maximum of 5 kg', resp2.json().get('error', '').lower())
        self.assertIn('already have 3', resp2.json().get('error', '').lower())

        # Verify cart quantity remained 3
        cart = Cart.objects.get(user=self.buyer_1)
        items = CartItem.objects.filter(cart=cart, product=self.potatoes)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.first().quantity, 3)

        # Adding 2 kg more (3 + 2 = 5 kg) reaches limit and succeeds
        resp3 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp3.json().get('item_quantity'), 5)

        # Attempting 1 more when at 5 kg is blocked
        resp4 = self.client_alice.post(
            url,
            data=json.dumps({'product_id': self.potatoes.id, 'delta': 1}),
            content_type='application/json'
        )
        self.assertEqual(resp4.status_code, 400)
        self.assertIn('maximum of 5 kg', resp4.json().get('error', '').lower())

    # --------------------------------------------------------------------------
    # 8. Checkout rejects quantities above the configured maximum
    # --------------------------------------------------------------------------
    def test_08_checkout_rejects_quantities_above_configured_maximum(self):
        """Even if cart item was forged to 8 kg of potatoes, checkout gateway prevents order completion."""
        cart = Cart.objects.create(user=self.buyer_1)
        # Directly insert 8 kg
        cart_item = CartItem.objects.create(cart=cart, product=self.potatoes, quantity=8)
        self.assertFalse(cart_item.is_available)

        checkout_url = reverse('orders:checkout')
        checkout_payload = {
            'address_id': str(self.address_1.id),
            'payment_method': 'COD',
            'delivery_slot': '07:00 AM - 10:00 AM',
            'checkout_token': 'TOKEN-CHECKOUT-LIMIT-TEST'
        }

        # GET checkout page redirects to cart with error
        resp_get = self.client_alice.get(checkout_url)
        self.assertEqual(resp_get.status_code, 302)
        self.assertEqual(resp_get.url, reverse('cart:view_cart'))

        # POST checkout attempt also blocked
        resp_post = self.client_alice.post(checkout_url, data=checkout_payload)
        self.assertEqual(resp_post.status_code, 302)
        self.assertEqual(resp_post.url, reverse('cart:view_cart'))

        # No order created and stock unchanged
        self.assertEqual(Order.objects.filter(buyer=self.buyer_1).count(), 0)
        self.stock_potatoes.refresh_from_db()
        self.assertEqual(self.stock_potatoes.current_stock, 200)

    # --------------------------------------------------------------------------
    # 9. Direct API or manually altered requests cannot bypass the backend checks
    # --------------------------------------------------------------------------
    def test_09_direct_api_or_tampered_requests_blocked(self):
        """Forged requests to /cart/update/ or /cart/add/ with massive, altered, negative, or decimal quantities are rejected."""
        cart = Cart.objects.create(user=self.buyer_1)
        cart_item = CartItem.objects.create(cart=cart, product=self.potatoes, quantity=2)

        update_url = reverse('cart:update_cart', kwargs={'item_id': cart_item.id})

        # Tamper cart update endpoint with 9999
        resp_9999 = self.client_alice.post(
            update_url,
            data=json.dumps({'quantity': 9999}),
            content_type='application/json'
        )
        self.assertEqual(resp_9999.status_code, 400)
        self.assertIn('maximum of 5 kg', resp_9999.json().get('error', '').lower())

        # Tamper cart update endpoint with 6 kg
        resp_6 = self.client_alice.post(
            update_url,
            data=json.dumps({'quantity': 6}),
            content_type='application/json'
        )
        self.assertEqual(resp_6.status_code, 400)
        self.assertIn('maximum of 5 kg', resp_6.json().get('error', '').lower())

        # Tamper with decimal above limit: 5.5 kg potatoes (exceeds 5 kg maximum)
        add_url = reverse('cart:add_to_cart')
        resp_dec_exceed = self.client_alice.post(
            add_url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': 5.5}),
            content_type='application/json'
        )
        self.assertEqual(resp_dec_exceed.status_code, 400)
        self.assertIn('maximum of 5 kg', resp_dec_exceed.json().get('error', '').lower())

        # Tamper with decimal above limit for milk: 5.2 litres (exceeds 5 litres maximum)
        resp_dec_milk = self.client_alice.post(
            add_url,
            data=json.dumps({'product_id': self.milk.id, 'quantity': 5.2}),
            content_type='application/json'
        )
        self.assertEqual(resp_dec_milk.status_code, 400)
        self.assertIn('maximum of 5 litres', resp_dec_milk.json().get('error', '').lower())

        # Tamper with fractional quantity for whole-unit product (e.g. Lay's packets: 2.5 packets)
        resp_frac_lays = self.client_alice.post(
            add_url,
            data=json.dumps({'product_id': self.lays.id, 'quantity': 2.5}),
            content_type='application/json'
        )
        self.assertEqual(resp_frac_lays.status_code, 400)

        # Tamper with negative quantity
        resp_neg = self.client_alice.post(
            add_url,
            data=json.dumps({'product_id': self.potatoes.id, 'quantity': -5}),
            content_type='application/json'
        )
        self.assertEqual(resp_neg.status_code, 400)

        # Direct service validation check with Decimal
        is_valid, err, actual_max = validate_purchase_quantity(self.potatoes, Decimal('5.5'))
        self.assertFalse(is_valid)
        self.assertIn('maximum of 5 kg', err.lower())

        # Verify property aliases: selling_unit and stock_quantity
        self.assertEqual(self.potatoes.selling_unit, 'kg')
        self.assertEqual(self.potatoes.stock_quantity, 200)

        # DB quantity was not changed
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 2)

    # --------------------------------------------------------------------------
    # 10. Different units receive the correct limits
    # --------------------------------------------------------------------------
    def test_10_different_units_receive_correct_limits(self):
        """
        Verify unit-based default limits:
        - kg -> 5
        - litre / L -> 5
        - bunch -> 5
        - packet -> 5
        - piece -> 5
        - bottle -> 5
        - box -> 5
        - custom admin configured limits override the default.
        """
        self.assertEqual(Product.get_default_limit_for_unit('kg'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('kilogram'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('litre'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('liter'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('L'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('bunch'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('bunches'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('packet'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('packets'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('pack'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('piece'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('pieces'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('bottle'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('bottles'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('box'), 5)
        self.assertEqual(Product.get_default_limit_for_unit('boxes'), 5)
        # Safe default for unrecognised
        self.assertEqual(Product.get_default_limit_for_unit('unrecognised_unit'), 5)

        # Bottle product test
        bottle_oil = Product.objects.create(
            seller=self.seller_1,
            category=self.category_snacks,
            name='Sunflower Oil Bottle',
            sku='SKU-OIL-BOTTLE',
            unit='bottle',
            unit_value='1 L bottle',
            selling_price=Decimal('150.00'),
            mrp=Decimal('160.00'),
            product_cost=Decimal('120.00'),
            is_active=True
        )
        StockRecord.objects.create(product=bottle_oil, current_stock=30)
        self.assertEqual(bottle_oil.per_order_limit, 5)

        # Custom configured limit by admin
        bottle_oil.max_quantity_per_order = 2
        bottle_oil.save()
        self.assertEqual(bottle_oil.per_order_limit, 2)
        self.assertEqual(bottle_oil.actual_max_order_quantity, 2)

        # Ordering 3 bottles now rejected by custom limit of 2
        url = reverse('cart:add_to_cart')
        resp = self.client_alice.post(
            url,
            data=json.dumps({'product_id': bottle_oil.id, 'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('maximum of 2 bottles', resp.json().get('error', '').lower())

    # --------------------------------------------------------------------------
    # 11. Sellers cannot change another seller's products or stock
    # --------------------------------------------------------------------------
    def test_11_sellers_cannot_change_another_seller_products_or_stock(self):
        """Seller 2 (Dairy) attempts to adjust stock of Lay's (Seller 1). Must be rejected and stock unaffected."""
        inv_url = reverse('seller:inventory')
        
        # Seller 2 posts an adjustment targeting Seller 1's product
        resp = self.client_seller2.post(inv_url, data={
            'product_id': self.potatoes.id,
            'quantity_change': 50,
            'change_type': 'RESTOCK',
            'reason': 'Malicious forged inward'
        })
        self.stock_potatoes.refresh_from_db()
        self.assertEqual(self.stock_potatoes.current_stock, 200) # Unchanged!

        # Also Seller 2 cannot access product edit for Seller 1's product
        edit_url = reverse('seller:product_edit', kwargs={'product_id': self.potatoes.id})
        resp_edit = self.client_seller2.get(edit_url)
        self.assertEqual(resp_edit.status_code, 404)

    # --------------------------------------------------------------------------
    # 12. Existing login, product listing, cart, checkout, and order features continue to work
    # --------------------------------------------------------------------------
    def test_12_existing_functionality_continues_to_work(self):
        """
        Placing an order within permitted limits (4 kg potatoes and 2 litres milk)
        successfully checks out, deducts stock atomically, and cancellation restores stock.
        """
        # Storefront listing works
        home_url = reverse('catalog:home')
        resp_home = self.client_alice.get(home_url)
        self.assertEqual(resp_home.status_code, 200)

        # Product detail works
        detail_url = reverse('catalog:product_detail', kwargs={'slug': self.potatoes.slug})
        resp_detail = self.client_alice.get(detail_url)
        self.assertEqual(resp_detail.status_code, 200)

        # Cart addition works for 4 kg potatoes and 2 litres milk
        cart = Cart.objects.create(user=self.buyer_1)
        CartItem.objects.create(cart=cart, product=self.potatoes, quantity=4)
        CartItem.objects.create(cart=cart, product=self.milk, quantity=2)

        checkout_url = reverse('orders:checkout')
        checkout_payload = {
            'address_id': str(self.address_1.id),
            'payment_method': 'COD',
            'delivery_slot': '07:00 AM - 10:00 AM',
            'checkout_token': 'TOKEN-EXISTING-FLOW-123'
        }

        resp_checkout = self.client_alice.post(checkout_url, data=checkout_payload)
        self.assertEqual(resp_checkout.status_code, 302)

        # Verify stock deducted
        self.stock_potatoes.refresh_from_db()
        self.assertEqual(self.stock_potatoes.current_stock, 196) # 200 - 4
        self.stock_milk.refresh_from_db()
        self.assertEqual(self.stock_milk.current_stock, 48) # 50 - 2

        # Verify order created
        order = Order.objects.filter(buyer=self.buyer_1).first()
        self.assertIsNotNone(order)
        self.assertTrue(order.stock_deducted)

        # Cancel order restores stock
        cancel_url = reverse('orders:cancel_order', kwargs={'order_id': order.order_id})
        resp_cancel = self.client_alice.post(cancel_url)
        self.assertEqual(resp_cancel.status_code, 302)

        self.stock_potatoes.refresh_from_db()
        self.assertEqual(self.stock_potatoes.current_stock, 200)
        self.stock_milk.refresh_from_db()
        self.assertEqual(self.stock_milk.current_stock, 50)
