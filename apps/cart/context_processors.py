from apps.cart.views import get_or_create_cart


def cart_context(request):
    """Provides authoritative cart total items, subtotal, and item map to all templates."""
    total_items = 0
    subtotal = 0
    cart_items_map = {}
    cart = None

    try:
        cart = get_or_create_cart(request, create=False)
        if cart:
            total_items = cart.total_items
            subtotal = cart.subtotal
            cart_items_map = {item.product_id: item.quantity for item in cart.items.all()}
    except Exception:
        pass

    return {
        'nav_cart': cart,
        'nav_cart_count': total_items,
        'nav_cart_subtotal': subtotal,
        'cart_items_map': cart_items_map,
    }
