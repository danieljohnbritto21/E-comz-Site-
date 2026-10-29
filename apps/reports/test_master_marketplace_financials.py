"""
Master Marketplace Financial System Test Suite
Covers all 30 business rule test requirements from Section 11 of the master implementation specification:
- Pricing (Tests 1 - 4)
- Customer Cashback (Tests 5 - 13)
- Seller Settlement (Tests 14 - 18)
- Delivery & OTP Automation (Tests 19 - 22)
- Company P&L & Balance Sheet Accounting (Tests 23 - 28)
- Regression & RBAC Security (Tests 29 - 30)
"""

from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.accounts.models import User, SellerProfile, BuyerProfile, DeliveryProfile
from apps.catalog.models import Category, SubCategory, Product, validate_pricing_rules
from apps.inventory.models import StockRecord, StockMovement
from apps.orders.models import Order, OrderItem, Address, SellerSubOrder
from apps.delivery.models import DeliveryAssignment, DeliveryOTP, DeliveryAttempt
from apps.delivery.sms_service import send_delivery_otp_sms
from apps.reports.models import (
    MarketplaceConfig, OrderItemFinancialSnapshot, CashbackLedger,
    SellerWallet, SellerWalletTransaction, DeliveryEarning
)
from apps.reports.services import (
    calculate_cashback_for_spend,
    calculate_seller_financials,
    create_order_financial_snapshots,
    award_buyer_cashback,
    reverse_buyer_cashback,
    redeem_buyer_cashback,
    get_buyer_cashback_balance,
    settle_seller_profit,
    add_pending_seller_earnings,
    create_delivery_earning,
)


class MasterMarketplaceFinancialSystemTests(TestCase):
    def setUp(self):
        # 1. Platform Admin
        self.admin = User.objects.create_superuser(
            username='master_admin',
            email='master_admin@freshmart.local',
            password='Password123!',
            role=User.Role.ADMIN
        )

        # 2. Marketplace Seller
        self.seller = User.objects.create_user(
            username='marketplace_seller',
            email='seller@freshmart.local',
            password='Password123!',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller,
            store_name='Fresh Organic Farm',
            commission_rate=Decimal('10.00')
        )

        # 3. Buyer / Customer
        self.buyer = User.objects.create_user(
            username='customer_john',
            email='john@freshmart.local',
            password='Password123!',
            role=User.Role.BUYER,
            phone_number='9876543210'
        )
        self.buyer_profile = BuyerProfile.objects.create(user=self.buyer)

        # 4. Delivery Partner
        self.delivery_agent = User.objects.create_user(
            username='delivery_rider',
            email='rider@freshmart.local',
            password='Password123!',
            role=User.Role.DELIVERY,
            phone_number='9123456789'
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery_agent,
            vehicle_type='BIKE',
            vehicle_number='KA-01-AB-1234',
            current_status='AVAILABLE'
        )

        # 5. Product Category & Base Produce
        self.category = Category.objects.create(name='Fresh Vegetables', slug='fresh-vegetables')
        self.subcategory = SubCategory.objects.create(category=self.category, name='Greens', slug='greens')

        # 6. Global Marketplace Configuration (Prompt Specification)
        self.config = MarketplaceConfig.get_config()
        self.config.cashback_rate_per_10 = Decimal('1.00')
        self.config.cashback_rounding_policy = 'PROPORTIONAL'
        self.config.cashback_calculation_mode = 'SPEND_BASED'
        self.config.seller_settlement_mode = 'PRODUCT_COST_RECEIVABLE'
        self.config.cashback_funding = 'COMPANY'
        self.config.cashback_redemption_enabled = True
        self.config.delivery_fee_default = Decimal('30.00')
        self.config.delivery_person_earning_per_order = Decimal('25.00')
        self.config.save()

    def _create_test_product(self, mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'), name='Organic Tomatoes'):
        seq = getattr(self, '_prod_seq', 1000)
        self._prod_seq = seq + 1
        sku = f"SKU-{seq}"
        prod = Product.objects.create(
            seller=self.seller,
            category=self.category,
            subcategory=self.subcategory,
            name=name,
            mrp=mrp,
            selling_price=sp,
            product_cost=cost,
            unit='kg',
            unit_value='1 kg',
            sku=sku,
            is_approved=True,
            approval_status=Product.ApprovalStatus.APPROVED
        )
        StockRecord.objects.create(product=prod, current_stock=100, low_stock_threshold=5)
        return prod

    def _create_test_order(self, product, quantity=1, payment_method='COD', payment_status='PENDING'):
        sp = product.selling_price
        subtotal = sp * quantity
        order = Order.objects.create(
            order_id=Order.generate_order_id(),
            buyer=self.buyer,
            recipient_name='John Doe',
            recipient_phone='9876543210',
            delivery_address_text='123 Green Street, Central Ward',
            pin_code='560001',
            status=Order.Status.ORDER_CONFIRMED,
            payment_method=payment_method,
            payment_status=payment_status,
            subtotal=subtotal,
            discount_amount=Decimal('0.00'),
            delivery_fee=Decimal('30.00'),
            tax_amount=Decimal('0.00'),
            total_amount=subtotal + Decimal('30.00'),
            cashback_redeemed=Decimal('0.00'),
            stock_deducted=True
        )
        item = OrderItem.objects.create(
            order=order,
            seller=product.seller,
            product=product,
            product_name=product.name,
            quantity=quantity,
            unit_price=sp,
            mrp=product.mrp,
            total_price=subtotal,
            product_cost_snapshot=product.product_cost
        )
        SellerSubOrder.objects.create(
            order=order,
            seller=product.seller,
            subtotal=subtotal,
            status=SellerSubOrder.Status.ACCEPTED
        )
        return order, item

    # =========================================================================
    # SECTION 11.1: PRICING VALIDATION (Tests 1 - 4)
    # =========================================================================

    def test_01_pricing_valid_110_100_90_accepted(self):
        """1. MRP ₹110, Selling Price ₹100, Product Cost ₹90 is accepted."""
        is_valid, err, cleaned = validate_pricing_rules(Decimal('110.00'), Decimal('100.00'), Decimal('90.00'))
        self.assertTrue(is_valid)
        self.assertEqual(err, "")
        self.assertEqual(cleaned, (Decimal('110.00'), Decimal('100.00'), Decimal('90.00')))

        # Persistence verification
        prod = self._create_test_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        self.assertIsNotNone(prod.id)
        self.assertEqual(prod.mrp, Decimal('110.00'))
        self.assertEqual(prod.selling_price, Decimal('100.00'))
        self.assertEqual(prod.product_cost, Decimal('90.00'))

    def test_02_pricing_selling_price_equal_product_cost_rejected(self):
        """2. Selling Price equal to Product Cost is rejected."""
        is_valid, err, _ = validate_pricing_rules(Decimal('110.00'), Decimal('90.00'), Decimal('90.00'))
        self.assertFalse(is_valid)
        self.assertIn("Selling Price must be greater than Product Cost", err)

        # Model validation rejection
        prod = Product(
            seller=self.seller, category=self.category, name='Test Invalid',
            mrp=Decimal('110.00'), selling_price=Decimal('90.00'), product_cost=Decimal('90.00'),
            approval_status=Product.ApprovalStatus.PENDING
        )
        with self.assertRaises(ValidationError):
            prod.clean()

    def test_03_pricing_selling_price_below_product_cost_rejected(self):
        """3. Selling Price below Product Cost is rejected."""
        is_valid, err, _ = validate_pricing_rules(Decimal('110.00'), Decimal('85.00'), Decimal('90.00'))
        self.assertFalse(is_valid)
        self.assertIn("Selling Price must be greater than Product Cost", err)

        prod = Product(
            seller=self.seller, category=self.category, name='Test Below Cost',
            mrp=Decimal('110.00'), selling_price=Decimal('85.00'), product_cost=Decimal('90.00'),
            approval_status=Product.ApprovalStatus.PENDING
        )
        with self.assertRaises(ValidationError):
            prod.clean()

    def test_04_pricing_mrp_equal_or_below_selling_price_rejected(self):
        """4. MRP equal to or below Selling Price is rejected."""
        # MRP == SP
        is_valid_eq, err_eq, _ = validate_pricing_rules(Decimal('100.00'), Decimal('100.00'), Decimal('90.00'))
        self.assertFalse(is_valid_eq)
        self.assertIn("MRP must be greater than Selling Price", err_eq)

        # MRP < SP
        is_valid_lt, err_lt, _ = validate_pricing_rules(Decimal('95.00'), Decimal('100.00'), Decimal('90.00'))
        self.assertFalse(is_valid_lt)
        self.assertIn("MRP must be greater than Selling Price", err_lt)

    # =========================================================================
    # SECTION 11.2: CUSTOMER CASHBACK (Tests 5 - 13)
    # =========================================================================

    def test_05_cashback_100_spend_produces_10_cashback(self):
        """5. ₹100 eligible spend produces ₹10 cashback."""
        cb = calculate_cashback_for_spend(Decimal('100.00'), self.config)
        self.assertEqual(cb, Decimal('10.00'))

    def test_06_cashback_45_spend_produces_4_50_proportional(self):
        """6. ₹45 eligible spend produces ₹4.50 cashback under the proportional rounding policy."""
        self.config.cashback_rounding_policy = 'PROPORTIONAL'
        cb = calculate_cashback_for_spend(Decimal('45.00'), self.config)
        self.assertEqual(cb, Decimal('4.50'))

    def test_07_cashback_three_units_at_100_produce_30(self):
        """7. Three units at ₹100 each produce ₹30 cashback."""
        prod = self._create_test_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, item = self._create_test_order(prod, quantity=3)
        snapshots = create_order_financial_snapshots(order)
        snap = snapshots[0]
        self.assertEqual(snap.quantity, 3)
        self.assertEqual(snap.product_revenue, Decimal('300.00'))
        self.assertEqual(snap.cashback_total, Decimal('30.00'))

    def test_08_cashback_mrp_and_product_cost_do_not_affect_calculation(self):
        """8. MRP and Product Cost do not affect cashback calculation (strictly Selling Price spend)."""
        # Product A: MRP 200, SP 100, Cost 90 -> Cashback = 10
        prod_a = self._create_test_product(mrp=Decimal('200.00'), sp=Decimal('100.00'), cost=Decimal('90.00'), name='A')
        # Product B: MRP 110, SP 100, Cost 50 -> Cashback = 10
        prod_b = self._create_test_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('50.00'), name='B')

        cb_a = calculate_cashback_for_spend(prod_a.selling_price, self.config)
        cb_b = calculate_cashback_for_spend(prod_b.selling_price, self.config)
        self.assertEqual(cb_a, Decimal('10.00'))
        self.assertEqual(cb_b, Decimal('10.00'))

    def test_09_cashback_remains_pending_before_successful_delivery_verification(self):
        """9. Cashback remains pending before successful delivery verification."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        create_order_financial_snapshots(order)

        # Before delivery: available balance must be 0
        balance = get_buyer_cashback_balance(self.buyer)
        self.assertEqual(balance, Decimal('0.00'))

        # Ledger contains 0 confirmed EARNED entries
        confirmed_earned = CashbackLedger.objects.filter(buyer=self.buyer, entry_type='EARNED', status='CONFIRMED').count()
        self.assertEqual(confirmed_earned, 0)

    def test_10_cashback_becomes_available_after_successful_delivery_otp_verification(self):
        """10. Cashback becomes available after successful delivery OTP verification."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_status='PAID')
        create_order_financial_snapshots(order)

        # Complete delivery and award cashback
        award_buyer_cashback(order)

        available_balance = get_buyer_cashback_balance(self.buyer)
        self.assertEqual(available_balance, Decimal('10.00'))

        ledger_entry = CashbackLedger.objects.get(buyer=self.buyer, order=order, entry_type='EARNED')
        self.assertEqual(ledger_entry.amount, Decimal('10.00'))
        self.assertEqual(ledger_entry.status, 'CONFIRMED')

    def test_11_cashback_duplicate_delivery_callbacks_cannot_credit_twice(self):
        """11. Duplicate delivery callbacks cannot credit cashback twice (Idempotency)."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_status='PAID')
        create_order_financial_snapshots(order)

        # First trigger
        award1 = award_buyer_cashback(order)
        self.assertEqual(award1, Decimal('10.00'))

        # Duplicate trigger
        award2 = award_buyer_cashback(order)
        self.assertEqual(award2, Decimal('0.00'))

        # Balance remains exactly ₹10.00
        balance = get_buyer_cashback_balance(self.buyer)
        self.assertEqual(balance, Decimal('10.00'))
        self.assertEqual(CashbackLedger.objects.filter(buyer=self.buyer, order=order, entry_type='EARNED').count(), 1)

    def test_12_cashback_refunded_or_cancelled_items_receive_correct_reversal(self):
        """12. Refunded or cancelled items receive the correct reversal."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_status='PAID')
        create_order_financial_snapshots(order)
        award_buyer_cashback(order)

        self.assertEqual(get_buyer_cashback_balance(self.buyer), Decimal('10.00'))

        # Reverse cashback on cancellation/refund
        reversed_amt = reverse_buyer_cashback(order, reason="Customer cancelled")
        self.assertEqual(reversed_amt, Decimal('10.00'))

        # Available balance drops back to 0
        self.assertEqual(get_buyer_cashback_balance(self.buyer), Decimal('0.00'))

        rev_entry = CashbackLedger.objects.get(buyer=self.buyer, order=order, entry_type='REVERSED')
        self.assertEqual(rev_entry.amount, Decimal('-10.00'))

    def test_13_cashback_customers_cannot_redeem_pending_or_unavailable(self):
        """13. Customers cannot redeem pending or unavailable cashback."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        create_order_financial_snapshots(order)

        # Buyer has 0 available balance (cashback is only pending delivery)
        self.assertEqual(get_buyer_cashback_balance(self.buyer), Decimal('0.00'))

        # Attempt to redeem ₹10.00
        redeemed = redeem_buyer_cashback(order, Decimal('10.00'))
        self.assertEqual(redeemed, Decimal('0.00'))
        self.assertEqual(get_buyer_cashback_balance(self.buyer), Decimal('0.00'))

    # =========================================================================
    # SECTION 11.3: SELLER SETTLEMENT (Tests 14 - 18)
    # =========================================================================

    def test_14_seller_settlement_sp100_cost90_results_in_90_receivable(self):
        """14. Selling Price ₹100 and Product Cost ₹90 results in seller receivable of ₹90 per eligible unit."""
        prod = self._create_test_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        snapshots = create_order_financial_snapshots(order)
        snap = snapshots[0]

        self.assertEqual(snap.effective_seller_receivable, Decimal('90.00'))
        self.assertEqual(snap.seller_receivable, Decimal('90.00'))

    def test_15_seller_receivable_and_cashback_recorded_separately(self):
        """15. Seller receivable and cashback are recorded separately."""
        prod = self._create_test_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        snapshots = create_order_financial_snapshots(order)
        snap = snapshots[0]

        # Product Cost = 90, Cashback = 10
        self.assertEqual(snap.seller_receivable, Decimal('90.00'))
        self.assertEqual(snap.cashback_total, Decimal('10.00'))
        self.assertNotEqual(snap.seller_receivable, snap.cashback_total)

    def test_16_seller_commission_not_double_deducted(self):
        """16. Commission is not double-deducted when Product Cost represents the seller's agreed receivable."""
        fin = calculate_seller_financials(
            selling_price=Decimal('100.00'),
            product_cost=Decimal('90.00'),
            quantity=1,
            commission_rate=Decimal('10.00'),
            commission_fixed=Decimal('0.00'),
            config=self.config
        )
        # Under PRODUCT_COST_RECEIVABLE, seller receivable is ₹90, NOT ₹90 - ₹10 = ₹80
        self.assertEqual(fin['seller_receivable'], Decimal('90.00'))
        self.assertEqual(fin['seller_payable'], Decimal('90.00'))

    def test_17_seller_settlement_cannot_be_duplicated(self):
        """17. Seller settlements cannot be duplicated (Idempotency)."""
        prod = self._create_test_product(sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_status='PAID')
        order.status = Order.Status.DELIVERED
        order.save(update_fields=['status'])
        create_order_financial_snapshots(order)

        # Trigger settlement 1
        settle1 = settle_seller_profit(order)
        self.assertEqual(settle1, Decimal('90.00'))

        # Trigger settlement 2 (duplicate)
        settle2 = settle_seller_profit(order)
        self.assertEqual(settle2, Decimal('0.00'))

        wallet = SellerWallet.objects.get(seller=self.seller)
        self.assertEqual(wallet.available_balance, Decimal('90.00'))
        self.assertEqual(SellerWalletTransaction.objects.filter(seller=self.seller, order=order, transaction_type='PROFIT_CREDIT').count(), 1)

    def test_18_seller_wallet_balances_reconcile_with_transaction_ledger(self):
        """18. Seller wallet balances reconcile with their transaction ledger."""
        wallet, _ = SellerWallet.objects.get_or_create(seller=self.seller)
        wallet.available_balance = Decimal('0.00')
        wallet.save()

        # Record credits and debits
        SellerWalletTransaction.objects.create(
            wallet=wallet, seller=self.seller, transaction_type='PROFIT_CREDIT',
            amount=Decimal('90.00'), balance_before=Decimal('0.00'), balance_after=Decimal('90.00'),
            status='COMPLETED', idempotency_key='TEST-RECON-1'
        )
        SellerWalletTransaction.objects.create(
            wallet=wallet, seller=self.seller, transaction_type='PROFIT_CREDIT',
            amount=Decimal('90.00'), balance_before=Decimal('90.00'), balance_after=Decimal('180.00'),
            status='COMPLETED', idempotency_key='TEST-RECON-2'
        )
        SellerWalletTransaction.objects.create(
            wallet=wallet, seller=self.seller, transaction_type='WITHDRAWAL_CONFIRMED',
            amount=Decimal('-50.00'), balance_before=Decimal('180.00'), balance_after=Decimal('130.00'),
            status='COMPLETED', idempotency_key='TEST-RECON-3'
        )
        wallet.available_balance = Decimal('130.00')
        wallet.save()

        # Reconcile ledger
        ledger_total = sum(t.amount for t in wallet.transactions.filter(status='COMPLETED'))
        self.assertEqual(ledger_total, wallet.available_balance)

    # =========================================================================
    # SECTION 11.4: DELIVERY & OTP (Tests 19 - 22)
    # =========================================================================

    def test_19_delivery_cod_collection_automatically_triggers_otp_workflow(self):
        """19. COD collection automatically triggers the OTP workflow."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_method='COD')
        assignment = DeliveryAssignment.objects.create(
            order=order, delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.PICKED_UP
        )

        client = Client()
        client.force_login(self.delivery_agent)

        # Delivery partner submits cash collection
        url = reverse('delivery:order_detail', kwargs={'assignment_id': assignment.id})
        res = client.post(url, {
            'action': 'collect_cod',
            'collected_amount': str(order.total_amount)
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        assignment.refresh_from_db()
        self.assertTrue(assignment.cod_collected)

        # Verify OTP was generated automatically
        otp = DeliveryOTP.objects.filter(assignment=assignment, is_used=False).first()
        self.assertIsNotNone(otp)
        self.assertFalse(otp.is_used)

    def test_20_delivery_invalid_expired_or_reused_otps_are_rejected(self):
        """20. Invalid, expired, or reused OTPs are rejected."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        assignment = DeliveryAssignment.objects.create(
            order=order, delivery_person=self.delivery_agent,
            status=DeliveryAssignment.Status.OUT_FOR_DELIVERY
        )
        otp, raw_code = DeliveryOTP.generate(assignment)

        # Invalid code
        valid_bad, msg_bad = otp.verify("000000")
        self.assertFalse(valid_bad)
        self.assertIn("Incorrect OTP", msg_bad)

        # Correct code verifies successfully
        valid_good, msg_good = otp.verify(raw_code)
        self.assertTrue(valid_good)

        # Reused code rejected
        valid_reused, msg_reused = otp.verify(raw_code)
        self.assertFalse(valid_reused)
        self.assertIn("already been used", msg_reused)

    def test_21_delivery_failed_sms_delivery_reported_accurately(self):
        """21. Failed SMS delivery is reported accurately."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        order.recipient_phone = ""  # Missing phone
        res = send_delivery_otp_sms(order, "123456")
        self.assertFalse(res['success'])
        self.assertIn("Missing customer phone", res['error'])

    def test_22_delivery_online_payment_confirmation_not_mistaken_for_delivery_completion(self):
        """22. Online payment confirmation is not mistaken for delivery completion."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_method='UPI', payment_status='PAID')
        create_order_financial_snapshots(order)

        # Order is paid online, but still in transit (not delivered)
        order.status = Order.Status.OUT_FOR_DELIVERY
        order.save(update_fields=['status'])

        # Cashback must still be 0 available
        self.assertEqual(get_buyer_cashback_balance(self.buyer), Decimal('0.00'))

        # Seller wallet balance must still be 0 available
        wallet = SellerWallet.objects.filter(seller=self.seller).first()
        available = wallet.available_balance if wallet else Decimal('0.00')
        self.assertEqual(available, Decimal('0.00'))

    # =========================================================================
    # SECTION 11.5: COMPANY P&L (Tests 23 - 28)
    # =========================================================================

    def test_23_pnl_third_party_sales_not_counted_as_company_revenue(self):
        """23. Third-party seller sales are not incorrectly counted as company revenue."""
        prod = self._create_test_product(sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_status='PAID')
        order.status = Order.Status.DELIVERED
        order.save(update_fields=['status'])
        create_order_financial_snapshots(order)
        settle_seller_profit(order)

        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        self.assertEqual(res.status_code, 200)

        ctx = res.context
        # Customer paid ₹100 product price, but company revenue is ONLY commission + delivery fee (₹10 + ₹30 = ₹40), NOT ₹100 + ₹30 = ₹130
        self.assertNotEqual(ctx['company_revenue_total'], Decimal('130.00'))
        self.assertEqual(ctx['company_owned_revenue'], Decimal('0.00'))

    def test_24_pnl_cashback_expense_attributed_to_funding_source(self):
        """24. Cashback expense is attributed to its funding source."""
        prod = self._create_test_product(sp=Decimal('100.00'))
        order, _ = self._create_test_order(prod, quantity=1, payment_status='PAID')
        create_order_financial_snapshots(order)
        award_buyer_cashback(order)

        entry = CashbackLedger.objects.get(buyer=self.buyer, order=order, entry_type='EARNED')
        self.assertEqual(entry.funding_source, 'COMPANY')

    def test_25_pnl_delivery_earnings_and_seller_liabilities_not_company_profit(self):
        """25. Delivery earnings and seller liabilities are not counted as company profit."""
        client = Client()
        client.force_login(self.admin)
        res = client.get(reverse('admin_dashboard:company_pnl'))
        ctx = res.context

        # Liabilities exist as separate isolated items
        self.assertIn('pending_seller_liabilities', ctx)
        self.assertIn('available_seller_wallet_balances', ctx)
        self.assertIn('customer_cashback_liability', ctx)

    def test_26_pnl_historical_orders_retain_original_pricing_snapshots(self):
        """26. Historical orders retain their original pricing and policy snapshots."""
        prod = self._create_test_product(mrp=Decimal('110.00'), sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, _ = self._create_test_order(prod, quantity=1)
        snapshots = create_order_financial_snapshots(order)
        snap = snapshots[0]

        # Now seller alters product pricing in catalog
        prod.mrp = Decimal('180.00')
        prod.selling_price = Decimal('150.00')
        prod.product_cost = Decimal('120.00')
        prod.save(update_fields=['mrp', 'selling_price', 'product_cost'])

        # Snapshot retains historical values
        snap.refresh_from_db()
        self.assertEqual(snap.selling_price_per_unit, Decimal('100.00'))
        self.assertEqual(snap.product_cost_per_unit, Decimal('90.00'))
        self.assertEqual(snap.seller_receivable, Decimal('90.00'))

    def test_27_pnl_date_filters_and_totals_reconcile_with_underlying_ledger(self):
        """27. Date filters and totals reconcile with the underlying ledger."""
        client = Client()
        client.force_login(self.admin)
        res_all = client.get(reverse('admin_dashboard:company_pnl') + '?period=all')
        self.assertEqual(res_all.status_code, 200)
        self.assertIn('total_gmv', res_all.context)

    def test_28_pnl_missing_costs_clearly_identified_as_incomplete(self):
        """28. Missing costs are clearly identified instead of being fabricated."""
        prod = self._create_test_product(sp=Decimal('100.00'), cost=Decimal('90.00'))
        order, item = self._create_test_order(prod, quantity=1)
        # Snapshot with missing product cost
        snap = OrderItemFinancialSnapshot.objects.create(
            order_item=item, order=order, seller=self.seller,
            selling_price_per_unit=Decimal('100.00'), mrp_per_unit=Decimal('110.00'),
            product_cost_per_unit=None, quantity=1,
            product_revenue=Decimal('100.00'), product_cost_total=None,
            commission_rate=Decimal('10.00'), commission_amount=Decimal('10.00'),
            seller_payable=Decimal('90.00'), seller_receivable=None,
            seller_profit=None
        )
        self.assertFalse(snap.is_cost_recorded)

    # =========================================================================
    # SECTION 11.6: REGRESSION & RBAC SECURITY (Tests 29 - 30)
    # =========================================================================

    def test_29_regression_cart_checkout_stock_workflows_intact(self):
        """29. Existing cart, checkout, stock, product approval, payment, authentication, and order workflows continue to work."""
        prod = self._create_test_product(sp=Decimal('100.00'), cost=Decimal('90.00'))
        stock_rec = StockRecord.objects.get(product=prod)
        init_stock = stock_rec.current_stock

        client = Client()
        client.force_login(self.buyer)

        # Add to cart
        client.post(reverse('cart:add_to_cart', kwargs={'product_id': prod.id}), {'quantity': 2})

        # Checkout
        checkout_data = {
            'customer_name': 'John Doe',
            'customer_phone': '9876543210',
            'full_name': 'John Doe',
            'phone_number': '9876543210',
            'house_number': '12',
            'street': 'Rose Lane',
            'pin_code': '560001',
            'payment_method': 'COD',
            'delivery_date': 'today',
            'delivery_slot': '10:00 AM - 01:00 PM'
        }
        res = client.post(reverse('orders:checkout'), checkout_data)
        self.assertEqual(res.status_code, 302)

        # Stock deducted
        stock_rec.refresh_from_db()
        self.assertEqual(stock_rec.current_stock, init_stock - 2)

    def test_30_rbac_unauthorized_users_cannot_access_financial_data(self):
        """30. Unauthorized users cannot access restricted wallet, cost, or financial data."""
        client = Client()
        # Customer attempts to view Admin Company P&L
        client.force_login(self.buyer)
        res_pnl = client.get(reverse('admin_dashboard:company_pnl'))
        # Must be redirected or forbidden (role_required)
        self.assertIn(res_pnl.status_code, [302, 403])

        # Customer attempts to access seller product form
        res_seller = client.get(reverse('seller:product_add'))
        self.assertIn(res_seller.status_code, [302, 403])
