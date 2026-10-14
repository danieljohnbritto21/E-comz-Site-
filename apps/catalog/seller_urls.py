from django.urls import path
from apps.catalog import seller_views

app_name = 'seller'

urlpatterns = [
    path('dashboard/', seller_views.seller_dashboard, name='dashboard'),
    path('products/', seller_views.seller_product_list, name='product_list'),
    path('products/add/', seller_views.seller_product_add, name='product_add'),
    path('products/edit/<int:product_id>/', seller_views.seller_product_edit, name='product_edit'),
    path('products/toggle/<int:product_id>/', seller_views.seller_product_toggle_status, name='product_toggle'),
    path('products/delete/<int:product_id>/', seller_views.seller_product_delete, name='product_delete'),
    path('inventory/', seller_views.seller_inventory_view, name='inventory'),
    path('orders/', seller_views.seller_orders_view, name='orders'),
    path('orders/<str:order_id>/', seller_views.seller_order_detail_view, name='order_detail'),
    path('categories/', seller_views.seller_categories_view, name='categories'),
    path('offers/', seller_views.seller_offers_view, name='offers'),
    path('customers/', seller_views.seller_customers_view, name='customers'),
    path('reports/', seller_views.seller_reports_view, name='reports'),
    path('settings/', seller_views.seller_settings_view, name='settings'),
]
