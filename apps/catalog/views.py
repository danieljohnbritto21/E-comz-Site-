from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.db.models import Q
from django.contrib import messages
from apps.catalog.models import Category, SubCategory, Product, ProductImage, Review
from apps.cart.models import Wishlist


def home_view(request):
    """Public grocery home page featuring categories, banners, and curated product collections."""
    categories = Category.objects.filter(is_active=True).order_by('sort_order', 'name')
    
    # Active products with valid stock
    base_qs = Product.objects.filter(is_active=True, is_approved=True).select_related('category', 'seller', 'stock_record').prefetch_related('images')

    featured_products = base_qs.order_by('-created_at')[:8]
    best_sellers = base_qs.filter(is_best_seller=True)[:8]
    if not best_sellers.exists():
        best_sellers = base_qs.order_by('-created_at')[:8]
    fresh_arrivals = base_qs.order_by('-created_at')[:8]

    # Wishlist item ids for authenticated user
    user_wishlist_ids = []
    if request.user.is_authenticated:
        user_wishlist_ids = list(Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True))

    return render(request, 'public/home.html', {
        'categories': categories,
        'featured_products': featured_products,
        'best_sellers': best_sellers,
        'fresh_arrivals': fresh_arrivals,
        'user_wishlist_ids': user_wishlist_ids,
    })


def product_list_view(request, category_slug=None):
    """Product catalog with searching, category filtering, price filters, and sorting."""
    products = Product.objects.filter(is_active=True, is_approved=True).select_related('category', 'seller', 'stock_record').prefetch_related('images')
    categories = Category.objects.filter(is_active=True)
    selected_category = None

    # Filter by category
    cat_param = request.GET.get('category') or category_slug
    if cat_param:
        selected_category = get_object_or_404(Category, slug=cat_param)
        products = products.filter(category=selected_category)

    # Search keyword
    query = request.GET.get('q', '').strip()
    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query)
        )

    # Filter by stock availability
    stock_filter = request.GET.get('stock')
    if stock_filter == 'in_stock':
        products = products.filter(stock_record__current_stock__gt=0)

    # Sorting
    sort = request.GET.get('sort', 'newest')
    if sort == 'price_low':
        products = products.order_by('selling_price')
    elif sort == 'price_high':
        products = products.order_by('-selling_price')
    elif sort == 'popular':
        products = products.order_by('-is_best_seller', '-created_at')
    else:
        products = products.order_by('-created_at')

    # Pagination
    paginator = Paginator(products, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    user_wishlist_ids = []
    if request.user.is_authenticated:
        user_wishlist_ids = list(Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True))

    return render(request, 'public/product_list.html', {
        'products': page_obj,
        'categories': categories,
        'selected_category': selected_category,
        'query': query,
        'sort': sort,
        'stock_filter': stock_filter,
        'user_wishlist_ids': user_wishlist_ids,
    })


def product_detail_view(request, slug):
    """Comprehensive product detail page with image gallery, nutrition, reviews, and similar items."""
    product = get_object_or_404(
        Product.objects.select_related('category', 'seller', 'seller__seller_profile', 'stock_record').prefetch_related('images', 'reviews', 'reviews__user'),
        slug=slug
    )

    similar_products = Product.objects.filter(
        category=product.category,
        is_active=True
    ).exclude(id=product.id)[:4]

    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()

    # Handling review submission
    if request.method == 'POST' and request.user.is_authenticated:
        rating = int(request.POST.get('rating', 5))
        comment = request.POST.get('comment', '').strip()
        if comment:
            Review.objects.create(
                product=product,
                user=request.user,
                rating=rating,
                comment=comment,
                is_verified_purchase=True
            )
            messages.success(request, "Thank you! Your product review has been published.")
            return redirect('catalog:product_detail', slug=product.slug)

    return render(request, 'public/product_detail.html', {
        'product': product,
        'similar_products': similar_products,
        'in_wishlist': in_wishlist,
    })


def search_suggestions_api(request):
    """API endpoint providing instant search suggestions for the navbar."""
    query = request.GET.get('q', '').strip()
    results = []
    if len(query) >= 2:
        matches = Product.objects.filter(
            Q(name__icontains=query) | Q(brand__icontains=query),
            is_active=True
        ).select_related('stock_record')[:6]

        for p in matches:
            results.append({
                'name': p.name,
                'slug': p.slug,
                'selling_price': str(p.selling_price),
                'image': p.primary_image_url,
                'unit': p.unit_value,
            })
    return JsonResponse({'results': results})
