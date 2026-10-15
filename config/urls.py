from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    # Technical Django Admin
    path('admin/', admin.site.urls),

    # Platform Administrator Portal
    path('admin-dashboard/', include('apps.accounts.admin_urls', namespace='admin_dashboard')),

    # Authentication & User Accounts
    path('auth/', include('apps.accounts.urls', namespace='accounts')),
    path('accounts/profile/', RedirectView.as_view(pattern_name='accounts:profile', permanent=False)),
    path('accounts/', RedirectView.as_view(pattern_name='accounts:profile', permanent=False)),
    path('account/', RedirectView.as_view(pattern_name='accounts:profile', permanent=False)),

    # Seller Dashboard & Product Management
    path('seller/', include('apps.catalog.seller_urls', namespace='seller')),

    # Delivery Partner Dashboard & Fleet Execution
    path('delivery/', include('apps.delivery.urls', namespace='delivery')),

    # Shopping Basket & Cart Actions
    path('cart/', include('apps.cart.urls', namespace='cart')),

    # Buyer Checkout, Orders & Real-time Tracking
    path('orders/', include('apps.orders.urls', namespace='orders')),

    # Notifications System
    path('notifications/', include('apps.notifications.urls', namespace='notifications')),

    # Public Storefront & Grocery Catalog
    path('', include('apps.catalog.urls', namespace='catalog')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
