from django.contrib import admin
from apps.inventory.models import StockRecord, StockMovement


@admin.register(StockRecord)
class StockRecordAdmin(admin.ModelAdmin):
    list_display = ('product', 'current_stock', 'low_stock_threshold', 'last_restocked_at')
    search_fields = ('product__name', 'product__sku')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'change_type', 'quantity', 'stock_before', 'stock_after', 'created_by', 'created_at')
    list_filter = ('change_type',)
    search_fields = ('product__name',)
