from django.urls import path
from apps.delivery import views

app_name = 'delivery'

urlpatterns = [
    path('dashboard/', views.delivery_dashboard, name='dashboard'),
    path('jobs/<int:assignment_id>/', views.delivery_order_detail, name='order_detail'),
    path('history/', views.delivery_history, name='history'),
    path('earnings/', views.delivery_earnings_view, name='earnings'),
]
