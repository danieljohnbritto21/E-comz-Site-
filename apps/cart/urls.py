from django.urls import path
from apps.cart import views

app_name = 'cart'

urlpatterns = [
    path('', views.view_cart, name='view_cart'),
    path('add/', views.add_to_cart_api, name='add_to_cart'),
    path('add/<int:product_id>/', views.add_to_cart_api, name='add_to_cart'),
    path('update/<int:item_id>/', views.update_cart_api, name='update_cart'),
    path('remove/<int:item_id>/', views.remove_cart_api, name='remove_cart'),
    path('wishlist/', views.view_wishlist, name='view_wishlist'),
    path('wishlist/toggle/<int:product_id>/', views.toggle_wishlist_api, name='toggle_wishlist'),
]
