import time
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.conf import settings
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord
from apps.cart.models import Cart, CartItem


class FreshMartSecurityAndSessionTests(TestCase):
    """
    Automated security tests validating:
    1. Unauthenticated visitor cannot access admin dashboard.
    2. Correct admin credentials allow access to admin dashboard.
    3. Incorrect admin credentials are rejected.
    4. Customer cannot access admin dashboard.
    5. Seller cannot access admin dashboard.
    6. Delivery person cannot access admin dashboard.
    7. Changing the URL to an admin endpoint does not bypass authentication.
    8. Direct API requests cannot bypass admin permissions.
    9. After admin logout, the old session cannot access protected admin pages or APIs.
    10. Refreshing after logout does not restore authentication.
    11. Anti-caching headers prevent browser back-button data leakage.
    12. An expired session requires authentication again.
    13. Logging in as another account does not retain the previous account's privileges.
    14. Repeated login and logout cycles work correctly.
    15. Successful login rotates the session key.
    16. CSRF protection remains enabled for state-changing requests.
    17. Normal authorized admin navigation and dashboard refresh continue working.
    18. Customer shopping, seller product management, and delivery workflows still function correctly.
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.csrf_client = Client(enforce_csrf_checks=True)

        # Admin user
        self.admin = User.objects.create_superuser(
            username='fresh_admin',
            email='admin@freshmart.com',
            password='AdminPassword123!',
            role=User.Role.ADMIN
        )

        # Customer user
        self.customer = User.objects.create_user(
            username='fresh_buyer',
            email='buyer@freshmart.com',
            password='BuyerPassword123!',
            role=User.Role.BUYER
        )
        self.buyer_profile = BuyerProfile.objects.create(user=self.customer)

        # Seller user
        self.seller = User.objects.create_user(
            username='fresh_seller',
            email='seller@freshmart.com',
            password='SellerPassword123!',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller,
            store_name='Green Farms',
            store_slug='green-farms',
            is_approved=True
        )

        # Delivery user
        self.delivery = User.objects.create_user(
            username='fresh_delivery',
            email='delivery@freshmart.com',
            password='DeliveryPassword123!',
            role=User.Role.DELIVERY
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery,
            vehicle_type='BIKE'
        )

        # Sample category & product
        self.category = Category.objects.create(name='Organic Veggies', slug='organic-veggies')
        self.product = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name='Organic Carrots',
            slug='organic-carrots',
            mrp=Decimal('40.00'),
            selling_price=Decimal('35.00'),
            unit_value='1 kg',
            sku='CARROT-ORG-01',
            is_active=True,
            is_approved=True
        )
        StockRecord.objects.create(product=self.product, current_stock=50, low_stock_threshold=5)

    # 1. An unauthenticated visitor cannot access the admin dashboard.
    def test_01_unauthenticated_visitor_cannot_access_admin_dashboard(self):
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp.url)
        self.assertIn('role=ADMIN', resp.url)

    # 2. Correct admin credentials allow access to the admin dashboard.
    def test_02_correct_admin_credentials_allow_access(self):
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'fresh_admin',
            'password': 'AdminPassword123!'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('admin_dashboard:dashboard'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.admin.id)

    # 3. Incorrect admin credentials are rejected.
    def test_03_incorrect_admin_credentials_rejected(self):
        resp = self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'fresh_admin',
            'password': 'WrongPassword999!'
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, "Invalid username or password.")

    # 4. A customer cannot access the admin dashboard.
    def test_04_customer_cannot_access_admin_dashboard(self):
        self.client.force_login(self.customer)
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('catalog:home'))

    # 5. A seller cannot access the admin dashboard.
    def test_05_seller_cannot_access_admin_dashboard(self):
        self.client.force_login(self.seller)
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('seller:dashboard'))

    # 6. A delivery person cannot access the admin dashboard.
    def test_06_delivery_person_cannot_access_admin_dashboard(self):
        self.client.force_login(self.delivery)
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('delivery:dashboard'))

    # 7. Changing the URL to an admin endpoint does not bypass authentication.
    def test_07_url_manipulation_does_not_bypass_authentication(self):
        admin_endpoints = [
            reverse('admin_dashboard:dashboard'),
            reverse('admin_dashboard:orders'),
            reverse('admin_dashboard:sellers'),
            reverse('admin_dashboard:buyers'),
            reverse('admin_dashboard:delivery_team'),
            reverse('admin_dashboard:products'),
            reverse('admin_dashboard:categories'),
            reverse('admin_dashboard:reports'),
            reverse('admin_dashboard:export_csv'),
        ]
        for url in admin_endpoints:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f"Endpoint {url} failed auth gating")
            self.assertIn(reverse('accounts:login'), resp.url)

    # 8. Direct API requests cannot bypass admin permissions.
    def test_08_api_requests_cannot_bypass_admin_permissions(self):
        # Unauthenticated API request
        resp = self.client.get(reverse('accounts:api_account_details'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp.url)

        # Customer API request: admin data is NOT included
        self.client.force_login(self.customer)
        resp_cust = self.client.get(reverse('accounts:api_account_details'))
        self.assertEqual(resp_cust.status_code, 200)
        data = resp_cust.json()
        self.assertNotIn('admin', data['account'])

        # Admin API request: admin data IS included
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse('accounts:api_account_details'))
        self.assertEqual(resp_admin.status_code, 200)
        admin_data = resp_admin.json()
        self.assertIn('admin', admin_data['account'])

    # 9. After admin logout, the old session cannot access protected admin pages or APIs.
    def test_09_logout_invalidates_session(self):
        self.client.login(username='fresh_admin', password='AdminPassword123!')
        self.assertIn('_auth_user_id', self.client.session)

        # Call logout
        resp_logout = self.client.post(reverse('accounts:logout'))
        self.assertEqual(resp_logout.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

        # Old session cannot access dashboard
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp.url)

    # 10. Refreshing after logout does not restore authentication.
    def test_10_refresh_after_logout_does_not_restore_authentication(self):
        self.client.login(username='fresh_admin', password='AdminPassword123!')
        self.client.post(reverse('accounts:logout'))

        # Repeat refresh calls
        for _ in range(3):
            resp = self.client.get(reverse('admin_dashboard:dashboard'))
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse('accounts:login'), resp.url)

    # 11. Anti-caching headers prevent browser back-button data leakage.
    def test_11_anti_caching_headers_on_admin_pages(self):
        self.client.login(username='fresh_admin', password='AdminPassword123!')
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 200)
        cache_control = resp.headers.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('no-cache', cache_control)
        self.assertIn('must-revalidate', cache_control)
        self.assertEqual(resp.headers.get('Pragma'), 'no-cache')
        self.assertEqual(resp.headers.get('Expires'), '0')

    # 12. An expired session requires authentication again.
    def test_12_expired_session_requires_authentication(self):
        self.client.login(username='fresh_admin', password='AdminPassword123!')
        # Expire session manually
        session = self.client.session
        session.flush()

        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp.url)

    # 13. Logging in as another account does not retain the previous account's privileges.
    def test_13_login_as_another_account_does_not_retain_privileges(self):
        # First log in as Admin
        self.client.login(username='fresh_admin', password='AdminPassword123!')
        resp_admin = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp_admin.status_code, 200)

        # Log out
        self.client.post(reverse('accounts:logout'))

        # Log in as Customer
        self.client.login(username='fresh_buyer', password='BuyerPassword123!')
        resp_buyer_admin = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp_buyer_admin.status_code, 302)
        self.assertRedirects(resp_buyer_admin, reverse('catalog:home'))

    # 14. Repeated login and logout cycles work correctly.
    def test_14_repeated_login_logout_cycles(self):
        for _ in range(3):
            # Login
            resp_login = self.client.post(reverse('accounts:login'), {
                'role': 'ADMIN',
                'username': 'fresh_admin',
                'password': 'AdminPassword123!'
            })
            self.assertEqual(resp_login.status_code, 302)
            self.assertEqual(int(self.client.session['_auth_user_id']), self.admin.id)

            # Access
            resp_dash = self.client.get(reverse('admin_dashboard:dashboard'))
            self.assertEqual(resp_dash.status_code, 200)

            # Logout
            resp_logout = self.client.post(reverse('accounts:logout'))
            self.assertEqual(resp_logout.status_code, 302)
            self.assertNotIn('_auth_user_id', self.client.session)

    # 15. Successful login rotates the session key.
    def test_15_successful_login_rotates_session_key(self):
        # Generate initial session key
        self.client.get(reverse('accounts:login'))
        initial_key = self.client.session.session_key

        self.client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'fresh_admin',
            'password': 'AdminPassword123!'
        })
        authenticated_key = self.client.session.session_key
        self.assertNotEqual(initial_key, authenticated_key)

    # 16. CSRF protection remains enabled for state-changing requests.
    def test_16_csrf_protection_enabled_for_state_changing_requests(self):
        # Post to login without CSRF token using enforcing client -> 403 Forbidden
        resp = self.csrf_client.post(reverse('accounts:login'), {
            'role': 'ADMIN',
            'username': 'fresh_admin',
            'password': 'AdminPassword123!'
        })
        self.assertEqual(resp.status_code, 403)

    # 17. Normal authorized admin navigation and dashboard refresh continue working.
    def test_17_authorized_admin_navigation_and_refresh_work(self):
        self.client.login(username='fresh_admin', password='AdminPassword123!')
        
        # Dashboard view
        resp = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Platform Master Overview")

        # Refresh dashboard view
        resp_refresh = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(resp_refresh.status_code, 200)

        # Orders list
        resp_orders = self.client.get(reverse('admin_dashboard:orders'))
        self.assertEqual(resp_orders.status_code, 200)

        # Sellers list
        resp_sellers = self.client.get(reverse('admin_dashboard:sellers'))
        self.assertEqual(resp_sellers.status_code, 200)

    # 18. Customer shopping, seller product management, and delivery workflows still function correctly.
    def test_18_other_roles_workflows_still_function_correctly(self):
        # Customer cart
        self.client.force_login(self.customer)
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        item, _ = CartItem.objects.get_or_create(cart=cart, product=self.product, defaults={'quantity': 1})
        resp_cart = self.client.get(reverse('cart:view_cart'))
        self.assertEqual(resp_cart.status_code, 200)
        self.assertContains(resp_cart, 'Organic Carrots')

        # Seller product list
        self.client.force_login(self.seller)
        resp_seller = self.client.get(reverse('seller:product_list'))
        self.assertEqual(resp_seller.status_code, 200)

        # Delivery dashboard
        self.client.force_login(self.delivery)
        resp_delivery = self.client.get(reverse('delivery:dashboard'))
        self.assertEqual(resp_delivery.status_code, 200)
