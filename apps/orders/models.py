import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.catalog.models import Product


class Address(models.Model):
    class AddressType(models.TextChoices):
        HOME = 'HOME', 'Home'
        WORK = 'WORK', 'Work'
        OTHER = 'OTHER', 'Other'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='addresses')
    full_name = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=20)
    house_number = models.CharField(max_length=100)
    street = models.CharField(max_length=150)
    area = models.CharField(max_length=150)
    city = models.CharField(max_length=100, default='Central City')
    state = models.CharField(max_length=100, default='State')
    pin_code = models.CharField(max_length=10)
    landmark = models.CharField(max_length=150, blank=True, default='')
    address_type = models.CharField(max_length=20, choices=AddressType.choices, default=AddressType.HOME)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', '-created_at']

    def save(self, *args, **kwargs):
        if self.is_default:
            Address.objects.filter(user=self.user).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @property
    def formatted(self):
        parts = [self.house_number, self.street, self.area, self.city, f"{self.state} - {self.pin_code}"]
        if self.landmark:
            parts.insert(3, f"Near {self.landmark}")
        return ", ".join(p for p in parts if p)

    def __str__(self):
        return f"{self.full_name} ({self.get_address_type_display()}): {self.formatted}"


class Order(models.Model):
    class Status(models.TextChoices):
        ORDER_PLACED = 'ORDER_PLACED', 'Order Placed'
        ORDER_CONFIRMED = 'ORDER_CONFIRMED', 'Order Confirmed'
        PREPARING = 'PREPARING', 'Preparing Your Order'
        PACKED = 'PACKED', 'Packed'
        READY_FOR_PICKUP = 'READY_FOR_PICKUP', 'Ready for Pickup'
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
        DELIVERED = 'DELIVERED', 'Delivered'
        CANCELLED = 'CANCELLED', 'Cancelled'
        FAILED_DELIVERY = 'FAILED_DELIVERY', 'Failed Delivery'
        RETURN_REQUESTED = 'RETURN_REQUESTED', 'Return Requested'
        RETURNED = 'RETURNED', 'Returned'
        REFUND_PENDING = 'REFUND_PENDING', 'Refund Pending'
        REFUNDED = 'REFUNDED', 'Refunded'

    class PaymentMethod(models.TextChoices):
        COD = 'COD', 'Cash on Delivery'
        UPI = 'UPI', 'UPI Payment'
        GPAY = 'GPAY', 'Google Pay'
        PHONEPE = 'PHONEPE', 'PhonePe'
        CARD = 'CARD', 'Credit / Debit Card'
        NETBANKING = 'NETBANKING', 'Net Banking'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending / Unpaid'
        PAID = 'PAID', 'Paid / Verified'
        FAILED = 'FAILED', 'Payment Failed'
        REFUNDED = 'REFUNDED', 'Refunded'

    order_id = models.CharField(max_length=32, unique=True, db_index=True)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    
    # Snapshot of address details
    recipient_name = models.CharField(max_length=120)
    recipient_phone = models.CharField(max_length=20)
    delivery_address_text = models.TextField()
    pin_code = models.CharField(max_length=10)
    address_type = models.CharField(max_length=20, default='HOME')

    delivery_date = models.DateField(default=timezone.now)
    delivery_slot = models.CharField(max_length=100, default='10:00 AM - 01:00 PM')
    delivery_instructions = models.TextField(blank=True, default='')

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ORDER_PLACED)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COD)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    transaction_id = models.CharField(max_length=100, blank=True, default='')

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    stock_deducted = models.BooleanField(default=False, help_text='True if stock was deducted for this order')
    stock_restored = models.BooleanField(default=False, help_text='True if stock was restored upon cancellation')
    checkout_token = models.CharField(max_length=64, blank=True, default='', db_index=True)

    # Financial tracking
    cashback_earned = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        help_text='Total cashback earned by buyer for this order.'
    )
    cashback_redeemed = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        help_text='Cashback amount redeemed/applied by buyer at checkout.'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def generate_order_id(cls):
        date_str = timezone.now().strftime('%Y%m%d')
        unique_token = uuid.uuid4().hex[:6].upper()
        return f"ORD-{date_str}-{unique_token}"

    @property
    def status_badge_class(self):
        badges = {
            self.Status.ORDER_PLACED: 'badge-placed',
            self.Status.ORDER_CONFIRMED: 'badge-confirmed',
            self.Status.PREPARING: 'badge-preparing',
            self.Status.PACKED: 'badge-packed',
            self.Status.READY_FOR_PICKUP: 'badge-ready',
            self.Status.OUT_FOR_DELIVERY: 'badge-out',
            self.Status.DELIVERED: 'badge-delivered',
            self.Status.CANCELLED: 'badge-cancelled',
            self.Status.FAILED_DELIVERY: 'badge-failed',
            self.Status.RETURN_REQUESTED: 'badge-warning',
            self.Status.RETURNED: 'badge-secondary',
            self.Status.REFUND_PENDING: 'badge-warning',
            self.Status.REFUNDED: 'badge-refunded',
        }
        return badges.get(self.status, 'badge-secondary')

    @property
    def is_cancellable(self):
        return self.status in [self.Status.ORDER_PLACED, self.Status.ORDER_CONFIRMED, self.Status.PREPARING]

    @property
    def delivery_address(self):
        return self.delivery_address_text

    def add_status_history(self, status, user=None, comment=''):
        self.status = status
        self.save(update_fields=['status', 'updated_at'])
        OrderStatusHistory.objects.create(
            order=self,
            status=status,
            updated_by=user,
            comment=comment
        )

    def __str__(self):
        from apps.catalog.currency import format_inr
        return f"{self.order_id} - {format_inr(self.total_amount)} ({self.get_status_display()})"


class SellerSubOrder(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Acceptance'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        PREPARING = 'PREPARING', 'Preparing'
        PACKED = 'PACKED', 'Packed'
        READY_FOR_PICKUP = 'READY_FOR_PICKUP', 'Ready for Pickup'
        HANDED_OVER = 'HANDED_OVER', 'Handed Over to Delivery'
        SHIPPED = 'SHIPPED', 'Handed to Delivery'
        DELIVERED = 'DELIVERED', 'Delivered'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELLED = 'CANCELLED', 'Cancelled'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='sub_orders')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_sub_orders')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(blank=True, default='')
    handover_confirmed_by_seller = models.BooleanField(default=False)
    handover_time = models.DateTimeField(null=True, blank=True)
    stock_restored = models.BooleanField(default=False, help_text='True if stock was restored upon rejection/cancellation')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('order', 'seller')

    @property
    def sub_order_id(self):
        return f"{self.order.order_id}-S{self.seller_id}"

    def __str__(self):
        return f"#{self.sub_order_id} - Seller: {self.seller.username} ({self.get_status_display()})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    sub_order = models.ForeignKey(SellerSubOrder, on_delete=models.CASCADE, related_name='items', null=True, blank=True)
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='order_items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='sold_items')
    
    # Snapshot details
    product_name = models.CharField(max_length=200)
    brand = models.CharField(max_length=100, blank=True, default='')
    unit_value = models.CharField(max_length=50, blank=True, default='')
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    product_cost_snapshot = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Product cost per unit at time of order.'
    )
    quantity = models.PositiveIntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    image_url = models.CharField(max_length=500, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def subtotal(self):
        return self.total_price

    @property
    def unit(self):
        return self.unit_value

    def __str__(self):
        return f"{self.product_name} x {self.quantity} in {self.order.order_id}"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    status = models.CharField(max_length=30)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    comment = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    @property
    def notes(self):
        return self.comment

    def __str__(self):
        return f"{self.order.order_id} -> {self.status} at {self.created_at}"
