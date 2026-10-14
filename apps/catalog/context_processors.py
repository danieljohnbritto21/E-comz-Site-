from apps.catalog.models import Category


def catalog_context(request):
    """Provides active product categories to global headers and navigation."""
    categories = Category.objects.filter(is_active=True).order_by('sort_order', 'name')[:12]
    return {
        'nav_categories': categories,
    }
