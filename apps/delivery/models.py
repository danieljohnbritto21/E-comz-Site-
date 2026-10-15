import hashlib
import hmac
import secrets
import string
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.orders.models import Order


class DeliveryAssignment(models.Model):
    class Status(models.TextChoices):
        ASSIGNED = 'ASSIGNED', 'Assigned'
        ACCEPTED = 'ACCEPTED', 'Accepted by Delivery Agent'
        ARRIVED_AT_STORE = 'ARRIVED_AT_STORE', 'Arrived at Store'
        PICKED_UP = 'PICKED_UP', 'Picked Up from Seller'
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
        DELIVERED = 'DELIVERED', 'Delivered'
        FAILED = 'FAILED', 'Delivery Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='delivery_assignment')
    delivery_person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='deliveries',
        limit_choices_to={'role': 'DELIVERY'}
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_deliveries'
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ASSIGNED)
    pickup_time = models.DateTimeField(null=True, blank=True)
    delivered_time = models.DateTimeField(null=True, blank=True)
    
    # Store pickup verification
    shop_verified = models.BooleanField(default=False)
    shop_verified_at = models.DateTimeField(null=True, blank=True)

    # Cash on Delivery collection tracking
    cod_collected = models.BooleanField(default=False)
    cod_collected_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cod_collected_at = models.DateTimeField(null=True, blank=True)

    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-assigned_at']

    @property
    def is_cod(self):
        return self.order.payment_method == 'COD'

    @property
    def cod_amount_due(self):
        if self.is_cod and not self.cod_collected and self.order.payment_status != 'PAID':
            return self.order.total_amount
        return Decimal('0.00')

    @property
    def is_customer_details_unlocked(self):
        """Privacy Rule: Customer name, phone & address are unlocked ONLY after verified store pickup."""
        return self.status in [self.Status.PICKED_UP, self.Status.OUT_FOR_DELIVERY, self.Status.DELIVERED]

    def __str__(self):
        return f"{self.order.order_id} -> {self.delivery_person.get_full_name() or self.delivery_person.username} ({self.get_status_display()})"


class DeliveryOTP(models.Model):
    assignment = models.ForeignKey(DeliveryAssignment, on_delete=models.CASCADE, related_name='otps')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='delivery_otps')
    hashed_otp = models.CharField(max_length=128)
    salt = models.CharField(max_length=32)
    recipient_phone = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    sms_status = models.CharField(max_length=50, default='SENT')
    sms_reference = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def generate(cls, assignment, raw_code=None, expiry_minutes=5):
        """Generate a cryptographically secure 6-digit OTP, store salted hash, and return (instance, raw_code)."""
        if not raw_code:
            raw_code = ''.join(secrets.choice(string.digits) for _ in range(6))
        
        salt = secrets.token_hex(16)
        hashed_otp = hashlib.sha256((salt + str(raw_code)).encode('utf-8')).hexdigest()
        expires_at = timezone.now() + timezone.timedelta(minutes=expiry_minutes)

        # Invalidate any prior active OTPs for this order
        cls.objects.filter(order=assignment.order, is_used=False).update(is_used=True)

        otp_instance = cls.objects.create(
            assignment=assignment,
            order=assignment.order,
            hashed_otp=hashed_otp,
            salt=salt,
            recipient_phone=assignment.order.recipient_phone,
            expires_at=expires_at,
            max_attempts=5,
            is_used=False,
            sms_reference=f"SMS-{secrets.token_hex(4).upper()}-CODE-{raw_code}"
        )
        return otp_instance, raw_code

    @property
    def customer_code(self):
        """Returns the customer's 6-digit delivery verification code."""
        if self.sms_reference and '-CODE-' in self.sms_reference:
            return self.sms_reference.split('-CODE-')[-1]
        return ''

    @property
    def simulated_code(self):
        """Alias for backward compatibility."""
        return self.customer_code


    def verify(self, raw_code):
        """Verify the user-provided OTP code. Returns (is_valid: bool, message: str)."""
        if self.is_used:
            return False, "This OTP has already been used."
        
        if timezone.now() > self.expires_at:
            return False, "This OTP has expired. Please request a new OTP."
        
        if self.attempts_count >= self.max_attempts:
            return False, "Maximum verification attempts reached (5). Please request a new OTP."

        computed_hash = hashlib.sha256((self.salt + str(raw_code).strip()).encode('utf-8')).hexdigest()
        if hmac.compare_digest(self.hashed_otp, computed_hash):
            self.is_used = True
            self.used_at = timezone.now()
            self.save(update_fields=['is_used', 'used_at'])
            return True, "OTP verified successfully."
        else:
            self.attempts_count += 1
            self.save(update_fields=['attempts_count'])
            remaining = self.max_attempts - self.attempts_count
            if remaining <= 0:
                return False, "Incorrect OTP. Maximum attempts exceeded. Please request a new OTP."
            return False, f"Incorrect OTP. {remaining} attempt(s) remaining."

    def __str__(self):
        return f"OTP for {self.order.order_id} ({'Used' if self.is_used else 'Active'})"


class DeliveryAttempt(models.Model):
    class FailureReason(models.TextChoices):
        CUSTOMER_UNAVAILABLE = 'CUSTOMER_UNAVAILABLE', 'Customer not reachable / unavailable'
        INCORRECT_ADDRESS = 'INCORRECT_ADDRESS', 'Incorrect or incomplete address'
        CUSTOMER_REFUSED = 'CUSTOMER_REFUSED', 'Customer refused to accept'
        DAMAGED_PACKAGE = 'DAMAGED_PACKAGE', 'Package damaged in transit'
        RESCHEDULED = 'RESCHEDULED', 'Customer requested reschedule'
        OTHER = 'OTHER', 'Other reason'

    assignment = models.ForeignKey(DeliveryAssignment, on_delete=models.CASCADE, related_name='attempts')
    status = models.CharField(max_length=30)
    failure_reason = models.CharField(max_length=50, choices=FailureReason.choices, blank=True, null=True)
    notes = models.TextField(blank=True, default='')
    proof_of_delivery = models.CharField(max_length=255, blank=True, default='', help_text='Signature note or recipient name')
    cod_collected = models.BooleanField(default=False)
    cod_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Attempt on {self.assignment.order.order_id}: {self.status}"
