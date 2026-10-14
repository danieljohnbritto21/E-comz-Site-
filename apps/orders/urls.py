from django.urls import path
from apps.orders import views

app_name = 'orders'

urlpatterns = [
    path('checkout/', views.checkout_view, name='checkout'),
    path('payment/<str:order_id>/', views.payment_gateway_view, name='payment_gateway'),
    path('payment/<str:order_id>/verify/', views.payment_verify_view, name='payment_verify'),
    path('confirmation/<str:order_id>/', views.order_confirmation_view, name='confirmation'),
    path('track/<str:order_id>/', views.order_track_view, name='track'),
    path('history/', views.order_history_view, name='order_history'),
    path('cancel/<str:order_id>/', views.cancel_order_view, name='cancel_order'),
]
