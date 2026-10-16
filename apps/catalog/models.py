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
        ('l', 'Litre (L)'),
        ('ml', 'Millilitre (ml)'),
        ('pcs', 'Piece(s)'),
        ('pack', 'Pack'),
        ('bunch', 'Bunch'),
        ('dozen', 'Dozen'),
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
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='kg')
    unit_value = models.CharField(max_length=50, default='1 kg', help_text='Display unit, e.g. 500g, 1 kg, 1 bunch')
    
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
