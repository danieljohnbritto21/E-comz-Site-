import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse

from apps.accounts.models import User, SellerProfile, BuyerProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord
from apps.cart.models import Cart, CartItem
from apps.orders.models import Order, OrderItem, Address


class FreshMartCartSynchronizationTests(TestCase):
    """
    Automated regression test suite verifying cart synchronization, floating basket counter,
    view basket navigation, session persistence, and guest-to-customer merging.
    """

    def setUp(self):
        # 1. Customer User A
        self.buyer_a = User.objects.create_user(
            username='daniel_buyer',
            email='daniel@freshmart.test',
            password='password123',
            role=User.Role.BUYER
        )
        self.buyer_profile_a = BuyerProfile.objects.create(user=self.buyer_a)

        # 2. Customer User B
        self.buyer_b = User.objects.create_user(
            username='other_buyer',
            email='other@freshmart.test',
            password='password123',
            role=User.Role.BUYER
        )
        self.buyer_profile_b = BuyerProfile.objects.create(user=self.buyer_b)

        # 3. Seller
        self.seller = User.objects.create_user(
            username='fresh_farms',
            email='seller@freshmart.test',
            password='password123',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller,
            store_name='Green Valley Organics',
            store_slug='green-valley-organics',
            is_approved=True
        )

        # 4. Category
        self.category = Category.objects.create(name='Groceries', slug='groceries')

        # 5. Products
        # Product 1: Mustard Oil (Kisan Gold)
        self.mustard_oil = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Pure Cold-Pressed Kachi Ghani Mustard Oil',
            slug='mustard-oil-1l',
            sku='FM-OIL-001',
            selling_price=Decimal('149.00'),
            mrp=Decimal('185.00'),
            unit='bottle',
            max_quantity_per_order=50,
            is_active=True,
            is_approved=True
        )
        self.oil_stock = StockRecord.objects.create(
            product=self.mustard_oil,
            current_stock=100,
            low_stock_threshold=5
        )

        # Product 2: Lay's Chips
        self.chips = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name="Lay's Classic Salted Potato Chips",
            slug='lays-classic-salted',
            sku='FM-CHIPS-001',
            selling_price=Decimal('20.00'),
            mrp=Decimal('20.00'),
            unit='packet',
            max_quantity_per_order=10,
            is_active=True,
            is_approved=True
        )
        self.chips_stock = StockRecord.objects.create(
            product=self.chips,
            current_stock=50,
            low_stock_threshold=5
        )

        # Product 3: Country Brown Eggs
        self.eggs = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Farm Fresh Country Brown Eggs',
            slug='farm-fresh-eggs-6',
            sku='FM-EGGS-001',
            selling_price=Decimal('54.00'),
            mrp=Decimal('65.00'),
            unit='piece',
            max_quantity_per_order=10,
            is_active=True,
            is_approved=True
        )
        self.eggs_stock = StockRecord.objects.create(
            product=self.eggs,
            current_stock=30,
            low_stock_threshold=5
        )

        self.client_a = Client()
        self.client_b = Client()

    def test_1_adding_products_updates_floating_basket_counter_and_total(self):
        """1. Adding products via API successfully updates floating basket total_items and subtotal."""
        self.client_a.force_login(self.buyer_a)

        # Add 1 packet of chips (₹20)
        res1 = self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 1}),
            content_type='application/json'
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertTrue(data1['success'])
        self.assertEqual(data1['total_items'], 1)
        self.assertIn('20.00', data1['subtotal'])

        # Add 2 cartons of eggs (2 x ₹54 = ₹108, total = ₹128.00)
        res2 = self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.eggs.id, 'delta': 2}),
            content_type='application/json'
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2['total_items'], 3)
        self.assertIn('128.00', data2['subtotal'])

    def test_2_view_basket_displays_same_actual_cart_contents(self):
        """2. Clicking View Basket loads the exact same products, quantities, prices, and seller details."""
        self.client_a.force_login(self.buyer_a)

        # Add items
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 2}),
            content_type='application/json'
        )
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.eggs.id, 'delta': 3}),
            content_type='application/json'
        )

        # Navigate to /cart/
        response = self.client_a.get(reverse('cart:view_cart'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lay&#x27;s Classic Salted Potato Chips")
        self.assertContains(response, 'Farm Fresh Country Brown Eggs')
        self.assertContains(response, 'Green Valley Organics')
        self.assertContains(response, '(5 items &bull; 2 product lines)')
        self.assertNotContains(response, 'Your shopping basket is empty')

    def test_3_basket_containing_39_total_units_does_not_become_empty_on_navigation(self):
        """3. Exactly reproducing the user's issue: 39 items (37 oil + 1 chips + 1 eggs) preserves all items on /cart/."""
        self.client_a.force_login(self.buyer_a)

        # Add 37 oil, 1 chips, 1 eggs = 39 total items
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.mustard_oil.id, 'delta': 37}),
            content_type='application/json'
        )
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 1}),
            content_type='application/json'
        )
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.eggs.id, 'delta': 1}),
            content_type='application/json'
        )

        # Verify shop page floating dock has 39 items
        shop_res = self.client_a.get(reverse('catalog:home'))
        self.assertEqual(shop_res.status_code, 200)
        self.assertContains(shop_res, '39</span>')
        self.assertContains(shop_res, '39')

        # Navigate to /cart/ (View Basket)
        cart_res = self.client_a.get(reverse('cart:view_cart'))
        self.assertEqual(cart_res.status_code, 200)
        self.assertNotContains(cart_res, 'Your shopping basket is empty')
        self.assertContains(cart_res, '(39 items &bull; 3 product lines)')
        self.assertContains(cart_res, 'Pure Cold-Pressed Kachi Ghani Mustard Oil')
        self.assertContains(cart_res, "Lay&#x27;s Classic Salted Potato Chips")
        self.assertContains(cart_res, 'Farm Fresh Country Brown Eggs')
        # Total: (37 * 149 = 5513) + 20 + 54 = 5587.00
        self.assertContains(cart_res, '5,587.00')

    def test_4_refreshing_shop_or_basket_preserves_cart_contents(self):
        """4. Refreshing pages repeatedly maintains the same authoritative cart."""
        self.client_a.force_login(self.buyer_a)
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 4}),
            content_type='application/json'
        )

        for _ in range(3):
            res_shop = self.client_a.get(reverse('catalog:home'))
            self.assertEqual(res_shop.context['nav_cart_count'], 4)
            res_cart = self.client_a.get(reverse('cart:view_cart'))
            self.assertEqual(res_cart.context['cart'].total_items, 4)

    def test_5_incrementing_decrementing_and_removing_products(self):
        """5. Stepper adjustments update cart quantities, subtotal, and allow deletion at 0."""
        self.client_a.force_login(self.buyer_a)
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 2}),
            content_type='application/json'
        )

        user_cart = Cart.objects.get(user=self.buyer_a)
        item = user_cart.items.get(product=self.chips)

        # Increment to 3
        res_inc = self.client_a.post(
            reverse('cart:update_cart', kwargs={'item_id': item.id}),
            data=json.dumps({'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(res_inc.status_code, 200)
        self.assertEqual(res_inc.json()['total_items'], 3)
        self.assertIn('60.00', res_inc.json()['subtotal'])

        # Decrement via delta to 1
        res_dec = self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': -2}),
            content_type='application/json'
        )
        self.assertEqual(res_dec.status_code, 200)
        self.assertEqual(res_dec.json()['item_quantity'], 1)
        self.assertEqual(res_dec.json()['total_items'], 1)

        # Remove item completely
        res_rem = self.client_a.post(reverse('cart:remove_cart', kwargs={'item_id': item.id}))
        self.assertEqual(res_rem.status_code, 200)
        self.assertEqual(res_rem.json()['total_items'], 0)
        self.assertEqual(user_cart.items.count(), 0)

    def test_6_basket_totals_match_stored_cart_data(self):
        """6. Unit price, line subtotals, delivery fee, and grand total adhere to authoritative server calculations."""
        self.client_a.force_login(self.buyer_a)
        # 2 x chips @ ₹20 = ₹40. Subtotal < 499 -> delivery fee = ₹40. Grand total = ₹80.
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 2}),
            content_type='application/json'
        )
        user_cart = Cart.objects.get(user=self.buyer_a)
        self.assertEqual(user_cart.subtotal, Decimal('40.00'))
        self.assertEqual(user_cart.delivery_fee, Decimal('40.00'))
        self.assertEqual(user_cart.grand_total, Decimal('80.00'))

        res = self.client_a.get(reverse('cart:view_cart'))
        self.assertContains(res, '₹40.00')
        self.assertContains(res, '₹80.00')

    def test_7_empty_cart_displays_correct_empty_state(self):
        """7. An empty cart renders the empty basket design with aisles button."""
        self.client_a.force_login(self.buyer_a)
        res = self.client_a.get(reverse('cart:view_cart'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Your shopping basket is empty')
        self.assertContains(res, 'Browse Grocery Aisles')
        self.assertContains(res, '(0 items')

    def test_8_different_customers_cannot_access_one_another_carts(self):
        """8. Strict buyer isolation prevents Customer B from accessing Customer A's cart items."""
        self.client_a.force_login(self.buyer_a)
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 3}),
            content_type='application/json'
        )
        cart_a = Cart.objects.get(user=self.buyer_a)
        item_a = cart_a.items.first()

        # Customer B logs in
        self.client_b.force_login(self.buyer_b)
        res_b = self.client_b.get(reverse('cart:view_cart'))
        # Customer B has 0 items
        self.assertEqual(res_b.context['cart'].total_items, 0)
        self.assertContains(res_b, 'Your shopping basket is empty')

        # Customer B cannot update or remove Customer A's item (returns 404)
        res_update = self.client_b.post(
            reverse('cart:update_cart', kwargs={'item_id': item_a.id}),
            data=json.dumps({'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(res_update.status_code, 404)

        res_del = self.client_b.post(reverse('cart:remove_cart', kwargs={'item_id': item_a.id}))
        self.assertEqual(res_del.status_code, 404)

    def test_9_login_and_guest_cart_merging_works_seamlessly(self):
        """9. Guests receive login prompt with preserved product, and login merges into buyer cart."""
        guest_client = Client()

        # Guest attempts to add 2 eggs
        res_guest = guest_client.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.eggs.id, 'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(res_guest.status_code, 401)
        data = res_guest.json()
        self.assertTrue(data.get('require_login'))
        self.assertIn('pending_product_id', data.get('login_url'))

        # Guest logs in as buyer_a with preserved product
        login_res = guest_client.post(
            reverse('accounts:login'),
            {
                'username': 'daniel_buyer',
                'password': 'password123',
                'role': 'BUYER',
                'pending_product_id': self.eggs.id,
                'pending_qty': '2'
            }
        )
        self.assertEqual(login_res.status_code, 302)

        # Cart for buyer_a now contains the 2 eggs
        cart_logged_res = guest_client.get(reverse('cart:view_cart'))
        self.assertEqual(cart_logged_res.status_code, 200)
        self.assertEqual(cart_logged_res.context['cart'].total_items, 2)
        self.assertContains(cart_logged_res, 'Farm Fresh Country Brown Eggs')

        # Also test direct merge_session_cart functionality
        session_cart = Cart.objects.create(session_key='test-session-key-123')
        CartItem.objects.create(cart=session_cart, product=self.chips, quantity=3)
        from apps.cart.views import merge_session_cart
        merge_session_cart(self.buyer_a, session_key='test-session-key-123')
        buyer_cart = Cart.objects.get(user=self.buyer_a)
        self.assertEqual(buyer_cart.total_items, 5)  # 2 eggs + 3 chips

    def test_10_stock_and_per_order_quantity_restrictions_remain_enforced(self):
        """10. Cart updates strictly enforce stock availability and max_quantity_per_order limits."""
        self.client_a.force_login(self.buyer_a)

        # Attempt to order more than max_quantity_per_order (chips limit = 10)
        res_limit = self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.chips.id, 'delta': 11}),
            content_type='application/json'
        )
        self.assertEqual(res_limit.status_code, 400)
        self.assertFalse(res_limit.json()['success'])
        self.assertIn('maximum', res_limit.json()['error'].lower())

        # Attempt to order out of stock item
        out_of_stock_prod = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Exotic Apples',
            slug='exotic-apples',
            sku='FM-APPLES-OOS',
            selling_price=Decimal('199.00'),
            mrp=Decimal('240.00'),
            unit='kg',
            max_quantity_per_order=5,
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=out_of_stock_prod, current_stock=0)

        res_oos = self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': out_of_stock_prod.id, 'delta': 1}),
            content_type='application/json'
        )
        self.assertEqual(res_oos.status_code, 400)
        self.assertIn('stock', res_oos.json()['error'].lower())

    def test_11_product_listings_checkout_and_order_creation_still_work(self):
        """11. End-to-end integration: adding items, loading checkout, and creating order functions smoothly."""
        self.client_a.force_login(self.buyer_a)

        # Add 1 bottle of oil
        self.client_a.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.mustard_oil.id, 'delta': 1}),
            content_type='application/json'
        )

        # Product list reflects cart item
        prod_list_res = self.client_a.get(reverse('catalog:product_list'))
        self.assertEqual(prod_list_res.status_code, 200)
        self.assertEqual(prod_list_res.context['cart_items_map'][self.mustard_oil.id], 1)

        # Checkout page loads
        checkout_res = self.client_a.get(reverse('orders:checkout'))
        self.assertEqual(checkout_res.status_code, 200)
        self.assertContains(checkout_res, 'Order Summary')

        # Place order
        order_post = self.client_a.post(
            reverse('orders:checkout'),
            data={
                'full_name': 'Daniel',
                'phone_number': '9876543210',
                'house_number': 'Flat 4B',
                'street': 'Lake View Road',
                'pin_code': '560001',
                'payment_method': 'COD'
            }
        )
        self.assertEqual(order_post.status_code, 302)
        order = Order.objects.filter(buyer=self.buyer_a).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.items.count(), 1)

        # Cart is now empty after checkout
        cart_after = Cart.objects.get(user=self.buyer_a)
        self.assertEqual(cart_after.total_items, 0)


class CustomerCartAuthenticationRegressionTests(TestCase):
    """
    Automated regression tests specifically validating:
    1. A logged-in customer can add three products to the cart.
    2. Removing one product does not log out the customer.
    3. The customer remains authenticated after removing a product.
    4. The other two products remain in the cart with their correct quantities.
    5. The basket counter and total update correctly.
    6. The customer can continue browsing after removal.
    7. The customer can add another product without logging in again.
    8. The customer can proceed to checkout while still authenticated.
    9. Another customer's cart remains inaccessible.
    10. Invalid removal requests and CSRF failures do not accidentally log out the customer.
    11. Existing login, logout, cart, checkout, seller, and admin functionality continues to work.
    """

    def setUp(self):
        # 1. Primary Customer
        self.customer = User.objects.create_user(
            username='reg_buyer',
            email='reg_buyer@freshmart.test',
            password='BuyerPassword123!',
            role=User.Role.BUYER
        )
        self.customer_profile = BuyerProfile.objects.create(user=self.customer)

        # 2. Second Customer for isolation tests
        self.other_customer = User.objects.create_user(
            username='reg_other_buyer',
            email='other_reg@freshmart.test',
            password='BuyerPassword123!',
            role=User.Role.BUYER
        )
        self.other_profile = BuyerProfile.objects.create(user=self.other_customer)

        # 3. Seller
        self.seller = User.objects.create_user(
            username='reg_seller',
            email='seller_reg@freshmart.test',
            password='SellerPassword123!',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller,
            store_name='Fresh Valley Organics',
            store_slug='fresh-valley-organics',
            is_approved=True
        )

        # 4. Admin
        self.admin = User.objects.create_superuser(
            username='reg_admin',
            email='admin_reg@freshmart.test',
            password='AdminPassword123!',
            role=User.Role.ADMIN
        )

        # 5. Category
        self.category = Category.objects.create(name='Pantry & Dairy', slug='pantry-dairy')

        # 6. Products (4 total products for tests)
        self.prod1 = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Organic Cow Milk',
            slug='organic-cow-milk',
            sku='FM-MILK-001',
            selling_price=Decimal('60.00'),
            mrp=Decimal('65.00'),
            unit='litre',
            max_quantity_per_order=10,
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.prod1, current_stock=50, low_stock_threshold=5)

        self.prod2 = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Fresh White Bread',
            slug='fresh-white-bread',
            sku='FM-BREAD-001',
            selling_price=Decimal('45.00'),
            mrp=Decimal('50.00'),
            unit='packet',
            max_quantity_per_order=8,
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.prod2, current_stock=40, low_stock_threshold=5)

        self.prod3 = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Creamy Peanut Butter',
            slug='creamy-peanut-butter',
            sku='FM-PB-001',
            selling_price=Decimal('180.00'),
            mrp=Decimal('210.00'),
            unit='jar',
            max_quantity_per_order=5,
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.prod3, current_stock=30, low_stock_threshold=5)

        self.prod4 = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Alphonso Mango Jam',
            slug='alphonso-mango-jam',
            sku='FM-JAM-001',
            selling_price=Decimal('120.00'),
            mrp=Decimal('140.00'),
            unit='jar',
            max_quantity_per_order=6,
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.prod4, current_stock=25, low_stock_threshold=5)

        self.client = Client()

    def test_01_logged_in_customer_can_add_three_products(self):
        """1. A logged-in customer can add three products to the cart."""
        self.client.force_login(self.customer)

        # Add Product 1 (qty 2)
        r1 = self.client.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.prod1.id, 'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()['success'])

        # Add Product 2 (qty 1)
        r2 = self.client.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.prod2.id, 'quantity': 1}),
            content_type='application/json'
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()['success'])

        # Add Product 3 (qty 3)
        r3 = self.client.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.prod3.id, 'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(r3.status_code, 200)
        self.assertTrue(r3.json()['success'])

        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 3)
        self.assertEqual(cart.total_items, 6)

    def test_02_removing_one_product_does_not_log_out_customer(self):
        """2. Removing one product does not log out the customer."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=2)
        i2 = CartItem.objects.create(cart=cart, product=self.prod2, quantity=1)
        i3 = CartItem.objects.create(cart=cart, product=self.prod3, quantity=3)

        # Remove Product 2
        res = self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i2.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])

        # Session must still hold the authenticated user ID
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.customer.id)

    def test_03_customer_remains_authenticated_after_removing_product(self):
        """3. The customer remains authenticated after removing a product."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=2)
        i2 = CartItem.objects.create(cart=cart, product=self.prod2, quantity=1)
        i3 = CartItem.objects.create(cart=cart, product=self.prod3, quantity=1)

        # Remove item
        self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i1.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        # Navigate to cart page
        cart_page = self.client.get(reverse('cart:view_cart'))
        self.assertEqual(cart_page.status_code, 200)
        self.assertTrue(cart_page.wsgi_request.user.is_authenticated)
        self.assertEqual(cart_page.wsgi_request.user.id, self.customer.id)

    def test_04_other_two_products_remain_in_cart_with_correct_quantities(self):
        """4. The other two products remain in the cart with their correct quantities."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=2)
        i2 = CartItem.objects.create(cart=cart, product=self.prod2, quantity=4)
        i3 = CartItem.objects.create(cart=cart, product=self.prod3, quantity=1)

        # Remove item 2
        self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i2.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        cart.refresh_from_db()
        remaining_items = cart.items.all()
        self.assertEqual(remaining_items.count(), 2)

        item1 = remaining_items.filter(product=self.prod1).first()
        self.assertIsNotNone(item1)
        self.assertEqual(item1.quantity, 2)

        item3 = remaining_items.filter(product=self.prod3).first()
        self.assertIsNotNone(item3)
        self.assertEqual(item3.quantity, 1)

    def test_05_basket_counter_and_total_update_correctly(self):
        """5. The basket counter and total update correctly."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        # prod1: 2 * 60 = 120. prod2: 1 * 45 = 45. prod3: 1 * 180 = 180.
        # Subtotal: 345.00. Delivery (< 499): 40.00. Grand total: 385.00.
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=2)
        i2 = CartItem.objects.create(cart=cart, product=self.prod2, quantity=1)
        i3 = CartItem.objects.create(cart=cart, product=self.prod3, quantity=1)

        # Remove prod2 (45.00). New subtotal = 120 + 180 = 300.00. New total_items = 3.
        # New delivery (< 499) = 40.00. New grand total = 340.00.
        res = self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i2.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data['total_items'], 3)
        self.assertEqual(data['product_lines'], 2)
        self.assertIn('300.00', data['subtotal'])
        self.assertIn('340.00', data['grand_total'])

        # Context processor also reflects 3 items
        page_res = self.client.get(reverse('catalog:home'))
        self.assertEqual(page_res.context['nav_cart_count'], 3)

    def test_06_customer_can_continue_browsing_after_removal(self):
        """6. The customer can continue browsing after removal."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=1)

        self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i1.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        # Browse home and product list
        home_res = self.client.get(reverse('catalog:home'))
        self.assertEqual(home_res.status_code, 200)
        self.assertTrue(home_res.wsgi_request.user.is_authenticated)

        shop_res = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(shop_res.status_code, 200)
        self.assertTrue(shop_res.wsgi_request.user.is_authenticated)

    def test_07_customer_can_add_another_product_without_logging_in_again(self):
        """7. The customer can add another product without logging in again."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=1)
        i2 = CartItem.objects.create(cart=cart, product=self.prod2, quantity=1)
        i3 = CartItem.objects.create(cart=cart, product=self.prod3, quantity=1)

        # Remove i1
        self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i1.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        # Add 4th product without logging in again
        add_res = self.client.post(
            reverse('cart:add_to_cart'),
            data=json.dumps({'product_id': self.prod4.id, 'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(add_res.status_code, 200)
        self.assertTrue(add_res.json()['success'])
        self.assertNotIn('require_login', add_res.json())

        # Cart now has prod2, prod3, and prod4
        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 3)
        self.assertEqual(cart.total_items, 4)  # 1 + 1 + 2

    def test_08_customer_can_proceed_to_checkout_while_still_authenticated(self):
        """8. The customer can proceed to checkout while still authenticated."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        i1 = CartItem.objects.create(cart=cart, product=self.prod1, quantity=1)
        i2 = CartItem.objects.create(cart=cart, product=self.prod2, quantity=1)

        # Remove i1
        self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': i1.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        # Proceed to checkout
        checkout_page = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout_page.status_code, 200)
        self.assertTrue(checkout_page.wsgi_request.user.is_authenticated)
        self.assertContains(checkout_page, 'Order Summary')

    def test_09_another_customers_cart_remains_inaccessible(self):
        """9. Another customer's cart remains inaccessible."""
        self.client.force_login(self.customer)
        cart_a, _ = Cart.objects.get_or_create(user=self.customer)
        item_a = CartItem.objects.create(cart=cart_a, product=self.prod1, quantity=2)

        client_b = Client()
        client_b.force_login(self.other_customer)

        # Client B tries to remove Client A's item
        res_del = client_b.post(
            reverse('cart:remove_cart', kwargs={'item_id': item_a.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(res_del.status_code, 404)
        self.assertFalse(res_del.json()['success'])

        # Item still exists in customer A's cart
        cart_a.refresh_from_db()
        self.assertEqual(cart_a.items.count(), 1)
        # Both customers still authenticated
        self.assertEqual(int(self.client.session['_auth_user_id']), self.customer.id)
        self.assertEqual(int(client_b.session['_auth_user_id']), self.other_customer.id)

    def test_10_invalid_removal_requests_and_csrf_failures_do_not_log_out_customer(self):
        """10. Invalid removal requests and CSRF failures do not accidentally log out the customer."""
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        CartItem.objects.create(cart=cart, product=self.prod1, quantity=2)

        # 1. Non-existent item ID returns clean 404 without touching session
        res_404 = self.client.post(
            reverse('cart:remove_cart', kwargs={'item_id': 999999}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(res_404.status_code, 404)
        self.assertFalse(res_404.json()['success'])
        self.assertEqual(int(self.client.session['_auth_user_id']), self.customer.id)

        # 2. CSRF rejection does not log out the customer
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.customer)
        res_csrf = csrf_client.post(reverse('cart:remove_cart', kwargs={'item_id': 999999}))
        self.assertEqual(res_csrf.status_code, 403)
        self.assertEqual(int(csrf_client.session['_auth_user_id']), self.customer.id)

    def test_11_existing_login_logout_cart_checkout_seller_and_admin_continue_working(self):
        """11. Existing login, logout, cart, checkout, seller, and admin functionality continues to work."""
        # A. Normal login
        login_res = self.client.post(
            reverse('accounts:login'),
            {
                'username': 'reg_buyer',
                'password': 'BuyerPassword123!',
                'role': 'BUYER'
            }
        )
        self.assertEqual(login_res.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.customer.id)

        # B. Normal logout
        logout_res = self.client.post(reverse('accounts:logout'))
        self.assertEqual(logout_res.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

        # C. Seller dashboard works for seller
        seller_client = Client()
        seller_client.force_login(self.seller)
        seller_dash = seller_client.get(reverse('seller:dashboard'))
        self.assertEqual(seller_dash.status_code, 200)

        # D. Admin dashboard works for admin
        admin_client = Client()
        admin_client.force_login(self.admin)
        admin_dash = admin_client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(admin_dash.status_code, 200)

