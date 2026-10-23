"""
Fresh Mart — Financial & Marketplace Configuration Models.

All monetary fields use DecimalField with max_digits=12, decimal_places=2.
All wallet/ledger operations must use database transactions and idempotency keys.
"""
import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import MinValueValidator


class MarketplaceConfig(models.Model):
    """
    Singleton marketplace configuration.
    Only one row should exist. Use MarketplaceConfig.get_config() to access.
    Changes are audited via updated_by and updated_at.
    """
    # Commission settings
    commission_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Default platform commission percentage. Per-seller override is on SellerProfile.commission_rate.'
    )
    commission_fixed_per_unit = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Fixed commission per eligible unit (added to percentage).'
    )
    COMMISSION_BASE_CHOICES = [
        ('SELLING_PRICE', 'Selling Price'),
        ('MRP', 'Maximum Retail Price'),
    ]
    commission_base = models.CharField(
        max_length=20, choices=COMMISSION_BASE_CHOICES, default='SELLING_PRICE',
        help_text='Base price for commission calculation.'
    )

    # Cashback settings
    cashback_per_unit = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Default cashback amount per eligible product unit.'
    )
    CASHBACK_FUNDING_CHOICES = [
        ('COMPANY', 'Company Funded'),
        ('SELLER', 'Seller Funded'),
        ('PROMOTIONAL', 'Promotional Campaign'),
    ]
    cashback_funding = models.CharField(
        max_length=20, choices=CASHBACK_FUNDING_CHOICES, default='COMPANY',
        help_text='Who funds the buyer cashback reward.'
    )

    # Delivery settings
    delivery_fee_default = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('30.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Default delivery fee per eligible order.'
    )
    delivery_fee_free_above = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Subtotal above which delivery is free. 0 = always charge.'
    )
    delivery_person_earning_per_order = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('30.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Default delivery person earning per eligible delivered order.'
    )

    # Settlement settings
    seller_settlement_require_delivery = models.BooleanField(
        default=True, help_text='Require successful delivery before settling seller profit.'
    )
    seller_settlement_require_payment = models.BooleanField(
        default=True, help_text='Require payment verification before settling seller profit.'
    )
    return_period_days = models.PositiveIntegerField(
        default=0, help_text='Days after delivery to wait before settling (return window). 0 = settle immediately on delivery.'
    )

    # Audit
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='marketplace_config_changes'
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Marketplace Configuration'
        verbose_name_plural = 'Marketplace Configuration'

    def save(self, *args, **kwargs):
        # Enforce singleton: always use pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        """Get or create the singleton marketplace configuration."""
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    def __str__(self):
        return f"Marketplace Config (Commission: {self.commission_percentage}%, Cashback: ₹{self.cashback_per_unit}/unit, Delivery: ₹{self.delivery_fee_default})"


class MarketplaceConfigHistory(models.Model):
    """Audit trail for marketplace configuration changes."""
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='config_change_history'
    )
    field_name = models.CharField(max_length=100)
    old_value = models.CharField(max_length=255, blank=True, default='')
    new_value = models.CharField(max_length=255, blank=True, default='')
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.field_name}: {self.old_value} → {self.new_value} by {self.changed_by} at {self.changed_at}"


class ProductApprovalHistory(models.Model):
    """Audit trail for product approval decisions."""
    ACTION_CHOICES = [
        ('SUBMITTED', 'Submitted for Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('SUSPENDED', 'Suspended'),
        ('RESUBMITTED', 'Resubmitted After Edit'),
    ]
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.CASCADE, related_name='approval_history'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='product_approval_actions'
    )
    reason = models.TextField(blank=True, default='')
    previous_status = models.CharField(max_length=20, blank=True, default='')
    new_status = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.name}: {self.action} by {self.admin_user} at {self.created_at}"


class OrderItemFinancialSnapshot(models.Model):
    """
    Immutable financial snapshot for each order item at time of order.
    Created during checkout. Never modified after creation.
    Used for commission, seller payable, profit, and cashback calculations.
    """
    SETTLEMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SETTLED', 'Settled to Wallet'),
        ('REVERSED', 'Reversed'),
        ('CANCELLED', 'Cancelled'),
        ('INELIGIBLE', 'Ineligible'),
    ]

    order_item = models.OneToOneField(
        'orders.OrderItem', on_delete=models.CASCADE, related_name='financial_snapshot'
    )
    order = models.ForeignKey(
        'orders.Order', on_delete=models.CASCADE, related_name='financial_snapshots'
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='financial_snapshots'
    )

    # Price snapshots (immutable)
    selling_price_per_unit = models.DecimalField(max_digits=12, decimal_places=2)
    mrp_per_unit = models.DecimalField(max_digits=12, decimal_places=2)
    product_cost_per_unit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Product cost at time of order. Null if seller did not provide cost.'
    )
    quantity = models.PositiveIntegerField()

    # Calculated financials (immutable once set)
    product_revenue = models.DecimalField(max_digits=12, decimal_places=2, help_text='selling_price × quantity')
    product_cost_total = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='product_cost × quantity. Null if cost unavailable.'
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Commission snapshot
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, help_text='Commission % applied')
    commission_fixed = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Total commission for this item')

    # Seller financials
    seller_payable = models.DecimalField(max_digits=12, decimal_places=2, help_text='Revenue - commission')
    seller_profit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='seller_payable - product_cost_total. Null if cost unavailable.'
    )

    # Cashback
    cashback_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cashback_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    cashback_funding = models.CharField(max_length=20, default='COMPANY')

    # Settlement tracking
    settlement_status = models.CharField(
        max_length=20, choices=SETTLEMENT_STATUS_CHOICES, default='PENDING'
    )
    settled_at = models.DateTimeField(null=True, blank=True)
    settlement_idempotency_key = models.CharField(
        max_length=64, unique=True, blank=True,
        help_text='Unique key to prevent duplicate settlement.'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.settlement_idempotency_key:
            self.settlement_idempotency_key = f"OI-{self.order_item_id}-{uuid.uuid4().hex[:8]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Snapshot: OrderItem#{self.order_item_id} | Revenue:{self.product_revenue} | Commission:{self.commission_amount} | Profit:{self.seller_profit}"


class CashbackLedger(models.Model):
    """
    Auditable buyer cashback ledger.
    Tracks earned, redeemed, reversed cashback with order references.
    """
    TYPE_CHOICES = [
        ('EARNED', 'Cashback Earned'),
        ('REDEEMED', 'Cashback Redeemed'),
        ('REVERSED', 'Cashback Reversed'),
        ('EXPIRED', 'Cashback Expired'),
        ('ADMIN_ADJUSTMENT', 'Admin Adjustment'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending Confirmation'),
        ('CONFIRMED', 'Confirmed'),
        ('REVERSED', 'Reversed'),
    ]

    transaction_id = models.CharField(max_length=64, unique=True, db_index=True)
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cashback_entries'
    )
    order = models.ForeignKey(
        'orders.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cashback_entries'
    )
    order_item = models.ForeignKey(
        'orders.OrderItem', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cashback_entries'
    )
    entry_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Positive for credit, negative for debit')
    balance_before = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CONFIRMED')
    funding_source = models.CharField(max_length=20, default='COMPANY')
    description = models.CharField(max_length=500, blank=True, default='')
    related_entry = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Link to original entry when reversing.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            self.transaction_id = f"CB-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Cashback {self.entry_type}: ₹{self.amount} for {self.buyer.username} [{self.transaction_id}]"


class SellerWallet(models.Model):
    """
    Per-seller profit wallet. One wallet per seller.
    Balance must always reconcile with SellerWalletTransaction ledger.
    """
    seller = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_wallet'
    )
    available_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Settled profit available for withdrawal.'
    )
    pending_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Earnings awaiting settlement conditions.'
    )
    lifetime_credited = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Cumulative profit credits (not including reversals).'
    )
    total_withdrawn = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Confirmed completed withdrawals.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet: {self.seller.username} | Available: ₹{self.available_balance} | Pending: ₹{self.pending_earnings}"


class SellerWalletTransaction(models.Model):
    """
    Immutable seller wallet ledger entry.
    Each financial event creates a new transaction — never modify or delete existing entries.
    """
    TYPE_CHOICES = [
        ('EARNING_PENDING', 'Earning Pending Settlement'),
        ('PROFIT_CREDIT', 'Settled Profit Credit'),
        ('REVERSAL', 'Profit Reversal (Refund/Return)'),
        ('WITHDRAWAL_REQUEST', 'Withdrawal Requested'),
        ('WITHDRAWAL_CONFIRMED', 'Withdrawal Confirmed'),
        ('WITHDRAWAL_REJECTED', 'Withdrawal Rejected'),
        ('ADMIN_ADJUSTMENT', 'Admin Manual Adjustment'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('REVERSED', 'Reversed'),
        ('FAILED', 'Failed'),
    ]

    transaction_id = models.CharField(max_length=64, unique=True, db_index=True)
    wallet = models.ForeignKey(SellerWallet, on_delete=models.CASCADE, related_name='transactions')
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallet_transactions'
    )
    order = models.ForeignKey(
        'orders.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='wallet_transactions'
    )
    order_item = models.ForeignKey(
        'orders.OrderItem', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='wallet_transactions'
    )
    transaction_type = models.CharField(max_length=25, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text='Positive for credit, negative for debit')
    balance_before = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='COMPLETED')
    description = models.TextField(blank=True, default='')
    idempotency_key = models.CharField(
        max_length=128, unique=True,
        help_text='Unique key to prevent duplicate transactions.'
    )
    related_transaction = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Link to original transaction when reversing.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            self.transaction_id = f"SWT-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_type}: ₹{self.amount} for {self.seller.username} [{self.transaction_id}]"


class DeliveryEarning(models.Model):
    """Delivery person earning record per eligible order."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SETTLED', 'Settled'),
        ('REVERSED', 'Reversed'),
        ('CANCELLED', 'Cancelled'),
    ]

    delivery_person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delivery_earnings'
    )
    order = models.ForeignKey(
        'orders.Order', on_delete=models.CASCADE, related_name='delivery_earnings'
    )
    assignment = models.ForeignKey(
        'delivery.DeliveryAssignment', on_delete=models.CASCADE, related_name='earnings'
    )
    earning_amount = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee_charged = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Delivery fee charged to customer for this order.'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    idempotency_key = models.CharField(max_length=128, unique=True)
    description = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = f"DE-{self.order_id}-{self.delivery_person_id}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Earning: ₹{self.earning_amount} for {self.delivery_person.username} on Order#{self.order.order_id}"


class DeliveryPayout(models.Model):
    """Delivery person payout tracking."""
    STATUS_CHOICES = [
        ('REQUESTED', 'Requested'),
        ('APPROVED', 'Approved'),
        ('PAID', 'Paid'),
        ('REJECTED', 'Rejected'),
    ]
    delivery_person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delivery_payouts'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='REQUESTED')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_delivery_payouts'
    )
    description = models.TextField(blank=True, default='')
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"DeliveryPayout: ₹{self.amount} for {self.delivery_person.username} ({self.status})"


class SellerPayout(models.Model):
    """Seller payout/withdrawal tracking."""
    STATUS_CHOICES = [
        ('REQUESTED', 'Requested'),
        ('APPROVED', 'Approved'),
        ('PAID', 'Paid'),
        ('REJECTED', 'Rejected'),
    ]
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_payouts'
    )
    wallet = models.ForeignKey(
        SellerWallet, on_delete=models.CASCADE, related_name='payouts'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='REQUESTED')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_seller_payouts'
    )
    wallet_transaction = models.ForeignKey(
        SellerWalletTransaction, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payout'
    )
    description = models.TextField(blank=True, default='')
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"SellerPayout: ₹{self.amount} for {self.seller.username} ({self.status})"
