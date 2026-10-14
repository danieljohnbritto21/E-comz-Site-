import uuid
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User, SellerProfile, BuyerProfile
from apps.catalog.models import Category, Product, ProductImage
from apps.inventory.models import StockRecord, StockMovement
from apps.cart.models import Cart, CartItem
from apps.orders.models import Order, OrderItem, SellerSubOrder, Address


class GrocerySynchronizationAndCheckoutTests(TestCase):
    def setUp(self):
        # Create category
        self.category = Category.objects.create(name="Farm Vegetables", slug="farm-vegetables")

        # Create Seller A
        self.seller_a = User.objects.create_user(
            username="seller_a",
            email="seller_a@freshmart.com",
            password="password123",
            role=User.Role.SELLER,
            first_name="Green Valley Store"
        )
        SellerProfile.objects.create(user=self.seller_a, store_name="Green Valley Store", store_slug="green-valley-store")

        # Create Seller B
        self.seller_b = User.objects.create_user(
            username="seller_b",
            email="seller_b@freshmart.com",
            password="password123",
            role=User.Role.SELLER,
            first_name="Sunrise Organics"
        )
        SellerProfile.objects.create(user=self.seller_b, store_name="Sunrise Organics", store_slug="sunrise-organics")

        # Create Buyer
        self.buyer = User.objects.create_user(
            username="buyer_one",
            email="buyer1@freshmart.com",
            password="password123",
            role=User.Role.BUYER,
            first_name="Pooja Sharma",
            phone_number="9876543210"
        )
        BuyerProfile.objects.create(user=self.buyer)

        # Clients
        self.client_seller_a = Client()
        self.client_seller_a.force_login(self.seller_a)

        self.client_seller_b = Client()
        self.client_seller_b.force_login(self.seller_b)

        self.client_buyer = Client()
        self.client_buyer.force_login(self.buyer)

    def test_01_seller_adds_product_and_views_in_all_products(self):
        """Scenario 1: Seller adds product, and it appears in seller's All Products page."""
        response = self.client_seller_a.post(reverse('seller:product_add'), {
            'name': 'Organic Crunchy Carrots',
            'category': self.category.id,
            'brand': 'Organic Valley',
            'mrp': '80.00',
            'selling_price': '65.00',
            'unit': 'kg',
            'unit_value': '1 kg',
            'initial_stock': '30',
            'low_stock_threshold': '5',
            'is_active': 'on',
            'is_featured': 'on',
        })
        self.assertEqual(response.status_code, 302)

        # Check DB
        product = Product.objects.get(name='Organic Crunchy Carrots')
        self.assertEqual(product.seller, self.seller_a)
        self.assertEqual(product.stock_record.current_stock, 30)
        self.assertTrue(product.is_active)

        # Check Seller All Products list
        res_list = self.client_seller_a.get(reverse('seller:product_list'))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, 'Organic Crunchy Carrots')

    def test_02_buyer_sees_eligible_new_product(self):
        """Scenario 2: Buyer sees the newly added active product in the store."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Fresh English Cucumbers',
            mrp=Decimal('60.00'),
            selling_price=Decimal('45.00'),
            unit_value='500 g',
            sku='SKU-CUCUMBER-1',
            is_active=True,
            is_approved=True,
            is_featured=True
        )
        StockRecord.objects.create(product=product, current_stock=20, low_stock_threshold=5)

        # Buyer shop / home
        response = self.client_buyer.get(reverse('catalog:home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fresh English Cucumbers')

    def test_03_buyer_opens_product_details(self):
        """Scenario 3: Buyer opens product details page."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Ripe Shimla Apples',
            mrp=Decimal('180.00'),
            selling_price=Decimal('150.00'),
            unit_value='1 kg',
            sku='SKU-APPLE-1',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=15, low_stock_threshold=3)

        response = self.client_buyer.get(reverse('catalog:product_detail', args=[product.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ripe Shimla Apples')
        self.assertContains(response, '150')

    def test_04_buyer_adds_product_to_cart(self):
        """Scenario 4: Buyer adds product to cart."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Fresh Mint Leaves',
            mrp=Decimal('25.00'),
            selling_price=Decimal('20.00'),
            unit_value='1 bunch',
            sku='SKU-MINT-1',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=25, low_stock_threshold=5)

        response = self.client_buyer.post(reverse('cart:add_to_cart', args=[product.id]), {
            'quantity': 2
        })
        self.assertEqual(response.status_code, 302)

        cart = Cart.objects.get(user=self.buyer)
        self.assertEqual(cart.total_items, 2)
        self.assertEqual(cart.subtotal, Decimal('40.00'))

    def test_05_to_08_checkout_online_payment_verification_and_seller_synchronization(self):
        """Scenario 5-8: Buyer enters checkout details, completes test payment, payment is verified server-side, and order appears in Seller A's orders."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Premium Alphonso Mangoes',
            mrp=Decimal('600.00'),
            selling_price=Decimal('500.00'),
            unit_value='1 box',
            sku='SKU-MANGO-1',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=10, low_stock_threshold=2)

        # Add to cart
        self.client_buyer.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 1})

        # Submit Checkout Form with UPI
        checkout_res = self.client_buyer.post(reverse('orders:checkout'), {
            'customer_name': 'Pooja Sharma',
            'customer_phone': '9876543210',
            'customer_email': 'pooja@example.com',
            'full_name': 'Pooja Sharma',
            'phone_number': '9876543210',
            'house_number': 'Flat 401',
            'street': 'Lotus Boulevard',
            'area': 'Sector 100',
            'city': 'Noida',
            'state': 'UP',
            'pin_code': '201304',
            'delivery_date': 'today',
            'delivery_slot': '10:00 AM - 01:00 PM',
            'payment_method': 'UPI'
        })
        # Should redirect to payment gateway
        order = Order.objects.filter(buyer=self.buyer).first()
        self.assertIsNotNone(order)
        self.assertRedirects(checkout_res, reverse('orders:payment_gateway', args=[order.order_id]))
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)

        # Complete Server-side Payment Verification
        verify_res = self.client_buyer.post(reverse('orders:payment_verify', args=[order.order_id]), {
            'action': 'success'
        })
        self.assertRedirects(verify_res, reverse('orders:confirmation', args=[order.order_id]))

        # Refresh order
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(order.status, Order.Status.ORDER_CONFIRMED)
        self.assertTrue(order.transaction_id.startswith('TXN-VERIFIED-'))

        # Seller A Order Management Page
        seller_orders_res = self.client_seller_a.get(reverse('seller:orders'))
        self.assertEqual(seller_orders_res.status_code, 200)
        self.assertContains(seller_orders_res, order.order_id)
        self.assertContains(seller_orders_res, 'Paid Online')

    def test_09_seller_cannot_see_or_access_another_sellers_orders(self):
        """Scenario 9: Seller B cannot view or access Seller A's suborder."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Organic Tomatoes',
            mrp=Decimal('50.00'),
            selling_price=Decimal('40.00'),
            unit_value='1 kg',
            sku='SKU-TOM-1',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=20, low_stock_threshold=5)

        order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.buyer,
            recipient_name='Pooja',
            recipient_phone='9876543210',
            delivery_address_text='Noida Sector 100',
            pin_code='201304',
            subtotal=Decimal('40.00'),
            total_amount=Decimal('40.00'),
            payment_method='COD',
            payment_status=Order.PaymentStatus.PENDING
        )
        SellerSubOrder.objects.create(order=order, seller=self.seller_a, subtotal=Decimal('40.00'))

        # Seller B tries to view Seller A's order detail
        response = self.client_seller_b.get(reverse('seller:order_detail', args=[order.order_id]))
        # Must return 404 Not Found (isolated by seller identity)
        self.assertEqual(response.status_code, 404)

        # Also Seller B order list must not contain Seller A's order
        res_list = self.client_seller_b.get(reverse('seller:orders'))
        self.assertNotContains(res_list, order.order_id)

    def test_10_cod_orders_appear_with_unpaid_status(self):
        """Scenario 10: Cash on Delivery orders appear with PENDING payment status and are visible to seller for fulfillment."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Fresh Cow Milk 1L',
            mrp=Decimal('70.00'),
            selling_price=Decimal('65.00'),
            unit_value='1 L',
            sku='SKU-MILK-1',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=10, low_stock_threshold=2)

        self.client_buyer.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 1})

        # Submit checkout with COD
        checkout_res = self.client_buyer.post(reverse('orders:checkout'), {
            'customer_name': 'Pooja',
            'customer_phone': '9876543210',
            'customer_email': 'pooja@test.com',
            'full_name': 'Pooja',
            'phone_number': '9876543210',
            'house_number': '12B',
            'street': 'Main Street',
            'area': 'Downtown',
            'city': 'Central City',
            'pin_code': '110001',
            'payment_method': 'COD'
        })

        order = Order.objects.filter(buyer=self.buyer, payment_method='COD').first()
        self.assertIsNotNone(order)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertRedirects(checkout_res, reverse('orders:confirmation', args=[order.order_id]))

        # Seller view shows Cash on Delivery
        res_orders = self.client_seller_a.get(reverse('seller:orders'))
        self.assertContains(res_orders, 'Cash on Delivery')

    def test_11_failed_online_payments_not_marked_as_paid_and_restores_stock(self):
        """Scenario 11: Failed online payment is marked as FAILED and restores reserved stock."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Organic Cauliflower',
            mrp=Decimal('40.00'),
            selling_price=Decimal('35.00'),
            unit_value='1 pc',
            sku='SKU-CAULI-1',
            is_active=True,
            is_approved=True
        )
        record = StockRecord.objects.create(product=product, current_stock=10, low_stock_threshold=2)

        # Add to cart
        self.client_buyer.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 2})

        # Checkout
        self.client_buyer.post(reverse('orders:checkout'), {
            'customer_name': 'Pooja',
            'customer_phone': '9876543210',
            'customer_email': 'pooja@test.com',
            'full_name': 'Pooja',
            'phone_number': '9876543210',
            'house_number': '12B',
            'street': 'Main Street',
            'area': 'Downtown',
            'city': 'Central City',
            'pin_code': '110001',
            'payment_method': 'CARD'
        })
        order = Order.objects.filter(buyer=self.buyer).order_by('-created_at').first()

        # Stock deducted during checkout
        record.refresh_from_db()
        self.assertEqual(record.current_stock, 8)

        # Simulate Payment Decline / Cancel
        fail_res = self.client_buyer.post(reverse('orders:payment_verify', args=[order.order_id]), {
            'action': 'cancel'
        })
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.FAILED)
        self.assertEqual(order.status, Order.Status.CANCELLED)

        # Stock is restored!
        record.refresh_from_db()
        self.assertEqual(record.current_stock, 10)

    def test_12_inventory_never_becomes_negative(self):
        """Scenario 12: Prevents ordering more than available stock."""
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Limited Exotic Dragonfruit',
            mrp=Decimal('200.00'),
            selling_price=Decimal('180.00'),
            unit_value='1 pc',
            sku='SKU-DRAGON-1',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=1, low_stock_threshold=1)

        # Buyer attempts to add 5 units
        res = self.client_buyer.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 5})
        cart = Cart.objects.get(user=self.buyer)
        item = cart.items.first()
        # Cart clamps quantity to available stock (1)
        self.assertEqual(item.quantity, 1)

    def test_13_buyer_can_track_order(self):
        """Scenario 13: Buyer can view order tracking."""
        order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.buyer,
            recipient_name='Pooja',
            recipient_phone='9876543210',
            delivery_address_text='Noida Sector 100',
            pin_code='201304',
            subtotal=Decimal('100.00'),
            total_amount=Decimal('100.00'),
            status=Order.Status.ORDER_CONFIRMED,
            payment_method='COD'
        )
        response = self.client_buyer.get(reverse('orders:track', args=[order.order_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.order_id)

    def test_14_seller_empty_states(self):
        """Scenario 14 & 15: Empty states display helpful guidance when no products/orders exist."""
        # Seller B currently has 0 products
        res_prod = self.client_seller_b.get(reverse('seller:product_list'))
        self.assertContains(res_prod, 'No products match your criteria')

        # Seller B currently has 0 orders
        res_orders = self.client_seller_b.get(reverse('seller:orders'))
        self.assertContains(res_orders, 'No orders matching this filter')

    def test_15_guest_checkout_does_not_redirect_to_login(self):
        """Checkout button must not log off or redirect guest to login; renders checkout page with 200 OK."""
        guest_client = Client()
        # Add item to guest session cart
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Fresh Red Tomatoes',
            mrp=Decimal('40.00'),
            selling_price=Decimal('30.00'),
            unit_value='1 kg',
            sku='SKU-TOMATO-GST',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=25, low_stock_threshold=5)
        guest_client.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 2})

        # Guest clicks Checkout
        response = guest_client.get(reverse('orders:checkout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Secure Checkout')
        self.assertContains(response, 'Fresh Red Tomatoes')

    def test_16_guest_can_place_order_seamlessly(self):
        """Guest buyer enters details and places COD order; user is created and order confirmed."""
        guest_client = Client()
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Green Spinach',
            mrp=Decimal('50.00'),
            selling_price=Decimal('40.00'),
            unit_value='500 g',
            sku='SKU-SPINACH-GST',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=20, low_stock_threshold=5)
        guest_client.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 1})

        post_data = {
            'customer_name': 'Aarav Patel',
            'customer_phone': '9123456780',
            'customer_email': 'aarav.guest@freshmart.com',
            'full_name': 'Aarav Patel',
            'phone_number': '9123456780',
            'house_number': 'Flat 102',
            'street': 'Lotus Boulevard',
            'area': 'Sector 100',
            'city': 'Noida',
            'state': 'Uttar Pradesh',
            'pin_code': '201304',
            'address_type': 'HOME',
            'delivery_date': 'today',
            'delivery_slot': '10:00 AM - 01:00 PM',
            'payment_method': 'COD'
        }
        res = guest_client.post(reverse('orders:checkout'), post_data)
        self.assertEqual(res.status_code, 302)
        
        # User created
        user = User.objects.filter(email='aarav.guest@freshmart.com').first()
        self.assertIsNotNone(user)
        self.assertEqual(user.role, User.Role.BUYER)

        # Order created
        order = Order.objects.filter(buyer=user).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.payment_method, 'COD')
        self.assertEqual(order.status, Order.Status.ORDER_CONFIRMED)
        self.assertRedirects(res, reverse('orders:confirmation', args=[order.order_id]))

    def test_17_guest_cart_merged_on_login(self):
        """Guest cart items are transferred to user cart when customer signs in."""
        guest_client = Client()
        product = Product.objects.create(
            seller=self.seller_a,
            category=self.category,
            name='Sweet Mangoes',
            mrp=Decimal('120.00'),
            selling_price=Decimal('100.00'),
            unit_value='1 kg',
            sku='SKU-MANGO-GST',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=product, current_stock=30, low_stock_threshold=5)
        guest_client.post(reverse('cart:add_to_cart', args=[product.id]), {'quantity': 3})

        # Login as existing buyer
        login_res = guest_client.post(reverse('accounts:login'), {
            'username': 'buyer_one',
            'password': 'password123',
            'role': 'BUYER',
            'next': '/orders/checkout/'
        })
        self.assertEqual(login_res.status_code, 302)
        self.assertEqual(login_res.url, '/orders/checkout/')

        # Buyer cart now has the 3 mangoes
        buyer_cart = Cart.objects.get(user=self.buyer)
        self.assertTrue(buyer_cart.items.filter(product=product, quantity=3).exists())

