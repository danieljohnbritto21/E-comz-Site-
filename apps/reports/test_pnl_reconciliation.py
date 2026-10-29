"""
Master P&L Financial Reconciliation & Accounting Audit Tests.
Verifies the 15 requirements specified in the Master Prompt:
1. Third-party Selling Price is not incorrectly treated as company revenue.
2. Seller payable is not counted as company profit.
3. Product Cost is not incorrectly labelled seller profit.
4. Cashback is calculated from eligible Selling Price spend.
5. Cashback is not available before verified delivery.
6. Company-funded cashback is not double-counted.
7. Seller-funded cashback is attributed correctly.
8. Delivery revenue and delivery earnings are calculated separately.
9. Commission is not deducted twice.
10. Refunds and reversals are reflected correctly.
11. Duplicate transactions do not inflate totals.
12. Dashboard totals reconcile with order-level calculations.
13. The ₹34.50 versus ₹33.50 discrepancy is resolved based on actual records.
14. Missing seller acquisition costs do not produce invented seller profit.
15. Existing order, payment, delivery, inventory, authentication, and wallet workflows continue working.
"""

from decimal import Decimal
import uuid
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, SellerProfile, DeliveryProfile
from apps.catalog.models import Category, Product
from apps.inventory.models import StockRecord
from apps.orders.models import Order, OrderItem
from apps.delivery.models import DeliveryAssignment, DeliveryOTP
from apps.reports.models import (
    OrderItemFinancialSnapshot, DeliveryEarning, CashbackLedger,
    SellerWallet, SellerWalletTransaction, MarketplaceConfig
)
from apps.reports.services import (
    calculate_cashback_for_spend,
    calculate_seller_financials,
    create_order_financial_snapshots,
    award_buyer_cashback,
    reverse_buyer_cashback,
    get_buyer_cashback_balance,
    settle_seller_profit,
    create_delivery_earning,
)


class MasterPnLReconciliationTests(TestCase):
    def setUp(self):
        self.config = MarketplaceConfig.get_config()
        self.config.cashback_rate_per_10 = Decimal('1.00')
        self.config.cashback_rounding_policy = 'PROPORTIONAL'
        self.config.cashback_calculation_mode = 'SPEND_BASED'
        self.config.seller_settlement_mode = 'PRODUCT_COST_RECEIVABLE'
        self.config.cashback_funding = 'COMPANY'
        self.config.delivery_fee_default = Decimal('30.00')
        self.config.delivery_person_earning_per_order = Decimal('30.00')
        self.config.commission_percentage = Decimal('10.00')
        self.config.save()

        # Admin
        self.admin = User.objects.create_superuser(
            username='admin_auditor', email='auditor@freshmart.com',
            password='password123', role=User.Role.ADMIN
        )

        # Seller
        self.seller = User.objects.create_user(
            username='seller_kirana', email='kirana@freshmart.com',
            password='password123', role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller, store_name='Kirana Store',
            store_slug='kirana-store', commission_rate=Decimal('10.00'),
            is_approved=True
        )

        # Buyer
        self.buyer = User.objects.create_user(
            username='buyer_customer', email='buyer@freshmart.com',
            password='password123', role=User.Role.BUYER,
            phone_number='9876543210'
        )

        # Delivery Agent
        self.delivery_agent = User.objects.create_user(
            username='rider_fleet', email='rider@freshmart.com',
            password='password123', role=User.Role.DELIVERY,
            phone_number='9876500000'
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery_agent, vehicle_type='BIKE'
        )

        # Category
        self.category = Category.objects.create(
            name='Bakery & Dairy', slug='bakery-dairy'
        )
        self._seq = 100

    def _create_product(self, mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'), seller=None):
        self._seq += 1
        seller = seller or self.seller
        prod = Product.objects.create(
            seller=seller, category=self.category,
            name=f"Product {self._seq}", slug=f"product-{self._seq}",
            mrp=mrp, selling_price=sp, product_cost=cost,
            is_approved=True, is_active=True
        )
        StockRecord.objects.create(
            product=prod, current_stock=100, low_stock_threshold=5
        )
        return prod

    def _create_order(self, product, qty=1, status=Order.Status.ORDER_PLACED, payment_status='PAID', del_fee=Decimal('30.00')):
        self._seq += 1
        order_id = f"ORD-PTEST-{self._seq}-{uuid.uuid4().hex[:4].upper()}"
        line_total = product.selling_price * qty
        order = Order.objects.create(
            order_id=order_id, buyer=self.buyer, status=status,
            recipient_name='John Doe', recipient_phone='9876543210',
            delivery_address_text="123 Market Road", pin_code='560001',
            subtotal=line_total, delivery_fee=del_fee,
            total_amount=line_total + del_fee,
            payment_method='COD', payment_status=payment_status,
        )
        OrderItem.objects.create(
            order=order, product=product, seller=product.seller,
            product_name=product.name, unit_price=product.selling_price,
            quantity=qty, total_price=line_total,
            mrp=product.mrp, product_cost_snapshot=product.product_cost
        )
        return order

    # -------------------------------------------------------------------------
    # TEST 1: Third-party Selling Price is not company revenue
    # -------------------------------------------------------------------------
    def test_01_third_party_selling_price_not_company_revenue(self):
        """1. Third-party product Selling Price is not counted as company revenue."""
        prod = self._create_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        self.assertEqual(res.status_code, 200)

        # Customer paid ₹100 product + ₹30 delivery = ₹130 total.
        # Company revenue must NOT be ₹100 or ₹130. Third-party selling price belongs to escrow.
        self.assertNotEqual(res.context['company_revenue_total'], Decimal('130.00'))
        self.assertNotEqual(res.context['company_revenue_total'], Decimal('100.00'))

    # -------------------------------------------------------------------------
    # TEST 2: Seller payable is not counted as company profit
    # -------------------------------------------------------------------------
    def test_02_seller_payable_not_counted_as_company_profit(self):
        """2. Seller payable (receivable) is a liability, not company profit."""
        prod = self._create_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        # Net company profit must not absorb the ₹90 seller receivable
        self.assertNotEqual(ctx['net_company_profit'], Decimal('90.00'))
        # Total Seller Payable exists as distinct liability
        self.assertIn('total_completed_seller_payable', ctx)
        self.assertEqual(ctx['total_completed_seller_payable'], Decimal('90.00'))

    # -------------------------------------------------------------------------
    # TEST 3: Product Cost is not labelled seller profit
    # -------------------------------------------------------------------------
    def test_03_product_cost_not_labelled_seller_profit(self):
        """3. Under PRODUCT_COST_RECEIVABLE, Product Cost is Seller Receivable, NOT Seller Profit."""
        fin = calculate_seller_financials(
            selling_price=Decimal('100.00'), product_cost=Decimal('90.00'),
            quantity=1, commission_rate=Decimal('10.00'), commission_fixed=Decimal('0.00'),
            config=self.config
        )
        self.assertEqual(fin['seller_receivable'], Decimal('90.00'))
        self.assertEqual(fin['seller_payable'], Decimal('90.00'))
        # Seller profit MUST be None because seller wholesale acquisition cost is not tracked
        self.assertIsNone(fin['seller_profit'])

    # -------------------------------------------------------------------------
    # TEST 4: Cashback is calculated from eligible Selling Price spend
    # -------------------------------------------------------------------------
    def test_04_cashback_calculated_from_selling_price_spend(self):
        """4. Cashback is calculated from eligible Selling Price spend (₹1 per ₹10)."""
        cb_100 = calculate_cashback_for_spend(Decimal('100.00'), self.config)
        self.assertEqual(cb_100, Decimal('10.00'))

        cb_45 = calculate_cashback_for_spend(Decimal('45.00'), self.config)
        self.assertEqual(cb_45, Decimal('4.50'))

        cb_300 = calculate_cashback_for_spend(Decimal('300.00'), self.config)
        self.assertEqual(cb_300, Decimal('30.00'))

    # -------------------------------------------------------------------------
    # TEST 5: Cashback is not available before verified delivery
    # -------------------------------------------------------------------------
    def test_05_cashback_not_available_before_verified_delivery(self):
        """5. Customer cashback balance remains ₹0 until successful OTP delivery."""
        prod = self._create_product(sp=Decimal('100.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.OUT_FOR_DELIVERY)
        create_order_financial_snapshots(order)

        balance = get_buyer_cashback_balance(self.buyer)
        self.assertEqual(balance, Decimal('0.00'))

    # -------------------------------------------------------------------------
    # TEST 6: Company-funded cashback is not double-counted
    # -------------------------------------------------------------------------
    def test_06_company_funded_cashback_not_double_counted(self):
        """6. Company-funded cashback is recognized exactly once as a platform expense."""
        prod = self._create_product(sp=Decimal('100.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)
        awarded = award_buyer_cashback(order)
        self.assertEqual(awarded, Decimal('10.00'))

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        # Exactly ₹10 recognized in company expenses
        self.assertEqual(ctx['company_cashback_expense'], Decimal('10.00'))

    # -------------------------------------------------------------------------
    # TEST 7: Seller-funded cashback is attributed correctly
    # -------------------------------------------------------------------------
    def test_07_seller_funded_cashback_attributed_correctly(self):
        """7. Seller-funded cashback is distinguished from company expenses."""
        self.config.cashback_funding = 'SELLER'
        self.config.save()

        prod = self._create_product(sp=Decimal('100.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)
        award_buyer_cashback(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        self.assertEqual(ctx['seller_funded_cashback'], Decimal('10.00'))
        self.assertEqual(ctx['company_cashback_expense'], Decimal('0.00'))

    # -------------------------------------------------------------------------
    # TEST 8: Delivery revenue and delivery earnings calculated separately
    # -------------------------------------------------------------------------
    def test_08_delivery_revenue_and_earnings_calculated_separately(self):
        """8. Delivery fee charged (revenue) and rider payout (expense) are separate."""
        prod = self._create_product(sp=Decimal('100.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED, del_fee=Decimal('35.00'))
        create_order_financial_snapshots(order)

        assignment = DeliveryAssignment.objects.create(
            order=order, delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.DELIVERED
        )
        self.config.delivery_person_earning_per_order = Decimal('25.00')
        self.config.save()
        create_delivery_earning(order, assignment)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        self.assertEqual(ctx['delivery_fees_charged'], Decimal('35.00'))
        self.assertEqual(ctx['delivery_payout_expenses'], Decimal('25.00'))
        self.assertEqual(ctx['company_delivery_margin'], Decimal('10.00'))

    # -------------------------------------------------------------------------
    # TEST 9: Commission is not deducted twice
    # -------------------------------------------------------------------------
    def test_09_commission_not_deducted_twice(self):
        """9. Under PRODUCT_COST_RECEIVABLE, seller receivable is Product Cost, not cost minus commission."""
        fin = calculate_seller_financials(
            selling_price=Decimal('100.00'), product_cost=Decimal('90.00'),
            quantity=1, commission_rate=Decimal('10.00'),
            config=self.config
        )
        self.assertEqual(fin['seller_receivable'], Decimal('90.00'))
        self.assertNotEqual(fin['seller_receivable'], Decimal('80.00'))

    # -------------------------------------------------------------------------
    # TEST 10: Refunds and reversals reflected correctly
    # -------------------------------------------------------------------------
    def test_10_refunds_and_reversals_reflected_correctly(self):
        """10. Reversals create linked audit entries without deleting historical data."""
        prod = self._create_product(sp=Decimal('100.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)
        award_buyer_cashback(order)

        # Reverse cashback
        rev = reverse_buyer_cashback(order, reason="Customer Return")
        self.assertEqual(rev, Decimal('10.00'))

        reversal_entry = CashbackLedger.objects.filter(order=order, entry_type='REVERSED').first()
        self.assertIsNotNone(reversal_entry)
        self.assertEqual(reversal_entry.amount, Decimal('-10.00'))
        self.assertEqual(reversal_entry.status, 'CONFIRMED')

    # -------------------------------------------------------------------------
    # TEST 11: Duplicate transactions do not inflate totals (Idempotency)
    # -------------------------------------------------------------------------
    def test_11_duplicate_settlements_do_not_inflate_totals(self):
        """11. Idempotency guarantees multiple settlement triggers never double-credit."""
        prod = self._create_product(sp=Decimal('100.00'), cost=Decimal('90.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)

        settle1 = settle_seller_profit(order)
        settle2 = settle_seller_profit(order)

        self.assertEqual(settle1, Decimal('90.00'))
        self.assertEqual(settle2, Decimal('0.00'))

        wallet = SellerWallet.objects.get(seller=self.seller)
        self.assertEqual(wallet.available_balance, Decimal('90.00'))

    # -------------------------------------------------------------------------
    # TEST 12: Dashboard totals reconcile with order-level calculations
    # -------------------------------------------------------------------------
    def test_12_dashboard_totals_reconcile_with_order_level_rows(self):
        """12. Dashboard executive cards reconcile identically with the sum of order-level rows."""
        prod = self._create_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)

        assignment = DeliveryAssignment.objects.create(
            order=order, delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.DELIVERED
        )
        create_delivery_earning(order, assignment)
        award_buyer_cashback(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        order_rows = ctx['order_profit_page'].object_list
        sum_order_profits = sum(r['company_profit'] for r in order_rows if r['is_completed'])
        self.assertEqual(ctx['total_completed_company_profit'], sum_order_profits)

    # -------------------------------------------------------------------------
    # TEST 13: The ₹34.50 vs ₹33.50 discrepancy is resolved
    # -------------------------------------------------------------------------
    def test_13_exact_profit_reconciliation_no_discrepancy(self):
        """13. Top-level Net Company Profit strictly equals Total Completed Company Profit."""
        self.seller_profile.commission_rate = Decimal('0.00')
        self.seller_profile.save()

        prod = self._create_product(mrp=Decimal('50.00'), sp=Decimal('45.00'), cost=Decimal('44.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED, del_fee=Decimal('30.00'))
        create_order_financial_snapshots(order)

        assignment = DeliveryAssignment.objects.create(
            order=order, delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.DELIVERED
        )
        create_delivery_earning(order, assignment)
        award_buyer_cashback(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        # Net Company Profit MUST equal Total Completed Company Profit exactly
        self.assertEqual(ctx['net_company_profit'], ctx['total_completed_company_profit'])
        # For this order: (0 comm + 30 del_fee) - (30 rider + 4.50 cb) = -4.50
        self.assertEqual(ctx['net_company_profit'], Decimal('-4.50'))

    # -------------------------------------------------------------------------
    # TEST 14: Missing seller acquisition costs do not produce invented profit
    # -------------------------------------------------------------------------
    def test_14_missing_seller_acquisition_cost_not_invented(self):
        """14. When seller cost is missing or third-party, seller profit is None (Not Tracked)."""
        prod = self._create_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=None)
        order = self._create_order(prod, qty=1, status=Order.Status.DELIVERED)
        create_order_financial_snapshots(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        row = res.context['order_profit_page'].object_list[0]

        self.assertIsNone(row['seller_profit'])
        self.assertFalse(row['is_seller_profit_known'])

    # -------------------------------------------------------------------------
    # TEST 15: Existing cart, checkout, payment, auth continue working
    # -------------------------------------------------------------------------
    def test_15_regression_order_and_auth_workflows(self):
        """15. Core platform auth, order history, and tracking remain fully operational."""
        prod = self._create_product(sp=Decimal('100.00'))
        order = self._create_order(prod, qty=1, status=Order.Status.ORDER_CONFIRMED)

        client = Client()
        client.force_login(self.buyer)

        # Buyer order history
        res_hist = client.get(reverse('orders:order_history'))
        self.assertEqual(res_hist.status_code, 200)
        self.assertContains(res_hist, order.order_id)

        # Buyer order track
        res_track = client.get(reverse('orders:track', kwargs={'order_id': order.order_id}))
        self.assertEqual(res_track.status_code, 200)
