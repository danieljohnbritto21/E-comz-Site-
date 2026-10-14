from django.db import models
from django.conf import settings
from decimal import Decimal
from apps.catalog.models import Product


class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='cart')
    session_key = models.CharField(max_length=40, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def subtotal(self):
        return sum(item.line_total for item in self.items.all())

    @property
    def total_mrp(self):
        return sum(item.product.mrp * item.quantity for item in self.items.all())

    @property
    def total_discount(self):
        return self.total_mrp - self.subtotal

    @property
    def delivery_fee(self):
        # Free delivery if subtotal > ₹499, otherwise ₹40
        if self.subtotal == 0 or self.subtotal >= 499:
            return Decimal('0.00')
        return Decimal('40.00')

    @property
    def grand_total(self):
        return self.subtotal + self.delivery_fee

    def __str__(self):
        if self.user:
            return f"Cart of {self.user.username}"
        return f"Guest Cart ({self.session_key})"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cart', 'product')

    @property
    def line_total(self):
        return self.product.selling_price * self.quantity

    @property
    def line_mrp_total(self):
        return self.product.mrp * self.quantity

    @property
    def is_available(self):
        return self.product.is_active and self.product.current_stock_count >= self.quantity

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlist')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='in_wishlists')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')

    def __str__(self):
        return f"{self.user.username} - {self.product.name}"
