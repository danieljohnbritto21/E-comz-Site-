from django.urls import path
from apps.accounts import admin_views

app_name = 'admin_dashboard'

urlpatterns = [
    path('', admin_views.admin_dashboard_view, name='dashboard'),
    path('orders/', admin_views.admin_orders_view, name='orders'),
    path('orders/assign/<str:order_id>/', admin_views.admin_assign_delivery, name='assign_delivery'),
    path('buyers/', admin_views.admin_buyers_view, name='buyers'),
    path('sellers/', admin_views.admin_sellers_view, name='sellers'),
    path('delivery/', admin_views.admin_delivery_view, name='delivery_team'),
    path('products/', admin_views.admin_products_view, name='products'),
    path('categories/', admin_views.admin_categories_view, name='categories'),
    path('reports/', admin_views.admin_reports_view, name='reports'),
    path('reports/export-csv/', admin_views.export_sales_csv, name='export_csv'),
]
