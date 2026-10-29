from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, SellerProfile, BuyerProfile, DeliveryProfile
from apps.catalog.models import Category, Product
from apps.orders.models import Order, OrderItem, Address
from apps.delivery.models import DeliveryAssignment
from apps.reports.models import (
    MarketplaceConfig, MarketplaceConfigHistory, ProductApprovalHistory,
    OrderItemFinancialSnapshot, CashbackLedger, SellerWallet,
    SellerWalletTransaction, DeliveryEarning, SellerPayout, DeliveryPayout
)
from apps.reports.services import (
    get_seller_commission_rate,
    calculate_seller_financials,
    create_order_financial_snapshots,
    award_buyer_cashback,
    reverse_buyer_cashback,
    redeem_buyer_cashback,
    restore_redeemed_cashback,
    get_buyer_cashback_balance,
    settle_seller_profit,
    add_pending_seller_earnings,
    reverse_seller_profit,
    create_delivery_earning,
    reverse_delivery_earning
)


class MarketplaceFinancialsAndApprovalTests(TestCase):
    """
    Comprehensive regression and integration tests for the Marketplace Financial System:
    - Product approval workflow and history audit
    - Per-seller & global commission rates
    - Buyer cashback lifecycle (accrual, redemption, reversal, balance tracking)
    - Seller profit wallet lifecycle (pending, settlement on delivery, payout debit, reversal)
    - Delivery partner earnings
    - Company P&L reconciliation
    """

    def setUp(self):
        # 1. Admin user
        self.admin = User.objects.create_superuser(
            username='admin_test',
            email='admin@freshmart.test',
            password='adminpassword123'
        )

        # 2. Seller user & profile
        self.seller_user = User.objects.create_user(
            username='green_farmer',
            email='farmer@freshmart.test',
            password='sellerpassword123',
            role=User.Role.SELLER
        )
        self.seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            store_name='Green Farm Organics',
            store_slug='green-farm-organics',
            commission_rate=Decimal('6.00'),  # 6% custom commission
            is_approved=True
        )

        # 3. Buyer user & profile
        self.buyer_user = User.objects.create_user(
            username='fresh_shopper',
            email='shopper@freshmart.test',
            password='buyerpassword123',
            role=User.Role.BUYER
        )
        self.buyer_profile = BuyerProfile.objects.create(user=self.buyer_user)

        # 4. Delivery partner user & profile
        self.rider_user = User.objects.create_user(
            username='speedy_rider',
            email='rider@freshmart.test',
            password='riderpassword123',
            role=User.Role.DELIVERY
        )
        self.delivery_profile = DeliveryProfile.objects.create(
            user=self.rider_user,
            vehicle_type='BIKE',
            vehicle_number='KA-01-AB-1234',
            license_number='DL1234567890'
        )

        # 5. Category
        self.category = Category.objects.create(name='Fresh Veggies', slug='fresh-veggies')

        # 6. Global MarketplaceConfig
        self.config = MarketplaceConfig.get_config()
        self.config.commission_percentage = Decimal('5.00')
        self.config.delivery_fee_default = Decimal('30.00')
        self.config.cashback_per_unit = Decimal('1.00')
        self.config.delivery_person_earning_per_order = Decimal('25.00')
        self.config.seller_settlement_mode = 'COMMISSION_DEDUCTION'
        self.config.cashback_calculation_mode = 'PER_UNIT'
        self.config.save()

    def test_product_approval_lifecycle_and_history(self):
        """Verify that newly created products default to PENDING and admin approvals log history."""
        product = Product.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Organic Tomatoes',
            sku='TOM-001',
            mrp=Decimal('60.00'),
            selling_price=Decimal('50.00'),
            product_cost=Decimal('35.00')
        )
        # Default state
        self.assertEqual(product.approval_status, Product.ApprovalStatus.PENDING)
        self.assertFalse(product.is_approved)

        # Admin approves
        product.approval_status = Product.ApprovalStatus.APPROVED
        product.save()
        ProductApprovalHistory.objects.create(
            product=product,
            action='APPROVED',
            admin_user=self.admin,
            reason='Approved after quality check',
            previous_status='PENDING',
            new_status='APPROVED'
        )
        product.refresh_from_db()
        self.assertTrue(product.is_approved)
        self.assertEqual(product.approval_history.count(), 1)
        self.assertEqual(product.approval_history.first().action, 'APPROVED')

        # Admin rejects
        product.approval_status = Product.ApprovalStatus.REJECTED
        product.rejection_reason = 'Packaging specifications not met'
        product.save()
        ProductApprovalHistory.objects.create(
            product=product,
            action='REJECTED',
            admin_user=self.admin,
            reason=product.rejection_reason,
            previous_status='APPROVED',
            new_status='REJECTED'
        )
        product.refresh_from_db()
        self.assertFalse(product.is_approved)
        self.assertEqual(product.approval_history.count(), 2)

    def test_commission_calculation_per_seller_and_default(self):
        """Verify per-seller commission override and fallback to global default."""
        rate_custom = get_seller_commission_rate(self.seller_user)
        self.assertEqual(rate_custom, Decimal('6.00'))

        other_seller = User.objects.create_user(
            username='default_seller',
            email='other@freshmart.test',
            password='password123',
            role=User.Role.SELLER
        )
        rate_default = get_seller_commission_rate(other_seller)
        self.assertEqual(rate_default, Decimal('5.00'))

    def test_buyer_cashback_lifecycle(self):
        """Verify buyer cashback accrual, balance check, redemption, and reversal."""
        product = Product.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Fresh Mint',
            sku='MNT-001',
            mrp=Decimal('20.00'),
            selling_price=Decimal('15.00'),
            product_cost=Decimal('10.00'),
            is_approved=True
        )
        order = Order.objects.create(
            order_id='FM-CB-001',
            buyer=self.buyer_user,
            recipient_name='Test Buyer',
            recipient_phone='9876543210',
            delivery_address_text='123 Test St',
            pin_code='560001',
            subtotal=Decimal('45.00'),
            delivery_fee=Decimal('30.00'),
            total_amount=Decimal('75.00')
        )
        order_item = OrderItem.objects.create(
            order=order,
            seller=self.seller_user,
            product=product,
            product_name=product.name,
            mrp=Decimal('20.00'),
            product_cost_snapshot=product.product_cost,
            unit_price=Decimal('15.00'),
            quantity=3,
            total_price=Decimal('45.00')
        )

        create_order_financial_snapshots(order)

        # 1. Award cashback (3 units x ₹1 = ₹3)
        total_awarded = award_buyer_cashback(order)
        self.assertEqual(total_awarded, Decimal('3.00'))
        self.assertEqual(get_buyer_cashback_balance(self.buyer_user), Decimal('3.00'))

        # 2. Redeem cashback on next order
        next_order = Order.objects.create(
            order_id='FM-CB-002',
            buyer=self.buyer_user,
            recipient_name='Test Buyer',
            recipient_phone='9876543210',
            delivery_address_text='123 Test St',
            pin_code='560001',
            subtotal=Decimal('30.00'),
            delivery_fee=Decimal('30.00'),
            total_amount=Decimal('60.00')
        )
        redeemed = redeem_buyer_cashback(next_order, Decimal('2.00'))
        self.assertEqual(redeemed, Decimal('2.00'))
        self.assertEqual(get_buyer_cashback_balance(self.buyer_user), Decimal('1.00'))

        # 3. Restore redeemed cashback
        restored = restore_redeemed_cashback(next_order)
        self.assertEqual(restored, Decimal('2.00'))
        self.assertEqual(get_buyer_cashback_balance(self.buyer_user), Decimal('3.00'))

        # 4. Reverse awarded cashback
        reversed_amt = reverse_buyer_cashback(order)
        self.assertEqual(reversed_amt, Decimal('3.00'))
        self.assertEqual(get_buyer_cashback_balance(self.buyer_user), Decimal('0.00'))

    def test_seller_wallet_lifecycle_and_order_snapshots(self):
        """Verify order snapshot creation, pending earnings, settlement upon delivery, and payout debit."""
        product = Product.objects.create(
            seller=self.seller_user,
            category=self.category,
            name='Organic Spinach',
            sku='SPI-001',
            mrp=Decimal('40.00'),
            selling_price=Decimal('30.00'),
            product_cost=Decimal('20.00'),
            is_approved=True
        )
        order = Order.objects.create(
            order_id='FM-WL-001',
            buyer=self.buyer_user,
            recipient_name='Test Buyer',
            recipient_phone='9876543210',
            delivery_address_text='123 Test St',
            pin_code='560001',
            subtotal=Decimal('60.00'),
            delivery_fee=Decimal('30.00'),
            total_amount=Decimal('90.00')
        )
        item = OrderItem.objects.create(
            order=order,
            seller=self.seller_user,
            product=product,
            product_name=product.name,
            mrp=Decimal('40.00'),
            product_cost_snapshot=product.product_cost,
            unit_price=Decimal('30.00'),
            quantity=2,
            total_price=Decimal('60.00')
        )

        # 1. Financial Snapshot creation
        snapshots = create_order_financial_snapshots(order)
        self.assertEqual(len(snapshots), 1)
        snap = snapshots[0]
        # Product revenue = 60.00. Commission = 6% of 60.00 = 3.60. Total cost = 2 * 20.00 = 40.00. Net profit = 60 - 3.60 - 40 = 16.40. Seller net revenue = 56.40
        self.assertEqual(snap.product_revenue, Decimal('60.00'))
        self.assertEqual(snap.commission_rate, Decimal('6.00'))
        self.assertEqual(snap.commission_amount, Decimal('3.60'))
        self.assertEqual(snap.seller_payable, Decimal('56.40'))
        self.assertEqual(snap.seller_profit, Decimal('16.40'))

        # 2. Add pending earnings
        add_pending_seller_earnings(order)
        wallet = SellerWallet.objects.get(seller=self.seller_user)
        self.assertEqual(wallet.pending_earnings, Decimal('16.40'))
        self.assertEqual(wallet.available_balance, Decimal('0.00'))

        # 3. Settle profit upon delivery
        settle_seller_profit(order)
        wallet.refresh_from_db()
        self.assertEqual(wallet.pending_earnings, Decimal('0.00'))
        self.assertEqual(wallet.available_balance, Decimal('16.40'))
        self.assertEqual(wallet.lifetime_credited, Decimal('16.40'))

        # 4. Payout request debits available wallet balance
        payout = SellerPayout.objects.create(
            seller=self.seller_user,
            wallet=wallet,
            amount=Decimal('10.00'),
            status='REQUESTED'
        )
        wallet.available_balance -= Decimal('10.00')
        wallet.total_withdrawn += Decimal('10.00')
        wallet.save()
        wallet.refresh_from_db()
        self.assertEqual(wallet.available_balance, Decimal('6.40'))
        self.assertEqual(wallet.total_withdrawn, Decimal('10.00'))

    def test_delivery_earnings_and_reversals(self):
        """Verify delivery partner earning creation and reversal."""
        order = Order.objects.create(
            order_id='FM-DEL-001',
            buyer=self.buyer_user,
            recipient_name='Test Buyer',
            recipient_phone='9876543210',
            delivery_address_text='123 Test St',
            pin_code='560001',
            subtotal=Decimal('100.00'),
            delivery_fee=Decimal('30.00'),
            total_amount=Decimal('130.00')
        )
        assignment = DeliveryAssignment.objects.create(
            order=order,
            delivery_person=self.rider_user,
            status=DeliveryAssignment.Status.ACCEPTED
        )

        earning = create_delivery_earning(order, assignment)
        self.assertIsNotNone(earning)
        self.assertEqual(earning.earning_amount, Decimal('25.00'))
        self.assertEqual(earning.status, 'SETTLED')

        # Reverse delivery earning
        reverse_delivery_earning(order)
        earning.refresh_from_db()
        self.assertEqual(earning.status, 'REVERSED')

    def test_admin_config_view_and_history(self):
        """Test admin marketplace config view updates and history audit."""
        client = Client()
        client.force_login(self.admin)

        post_data = {
            'commission_percentage': '7.50',
            'delivery_fee_default': '35.00',
            'delivery_fee_free_above': '599.00',
            'cashback_per_unit': '1.50',
            'delivery_person_earning_per_order': '30.00',
            'return_period_days': '2'
        }

        res = client.post(reverse('admin_dashboard:marketplace_config'), data=post_data)
        self.assertEqual(res.status_code, 302)

        config = MarketplaceConfig.get_config()
        self.assertEqual(config.commission_percentage, Decimal('7.50'))
        self.assertEqual(config.delivery_fee_default, Decimal('35.00'))

        history = MarketplaceConfigHistory.objects.filter(field_name='commission_percentage')
        self.assertGreaterEqual(history.count(), 1)
