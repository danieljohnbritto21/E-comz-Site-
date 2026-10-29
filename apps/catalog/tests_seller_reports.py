from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User, SellerProfile
from apps.orders.models import Order, OrderItem, SellerSubOrder
from apps.reports.models import OrderItemFinancialSnapshot, SellerWallet
from apps.catalog.models import Product, Category

class SellerReportsDynamicTests(TestCase):
    def setUp(self):
        # Create test seller A with 0.00% commission
        self.seller_a = User.objects.create_user(
            username='test_seller_a', email='seller_a@freshmart.com', password='password123',
            role=User.Role.SELLER, is_active=True
        )
        self.profile_a = SellerProfile.objects.create(
            user=self.seller_a, store_name="Green Valley Organics", commission_rate=Decimal('0.00')
        )
        self.wallet_a = SellerWallet.objects.create(
            seller=self.seller_a, available_balance=Decimal('250.00'), pending_earnings=Decimal('50.00')
        )

        # Create test seller B with custom 8.00% commission
        self.seller_b = User.objects.create_user(
            username='test_seller_b', email='seller_b@freshmart.com', password='password123',
            role=User.Role.SELLER, is_active=True
        )
        self.profile_b = SellerProfile.objects.create(
            user=self.seller_b, store_name="Dairy Best Farms", commission_rate=Decimal('8.00')
        )
        self.wallet_b = SellerWallet.objects.create(
            seller=self.seller_b, available_balance=Decimal('100.00'), pending_earnings=Decimal('0.00')
        )

        # Create buyer
        self.buyer = User.objects.create_user(
            username='test_buyer', email='buyer@freshmart.com', password='password123',
            role=User.Role.BUYER, is_active=True
        )

        # Create Category & Products
        self.category = Category.objects.create(name='Produce', slug='produce', is_active=True)
        self.product_a1 = Product.objects.create(
            seller=self.seller_a, category=self.category, name='Organic Tomatoes', slug='organic-tomatoes',
            sku='SKU-TOMATOES-001',
            mrp=Decimal('50.00'), selling_price=Decimal('40.00'), product_cost=Decimal('30.00'),
            unit='kg', is_active=True
        )
        self.product_a2 = Product.objects.create(
            seller=self.seller_a, category=self.category, name='Fresh Spinach', slug='fresh-spinach',
            sku='SKU-SPINACH-002',
            mrp=Decimal('30.00'), selling_price=Decimal('25.00'), product_cost=None, # Missing cost
            unit='bunch', is_active=True
        )
        self.product_b1 = Product.objects.create(
            seller=self.seller_b, category=self.category, name='Farm Fresh Milk', slug='farm-fresh-milk',
            sku='SKU-MILK-003',
            mrp=Decimal('70.00'), selling_price=Decimal('60.00'), product_cost=Decimal('45.00'),
            unit='litre', is_active=True
        )

        # Create Order 1 with items from Seller A (Delivered)
        self.order1 = Order.objects.create(
            order_id='ORD-TEST-001', buyer=self.buyer,
            recipient_name='Test Buyer', recipient_phone='9876543210',
            delivery_address_text='123 Market St, Central City', pin_code='560001',
            status=Order.Status.DELIVERED, payment_status=Order.PaymentStatus.PAID,
            subtotal=Decimal('105.00'), delivery_fee=Decimal('5.00'), total_amount=Decimal('110.00')
        )
        self.sub_a1 = SellerSubOrder.objects.create(
            order=self.order1, seller=self.seller_a,
            status=SellerSubOrder.Status.DELIVERED, subtotal=Decimal('105.00')
        )
        self.item_a1 = OrderItem.objects.create(
            order=self.order1, sub_order=self.sub_a1, seller=self.seller_a, product=self.product_a1,
            product_name='Organic Tomatoes', unit_price=Decimal('40.00'), mrp=Decimal('50.00'),
            product_cost_snapshot=Decimal('30.00'), quantity=2, total_price=Decimal('80.00')
        )
        self.snap_a1 = OrderItemFinancialSnapshot.objects.create(
            order_item=self.item_a1, order=self.order1, seller=self.seller_a,
            selling_price_per_unit=Decimal('40.00'), mrp_per_unit=Decimal('50.00'),
            product_cost_per_unit=Decimal('30.00'), quantity=2,
            product_revenue=Decimal('80.00'), product_cost_total=Decimal('60.00'),
            commission_rate=Decimal('0.00'), commission_amount=Decimal('0.00'),
            seller_payable=Decimal('80.00'), seller_profit=Decimal('20.00'),
            settlement_status='SETTLED'
        )

        self.item_a2 = OrderItem.objects.create(
            order=self.order1, sub_order=self.sub_a1, seller=self.seller_a, product=self.product_a2,
            product_name='Fresh Spinach', unit_price=Decimal('25.00'), mrp=Decimal('30.00'),
            product_cost_snapshot=None, quantity=1, total_price=Decimal('25.00')
        )
        self.snap_a2 = OrderItemFinancialSnapshot.objects.create(
            order_item=self.item_a2, order=self.order1, seller=self.seller_a,
            selling_price_per_unit=Decimal('25.00'), mrp_per_unit=Decimal('30.00'),
            product_cost_per_unit=None, quantity=1,
            product_revenue=Decimal('25.00'), product_cost_total=None,
            commission_rate=Decimal('0.00'), commission_amount=Decimal('0.00'),
            seller_payable=Decimal('25.00'), seller_profit=None,
            settlement_status='SETTLED'
        )

        # Create Order 2 with items from Seller A (Pending/Active)
        self.order2 = Order.objects.create(
            order_id='ORD-TEST-002', buyer=self.buyer,
            recipient_name='Test Buyer', recipient_phone='9876543210',
            delivery_address_text='123 Market St, Central City', pin_code='560001',
            status=Order.Status.PREPARING, payment_status=Order.PaymentStatus.PAID,
            subtotal=Decimal('40.00'), delivery_fee=Decimal('0.00'), total_amount=Decimal('40.00')
        )
        self.sub_a2 = SellerSubOrder.objects.create(
            order=self.order2, seller=self.seller_a,
            status=SellerSubOrder.Status.PREPARING, subtotal=Decimal('40.00')
        )
        self.item_a3 = OrderItem.objects.create(
            order=self.order2, sub_order=self.sub_a2, seller=self.seller_a, product=self.product_a1,
            product_name='Organic Tomatoes', unit_price=Decimal('40.00'), mrp=Decimal('50.00'),
            product_cost_snapshot=Decimal('30.00'), quantity=1, total_price=Decimal('40.00')
        )
        self.snap_a3 = OrderItemFinancialSnapshot.objects.create(
            order_item=self.item_a3, order=self.order2, seller=self.seller_a,
            selling_price_per_unit=Decimal('40.00'), mrp_per_unit=Decimal('50.00'),
            product_cost_per_unit=Decimal('30.00'), quantity=1,
            product_revenue=Decimal('40.00'), product_cost_total=Decimal('30.00'),
            commission_rate=Decimal('0.00'), commission_amount=Decimal('0.00'),
            seller_payable=Decimal('40.00'), seller_profit=Decimal('10.00'),
            settlement_status='PENDING'
        )

        # Create Order 3 with items from Seller A (CANCELLED)
        self.order3 = Order.objects.create(
            order_id='ORD-TEST-003', buyer=self.buyer,
            recipient_name='Test Buyer', recipient_phone='9876543210',
            delivery_address_text='123 Market St, Central City', pin_code='560001',
            status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.REFUNDED,
            subtotal=Decimal('40.00'), delivery_fee=Decimal('0.00'), total_amount=Decimal('40.00')
        )
        self.sub_a3 = SellerSubOrder.objects.create(
            order=self.order3, seller=self.seller_a,
            status=SellerSubOrder.Status.CANCELLED, subtotal=Decimal('40.00')
        )
        self.item_a4 = OrderItem.objects.create(
            order=self.order3, sub_order=self.sub_a3, seller=self.seller_a, product=self.product_a1,
            product_name='Organic Tomatoes', unit_price=Decimal('40.00'), mrp=Decimal('50.00'),
            product_cost_snapshot=Decimal('30.00'), quantity=1, total_price=Decimal('40.00')
        )
        self.snap_a4 = OrderItemFinancialSnapshot.objects.create(
            order_item=self.item_a4, order=self.order3, seller=self.seller_a,
            selling_price_per_unit=Decimal('40.00'), mrp_per_unit=Decimal('50.00'),
            product_cost_per_unit=Decimal('30.00'), quantity=1,
            product_revenue=Decimal('40.00'), product_cost_total=Decimal('30.00'),
            commission_rate=Decimal('0.00'), commission_amount=Decimal('0.00'),
            seller_payable=Decimal('40.00'), seller_profit=Decimal('10.00'),
            settlement_status='CANCELLED'
        )

        # Create Order 4 for Seller B (with 8% commission)
        self.order4 = Order.objects.create(
            order_id='ORD-TEST-004', buyer=self.buyer,
            recipient_name='Test Buyer', recipient_phone='9876543210',
            delivery_address_text='123 Market St, Central City', pin_code='560001',
            status=Order.Status.DELIVERED, payment_status=Order.PaymentStatus.PAID,
            subtotal=Decimal('60.00'), delivery_fee=Decimal('0.00'), total_amount=Decimal('60.00')
        )
        self.sub_b1 = SellerSubOrder.objects.create(
            order=self.order4, seller=self.seller_b,
            status=SellerSubOrder.Status.DELIVERED, subtotal=Decimal('60.00')
        )
        self.item_b1 = OrderItem.objects.create(
            order=self.order4, sub_order=self.sub_b1, seller=self.seller_b, product=self.product_b1,
            product_name='Farm Fresh Milk', unit_price=Decimal('60.00'), mrp=Decimal('70.00'),
            product_cost_snapshot=Decimal('45.00'), quantity=1, total_price=Decimal('60.00')
        )
        self.snap_b1 = OrderItemFinancialSnapshot.objects.create(
            order_item=self.item_b1, order=self.order4, seller=self.seller_b,
            selling_price_per_unit=Decimal('60.00'), mrp_per_unit=Decimal('70.00'),
            product_cost_per_unit=Decimal('45.00'), quantity=1,
            product_revenue=Decimal('60.00'), product_cost_total=Decimal('45.00'),
            commission_rate=Decimal('8.00'), commission_amount=Decimal('4.80'),
            seller_payable=Decimal('55.20'), seller_profit=Decimal('10.20'),
            settlement_status='SETTLED'
        )

    def test_seller_isolation(self):
        """Seller A cannot see Seller B's revenue, orders, products, or profits."""
        client_a = Client()
        client_a.force_login(self.seller_a)
        response_a = client_a.get(reverse('seller:reports'))
        self.assertEqual(response_a.status_code, 200)

        # Eligible revenue for Seller A: 80 (order 1) + 25 (order 1) + 40 (order 2) = 145.00 (order 3 cancelled excluded)
        self.assertEqual(response_a.context['total_revenue'], Decimal('145.00'))
        # Sub-orders for Seller A: 3 total
        self.assertEqual(response_a.context['total_orders_count'], 3)
        # Completed deliveries: 1 (order 1 delivered)
        self.assertEqual(response_a.context['completed_deliveries_count'], 1)
        # Cancelled suborders: 1 (order 3)
        self.assertEqual(response_a.context['cancelled_suborders_count'], 1)

        # Seller B check (8% commission)
        client_b = Client()
        client_b.force_login(self.seller_b)
        response_b = client_b.get(reverse('seller:reports'))
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(response_b.context['total_revenue'], Decimal('60.00'))
        self.assertEqual(response_b.context['total_commission'], Decimal('4.80'))
        self.assertEqual(response_b.context['total_seller_payable'], Decimal('55.20'))
        self.assertEqual(response_b.context['total_store_profit'], Decimal('10.20'))

        # Verify Seller B's product does not appear in Seller A's list
        seller_a_products = [p['product_name'] for p in response_a.context['top_products']]
        self.assertNotIn('Farm Fresh Milk', seller_a_products)
        self.assertIn('Organic Tomatoes', seller_a_products)

    def test_zero_commission_and_positive_profit(self):
        """
        Verify:
        Customer payment: Rs. 180.00
        Product cost: Rs. 176.00
        Platform commission: Rs. 0.00 (0%)
        Seller payable: Rs. 180.00
        Net seller profit: +Rs. 4.00
        """
        # Create a dedicated seller C with 0% commission
        seller_c = User.objects.create_user(
            username='seller_c_zero', email='seller_c@freshmart.com', password='password123',
            role=User.Role.SELLER, is_active=True
        )
        SellerProfile.objects.create(
            user=seller_c, store_name="Zero Commission Bakery", commission_rate=Decimal('0.00')
        )
        product_buns = Product.objects.create(
            seller=seller_c, category=self.category, name='Buns', slug='buns-zero',
            sku='SKU-BUNS-ZERO',
            mrp=Decimal('50.00'), selling_price=Decimal('45.00'), product_cost=Decimal('44.00'),
            unit='pack', is_active=True
        )

        # Order with 4 buns @ Rs. 45.00 = Rs. 180.00. Cost = 4 @ Rs. 44.00 = Rs. 176.00
        order = Order.objects.create(
            order_id='ORD-BUNS-180', buyer=self.buyer,
            recipient_name='Test Buyer', recipient_phone='9876543210',
            delivery_address_text='123 Market St', pin_code='560001',
            status=Order.Status.DELIVERED, payment_status=Order.PaymentStatus.PAID,
            subtotal=Decimal('180.00'), delivery_fee=Decimal('0.00'), total_amount=Decimal('180.00')
        )
        sub_c = SellerSubOrder.objects.create(
            order=order, seller=seller_c,
            status=SellerSubOrder.Status.DELIVERED, subtotal=Decimal('180.00')
        )
        item_buns = OrderItem.objects.create(
            order=order, sub_order=sub_c, seller=seller_c, product=product_buns,
            product_name='Buns', unit_price=Decimal('45.00'), mrp=Decimal('50.00'),
            product_cost_snapshot=Decimal('44.00'), quantity=4, total_price=Decimal('180.00')
        )
        OrderItemFinancialSnapshot.objects.create(
            order_item=item_buns, order=order, seller=seller_c,
            selling_price_per_unit=Decimal('45.00'), mrp_per_unit=Decimal('50.00'),
            product_cost_per_unit=Decimal('44.00'), quantity=4,
            product_revenue=Decimal('180.00'), product_cost_total=Decimal('176.00'),
            commission_rate=Decimal('0.00'), commission_amount=Decimal('0.00'),
            seller_payable=Decimal('180.00'), seller_profit=Decimal('4.00'),
            settlement_status='SETTLED'
        )

        client = Client()
        client.force_login(seller_c)
        response = client.get(reverse('seller:reports'))
        self.assertEqual(response.status_code, 200)
        ctx = response.context

        # Assertions
        self.assertEqual(ctx['total_revenue'], Decimal('180.00'))
        self.assertEqual(ctx['total_commission'], Decimal('0.00'))
        self.assertEqual(ctx['total_seller_payable'], Decimal('180.00'))
        self.assertEqual(ctx['total_product_cost'], Decimal('176.00'))
        self.assertEqual(ctx['total_store_profit'], Decimal('4.00'))

        # Check product table
        top_prods = list(ctx['top_products'])
        self.assertEqual(len(top_prods), 1)
        bun_row = top_prods[0]
        self.assertEqual(bun_row['product_name'], 'Buns')
        self.assertEqual(bun_row['units_sold'], 4)
        self.assertEqual(bun_row['revenue'], Decimal('180.00'))
        self.assertEqual(bun_row['commission_deducted'], Decimal('0.00'))
        self.assertEqual(bun_row['seller_payable'], Decimal('180.00'))
        self.assertEqual(bun_row['product_cost'], Decimal('176.00'))
        self.assertEqual(bun_row['net_profit'], Decimal('4.00'))

    def test_financial_reconciliation(self):
        """Gross revenue - Commission = Seller payable; Store profit = Payable - Product cost."""
        client_a = Client()
        client_a.force_login(self.seller_a)
        response = client_a.get(reverse('seller:reports'))
        ctx = response.context

        total_rev = ctx['total_revenue']
        total_comm = ctx['total_commission']
        total_payable = ctx['total_seller_payable']
        total_profit = ctx['total_store_profit']

        # Expected with 0% commission:
        # Snap a1: Rev 80, Comm 0.00, Payable 80.00, Cost 60.00, Profit 20.00
        # Snap a2: Rev 25, Comm 0.00, Payable 25.00, Cost None, Profit None
        # Snap a3: Rev 40, Comm 0.00, Payable 40.00, Cost 30.00, Profit 10.00
        # Sum Rev = 145.00
        # Sum Comm = 0.00
        # Sum Payable = 145.00
        # Sum Cost (with cost data) = 60 + 30 = 90.00
        # Sum Profit = 20.00 + 10.00 = 30.00
        self.assertEqual(total_rev, Decimal('145.00'))
        self.assertEqual(total_comm, Decimal('0.00'))
        self.assertEqual(total_payable, Decimal('145.00'))
        self.assertEqual(total_profit, Decimal('30.00'))
        self.assertEqual(total_payable, total_rev - total_comm)

    def test_summary_cards_and_product_table_consistency(self):
        """Summary cards and product table rows match exactly."""
        client_a = Client()
        client_a.force_login(self.seller_a)
        response = client_a.get(reverse('seller:reports'))
        ctx = response.context

        top_products = list(ctx['top_products'])
        sum_table_rev = sum(p['revenue'] for p in top_products)
        sum_table_comm = sum(p['commission_deducted'] for p in top_products)
        sum_table_payable = sum(p['seller_payable'] for p in top_products)

        self.assertEqual(ctx['total_revenue'], sum_table_rev)
        self.assertEqual(ctx['total_commission'], sum_table_comm)
        self.assertEqual(ctx['total_seller_payable'], sum_table_payable)

    def test_cancelled_order_exclusion(self):
        """Cancelled orders/sub-orders do not count as completed deliveries or add to revenue."""
        client_a = Client()
        client_a.force_login(self.seller_a)
        response = client_a.get(reverse('seller:reports'))
        ctx = response.context

        self.assertEqual(ctx['total_orders_count'], 3)
        self.assertEqual(ctx['completed_deliveries_count'], 1)
        self.assertEqual(ctx['cancelled_suborders_count'], 1)

    def test_top_products_sorting(self):
        """Sorting by revenue, units, and profit."""
        client_a = Client()
        client_a.force_login(self.seller_a)

        # Sort by revenue (default)
        resp_rev = client_a.get(reverse('seller:reports') + '?sort_by=revenue')
        prods_rev = list(resp_rev.context['top_products'])
        self.assertEqual(prods_rev[0]['product_name'], 'Organic Tomatoes')
        self.assertEqual(prods_rev[0]['revenue'], Decimal('120.00'))
        self.assertEqual(prods_rev[0]['units_sold'], 3) # 2 in order 1, 1 in order 2
        self.assertEqual(prods_rev[0]['orders_count'], 2) # Orders 1 and 2

        # Sort by profit
        resp_profit = client_a.get(reverse('seller:reports') + '?sort_by=profit')
        prods_profit = list(resp_profit.context['top_products'])
        self.assertEqual(prods_profit[0]['product_name'], 'Organic Tomatoes')
        self.assertEqual(prods_profit[0]['net_profit'], Decimal('30.00'))

    def test_empty_seller(self):
        """Empty seller displays 0 values and helpful empty message."""
        empty_seller = User.objects.create_user(
            username='empty_seller', email='empty@freshmart.com', password='password123',
            role=User.Role.SELLER, is_active=True
        )
        SellerProfile.objects.create(user=empty_seller, store_name="Brand New Store", commission_rate=Decimal('0.00'))

        client = Client()
        client.force_login(empty_seller)
        resp = client.get(reverse('seller:reports'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_revenue'], Decimal('0.00'))
        self.assertEqual(resp.context['total_orders_count'], 0)
        self.assertEqual(resp.context['completed_deliveries_count'], 0)
        self.assertEqual(resp.context['total_store_profit'], Decimal('0.00'))
        self.assertEqual(resp.context['total_units_sold'], 0)
        self.assertEqual(len(resp.context['top_products']), 0)
        self.assertContains(resp, "No Sales Data for All Time")

    def test_date_filters(self):
        """Date filtering updates cards and products consistently."""
        client_a = Client()
        client_a.force_login(self.seller_a)

        # Today filter
        resp_today = client_a.get(reverse('seller:reports') + '?period=today')
        self.assertEqual(resp_today.status_code, 200)
        self.assertTrue(resp_today.context['date_label'].startswith('Today'))
        self.assertEqual(resp_today.context['total_revenue'], Decimal('145.00'))

        # This Month filter
        resp_month = client_a.get(reverse('seller:reports') + '?period=month')
        self.assertEqual(resp_month.status_code, 200)
        self.assertTrue(resp_month.context['date_label'].startswith('This Month'))

        # This Year filter
        resp_year = client_a.get(reverse('seller:reports') + '?period=year')
        self.assertEqual(resp_year.status_code, 200)
        self.assertTrue(resp_year.context['date_label'].startswith('This Year'))

        # Custom future date range where no orders exist
        resp_future = client_a.get(reverse('seller:reports') + '?date_from=2099-01-01&date_to=2099-01-31')
        self.assertEqual(resp_future.status_code, 200)
        self.assertEqual(resp_future.context['total_revenue'], Decimal('0.00'))
        self.assertEqual(resp_future.context['total_orders_count'], 0)
        self.assertEqual(len(resp_future.context['top_products']), 0)
        self.assertContains(resp_future, "No Sales Data for Custom (2099-01-01 to 2099-01-31)")
