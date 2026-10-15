from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', _('Admin')
        BUYER = 'BUYER', _('Buyer / Customer')
        SELLER = 'SELLER', _('Seller')
        DELIVERY = 'DELIVERY', _('Delivery Person')

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        PENDING_APPROVAL = 'PENDING', _('Pending Approval')
        SUSPENDED = 'SUSPENDED', _('Suspended')
        REJECTED = 'REJECTED', _('Rejected')

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.BUYER,
        help_text=_('User account role on the marketplace.')
    )
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    account_status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text=_('Account moderation status.')
    )
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    def is_seller_role(self):
        return self.role == self.Role.SELLER

    def is_buyer_role(self):
        return self.role == self.Role.BUYER

    def is_delivery_role(self):
        return self.role == self.Role.DELIVERY

    @property
    def account_id(self):
        prefix_map = {
            self.Role.ADMIN: 'ADM',
            self.Role.SELLER: 'SEL',
            self.Role.DELIVERY: 'DEL',
            self.Role.BUYER: 'CUST',
        }
        prefix = prefix_map.get(self.role, 'USR')
        return f"{prefix}-{self.id:04d}"

    def __str__(self):
        return f"{self.username} [{self.account_id}] ({self.get_role_display()})"


class BuyerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='buyer_profile')
    loyalty_points = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def customer_account_id(self):
        return f"CUST-{self.user.id:04d}"

    def __str__(self):
        return f"Buyer: {self.user.username} [{self.customer_account_id}]"


class SellerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='seller_profile')
    store_name = models.CharField(max_length=150, unique=True)
    store_slug = models.SlugField(max_length=160, unique=True)
    store_description = models.TextField(blank=True, default='')
    store_logo = models.ImageField(upload_to='store_logos/', blank=True, null=True)
    store_banner = models.ImageField(upload_to='store_banners/', blank=True, null=True)
    store_phone = models.CharField(max_length=20, blank=True, default='')
    store_email = models.EmailField(blank=True, default='')
    pickup_address = models.TextField(help_text=_('Address from where delivery person picks up orders.'))
    city = models.CharField(max_length=100, default='Central City')
    pin_code = models.CharField(max_length=10, default='110001')
    serviceable_pincodes = models.CharField(
        max_length=255,
        default='110001,110002,110003,110004,110005',
        help_text=_('Comma separated pin codes where products can be delivered.')
    )
    preparation_time_minutes = models.PositiveIntegerField(default=30)
    is_approved = models.BooleanField(default=False)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=5.00, help_text=_('Platform commission percentage.'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def seller_account_id(self):
        return f"SEL-{self.user.id:04d}"

    def save(self, *args, **kwargs):
        if not self.store_slug:
            from django.utils.text import slugify
            self.store_slug = slugify(self.store_name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.store_name} [{self.seller_account_id}] ({self.user.username})"


class DeliveryProfile(models.Model):
    class VehicleType(models.TextChoices):
        BIKE = 'BIKE', _('Motorcycle / Bike')
        SCOOTER = 'SCOOTER', _('Scooter')
        BICYCLE = 'BICYCLE', _('Bicycle')
        VAN = 'VAN', _('Mini Van / Truck')

    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', _('Available for Orders')
        ON_DELIVERY = 'ON_DELIVERY', _('On Delivery')
        OFFLINE = 'OFFLINE', _('Offline')

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='delivery_profile')
    vehicle_type = models.CharField(max_length=20, choices=VehicleType.choices, default=VehicleType.BIKE)
    vehicle_number = models.CharField(max_length=30, blank=True, default='')
    license_number = models.CharField(max_length=50, blank=True, default='')
    current_status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    service_area = models.CharField(max_length=150, default='Downtown Sector')
    max_active_orders = models.PositiveIntegerField(default=5)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=5.00)
    total_deliveries_completed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def driver_account_id(self):
        return f"DEL-{self.user.id:04d}"

    def __str__(self):
        return f"Delivery: {self.user.get_full_name() or self.user.username} [{self.driver_account_id}] ({self.get_current_status_display()})"
