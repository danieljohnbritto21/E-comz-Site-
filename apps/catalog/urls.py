from django.urls import path
from apps.catalog import views

app_name = 'catalog'

urlpatterns = [
    path('', views.home_view, name='home'),
    path('buyer/', views.home_view, name='buyer_home'),
    path('shop/', views.product_list_view, name='product_list'),
    path('buyer/shop/', views.product_list_view, name='buyer_shop'),
    path('category/<slug:category_slug>/', views.product_list_view, name='category_products'),
    path('product/<slug:slug>/', views.product_detail_view, name='product_detail'),
    path('api/search-suggest/', views.search_suggestions_api, name='search_suggest'),
]
