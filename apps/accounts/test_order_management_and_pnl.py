from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User, SellerProfile, DeliveryProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord
from apps.orders.models import Order, OrderItem, SellerSubOrder
from apps.delivery.models import DeliveryAssignment, DeliveryOTP
from apps.reports.models import (
    OrderItemFinancialSnapshot, DeliveryEarning, CashbackLedger,
    SellerWallet, MarketplaceConfig
)


class OrderManagementAndPnLTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username='admin_boss',
            email='admin_boss@test.com',
            password='password123',
            role=User.Role.ADMIN
        )

        from apps.reports.models import MarketplaceConfig
        self.config = MarketplaceConfig.get_config()
        self.config.seller_settlement_mode = 'COMMISSION_DEDUCTION'
        self.config.cashback_calculation_mode = 'PER_UNIT'
        self.config.save()

        # Seller user
        self.seller_user = User.objects.create_user(
            username='fresh_farms',
            email='fresh@farms.com',
            password='password123',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            store_name='Fresh Farms Store',
            store_slug='fresh-farms-store',
            pickup_address='100 Orchard Way',
            is_approved=True
        )

        # Buyer user
        self.buyer_user = User.objects.create_user(
            username='happy_buyer',
            email='buyer@happy.com',
            password='password123',
            role=User.Role.BUYER
        )

        # Delivery rider
        self.delivery_user = User.objects.create_user(
            username='speedy_rider',
            email='rider@speedy.com',
            password='password123',
            role=User.Role.DELIVERY
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery_user,
            vehicle_type='BIKE',
            vehicle_number='KA-01-EE-9999',
            current_status=DeliveryProfile.Status.AVAILABLE
        )

        # Category and Product
        self.category = Category.objects.create(name='Organic Fruits', slug='organic-fruits')
        self.product = Product.objects.create(
            name='Fresh Organic Apples',
            slug='fresh-organic-apples',
            category=self.category,
            seller=self.seller_user,
            mrp=Decimal('150.00'),
            selling_price=Decimal('120.00'),
            product_cost=Decimal('80.00'),
            sku='APP-001',
            is_approved=True,
            approval_status=Product.ApprovalStatus.APPROVED,
            is_active=True
        )
        self.stock = StockRecord.objects.create(
            product=self.product,
            current_stock=50
        )

        # Order creation
        self.order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.buyer_user,
            recipient_name='Pooja Sharma',
            recipient_phone='9988776655',
            delivery_address_text='Flat 402, Green Meadows',
            pin_code='560001',
            payment_method=Order.PaymentMethod.COD,
            payment_status=Order.PaymentStatus.PENDING,
            status=Order.Status.ORDER_PLACED,
            subtotal=Decimal('240.00'),
            total_amount=Decimal('270.00'),
            delivery_fee=Decimal('30.00'),
            cashback_earned=Decimal('2.00')
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            seller=self.seller_user,
            quantity=2,
            unit_price=Decimal('120.00'),
            total_price=Decimal('240.00'),
            mrp=Decimal('150.00')
        )
        self.sub_order = SellerSubOrder.objects.create(
            order=self.order,
            seller=self.seller_user,
            status=SellerSubOrder.Status.PACKED,
            subtotal=Decimal('240.00')
        )
        self.assignment = DeliveryAssignment.objects.create(
            order=self.order,
            delivery_person=self.delivery_user,
            status=DeliveryAssignment.Status.ASSIGNED
        )
        self.snapshot = OrderItemFinancialSnapshot.objects.create(
            order=self.order,
            order_item=self.order_item,
            seller=self.seller_user,
            selling_price_per_unit=Decimal('120.00'),
            mrp_per_unit=Decimal('150.00'),
            quantity=2,
            product_revenue=Decimal('240.00'),
            product_cost_per_unit=Decimal('80.00'),
            product_cost_total=Decimal('160.00'),
            commission_rate=Decimal('10.00'),
            commission_amount=Decimal('24.00'),
            seller_payable=Decimal('216.00'),
            seller_profit=Decimal('56.00'),
            cashback_total=Decimal('2.00'),
            settlement_status='PENDING'
        )

    def test_admin_orders_view_renders_correctly(self):
        """Admin can access orders page with status tabs, search, filters, and pagination."""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('admin_dashboard:orders'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Marketplace Order Management')
        self.assertContains(response, self.order.order_id)
        self.assertContains(response, 'All Orders')
        self.assertContains(response, 'Placed')
        self.assertContains(response, 'Awaiting Rider')

    def test_admin_orders_filter_by_search_and_tab(self):
        """Admin orders search by keyword and filter by tab."""
        self.client.force_login(self.admin_user)
        # Search for Pooja Sharma
        response = self.client.get(reverse('admin_dashboard:orders'), {'q': 'Pooja'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.order_id)

        # Tab placed
        response_tab = self.client.get(reverse('admin_dashboard:orders'), {'tab': 'placed'})
        self.assertEqual(response_tab.status_code, 200)
        self.assertContains(response_tab, self.order.order_id)

        # Tab delivered should be empty
        response_del = self.client.get(reverse('admin_dashboard:orders'), {'tab': 'delivered'})
        self.assertEqual(response_del.status_code, 200)
        self.assertNotContains(response_del, self.order.order_id)

    def test_admin_order_detail_view(self):
        """Admin order detail view renders customer, items, financials, and lifecycle."""
        self.client.force_login(self.admin_user)
        url = reverse('admin_dashboard:order_detail', kwargs={'order_id': self.order.order_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Order #{self.order.order_id}")
        self.assertContains(response, "Pooja Sharma")
        self.assertContains(response, "Fresh Organic Apples")
        self.assertContains(response, "Marketplace Reconciliation")
        self.assertContains(response, "₹24.00")  # Commission
        self.assertContains(response, "₹216.00") # Seller payable

    def test_admin_confirm_order_action(self):
        """Admin confirms order using admin_order_action_view."""
        self.client.force_login(self.admin_user)
        url = reverse('admin_dashboard:order_action', kwargs={'order_id': self.order.order_id})
        response = self.client.post(url, {'action': 'confirm_order'})
        self.assertRedirects(response, reverse('admin_dashboard:order_detail', kwargs={'order_id': self.order.order_id}))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ORDER_CONFIRMED)

    def test_admin_cancel_order_action_restores_inventory(self):
        """Admin cancels order, inventory is restored and financials reversed."""
        self.client.force_login(self.admin_user)
        initial_stock = self.stock.current_stock
        url = reverse('admin_dashboard:order_action', kwargs={'order_id': self.order.order_id})
        response = self.client.post(url, {
            'action': 'cancel_order',
            'reason': 'Customer requested cancellation before dispatch'
        })
        self.assertRedirects(response, reverse('admin_dashboard:order_detail', kwargs={'order_id': self.order.order_id}))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_admin_company_pnl_view_all_periods(self):
        """Company P&L dashboard renders 18 standard KPIs, formula, and sub-ledgers."""
        self.client.force_login(self.admin_user)
        url = reverse('admin_dashboard:company_pnl')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Company Profit & Loss Statement')
        self.assertContains(response, 'Marketplace GMV')
        self.assertContains(response, 'Platform Revenue')
        self.assertContains(response, 'Platform Expenses')
        self.assertContains(response, 'Net Company Profit')
        self.assertContains(response, 'Dynamic Accounting Formula Reconciliation')
        self.assertContains(response, 'Cashback Ledger & Funding Source Attribution')
        self.assertContains(response, 'Item-Level Marketplace Pricing & Sales Ledger')

    def test_non_admin_forbidden_from_admin_dashboard(self):
        """Buyer and seller cannot access admin orders or company pnl and are redirected."""
        self.client.force_login(self.buyer_user)
        res1 = self.client.get(reverse('admin_dashboard:orders'))
        self.assertEqual(res1.status_code, 302)
        res2 = self.client.get(reverse('admin_dashboard:company_pnl'))
        self.assertEqual(res2.status_code, 302)

        self.client.force_login(self.seller_user)
        res3 = self.client.get(reverse('admin_dashboard:orders'))
        self.assertEqual(res3.status_code, 302)
        res4 = self.client.get(reverse('admin_dashboard:company_pnl'))
        self.assertEqual(res4.status_code, 302)

    def test_admin_can_reject_product_with_invalid_pricing(self):
        """Admin can reject a product that has invalid pricing (e.g. selling_price <= product_cost)."""
        self.client.force_login(self.admin_user)

        # Simulate a product submitted with selling_price <= product_cost
        bad_product = Product.objects.create(
            name='Bad Price Item',
            slug='bad-price-item',
            category=self.category,
            seller=self.seller_user,
            mrp=Decimal('100.00'),
            selling_price=Decimal('50.00'),
            product_cost=Decimal('40.00'),
            sku='BAD-001',
            is_approved=False,
            approval_status=Product.ApprovalStatus.PENDING,
            is_active=False
        )
        # Directly update product in DB to simulate invalid pricing (selling_price <= product_cost)
        Product.objects.filter(id=bad_product.id).update(
            selling_price=Decimal('50.00'),
            product_cost=Decimal('60.00') # Cost > Selling price
        )
        bad_product.refresh_from_db()

        # Admin rejects this bad product
        url = reverse('admin_dashboard:products')
        response = self.client.post(url, {
            'action': 'reject_product',
            'product_id': bad_product.id,
            'rejection_reason': 'Selling price ₹50 is lower than cost price ₹60.'
        })
        self.assertRedirects(response, url)

        bad_product.refresh_from_db()
        self.assertEqual(bad_product.approval_status, Product.ApprovalStatus.REJECTED)
        self.assertFalse(bad_product.is_approved)
        self.assertIn('lower than cost', bad_product.rejection_reason)

        # Admin trying to approve bad product should be rejected gracefully
        resp_approve = self.client.post(url, {
            'action': 'approve_product',
            'product_id': bad_product.id,
        })
        self.assertRedirects(resp_approve, url)
        bad_product.refresh_from_db()
        self.assertNotEqual(bad_product.approval_status, Product.ApprovalStatus.APPROVED)

    def test_company_pnl_order_wise_profit_and_summary_cards(self):
        """Test order-wise company profit, seller profit, averages, and summary cards."""
        from apps.reports.services import create_order_financial_snapshots

        self.client.force_login(self.admin_user)
        # Mark order as DELIVERED
        self.order.status = Order.Status.DELIVERED
        self.order.save()

        # Create snapshots
        create_order_financial_snapshots(self.order)

        # Create DeliveryEarning
        DeliveryEarning.objects.create(
            delivery_person=self.delivery_user,
            order=self.order,
            assignment=self.assignment,
            earning_amount=Decimal('20.00'),
            delivery_fee_charged=Decimal('30.00'),
            status='SETTLED',
            idempotency_key=f"EARN-{self.order.order_id}"
        )

        url = reverse('admin_dashboard:company_pnl')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # 1. Summary cards in response
        self.assertContains(response, 'Completed Orders')
        self.assertContains(response, 'Total Company Profit')
        self.assertContains(response, 'Total Seller Profit')
        self.assertContains(response, 'Avg Company Margin')
        self.assertContains(response, 'Avg Seller Margin')

        # 2. Order-wise breakdown table in response
        self.assertContains(response, 'Order-wise Profit Breakdown')
        self.assertContains(response, f"#{self.order.order_id}")
        self.assertContains(response, 'Fresh Farms Store')

        # 3. Exact calculations check
        # Revenue = 240, Comm = 24, DelRev = 30, Rider = 20, Cashback = 2
        # Company Profit = (24 + 30) - (20 + 2) = 32.00
        # Seller Payable = 216, Cost = 160 (2 * 80)
        # Seller Profit = 216 - 160 = 56.00
        self.assertEqual(response.context['completed_orders_count'], 1)
        self.assertEqual(response.context['total_completed_company_profit'], Decimal('32.00'))
        self.assertEqual(response.context['total_completed_seller_profit'], Decimal('56.00'))
        self.assertEqual(response.context['avg_company_profit_per_order'], Decimal('32.00'))
        self.assertEqual(response.context['avg_seller_profit_per_order'], Decimal('56.00'))

        # Check row in order_profit_page
        row = response.context['order_profit_page'].object_list[0]
        self.assertEqual(row['order_id'], self.order.order_id)
        self.assertEqual(row['company_profit'], Decimal('32.00'))
        self.assertEqual(row['seller_profit'], Decimal('56.00'))
        self.assertTrue(row['has_cost_data'])
        self.assertTrue(row['is_completed'])

    def test_company_pnl_insufficient_cost_data_handling(self):
        """When an order has missing product cost, seller profit displays Insufficient cost data and is not fabricated."""
        from apps.reports.services import create_order_financial_snapshots

        self.client.force_login(self.admin_user)

        # Create a product without product_cost
        no_cost_prod = Product.objects.create(
            name='Exotic Organic Mango',
            slug='exotic-organic-mango',
            category=self.category,
            seller=self.seller_user,
            mrp=Decimal('200.00'),
            selling_price=Decimal('180.00'),
            product_cost=None, # Missing cost
            sku='MANGO-001',
            is_approved=True,
            approval_status=Product.ApprovalStatus.APPROVED,
            is_active=True
        )

        ord_no_cost = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.buyer_user,
            recipient_name='Vikram Singh',
            recipient_phone='9811223344',
            delivery_address_text='B-12 Palm Grove',
            pin_code='560002',
            payment_method=Order.PaymentMethod.UPI,
            payment_status=Order.PaymentStatus.PAID,
            status=Order.Status.DELIVERED,
            subtotal=Decimal('180.00'),
            total_amount=Decimal('210.00'),
            delivery_fee=Decimal('30.00'),
            cashback_earned=Decimal('0.00')
        )
        OrderItem.objects.create(
            order=ord_no_cost,
            product=no_cost_prod,
            product_name=no_cost_prod.name,
            seller=self.seller_user,
            quantity=1,
            unit_price=Decimal('180.00'),
            total_price=Decimal('180.00'),
            mrp=Decimal('200.00')
        )
        create_order_financial_snapshots(ord_no_cost)

        url = reverse('admin_dashboard:company_pnl')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check that the order appears in order_profit_page
        row = next(r for r in response.context['order_profit_page'].object_list if r['order_id'] == ord_no_cost.order_id)
        self.assertFalse(row['has_cost_data'])
        self.assertIsNone(row['seller_profit'])
        self.assertContains(response, 'Insufficient cost data')

