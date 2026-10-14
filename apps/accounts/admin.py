from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'account_status', 'is_staff')
    list_filter = ('role', 'account_status', 'is_staff', 'is_active')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Marketplace Role & Moderation', {
            'fields': ('role', 'phone_number', 'account_status', 'avatar')
        }),
    )


@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = ('store_name', 'user', 'city', 'pin_code', 'is_approved', 'created_at')
    list_filter = ('is_approved',)
    search_fields = ('store_name', 'user__username', 'user__email')


@admin.register(DeliveryProfile)
class DeliveryProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'vehicle_type', 'vehicle_number', 'current_status', 'total_deliveries_completed')
    list_filter = ('vehicle_type', 'current_status')


@admin.register(BuyerProfile)
class BuyerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'loyalty_points', 'created_at')
