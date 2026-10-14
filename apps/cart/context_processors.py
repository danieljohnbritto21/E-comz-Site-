from apps.cart.models import Cart


def cart_context(request):
    """Provides cart total items and subtotal to all templates."""
    total_items = 0
    subtotal = 0
    cart = None
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    else:
        session_key = request.session.session_key
        if session_key:
            cart = Cart.objects.filter(session_key=session_key).first()

    cart_items_map = {}
    if cart:
        total_items = cart.total_items
        subtotal = cart.subtotal
        cart_items_map = {item.product_id: item.quantity for item in cart.items.all()}

    return {
        'nav_cart': cart,
        'nav_cart_count': total_items,
        'nav_cart_subtotal': subtotal,
        'cart_items_map': cart_items_map,
    }
