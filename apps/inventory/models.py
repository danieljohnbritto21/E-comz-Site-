from django.db import models, transaction
from django.conf import settings
from django.utils import timezone
from apps.catalog.models import Product


from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError


class StockRecord(models.Model):
    class Status(models.TextChoices):
        IN_STOCK = 'IN_STOCK', 'In Stock'
        LOW_STOCK = 'LOW_STOCK', 'Low Stock'
        OUT_OF_STOCK = 'OUT_OF_STOCK', 'Out of Stock'
        NEAR_EXPIRY = 'NEAR_EXPIRY', 'Near Expiry'
        EXPIRED = 'EXPIRED', 'Expired'

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='stock_record')
    current_stock = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    low_stock_threshold = models.PositiveIntegerField(default=10)
    expiry_date = models.DateField(blank=True, null=True)
    last_restocked_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_stock__gte=0),
                name='stockrecord_current_stock_non_negative'
            )
        ]

    def clean(self):
        if self.current_stock is not None and self.current_stock < 0:
            raise ValidationError({'current_stock': 'Current stock quantity cannot be negative.'})
        super().clean()

    @property
    def status(self):
        if self.expiry_date and self.expiry_date < timezone.now().date():
            return self.Status.EXPIRED
        if self.current_stock <= 0:
            return self.Status.OUT_OF_STOCK
        if self.current_stock <= self.low_stock_threshold:
            return self.Status.LOW_STOCK
        return self.Status.IN_STOCK

    @property
    def badge_class(self):
        if self.current_stock <= 0:
            return 'bg-danger text-white'
        elif self.current_stock <= self.low_stock_threshold:
            return 'bg-warning text-dark'
        return 'bg-success text-white'

    def get_unit_label(self, quantity=None):
        if quantity is None:
            quantity = self.current_stock
        return self.product.get_unit_label(quantity)

    @property
    def stock_display(self):
        return f"{self.current_stock} {self.get_unit_label()}"

    def adjust_stock(self, quantity_change, change_type, user=None, notes=""):
        """
        Thread-safe / atomic adjustment of stock with movement logging.
        quantity_change > 0 for addition, < 0 for subtraction.
        """
        with transaction.atomic():
            # Lock the row for update
            record = StockRecord.objects.select_for_update().get(pk=self.pk)
            stock_before = record.current_stock
            new_stock = stock_before + quantity_change
            if new_stock < 0:
                unit_label = record.product.get_unit_label(stock_before)
                raise ValueError(
                    f"Insufficient stock for {record.product.name}. "
                    f"Available: {stock_before} {unit_label}, Requested deduction: {-quantity_change} {record.product.get_unit_label(-quantity_change)}"
                )
            
            record.current_stock = new_stock
            if quantity_change > 0:
                record.last_restocked_at = timezone.now()
            record.save()

            StockMovement.objects.create(
                product=record.product,
                change_type=change_type,
                quantity=quantity_change,
                stock_before=stock_before,
                stock_after=new_stock,
                notes=notes,
                created_by=user
            )
            self.current_stock = new_stock
            return record

    def __str__(self):
        return f"{self.product.name} - Stock: {self.current_stock}"


class StockMovement(models.Model):
    class ChangeType(models.TextChoices):
        RESTOCK = 'RESTOCK', 'Stock Received / Inward'
        SALE = 'SALE', 'Customer Order Deducted'
        CANCEL_RESTORE = 'CANCEL_RESTORE', 'Cancelled Order Restored'
        DAMAGE = 'DAMAGE', 'Damaged / Discarded'
        MANUAL = 'MANUAL', 'Manual Inventory Correction'

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_movements')
    change_type = models.CharField(max_length=20, choices=ChangeType.choices, default=ChangeType.RESTOCK)
    quantity = models.IntegerField(help_text='Positive for addition, negative for deduction')
    stock_before = models.IntegerField()
    stock_after = models.IntegerField()
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.name}: {self.quantity:+d} ({self.get_change_type_display()})"
