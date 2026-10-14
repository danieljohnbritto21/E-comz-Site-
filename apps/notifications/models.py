from django.db import models
from django.conf import settings


class Notification(models.Model):
    class Category(models.TextChoices):
        ORDER = 'ORDER', 'Order Update'
        INVENTORY = 'INVENTORY', 'Inventory Alert'
        DELIVERY = 'DELIVERY', 'Delivery Update'
        ACCOUNT = 'ACCOUNT', 'Account & Store'
        SYSTEM = 'SYSTEM', 'System'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.ORDER)
    link = models.CharField(max_length=255, blank=True, default='')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def send(cls, user, title, message, category=Category.ORDER, link=''):
        return cls.objects.create(
            user=user,
            title=title,
            message=message,
            category=category,
            link=link
        )

    def __str__(self):
        return f"[{self.category}] {self.title} for {self.user.username}"
