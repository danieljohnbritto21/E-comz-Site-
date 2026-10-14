from django.urls import path
from apps.accounts import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('redirect/', views.role_redirect_view, name='role_redirect'),
    path('register/buyer/', views.register_buyer_view, name='register_buyer'),
    path('register/seller/', views.register_seller_view, name='register_seller'),
]
