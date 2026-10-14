from decimal import Decimal
import io
from PIL import Image
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.accounts.models import User, SellerProfile, DeliveryProfile, BuyerProfile
from apps.catalog.models import Category, Product, ProductImage
from apps.inventory.models import StockRecord, StockMovement
from apps.orders.models import Order, OrderItem, SellerSubOrder, Address
from apps.delivery.models import DeliveryAssignment


def generate_test_image():
    file = io.BytesIO()
    image = Image.new('RGBA', size=(100, 100), color=(16, 185, 129))
    image.save(file, 'png')
    file.name = 'test.png'
    file.seek(0)
    return SimpleUploadedFile(file.name, file.read(), content_type='image/png')


class GroceryMarketplaceTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username='test_admin',
            email='admin@test.com',
            password='password123',
            role=User.Role.ADMIN
        )

        # Seller user
        self.seller_user = User.objects.create_user(
            username='test_seller',
            email='seller@test.com',
            password='password123',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            store_name='Test Organics',
            store_slug='test-organics',
            pickup_address='123 Market St',
            is_approved=True
        )

        # Delivery user
        self.delivery_user = User.objects.create_user(
            username='test_delivery',
            email='delivery@test.com',
            password='password123',
            role=User.Role.DELIVERY
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery_user,
            vehicle_type='BIKE'
        )

        # Buyer user
        self.buyer_user = User.objects.create_user(
            username='test_buyer',
            email='buyer@test.com',
            password='password123',
            role=User.Role.BUYER
        )
        self.buyer_profile = BuyerProfile.objects.create(user=self.buyer_user)

        # Category & Product
        self.category = Category.objects.create(name='Fresh Produce', slug='fresh-produce')
        self.product = Product.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Organic Spinach',
            slug='organic-spinach',
            mrp=Decimal('40.00'),
            selling_price=Decimal('30.00'),
            sku='SKU-SPINACH-01',
            is_active=True,
            is_approved=True
        )
        self.stock_record = StockRecord.objects.create(
            product=self.product,
            current_stock=20,
            low_stock_threshold=5
        )

    # -------------------------------------------------------------------------
    # Scenario 1: Seller adds a valid active product -> appears in Seller & Buyer
    # -------------------------------------------------------------------------
    def test_scenario_1_seller_adds_valid_active_product(self):
        self.client.login(username='test_seller', password='password123')
        resp = self.client.post(reverse('seller:product_add'), {
            'name': 'Crisp Hydroponic Lettuce',
            'category': self.category.id,
            'brand': 'Green Farm',
            'description': 'Crisp organic green lettuce',
            'mrp': '60.00',
            'selling_price': '45.00',
            'unit': 'pack',
            'unit_value': '1 pack (200g)',
            'initial_stock': '30',
            'low_stock_threshold': '5',
            'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)

        # Appears in seller list
        seller_list = self.client.get(reverse('seller:product_list'))
        self.assertContains(seller_list, 'Crisp Hydroponic Lettuce')

        # Appears in buyer shop and buyer home
        buyer_shop = self.client.get(reverse('catalog:product_list'))
        self.assertContains(buyer_shop, 'Crisp Hydroponic Lettuce')

        buyer_home = self.client.get(reverse('catalog:home'))
        self.assertContains(buyer_home, 'Crisp Hydroponic Lettuce')

    # -------------------------------------------------------------------------
    # Scenario 2: Seller uploads multiple images
    # -------------------------------------------------------------------------
    def test_scenario_2_seller_multiple_images(self):
        self.client.login(username='test_seller', password='password123')
        img1 = generate_test_image()
        img2 = generate_test_image()

        resp = self.client.post(reverse('seller:product_add'), {
            'name': 'Golden Mangoes',
            'category': self.category.id,
            'brand': 'Mango King',
            'mrp': '120.00',
            'selling_price': '90.00',
            'unit': 'kg',
            'unit_value': '1 kg',
            'initial_stock': '15',
            'low_stock_threshold': '3',
            'is_active': 'on',
            'images': [img1, img2]
        })
        self.assertEqual(resp.status_code, 302)

        mango = Product.objects.get(name='Golden Mangoes')
        self.assertEqual(mango.images.count(), 2)
        self.assertTrue(mango.images.filter(is_primary=True).exists())

    # -------------------------------------------------------------------------
    # Scenario 3: Seller adds an inactive product -> hidden from Buyer shop
    # -------------------------------------------------------------------------
    def test_scenario_3_seller_adds_inactive_product(self):
        self.client.login(username='test_seller', password='password123')
        resp = self.client.post(reverse('seller:product_add'), {
            'name': 'Seasonal Dragonfruit',
            'category': self.category.id,
            'brand': 'Exotic Fruits',
            'mrp': '150.00',
            'selling_price': '130.00',
            'unit': 'pcs',
            'unit_value': '1 pc',
            'initial_stock': '10',
            'low_stock_threshold': '2',
            # is_active omitted -> inactive
        })
        self.assertEqual(resp.status_code, 302)

        # Appears in seller list
        seller_list = self.client.get(reverse('seller:product_list'))
        self.assertContains(seller_list, 'Seasonal Dragonfruit')

        # Hidden from buyer shop
        buyer_shop = self.client.get(reverse('catalog:product_list'))
        self.assertNotContains(buyer_shop, 'Seasonal Dragonfruit')

    # -------------------------------------------------------------------------
    # Scenario 4: Seller product with zero stock -> shows Out of Stock
    # -------------------------------------------------------------------------
    def test_scenario_4_product_with_zero_stock(self):
        zero_product = Product.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Rare Truffle Mushroom',
            slug='rare-truffle-mushroom',
            mrp=Decimal('500.00'),
            selling_price=Decimal('450.00'),
            sku='SKU-TRUFFLE-01',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=zero_product, current_stock=0, low_stock_threshold=2)

        buyer_shop = self.client.get(reverse('catalog:product_list'))
        self.assertContains(buyer_shop, 'Rare Truffle Mushroom')
        self.assertContains(buyer_shop, 'Out of Stock')

        # Cannot be added to cart
        self.client.login(username='test_buyer', password='password123')
        add_resp = self.client.post(
            reverse('cart:add_to_cart'),
            data={'product_id': zero_product.id, 'quantity': 1},
            content_type='application/json'
        )
        self.assertEqual(add_resp.status_code, 400)

    # -------------------------------------------------------------------------
    # Scenario 5: Buyer refreshes Shop page -> retrieves latest DB products
    # -------------------------------------------------------------------------
    def test_scenario_5_buyer_refreshes_shop_page(self):
        resp1 = self.client.get(reverse('catalog:product_list'))
        self.assertNotContains(resp1, 'Fresh Red Radish')

        # Seller adds product directly
        radish = Product.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Fresh Red Radish',
            slug='fresh-red-radish',
            mrp=Decimal('30.00'),
            selling_price=Decimal('20.00'),
            sku='SKU-RADISH-01',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=radish, current_stock=25)

        # Buyer refreshes
        resp2 = self.client.get(reverse('catalog:product_list'))
        self.assertContains(resp2, 'Fresh Red Radish')

    # -------------------------------------------------------------------------
    # Scenario 6: Buyer searches for a newly added product
    # -------------------------------------------------------------------------
    def test_scenario_6_buyer_searches_new_product(self):
        search_resp = self.client.get(reverse('catalog:product_list') + '?q=Spinach')
        self.assertContains(search_resp, 'Organic Spinach')

        api_resp = self.client.get(reverse('catalog:search_suggest') + '?q=Spin')
        self.assertEqual(api_resp.status_code, 200)
        self.assertTrue(len(api_resp.json()['results']) > 0)

    # -------------------------------------------------------------------------
    # Scenario 7: Unauthorized user product creation access denied
    # -------------------------------------------------------------------------
    def test_scenario_7_unauthorized_user_product_creation(self):
        # Buyer tries to access seller product creation
        self.client.login(username='test_buyer', password='password123')
        resp = self.client.get(reverse('seller:product_add'))
        self.assertEqual(resp.status_code, 302)

        # Anonymous visitor tries to access
        self.client.logout()
        resp_anon = self.client.get(reverse('seller:product_add'))
        self.assertEqual(resp_anon.status_code, 302)

    # -------------------------------------------------------------------------
    # Scenario 8: Product without image renders fallback placeholder
    # -------------------------------------------------------------------------
    def test_scenario_8_product_without_image(self):
        self.assertEqual(self.product.images.count(), 0)
        self.assertEqual(self.product.primary_image_url, '/static/images/placeholder_product.svg')

        resp = self.client.get(reverse('catalog:product_detail', kwargs={'slug': self.product.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'placeholder_product.svg')

    # -------------------------------------------------------------------------
    # Scenario 9 & 10: Invalid data handling & No false success
    # -------------------------------------------------------------------------
    def test_scenario_9_and_10_invalid_data_no_false_success(self):
        self.client.login(username='test_seller', password='password123')

        # Selling price exceeds MRP
        resp = self.client.post(reverse('seller:product_add'), {
            'name': 'Overpriced Item',
            'category': self.category.id,
            'mrp': '50.00',
            'selling_price': '100.00', # Invalid!
            'unit_value': '1 kg',
            'initial_stock': '10',
        })
        self.assertEqual(resp.status_code, 200) # Re-renders form with error
        self.assertContains(resp, 'Selling price cannot exceed Maximum Retail Price')
        self.assertFalse(Product.objects.filter(name='Overpriced Item').exists())

        # Missing product name
        resp_missing = self.client.post(reverse('seller:product_add'), {
            'name': '', # Empty!
            'category': self.category.id,
            'mrp': '50.00',
            'selling_price': '40.00',
        })
        self.assertEqual(resp_missing.status_code, 200)
        self.assertContains(resp_missing, 'Please enter a valid product name')

    # -------------------------------------------------------------------------
    # Additional E-Commerce Flow Tests (Cart, Checkout, Admin Dispatch)
    # -------------------------------------------------------------------------
    def test_cart_and_checkout_flow(self):
        self.client.login(username='test_buyer', password='password123')
        self.client.post(
            reverse('cart:add_to_cart'),
            data={'product_id': self.product.id, 'quantity': 2},
            content_type='application/json'
        )

        initial_stock = self.product.stock_record.current_stock
        resp = self.client.post(reverse('orders:checkout'), {
            'full_name': 'Test Recipient',
            'phone_number': '9876543210',
            'house_number': '101',
            'street': 'Garden Street',
            'area': 'Downtown',
            'pin_code': '110001',
            'delivery_slot': '10:00 AM - 01:00 PM',
            'payment_method': 'COD'
        })
        self.assertEqual(resp.status_code, 302)

        self.stock_record.refresh_from_db()
        self.assertEqual(self.stock_record.current_stock, initial_stock - 2)

    def test_admin_csv_report_export(self):
        self.client.login(username='test_admin', password='password123')
        response = self.client.get(reverse('admin_dashboard:export_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')


class RoleBasedLoginAuthenticationTests(TestCase):
    """
    Test suite verifying strict role-based login authentication:
    - Each role tab permits ONLY accounts with that exact matching authorized role.
    - Credentials for other roles are strictly rejected with an explicit error.
    - Role mismatch NEVER creates an authenticated session.
    - Protected dashboards reject cross-role access.
    """

    def setUp(self):
        self.client = Client()

        # Customer account
        self.customer = User.objects.create_user(
            username='customer_user',
            email='customer@freshmart.com',
            password='password123',
            role=User.Role.BUYER
        )
        BuyerProfile.objects.create(user=self.customer)

        # Seller account
        self.seller = User.objects.create_user(
            username='seller_user',
            email='seller@freshmart.com',
            password='password123',
            role=User.Role.SELLER
        )
        SellerProfile.objects.create(
            user=self.seller,
            store_name='Fresh Veggies Store',
            pickup_address='Shop 5, Downtown'
        )

        # Delivery Person account
        self.delivery = User.objects.create_user(
            username='delivery_user',
            email='delivery@freshmart.com',
            password='password123',
            role=User.Role.DELIVERY
        )
        DeliveryProfile.objects.create(user=self.delivery)

        # Admin account
        self.admin = User.objects.create_superuser(
            username='admin_user',
            email='admin@freshmart.com',
            password='password123',
            role=User.Role.ADMIN
        )

        # Non-admin staff account (is_staff=True, but role=BUYER, not superuser)
        self.staff_non_admin = User.objects.create_user(
            username='staff_user',
            email='staff@freshmart.com',
            password='password123',
            role=User.Role.BUYER,
            is_staff=True
        )

    # -------------------------------------------------------------------------
    # Test A: Customer Login
    # -------------------------------------------------------------------------
    def test_customer_login_success(self):
        """Correct customer credentials + Customer tab -> Login succeeds."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'BUYER',
            'username': 'customer_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.customer.id)

    def test_customer_login_with_email_success(self):
        """Customer login using registered email address + Customer tab -> Login succeeds."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'BUYER',
            'username': 'customer@freshmart.com',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.customer.id)

    def test_customer_tab_rejects_seller(self):
        """Correct seller credentials + Customer tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'BUYER',
            'username': 'seller_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Customer role")

    def test_customer_tab_rejects_delivery(self):
        """Correct delivery credentials + Customer tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'BUYER',
            'username': 'delivery_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Customer role")

    def test_customer_tab_rejects_admin(self):
        """Correct admin credentials + Customer tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'BUYER',
            'username': 'admin_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Customer role")

    def test_customer_tab_rejects_wrong_password(self):
        """Incorrect customer password + Customer tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'BUYER',
            'username': 'customer_user',
            'password': 'wrongpassword'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "Invalid username or password")

    # -------------------------------------------------------------------------
    # Test B: Seller Login
    # -------------------------------------------------------------------------
    def test_seller_login_success(self):
        """Correct seller credentials + Seller tab -> Login succeeds."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'SELLER',
            'username': 'seller_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.seller.id)
        self.assertRedirects(resp, reverse('seller:dashboard'))

    def test_seller_tab_rejects_customer(self):
        """Customer credentials + Seller tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'SELLER',
            'username': 'customer_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Seller role")

    def test_seller_tab_rejects_delivery(self):
        """Delivery credentials + Seller tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'SELLER',
            'username': 'delivery_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Seller role")

    def test_seller_tab_rejects_admin(self):
        """Admin credentials + Seller tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'SELLER',
            'username': 'admin_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Seller role")

    # -------------------------------------------------------------------------
    # Test C: Delivery Login
    # -------------------------------------------------------------------------
    def test_delivery_login_success(self):
        """Correct delivery credentials + Delivery tab -> Login succeeds."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'DELIVERY',
            'username': 'delivery_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.delivery.id)
        self.assertRedirects(resp, reverse('delivery:dashboard'))

    def test_delivery_tab_rejects_customer(self):
        """Customer credentials + Delivery tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'DELIVERY',
            'username': 'customer_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Delivery Person role")

    def test_delivery_tab_rejects_seller(self):
        """Seller credentials + Delivery tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'DELIVERY',
            'username': 'seller_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Delivery Person role")

    def test_delivery_tab_rejects_admin(self):
        """Admin credentials + Delivery tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'DELIVERY',
            'username': 'admin_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Delivery Person role")

    # -------------------------------------------------------------------------
    # Test D: Admin Login
    # -------------------------------------------------------------------------
    def test_admin_login_success(self):
        """Correct authorized admin credentials + Admin tab -> Login succeeds."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'admin_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.admin.id)
        self.assertRedirects(resp, reverse('admin_dashboard:dashboard'))

    def test_admin_tab_rejects_customer(self):
        """Customer credentials + Admin tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'customer_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Admin role")

    def test_admin_tab_rejects_seller(self):
        """Seller credentials + Admin tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'seller_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Admin role")

    def test_admin_tab_rejects_delivery(self):
        """Delivery credentials + Admin tab -> Login rejected."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'delivery_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Admin role")

    def test_admin_tab_rejects_non_admin_staff(self):
        """Ordinary staff without ADMIN role or superuser must not gain admin login."""
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'staff_user',
            'password': 'password123'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "This account cannot sign in using the Admin role")

    # -------------------------------------------------------------------------
    # Test E: Dashboard Protection & Server-side Access Control
    # -------------------------------------------------------------------------
    def test_unauthenticated_cannot_access_dashboards(self):
        """Unauthenticated visitor cannot access seller or admin dashboard."""
        resp_seller = self.client.get(reverse('seller:dashboard'))
        self.assertRedirects(resp_seller, reverse('accounts:login'))

        resp_admin = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertRedirects(resp_admin, reverse('accounts:login'))

        resp_delivery = self.client.get(reverse('delivery:dashboard'))
        self.assertRedirects(resp_delivery, reverse('accounts:login'))

    def test_customer_cannot_access_seller_dashboard(self):
        """Customer cannot open seller dashboard by directly entering URL."""
        self.client.force_login(self.customer)
        resp = self.client.get(reverse('seller:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('catalog:home'))

    def test_seller_cannot_access_admin_dashboard(self):
        """Seller cannot open admin dashboard."""
        self.client.force_login(self.seller)
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('seller:dashboard'))

    def test_delivery_cannot_access_seller_or_admin_dashboard(self):
        """Delivery person cannot open seller or admin dashboard."""
        self.client.force_login(self.delivery)
        resp_seller = self.client.get(reverse('seller:dashboard'))
        self.assertEqual(resp_seller.status_code, 302)

        resp_admin = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp_admin.status_code, 302)

    def test_already_authenticated_user_visiting_login_redirects_safely(self):
        """An already logged-in user visiting /auth/login/ is redirected safely without role change."""
        self.client.force_login(self.seller)
        resp = self.client.get(reverse('accounts:login'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('seller:dashboard'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.seller.id)

