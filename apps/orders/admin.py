from django.contrib import admin
from apps.orders.models import Order, OrderItem, SellerSubOrder, OrderStatusHistory, Address


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class SellerSubOrderInline(admin.TabularInline):
    model = SellerSubOrder
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_id', 'buyer', 'recipient_name', 'total_amount', 'status', 'payment_method', 'payment_status', 'created_at')
    list_filter = ('status', 'payment_method', 'payment_status')
    search_fields = ('order_id', 'recipient_name', 'recipient_phone')
    inlines = [OrderItemInline, SellerSubOrderInline]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'phone_number', 'city', 'pin_code', 'is_default')
