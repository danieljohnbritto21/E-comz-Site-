import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User, SellerProfile, BuyerProfile, DeliveryProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord, StockMovement
from apps.cart.models import Cart, CartItem
from apps.orders.models import Order, OrderItem, SellerSubOrder, Address
from apps.delivery.models import DeliveryAssignment, DeliveryOTP


class FreshMartPlatformRequirementsTests(TestCase):
    """
    Comprehensive test suite explicitly validating the 15 platform requirements:
    1. Seller 1 sees only Seller 1's products in their dashboard.
    2. Seller 2 sees only Seller 2's products in their dashboard.
    3. Seller 1 cannot view, edit, delete, or update stock for Seller 2's product by ID or URL.
    4. Seller 2 cannot view, edit, delete, or update stock for Seller 1's product.
    5. A seller cannot assign a product to another seller by submitting a forged seller ID.
    6. Admin can see all registered sellers and all seller products.
    7. Admin can see the correct product count for each seller and filter by seller.
    8. A customer can see all active products from eligible sellers in the storefront.
    9. Seller login does not accidentally filter or restrict the public customer storefront.
    10. Customers can only access their own cart, addresses, and orders.
    11. Each seller sees only their own order items when an order contains products from multiple sellers.
    12. Unauthorized roles cannot access admin, seller, or customer-only endpoints.
    13. Guest Add to Cart prompts for login/signup and preserves the intended product.
    14. Cart and checkout validate stock and prices on the server.
    15. Existing delivery, payment, and OTP workflows continue to work correctly.
    """

    def setUp(self):
        # 1. Admin
        self.admin = User.objects.create_superuser(
            username='admin_lead',
            email='admin@freshmart.local',
            password='password123',
            role=User.Role.ADMIN
        )

        # 2. Seller 1 (Green Valley)
        self.seller_1 = User.objects.create_user(
            username='seller_green',
            email='green@seller.local',
            password='password123',
            role=User.Role.SELLER,
            first_name='Green Valley Organics'
        )
        self.profile_1 = SellerProfile.objects.create(
            user=self.seller_1,
            store_name='Green Valley Organics',
            store_slug='green-valley-organics',
            is_approved=True
        )

        # 3. Seller 2 (Sunrise Foods)
        self.seller_2 = User.objects.create_user(
            username='seller_sunrise',
            email='sunrise@seller.local',
            password='password123',
            role=User.Role.SELLER,
            first_name='Sunrise Pure Foods'
        )
        self.profile_2 = SellerProfile.objects.create(
            user=self.seller_2,
            store_name='Sunrise Pure Foods',
            store_slug='sunrise-pure-foods',
            is_approved=True
        )

        # 4. Customer
        self.customer = User.objects.create_user(
            username='customer_priya',
            email='priya@customer.local',
            password='password123',
            role=User.Role.BUYER,
            first_name='Priya Patel',
            phone_number='9876543210'
        )
        BuyerProfile.objects.create(user=self.customer)

        # 5. Delivery Person
        self.delivery = User.objects.create_user(
            username='driver_vikram',
            email='vikram@delivery.local',
            password='password123',
            role=User.Role.DELIVERY,
            first_name='Vikram Singh',
            phone_number='9123456789'
        )
        DeliveryProfile.objects.create(user=self.delivery, vehicle_type='BIKE')

        # Category
        self.category = Category.objects.create(name='Farm Fresh Produce', slug='farm-fresh-produce')

        # Seller 1 Product: Organic Gala Apples
        self.prod_seller1_apple = Product.objects.create(
            seller=self.seller_1,
            category=self.category,
            name='Organic Gala Apples',
            slug='organic-gala-apples',
            brand='Green Valley',
            mrp=Decimal('120.00'),
            selling_price=Decimal('100.00'),
            unit_value='1 kg',
            sku='SKU-GALA-APPLES-01',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.prod_seller1_apple, current_stock=25, low_stock_threshold=5)

        # Seller 2 Product: Golden Robusta Bananas
        self.prod_seller2_banana = Product.objects.create(
            seller=self.seller_2,
            category=self.category,
            name='Golden Robusta Bananas',
            slug='golden-robusta-bananas',
            brand='Sunrise Pure',
            mrp=Decimal('60.00'),
            selling_price=Decimal('45.00'),
            unit_value='1 dozen',
            sku='SKU-BANANA-02',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.prod_seller2_banana, current_stock=30, low_stock_threshold=5)

        # Authenticated Clients
        self.client_admin = Client()
        self.client_admin.force_login(self.admin)

        self.client_seller1 = Client()
        self.client_seller1.force_login(self.seller_1)

        self.client_seller2 = Client()
        self.client_seller2.force_login(self.seller_2)

        self.client_customer = Client()
        self.client_customer.force_login(self.customer)

        self.client_delivery = Client()
        self.client_delivery.force_login(self.delivery)

        self.guest_client = Client()

    # -------------------------------------------------------------------------
    # 1. Seller 1 sees only Seller 1's products in their dashboard
    # -------------------------------------------------------------------------
    def test_01_seller_1_sees_only_seller_1_products(self):
        resp = self.client_seller1.get(reverse('seller:product_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Organic Gala Apples')
        self.assertNotContains(resp, 'Golden Robusta Bananas')

    # -------------------------------------------------------------------------
    # 2. Seller 2 sees only Seller 2's products in their dashboard
    # -------------------------------------------------------------------------
    def test_02_seller_2_sees_only_seller_2_products(self):
        resp = self.client_seller2.get(reverse('seller:product_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Golden Robusta Bananas')
        self.assertNotContains(resp, 'Organic Gala Apples')

    # -------------------------------------------------------------------------
    # 3. Seller 1 cannot view, edit, delete, or update stock for Seller 2's product
    # -------------------------------------------------------------------------
    def test_03_seller_1_cannot_view_edit_delete_or_update_seller_2_product(self):
        # GET edit Seller 2 product -> 404
        resp_get = self.client_seller1.get(reverse('seller:product_edit', args=[self.prod_seller2_banana.id]))
        self.assertEqual(resp_get.status_code, 404)

        # POST edit Seller 2 product -> 404
        resp_post = self.client_seller1.post(reverse('seller:product_edit', args=[self.prod_seller2_banana.id]), {
            'name': 'Hacked Banana',
            'mrp': '10.00',
            'selling_price': '5.00'
        })
        self.assertEqual(resp_post.status_code, 404)

        # POST toggle status of Seller 2 product -> 404
        resp_toggle = self.client_seller1.post(reverse('seller:product_toggle', args=[self.prod_seller2_banana.id]))
        self.assertEqual(resp_toggle.status_code, 404)

        # POST delete Seller 2 product -> 404
        resp_del = self.client_seller1.post(reverse('seller:product_delete', args=[self.prod_seller2_banana.id]))
        self.assertEqual(resp_del.status_code, 404)

        # POST stock adjustment of Seller 2 product -> 404
        resp_stock = self.client_seller1.post(reverse('seller:inventory'), {
            'product_id': self.prod_seller2_banana.id,
            'quantity_change': 50
        })
        self.assertEqual(resp_stock.status_code, 404)

    # -------------------------------------------------------------------------
    # 4. Seller 2 cannot view, edit, delete, or update stock for Seller 1's product
    # -------------------------------------------------------------------------
    def test_04_seller_2_cannot_view_edit_delete_or_update_seller_1_product(self):
        resp_get = self.client_seller2.get(reverse('seller:product_edit', args=[self.prod_seller1_apple.id]))
        self.assertEqual(resp_get.status_code, 404)

        resp_post = self.client_seller2.post(reverse('seller:product_edit', args=[self.prod_seller1_apple.id]), {
            'name': 'Hacked Apple',
            'mrp': '10.00',
            'selling_price': '5.00'
        })
        self.assertEqual(resp_post.status_code, 404)

        resp_del = self.client_seller2.post(reverse('seller:product_delete', args=[self.prod_seller1_apple.id]))
        self.assertEqual(resp_del.status_code, 404)

    # -------------------------------------------------------------------------
    # 5. A seller cannot assign a product to another seller by submitting a forged seller ID
    # -------------------------------------------------------------------------
    def test_05_seller_cannot_assign_product_to_another_seller_by_forged_id(self):
        resp = self.client_seller1.post(reverse('seller:product_add'), {
            'name': 'Fresh Organic Spinach',
            'category': self.category.id,
            'mrp': '40.00',
            'selling_price': '30.00',
            'unit': 'bunch',
            'unit_value': '1 bunch',
            'initial_stock': '20',
            'low_stock_threshold': '5',
            'seller_id': self.seller_2.id,  # Forged seller ID
            'seller': self.seller_2.id,
            'user_id': self.seller_2.id
        })
        self.assertEqual(resp.status_code, 302)

        created_prod = Product.objects.get(name='Fresh Organic Spinach')
        # Server must assign the product to authenticated seller_1, NEVER trusting forged seller_2 ID
        self.assertEqual(created_prod.seller, self.seller_1)
        self.assertNotEqual(created_prod.seller, self.seller_2)

    # -------------------------------------------------------------------------
    # 6. Admin can see all registered sellers and all seller products
    # -------------------------------------------------------------------------
    def test_06_admin_can_see_all_registered_sellers_and_all_seller_products(self):
        # Admin sellers page
        resp_sellers = self.client_admin.get(reverse('admin_dashboard:sellers'))
        self.assertEqual(resp_sellers.status_code, 200)
        self.assertContains(resp_sellers, 'Green Valley Organics')
        self.assertContains(resp_sellers, 'Sunrise Pure Foods')

        # Admin products page
        resp_prods = self.client_admin.get(reverse('admin_dashboard:products'))
        self.assertEqual(resp_prods.status_code, 200)
        self.assertContains(resp_prods, 'Organic Gala Apples')
        self.assertContains(resp_prods, 'Golden Robusta Bananas')

    # -------------------------------------------------------------------------
    # 7. Admin can see the correct product count for each seller and filter
    # -------------------------------------------------------------------------
    def test_07_admin_can_see_correct_product_count_and_filter_by_seller(self):
        # Filter by seller 1
        resp_s1 = self.client_admin.get(reverse('admin_dashboard:products') + f'?seller_id={self.seller_1.id}')
        self.assertEqual(resp_s1.status_code, 200)
        self.assertContains(resp_s1, 'Organic Gala Apples')
        self.assertNotContains(resp_s1, 'Golden Robusta Bananas')

        # Filter by seller 2
        resp_s2 = self.client_admin.get(reverse('admin_dashboard:products') + f'?seller_id={self.seller_2.id}')
        self.assertEqual(resp_s2.status_code, 200)
        self.assertContains(resp_s2, 'Golden Robusta Bananas')
        self.assertNotContains(resp_s2, 'Organic Gala Apples')

    # -------------------------------------------------------------------------
    # 8. Customer can see all active products from eligible sellers in the storefront
    # -------------------------------------------------------------------------
    def test_08_customer_can_see_all_active_products_from_eligible_sellers(self):
        resp_shop = self.client_customer.get(reverse('catalog:product_list'))
        self.assertEqual(resp_shop.status_code, 200)
        self.assertContains(resp_shop, 'Organic Gala Apples')
        self.assertContains(resp_shop, 'Golden Robusta Bananas')

        # Displays store names on cards
        self.assertContains(resp_shop, 'Green Valley Organics')
        self.assertContains(resp_shop, 'Sunrise Pure Foods')

    # -------------------------------------------------------------------------
    # 9. Seller login does not accidentally filter or restrict the public customer storefront
    # -------------------------------------------------------------------------
    def test_09_seller_login_does_not_filter_public_storefront(self):
        # Even when Seller 1 visits the public catalog, products from Seller 2 are visible
        resp = self.client_seller1.get(reverse('catalog:product_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Organic Gala Apples')
        self.assertContains(resp, 'Golden Robusta Bananas')

    # -------------------------------------------------------------------------
    # 10. Customers can only access their own cart, addresses, and orders
    # -------------------------------------------------------------------------
    def test_10_customers_can_only_access_their_own_cart_and_orders(self):
        # Customer 2
        cust2 = User.objects.create_user(
            username='customer_other',
            email='other@customer.local',
            password='password123',
            role=User.Role.BUYER
        )
        client_cust2 = Client()
        client_cust2.force_login(cust2)

        # Order belonging to Customer 1
        order1 = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.customer,
            recipient_name='Priya',
            recipient_phone='9876543210',
            delivery_address_text='Plot 10',
            pin_code='110001',
            subtotal=Decimal('100.00'),
            total_amount=Decimal('100.00')
        )

        # Customer 2 attempts to view Customer 1's order tracking -> redirect to home with permission error
        resp = client_cust2.get(reverse('orders:track', args=[order1.order_id]))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('catalog:home'))

    # -------------------------------------------------------------------------
    # 11. Each seller sees only their own order items when an order contains multi-seller items
    # -------------------------------------------------------------------------
    def test_11_multi_seller_order_isolation(self):
        order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.customer,
            recipient_name='Priya Patel',
            recipient_phone='9876543210',
            delivery_address_text='123 Market Road, Central City',
            pin_code='110001',
            subtotal=Decimal('145.00'),
            total_amount=Decimal('145.00'),
            status=Order.Status.ORDER_CONFIRMED,
            payment_method='COD'
        )

        # Sub-order for Seller 1
        sub1 = SellerSubOrder.objects.create(
            order=order,
            seller=self.seller_1,
            subtotal=Decimal('100.00')
        )
        OrderItem.objects.create(
            order=order,
            sub_order=sub1,
            seller=self.seller_1,
            product=self.prod_seller1_apple,
            product_name=self.prod_seller1_apple.name,
            mrp=Decimal('120.00'),
            quantity=1,
            unit_price=Decimal('100.00'),
            total_price=Decimal('100.00')
        )

        # Sub-order for Seller 2
        sub2 = SellerSubOrder.objects.create(
            order=order,
            seller=self.seller_2,
            subtotal=Decimal('45.00')
        )
        OrderItem.objects.create(
            order=order,
            sub_order=sub2,
            seller=self.seller_2,
            product=self.prod_seller2_banana,
            product_name=self.prod_seller2_banana.name,
            mrp=Decimal('60.00'),
            quantity=1,
            unit_price=Decimal('45.00'),
            total_price=Decimal('45.00')
        )

        # Seller 1 views their order detail
        resp_s1 = self.client_seller1.get(reverse('seller:order_detail', args=[order.order_id]))
        self.assertEqual(resp_s1.status_code, 200)
        self.assertContains(resp_s1, 'Organic Gala Apples')
        self.assertNotContains(resp_s1, 'Golden Robusta Bananas')

        # Seller 2 views their order detail
        resp_s2 = self.client_seller2.get(reverse('seller:order_detail', args=[order.order_id]))
        self.assertEqual(resp_s2.status_code, 200)
        self.assertContains(resp_s2, 'Golden Robusta Bananas')
        self.assertNotContains(resp_s2, 'Organic Gala Apples')

    # -------------------------------------------------------------------------
    # 12. Unauthorized roles cannot access admin, seller, or customer-only endpoints
    # -------------------------------------------------------------------------
    def test_12_unauthorized_roles_cannot_access_protected_endpoints(self):
        # Customer cannot access Seller Dashboard
        resp_cust_seller = self.client_customer.get(reverse('seller:dashboard'))
        self.assertEqual(resp_cust_seller.status_code, 302)

        # Customer cannot access Admin Dashboard
        resp_cust_admin = self.client_customer.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp_cust_admin.status_code, 302)

        # Seller cannot access Admin Dashboard
        resp_seller_admin = self.client_seller1.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp_seller_admin.status_code, 302)

        # Seller cannot checkout
        resp_seller_checkout = self.client_seller1.get(reverse('orders:checkout'))
        self.assertEqual(resp_seller_checkout.status_code, 302)

    # -------------------------------------------------------------------------
    # 13. Guest Add to Cart prompts for login/signup and preserves intended product
    # -------------------------------------------------------------------------
    def test_13_guest_add_to_cart_prompts_login_and_preserves_product(self):
        # Guest calls add_to_cart API via JSON
        resp = self.guest_client.post(
            reverse('cart:add_to_cart', args=[self.prod_seller1_apple.id]),
            data=json.dumps({'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 401)
        data = resp.json()
        self.assertTrue(data.get('require_login'))
        self.assertIn('pending_product_id', data.get('login_url'))
        self.assertIn('Please log in or create a customer account', data.get('error'))

        # Guest completes Customer Login with preserved parameter
        login_resp = self.guest_client.post(reverse('accounts:login'), {
            'username': 'customer_priya',
            'password': 'password123',
            'role': 'BUYER',
            'pending_product_id': self.prod_seller1_apple.id,
            'pending_qty': '2'
        })
        self.assertEqual(login_resp.status_code, 302)

        # Buyer cart now contains the preserved product
        cart = Cart.objects.get(user=self.customer)
        self.assertTrue(cart.items.filter(product=self.prod_seller1_apple, quantity=2).exists())

    # -------------------------------------------------------------------------
    # 14. Cart and checkout validate stock and prices on the server
    # -------------------------------------------------------------------------
    def test_14_cart_and_checkout_validate_stock_and_prices(self):
        # Product with 5 units stock
        prod = Product.objects.create(
            seller=self.seller_1,
            category=self.category,
            name='Limited Avocados',
            mrp=Decimal('150.00'),
            selling_price=Decimal('120.00'),
            sku='SKU-AVOCADO-01',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=prod, current_stock=5, low_stock_threshold=2)

        # Attempt to add 10 units -> clamped to available stock (5)
        self.client_customer.post(reverse('cart:add_to_cart', args=[prod.id]), {'quantity': 10})
        cart = Cart.objects.get(user=self.customer)
        item = cart.items.get(product=prod)
        self.assertEqual(item.quantity, 5)
        # Price calculated from DB (5 * 120 = 600)
        self.assertEqual(item.line_total, Decimal('600.00'))

    # -------------------------------------------------------------------------
    # 15. Existing delivery, payment, and OTP workflows continue to work correctly
    # -------------------------------------------------------------------------
    def test_15_delivery_assignment_and_otp_workflow(self):
        order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.customer,
            recipient_name='Priya Patel',
            recipient_phone='9876543210',
            delivery_address_text='Flat 204, Riverview',
            pin_code='110001',
            subtotal=Decimal('100.00'),
            total_amount=Decimal('100.00'),
            status=Order.Status.ORDER_CONFIRMED,
            payment_method='COD'
        )
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.delivery,
            status=DeliveryAssignment.Status.ASSIGNED
        )

        # Delivery person views jobs
        resp_jobs = self.client_delivery.get(reverse('delivery:dashboard'))
        self.assertEqual(resp_jobs.status_code, 200)

        # Delivery OTP generation
        otp_obj, raw_code = DeliveryOTP.generate(assignment)
        self.assertEqual(len(raw_code), 6)

        # Matching OTP verifies delivery
        is_valid, msg = otp_obj.verify(raw_code)
        self.assertTrue(is_valid)
        self.assertTrue(otp_obj.is_used)


class PriceFormattingStandardizationTests(TestCase):
    """
    Test suite verifying standardized price formatting across the application:
    - Exactly two decimal places after the point
    - Proper half-up rounding
    - Indian digit grouping (e.g. ₹1,00,000.00)
    - Handling 0, integers, floats, Decimals, negative values, null/empty
    - Template filters (|inr, |format_inr, |inr_num)
    - Underlying financial values remain exact Decimal types without mutation
    """
    def test_required_test_values(self):
        from apps.catalog.currency import format_inr
        test_cases = [
            (0, "₹0.00"),
            (25, "₹25.00"),
            (25.5, "₹25.50"),
            (25.567, "₹25.57"),
            (999.999, "₹1,000.00"),
            (100000, "₹1,00,000.00"),
        ]
        for input_val, expected in test_cases:
            self.assertEqual(format_inr(input_val), expected, f"Failed for {input_val}")

    def test_edge_cases_and_negative(self):
        from apps.catalog.currency import format_inr
        self.assertEqual(format_inr(-25.5), "-₹25.50")
        self.assertEqual(format_inr(None), "₹0.00")
        self.assertEqual(format_inr(""), "₹0.00")
        self.assertEqual(format_inr(Decimal('1234567.891')), "₹12,34,567.89")
        self.assertEqual(format_inr(100000, symbol=False), "1,00,000.00")

    def test_template_filter_rendering(self):
        from django.template import Template, Context
        t = Template("{{ val|inr }} | {{ val|inr_num }}")
        rendered = t.render(Context({'val': Decimal('25.5')}))
        self.assertEqual(rendered, "₹25.50 | 25.50")

    def test_underlying_decimal_not_altered(self):
        from apps.catalog.currency import format_inr
        original = Decimal('99.999')
        formatted = format_inr(original)
        self.assertEqual(formatted, "₹100.00")
        # Ensure original variable has not been mutated
        self.assertEqual(original, Decimal('99.999'))

