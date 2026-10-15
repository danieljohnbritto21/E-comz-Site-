import json
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from apps.cart.models import Cart, CartItem, Wishlist
from apps.catalog.models import Product


def get_or_create_cart(request):
    """Retrieves or creates cart for either authenticated user or anonymous session."""
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user)
    else:
        if not request.session.session_key:
            request.session.create()
        cart, _ = Cart.objects.get_or_create(session_key=request.session.session_key)
    return cart


def merge_session_cart(user, session_key):
    """Transfers all items from an anonymous session cart into the authenticated user's cart."""
    if not user or not session_key:
        return None

    session_cart = Cart.objects.filter(session_key=session_key).first()
    if not session_cart:
        return None

    user_cart, _ = Cart.objects.get_or_create(user=user)

    for item in session_cart.items.select_related('product'):
        user_item, created = CartItem.objects.get_or_create(
            cart=user_cart,
            product=item.product,
            defaults={'quantity': item.quantity}
        )
        if not created:
            user_item.quantity += item.quantity
            user_item.save(update_fields=['quantity', 'updated_at'])

    session_cart.delete()
    return user_cart



@require_POST
def add_to_cart_api(request, product_id=None):
    """Endpoint to add or adjust product quantity in cart with atomic stock validation (supports delta & quantity)."""
    try:
        delta = None
        quantity = None

        if request.content_type == 'application/json':
            data = json.loads(request.body.decode('utf-8') or '{}')
            product_id = product_id or data.get('product_id')
            if 'delta' in data:
                delta = int(data.get('delta', 1))
            else:
                quantity = int(data.get('quantity', 1))
        else:
            product_id = product_id or request.POST.get('product_id')
            if 'delta' in request.POST:
                delta = int(request.POST.get('delta', 1))
            else:
                quantity = int(request.POST.get('quantity', 1))

        if not product_id:
            return JsonResponse({'success': False, 'error': 'Invalid product ID'}, status=400)

        product = get_object_or_404(Product, id=product_id, is_active=True)

        # Authentication Check: Guests must log in or sign up before adding to cart
        if not request.user.is_authenticated:
            requested_qty = quantity or delta or 1
            request.session['pending_product_id'] = product.id
            request.session['pending_qty'] = requested_qty
            login_url = f"{reverse('accounts:login')}?role=BUYER&next={request.META.get('HTTP_REFERER', '/')}&pending_product_id={product.id}&pending_qty={requested_qty}"
            
            if request.content_type == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'require_login': True,
                    'login_url': login_url,
                    'error': 'Please log in or create a customer account to add products to your cart and continue shopping.'
                }, status=401)
            else:
                from django.contrib import messages
                messages.info(request, "Please log in or create a customer account to add products to your cart and continue shopping.")
                return redirect(login_url)

        # Role Check: Only BUYER accounts can use customer cart
        if request.user.role != 'BUYER':
            if request.content_type == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'error': 'Only customer accounts can add products to cart and place orders.'
                }, status=403)
            else:
                from django.contrib import messages
                messages.error(request, "Only customer accounts can add products to cart and place orders.")
                return redirect('catalog:home')

        cart = get_or_create_cart(request)
        cart_item = CartItem.objects.filter(cart=cart, product=product).first()

        if delta is not None:
            current_qty = cart_item.quantity if cart_item else 0
            new_quantity = current_qty + delta
        else:
            new_quantity = quantity if not cart_item else (cart_item.quantity + quantity)

        # If quantity reduced to zero or negative, remove item
        if new_quantity <= 0:
            if cart_item:
                cart_item.delete()
            return JsonResponse({
                'success': True,
                'message': f"Removed {product.name} from basket.",
                'product_id': product.id,
                'product_name': product.name,
                'item_quantity': 0,
                'total_items': cart.total_items,
                'subtotal': str(cart.subtotal),
                'grand_total': str(cart.grand_total),
            })

        # Validate stock
        if product.current_stock_count <= 0:
            if not request.content_type == 'application/json' and request.headers.get('x-requested-with') != 'XMLHttpRequest':
                from django.contrib import messages
                messages.error(request, f"{product.name} is currently out of stock.")
                return redirect('cart:view_cart')
            return JsonResponse({
                'success': False,
                'error': f"{product.name} is currently out of stock."
            }, status=400)

        clamped = False
        if new_quantity > product.current_stock_count:
            new_quantity = product.current_stock_count
            clamped = True

        if not cart_item:
            cart_item = CartItem.objects.create(cart=cart, product=product, quantity=new_quantity)
        else:
            cart_item.quantity = new_quantity
            cart_item.save()

        if request.content_type == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f"Added {product.name} to basket!",
                'product_id': product.id,
                'product_name': product.name,
                'item_quantity': cart_item.quantity,
                'total_items': cart.total_items,
                'subtotal': str(cart.subtotal),
                'grand_total': str(cart.grand_total),
            })
        return redirect('cart:view_cart')
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def update_cart_api(request, item_id):
    """AJAX endpoint to update item quantity in cart."""
    try:
        data = json.loads(request.body)
        quantity = int(data.get('quantity', 1))
        cart = get_or_create_cart(request)
        item = get_object_or_404(CartItem, id=item_id, cart=cart)

        if quantity <= 0:
            item.delete()
            return JsonResponse({'success': True, 'message': 'Item removed', 'total_items': cart.total_items})

        # Validate stock
        if item.product.current_stock_count < quantity:
            return JsonResponse({
                'success': False,
                'error': f"Only {item.product.current_stock_count} units available in stock."
            }, status=400)

        item.quantity = quantity
        item.save()

        return JsonResponse({
            'success': True,
            'total_items': cart.total_items,
            'subtotal': str(cart.subtotal)
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def remove_cart_api(request, item_id):
    """AJAX endpoint to remove item from cart."""
    cart = get_or_create_cart(request)
    item = get_object_or_404(CartItem, id=item_id, cart=cart)
    item.delete()
    return JsonResponse({
        'success': True,
        'total_items': cart.total_items,
        'subtotal': str(cart.subtotal)
    })


def view_cart(request):
    """Full shopping cart page with order breakdown and checkout gateway."""
    if request.user.is_authenticated and request.user.role != 'BUYER':
        from django.contrib import messages
        messages.warning(request, "Only customer accounts can access the shopping cart.")
        return redirect('catalog:home')

    cart = get_or_create_cart(request)
    cart_items = cart.items.select_related('product', 'product__stock_record', 'product__seller')
    
    # Check if any item exceeds available stock
    has_stock_error = False
    for item in cart_items:
        if not item.is_available:
            has_stock_error = True

    return render(request, 'buyer/cart.html', {
        'cart': cart,
        'cart_items': cart_items,
        'has_stock_error': has_stock_error,
    })


@require_POST
def toggle_wishlist_api(request, product_id):
    """Toggle product in user wishlist."""
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'error': 'Please sign in to save items to wishlist'}, status=401)

    product = get_object_or_404(Product, id=product_id)
    wishlist_item = Wishlist.objects.filter(user=request.user, product=product).first()
    
    if wishlist_item:
        wishlist_item.delete()
        return JsonResponse({'success': True, 'in_wishlist': False, 'message': f"Removed {product.name} from wishlist"})
    else:
        Wishlist.objects.create(user=request.user, product=product)
        return JsonResponse({'success': True, 'in_wishlist': True, 'message': f"Added {product.name} to wishlist"})


@login_required
def view_wishlist(request):
    """Buyer wishlist page."""
    items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__stock_record')
    return render(request, 'buyer/wishlist.html', {'wishlist_items': items})
