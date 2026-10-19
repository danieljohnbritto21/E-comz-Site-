from django.db import models
from django.utils.text import slugify
from django.conf import settings
from decimal import Decimal


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True, default='')
    icon = models.CharField(max_length=50, default='fa-leaf', help_text='FontAwesome icon class, e.g. fa-carrot')
    image = models.ImageField(upload_to='categories/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['sort_order', 'name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='subcategories')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'Subcategories'
        unique_together = ('category', 'slug')
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.category.name} > {self.name}"


class Product(models.Model):
    UNIT_CHOICES = (
        ('kg', 'Kilogram (kg)'),
        ('g', 'Gram (g)'),
        ('litre', 'Litre (L)'),
        ('litres', 'Litres (L)'),
        ('l', 'Litre (L)'),
        ('ml', 'Millilitre (ml)'),
        ('piece', 'Piece(s)'),
        ('pieces', 'Piece(s)'),
        ('pcs', 'Piece(s)'),
        ('packet', 'Packet(s)'),
        ('packets', 'Packet(s)'),
        ('pack', 'Packet(s)'),
        ('bunch', 'Bunch'),
        ('dozen', 'Dozen'),
        ('bottle', 'Bottle(s)'),
        ('bottles', 'Bottle(s)'),
        ('box', 'Box(es)'),
        ('boxes', 'Box(es)'),
    )

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='products',
        limit_choices_to={'role': 'SELLER'}
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    subcategory = models.ForeignKey(SubCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    brand = models.CharField(max_length=100, default='FreshFarm')
    description = models.TextField(blank=True, default='')
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default='kg')
    unit_value = models.CharField(max_length=50, default='1 kg', help_text='Display unit, e.g. 500g, 1 kg, 1 bunch')
    min_purchase_quantity = models.PositiveIntegerField(default=1, help_text='Minimum quantity that can be purchased')
    quantity_step = models.PositiveIntegerField(default=1, help_text='Supported selling increment (e.g. 1 packet, 1 kg)')
    max_quantity_per_order = models.PositiveIntegerField(null=True, blank=True, help_text='Maximum quantity a customer can order in a single order (bike delivery limit). If null, unit-based default applies.')
    
    mrp = models.DecimalField(max_digits=10, decimal_places=2, help_text='Maximum Retail Price')
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, help_text='Actual discount/selling price')
    sku = models.CharField(max_length=50, unique=True, help_text='Unique Stock Keeping Unit')
    
    origin = models.CharField(max_length=100, default='Local Organic Farm', blank=True)
    freshness_info = models.CharField(max_length=255, default='Farm Fresh, harvested within 24h', blank=True)
    storage_instructions = models.CharField(max_length=255, default='Store in a cool, dry place or refrigerate', blank=True)
    nutritional_info = models.TextField(blank=True, default='')
    return_policy = models.CharField(max_length=255, default='Same-day replacement for quality issues', blank=True)
    
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    is_best_seller = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(f"{self.name}-{self.seller.username}")
            candidate = base_slug
            idx = 1
            while Product.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{idx}"
                idx += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def discount_percentage(self):
        if self.mrp and self.mrp > 0 and self.selling_price < self.mrp:
            discount = ((self.mrp - self.selling_price) / self.mrp) * Decimal(100)
            return int(round(discount))
        return 0

    @property
    def savings_amount(self):
        if self.mrp and self.selling_price and self.mrp > self.selling_price:
            return self.mrp - self.selling_price
        return Decimal('0.00')

    @property
    def primary_image_url(self):
        primary = self.images.filter(is_primary=True).first()
        if not primary:
            primary = self.images.first()
        if primary and primary.image:
            return primary.image.url
        return '/static/images/placeholder_product.svg'

    @property
    def selling_unit(self):
        """Standard property alias for selling unit."""
        return self.unit

    @selling_unit.setter
    def selling_unit(self, value):
        self.unit = value

    @property
    def stock_quantity(self):
        """Standard property alias for available physical stock quantity."""
        return self.current_stock_count

    @property
    def current_stock_count(self):
        if hasattr(self, 'stock_record'):
            return self.stock_record.current_stock
        return 0

    @property
    def is_in_stock(self):
        return self.current_stock_count > 0

    @property
    def is_low_stock(self):
        if hasattr(self, 'stock_record'):
            return 0 < self.stock_record.current_stock <= self.stock_record.low_stock_threshold
        return False

    DEFAULT_UNIT_LIMITS = {
        'kg': 5,
        'kilogram': 5,
        'kilograms': 5,
        'kgs': 5,
        'g': 5,
        'gram': 5,
        'grams': 5,
        'litre': 5,
        'litres': 5,
        'liter': 5,
        'liters': 5,
        'l': 5,
        'ltr': 5,
        'ltrs': 5,
        'ml': 5,
        'bunch': 5,
        'bunches': 5,
        'packet': 5,
        'packets': 5,
        'pack': 5,
        'packs': 5,
        'piece': 5,
        'pieces': 5,
        'pc': 5,
        'pcs': 5,
        'bottle': 5,
        'bottles': 5,
        'box': 5,
        'boxes': 5,
        'dozen': 5,
        'dozens': 5,
    }
    SAFE_DEFAULT_ORDER_LIMIT = 5

    def get_unit_label(self, quantity=1):
        u = (self.unit or 'pcs').lower().strip()
        mapping = {
            'kg': ('kg', 'kg'),
            'kilogram': ('kg', 'kg'),
            'kilograms': ('kg', 'kg'),
            'kgs': ('kg', 'kg'),
            'g': ('g', 'g'),
            'gram': ('g', 'g'),
            'grams': ('g', 'g'),
            'l': ('litre', 'litres'),
            'litre': ('litre', 'litres'),
            'litres': ('litre', 'litres'),
            'liter': ('litre', 'litres'),
            'liters': ('litre', 'litres'),
            'ltr': ('litre', 'litres'),
            'ltrs': ('litre', 'litres'),
            'ml': ('ml', 'ml'),
            'pcs': ('piece', 'pieces'),
            'piece': ('piece', 'pieces'),
            'pieces': ('piece', 'pieces'),
            'pc': ('piece', 'pieces'),
            'pack': ('packet', 'packets'),
            'packet': ('packet', 'packets'),
            'packets': ('packet', 'packets'),
            'packs': ('packet', 'packets'),
            'bunch': ('bunch', 'bunches'),
            'bunches': ('bunch', 'bunches'),
            'bottle': ('bottle', 'bottles'),
            'bottles': ('bottle', 'bottles'),
            'box': ('box', 'boxes'),
            'boxes': ('box', 'boxes'),
            'dozen': ('dozen', 'dozens'),
            'dozens': ('dozen', 'dozens'),
        }
        singular, plural = mapping.get(u, (u, f"{u}s"))
        return singular if quantity == 1 else plural

    @classmethod
    def get_default_limit_for_unit(cls, unit):
        """
        Default per-order limit for bike delivery based on selling unit:
        kg / kilogram / kilograms / kgs: 5 kg
        litre / litres / liter / liters / l / ltr / ltrs: 5 litres
        bunch / bunches: 5 bunches
        packet / packets / pack / packs: 5 packets
        piece / pieces / pc / pcs: 5 pieces
        bottle / bottles: 5 bottles
        box / boxes: 5 boxes
        Unrecognised / other units: Safe default of 5
        """
        u = (unit or '').lower().strip()
        return cls.DEFAULT_UNIT_LIMITS.get(u, cls.SAFE_DEFAULT_ORDER_LIMIT)

    @property
    def per_order_limit(self):
        """Configured maximum quantity allowed per order. Falls back to unit-based default."""
        if self.max_quantity_per_order is not None and self.max_quantity_per_order > 0:
            return self.max_quantity_per_order
        return self.get_default_limit_for_unit(self.unit)

    @property
    def actual_max_order_quantity(self):
        """
        Actual maximum order quantity = the smaller of available stock and configured per-order limit.
        If stock is zero, returns 0.
        """
        stock = self.current_stock_count
        if stock <= 0:
            return 0
        limit = self.per_order_limit
        return min(stock, limit)

    @property
    def is_order_limit_restricted(self):
        """Returns True if the per-order limit is actively restricting the purchase below available stock."""
        return self.current_stock_count > self.per_order_limit

    @property
    def order_limit_display(self):
        limit = self.per_order_limit
        return f"{limit} {self.get_unit_label(limit)} per order"

    @property
    def stock_unit_display(self):
        count = self.current_stock_count
        return f"{count} {self.get_unit_label(count)}"

    def __str__(self):
        return f"{self.name} - Rs.{self.selling_price} ({self.seller.username})"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/%Y/%m/')
    alt_text = models.CharField(max_length=200, blank=True, default='')
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'id']

    def __str__(self):
        return f"Image for {self.product.name}"


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)], default=5)
    comment = models.TextField()
    is_verified_purchase = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.product.name} ({self.rating}★)"
