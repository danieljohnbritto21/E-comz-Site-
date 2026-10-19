import json
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, Http404
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from apps.cart.models import Cart, CartItem, Wishlist
from apps.catalog.models import Product
from apps.catalog.currency import format_inr


def merge_session_cart(user, session_key=None, session_cart_id=None):
    """Transfers all items from an anonymous session cart into the authenticated user's cart without duplication."""
    if not user or not user.is_authenticated:
        return None

    user_cart, _ = Cart.objects.get_or_create(user=user)

    candidate_carts = []
    if session_cart_id:
        c = Cart.objects.filter(id=session_cart_id, user__isnull=True).first()
        if c and c.id != user_cart.id and c not in candidate_carts:
            candidate_carts.append(c)
    if session_key:
        for c in Cart.objects.filter(session_key=session_key, user__isnull=True):
            if c.id != user_cart.id and c not in candidate_carts:
                candidate_carts.append(c)

    for session_cart in candidate_carts:
        for item in session_cart.items.select_related('product'):
            user_item = CartItem.objects.filter(cart=user_cart, product=item.product).first()
            if not user_item:
                CartItem.objects.create(
                    cart=user_cart,
                    product=item.product,
                    quantity=item.quantity
                )
            else:
                user_item.quantity += item.quantity
                user_item.save(update_fields=['quantity', 'updated_at'])
        session_cart.delete()

    return user_cart


def get_or_create_cart(request, create=True):
    """
    Authoritative single-source cart retriever.
    Ensures the exact same cart is loaded on the shop page, floating counter,
    and View Basket page, seamlessly synchronizing guest & authenticated buyer sessions.
    When create=False, does not create an empty Cart row in DB on read-only requests.
    """
    session_key = request.session.session_key
    session_cart_id = request.session.get('cart_id')

    if request.user.is_authenticated and request.user.role == 'BUYER':
        if create:
            if not session_key:
                request.session.save()
                session_key = request.session.session_key
            user_cart, _ = Cart.objects.get_or_create(user=request.user)
            # Merge any unauthenticated items created during this session
            merge_session_cart(request.user, session_key=session_key, session_cart_id=session_cart_id)
            if session_key and user_cart.session_key != session_key:
                user_cart.session_key = session_key
                user_cart.save(update_fields=['session_key', 'updated_at'])
            request.session['cart_id'] = user_cart.id
            return user_cart
        else:
            user_cart = Cart.objects.filter(user=request.user).first()
            if user_cart:
                if session_key and user_cart.session_key != session_key:
                    user_cart.session_key = session_key
                    user_cart.save(update_fields=['session_key', 'updated_at'])
                request.session['cart_id'] = user_cart.id
            return user_cart

    elif request.user.is_authenticated:
        # Non-buyer role (SELLER, DELIVERY, ADMIN)
        if create:
            cart, _ = Cart.objects.get_or_create(user=request.user)
            return cart
        return Cart.objects.filter(user=request.user).first()

    else:
        # Guest customer session
        cart = None
        if session_cart_id:
            cart = Cart.objects.filter(id=session_cart_id, user__isnull=True).first()
        if not cart and session_key:
            cart = Cart.objects.filter(session_key=session_key, user__isnull=True).first()
        if not cart and create:
            if not session_key:
                request.session.save()
                session_key = request.session.session_key
            cart = Cart.objects.create(session_key=session_key)
            request.session['cart_id'] = cart.id
        elif cart:
            if session_key and cart.session_key != session_key:
                cart.session_key = session_key
                cart.save(update_fields=['session_key', 'updated_at'])
            request.session['cart_id'] = cart.id

        return cart



from apps.inventory.services import validate_purchase_quantity


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
                try:
                    delta_raw = data.get('delta', 1)
                    dec_delta = Decimal(str(delta_raw))
                    delta = int(dec_delta) if dec_delta % Decimal('1') == Decimal('0') else dec_delta
                except (ValueError, TypeError, ArithmeticError):
                    return JsonResponse({'success': False, 'error': 'Invalid delta quantity specified.'}, status=400)
            else:
                try:
                    qty_raw = data.get('quantity', 1)
                    dec_qty = Decimal(str(qty_raw))
                    quantity = int(dec_qty) if dec_qty % Decimal('1') == Decimal('0') else dec_qty
                except (ValueError, TypeError, ArithmeticError):
                    return JsonResponse({'success': False, 'error': 'Invalid purchase quantity specified.'}, status=400)
        else:
            product_id = product_id or request.POST.get('product_id')
            if 'delta' in request.POST:
                try:
                    delta_raw = request.POST.get('delta', 1)
                    dec_delta = Decimal(str(delta_raw))
                    delta = int(dec_delta) if dec_delta % Decimal('1') == Decimal('0') else dec_delta
                except (ValueError, TypeError, ArithmeticError):
                    return JsonResponse({'success': False, 'error': 'Invalid delta quantity specified.'}, status=400)
            else:
                try:
                    qty_raw = request.POST.get('quantity', 1)
                    dec_qty = Decimal(str(qty_raw))
                    quantity = int(dec_qty) if dec_qty % Decimal('1') == Decimal('0') else dec_qty
                except (ValueError, TypeError, ArithmeticError):
                    return JsonResponse({'success': False, 'error': 'Invalid purchase quantity specified.'}, status=400)

        if not product_id:
            return JsonResponse({'success': False, 'error': 'Invalid product ID'}, status=400)

        product = get_object_or_404(Product, id=product_id, is_active=True)

        # Check authentication: Unauthenticated guests are prompted to login or register
        if not request.user.is_authenticated:
            target_amount = quantity or delta or 1
            request.session['pending_product_id'] = product.id
            request.session['pending_qty'] = target_amount
            login_url = f"{reverse('accounts:login')}?role=BUYER&pending_product_id={product.id}&pending_qty={target_amount}"
            if request.content_type == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'require_login': True,
                    'login_url': login_url,
                    'error': 'Please log in or create a customer account to add items to your cart and place orders.'
                }, status=401)
            else:
                from django.contrib import messages
                messages.info(request, "Please log in or create a customer account to add items to your basket.")
                return redirect(login_url)

        # Role Check: Non-buyer authenticated accounts cannot use the shopping cart
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
        # Merge duplicate cart lines for the same product if any exist
        existing_items = list(CartItem.objects.filter(cart=cart, product=product))
        if len(existing_items) > 1:
            cart_item = existing_items[0]
            for extra in existing_items[1:]:
                cart_item.quantity += extra.quantity
                extra.delete()
            cart_item.save(update_fields=['quantity', 'updated_at'])
        elif len(existing_items) == 1:
            cart_item = existing_items[0]
        else:
            cart_item = None
        current_qty = cart_item.quantity if cart_item else 0

        # Handle negative delta (decrement or removal)
        if delta is not None and delta < 0:
            new_quantity = current_qty + delta
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
                    'subtotal': format_inr(cart.subtotal),
                    'grand_total': format_inr(cart.grand_total),
                    'per_order_limit': product.per_order_limit,
                    'actual_max_order_quantity': product.actual_max_order_quantity,
                })
            else:
                cart_item.quantity = new_quantity
                cart_item.save(update_fields=['quantity', 'updated_at'])
                return JsonResponse({
                    'success': True,
                    'message': f"Updated {product.name} quantity in basket.",
                    'product_id': product.id,
                    'product_name': product.name,
                    'item_quantity': cart_item.quantity,
                    'total_items': cart.total_items,
                    'subtotal': format_inr(cart.subtotal),
                    'grand_total': format_inr(cart.grand_total),
                    'per_order_limit': product.per_order_limit,
                    'actual_max_order_quantity': product.actual_max_order_quantity,
                })

        # Validate addition against inventory & bike delivery order limits
        add_amount = delta if delta is not None else quantity
        is_valid, error_msg, actual_max = validate_purchase_quantity(
            product, requested_quantity=add_amount, current_cart_quantity=current_qty
        )
        target_quantity = current_qty + add_amount

        if not is_valid:
            if not request.content_type == 'application/json' and request.headers.get('x-requested-with') != 'XMLHttpRequest':
                from django.contrib import messages
                messages.error(request, error_msg)
                return redirect('cart:view_cart')
            return JsonResponse({
                'success': False,
                'error': error_msg,
                'available_stock': product.current_stock_count,
                'per_order_limit': product.per_order_limit,
                'max_quantity': actual_max,
                'actual_max_order_quantity': actual_max,
                'unit': product.get_unit_label(actual_max),
                'product_id': product.id
            }, status=400)

        # Apply validated quantity to cart
        if not cart_item:
            cart_item = CartItem.objects.create(cart=cart, product=product, quantity=target_quantity)
        else:
            cart_item.quantity = target_quantity
            cart_item.save(update_fields=['quantity', 'updated_at'])

        if request.content_type == 'application/json' or request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f"Added {product.name} to basket!",
                'product_id': product.id,
                'product_name': product.name,
                'item_quantity': cart_item.quantity,
                'total_items': cart.total_items,
                'subtotal': format_inr(cart.subtotal),
                'grand_total': format_inr(cart.grand_total),
                'available_stock': product.current_stock_count,
                'per_order_limit': product.per_order_limit,
                'actual_max_order_quantity': product.actual_max_order_quantity,
            })
        return redirect('cart:view_cart')
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def update_cart_api(request, item_id):
    """AJAX endpoint to update item quantity in cart."""
    try:
        data = json.loads(request.body)
        try:
            qty_raw = data.get('quantity', 1)
            dec_qty = Decimal(str(qty_raw))
            quantity = int(dec_qty) if dec_qty % Decimal('1') == Decimal('0') else dec_qty
        except (ValueError, TypeError, ArithmeticError):
            return JsonResponse({'success': False, 'error': 'Invalid quantity format.'}, status=400)

        cart = get_or_create_cart(request)
        item = get_object_or_404(CartItem, id=item_id, cart=cart)

        # Merge duplicate cart lines for the same product if any exist
        existing_items = list(CartItem.objects.filter(cart=cart, product=item.product))
        if len(existing_items) > 1:
            for extra in existing_items:
                if extra.id != item.id:
                    extra.delete()

        if quantity <= 0:
            item.delete()
            return JsonResponse({
                'success': True,
                'message': 'Item removed',
                'total_items': cart.total_items,
                'subtotal': format_inr(cart.subtotal),
                'grand_total': format_inr(cart.grand_total)
            })

        # Strict validation against inventory and bike delivery per-order limit
        is_valid, error_msg, actual_max = validate_purchase_quantity(
            item.product, requested_quantity=quantity, current_cart_quantity=0
        )
        if not is_valid:
            return JsonResponse({
                'success': False,
                'error': error_msg,
                'available_stock': item.product.current_stock_count,
                'per_order_limit': item.product.per_order_limit,
                'max_quantity': actual_max,
                'actual_max_order_quantity': actual_max,
                'unit': item.product.get_unit_label(actual_max)
            }, status=400)

        item.quantity = int(quantity)
        item.save(update_fields=['quantity', 'updated_at'])

        return JsonResponse({
            'success': True,
            'item_id': item.id,
            'quantity': item.quantity,
            'line_total': format_inr(item.line_total),
            'total_items': cart.total_items,
            'subtotal': format_inr(cart.subtotal),
            'grand_total': format_inr(cart.grand_total)
        })
    except Http404:
        return JsonResponse({'success': False, 'error': 'Cart item not found.'}, status=404)
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
        'subtotal': format_inr(cart.subtotal),
        'grand_total': format_inr(cart.grand_total)
    })


def view_cart(request):
    """Full shopping cart page with order breakdown and checkout gateway."""
    if request.user.is_authenticated and request.user.role != 'BUYER':
        from django.contrib import messages
        messages.warning(request, "Only customer accounts can access the shopping cart.")
        return redirect('catalog:home')

    cart = get_or_create_cart(request)
    cart_items = cart.items.select_related('product', 'product__stock_record', 'product__seller', 'product__seller__seller_profile')
    
    # Check if any item exceeds available stock or order limits
    has_stock_error = False
    for item in cart_items:
        if not item.is_available:
            has_stock_error = True

    from apps.accounts.decorators import apply_cache_control_headers
    response = render(request, 'buyer/cart.html', {
        'cart': cart,
        'cart_items': cart_items,
        'has_stock_error': has_stock_error,
    })
    return apply_cache_control_headers(response)


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
