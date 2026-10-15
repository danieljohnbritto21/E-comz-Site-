from django.urls import path
from apps.accounts import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('redirect/', views.role_redirect_view, name='role_redirect'),
    path('profile/', views.profile_view, name='profile'),
    path('account-details/', views.profile_view, name='account_details'),
    path('api/details/', views.api_account_details_view, name='api_account_details'),
    path('register/buyer/', views.register_buyer_view, name='register_buyer'),
    path('register/seller/', views.register_seller_view, name='register_seller'),
]
