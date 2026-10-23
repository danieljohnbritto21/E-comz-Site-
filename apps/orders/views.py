import uuid
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from apps.cart.models import Cart
from apps.orders.models import Order, OrderItem, SellerSubOrder, OrderStatusHistory, Address
from apps.inventory.models import StockMovement, StockRecord
from apps.inventory.services import restore_stock_for_order
from apps.notifications.models import Notification
from apps.accounts.models import User


from django.contrib.auth import login
from django.utils.text import slugify
from django.urls import reverse
from apps.cart.views import get_or_create_cart
from apps.accounts.models import User, BuyerProfile


def checkout_view(request):
    """Customer-only checkout gateway requiring an authenticated Customer account."""
    if not request.user.is_authenticated:
        messages.info(request, "Please log in or create a customer account to checkout.")
        return redirect(f"{reverse('accounts:login')}?role=BUYER&next={request.path}")

    if request.user.role != User.Role.BUYER:
        messages.error(request, "Only customer accounts can access checkout and place orders.")
        return redirect('catalog:home')

    cart = get_or_create_cart(request)
    if not cart or cart.total_items == 0:
        messages.warning(request, "Your basket is empty. Please add items before checkout.")
        return redirect('catalog:product_list')

    # Merge duplicate cart lines for the same product if any exist
    seen_products = {}
    for item in list(cart.items.select_related('product')):
        pid = item.product_id
        if pid in seen_products:
            primary_item = seen_products[pid]
            primary_item.quantity += item.quantity
            primary_item.save(update_fields=['quantity', 'updated_at'])
            item.delete()
        else:
            seen_products[pid] = item

    cart_items = cart.items.select_related('product', 'product__stock_record', 'product__seller')
    
    # Pre-check stock & bike delivery per-order limits
    for item in cart_items:
        prod = item.product
        stock = prod.current_stock_count
        limit = prod.per_order_limit
        actual_max = prod.actual_max_order_quantity
        stock_unit = prod.get_unit_label(stock)
        limit_unit = prod.get_unit_label(limit)

        if not item.is_available:
            if stock <= 0:
                messages.error(request, f"Cannot proceed to checkout. '{prod.name}' is currently out of stock. Please remove it from your basket.")
            elif item.quantity > stock:
                messages.error(request, f"Cannot proceed to checkout. '{prod.name}' only has {stock} {stock_unit} available in stock (you have {item.quantity} in basket). Please adjust your basket.")
            else:
                messages.error(request, f"Cannot proceed to checkout. You can order a maximum of {limit} {limit_unit} of '{prod.name}' per order (you have {item.quantity} in basket). Please adjust your basket.")
            return redirect('cart:view_cart')

    addresses = Address.objects.filter(user=request.user) if request.user.is_authenticated else Address.objects.none()

    # Pre-populate defaults
    customer_name = (request.user.get_full_name() or request.user.username) if request.user.is_authenticated else ''
    customer_phone = getattr(request.user, 'phone_number', '') if request.user.is_authenticated else ''
    customer_email = request.user.email if request.user.is_authenticated else ''
    full_name = customer_name
    phone = customer_phone
    house = ''
    street = ''
    area = ''
    city = 'Central City'
    state = 'State'
    pin_code = ''
    addr_type = 'HOME'
    landmark = ''
    delivery_date_choice = 'today'
    delivery_slot = '07:00 AM - 10:00 AM'
    instructions = ''
    payment_method = 'COD'
    address_id = ''

    if request.user.is_authenticated and addresses.exists():
        default_addr = addresses.filter(is_default=True).first() or addresses.first()
        if default_addr:
            address_id = str(default_addr.id)
            full_name = default_addr.full_name
            phone = default_addr.phone_number
            house = default_addr.house_number
            street = default_addr.street
            area = default_addr.area
            city = default_addr.city
            state = default_addr.state
            pin_code = default_addr.pin_code
            landmark = default_addr.landmark
            addr_type = default_addr.address_type

    def build_checkout_context():
        from apps.reports.models import MarketplaceConfig
        from apps.reports.services import get_buyer_cashback_balance
        config = MarketplaceConfig.get_config()
        total_items_count = sum(item.quantity for item in cart_items)
        expected_cashback = (config.cashback_per_unit * Decimal(total_items_count)).quantize(Decimal('0.01'))
        available_cashback = get_buyer_cashback_balance(request.user) if request.user.is_authenticated else Decimal('0.00')

        return {
            'cart': cart,
            'cart_items': cart_items,
            'addresses': addresses,
            'customer_name': customer_name,
            'customer_phone': customer_phone,
            'customer_email': customer_email,
            'full_name': full_name,
            'phone_number': phone,
            'house_number': house,
            'street': street,
            'area': area,
            'city': city,
            'state': state,
            'pin_code': pin_code,
            'landmark': landmark,
            'address_type': addr_type,
            'delivery_date': delivery_date_choice,
            'delivery_slot': delivery_slot,
            'delivery_instructions': instructions,
            'payment_method': payment_method,
            'address_id': address_id,
            'checkout_token': uuid.uuid4().hex[:16],
            'expected_cashback': expected_cashback,
            'available_cashback': available_cashback,
            'cashback_per_unit': config.cashback_per_unit,
        }

    if request.method == 'POST':
        # Retrieve all submitted fields
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = request.POST.get('customer_phone', '').strip()
        customer_email = request.POST.get('customer_email', '').strip()

        address_id = request.POST.get('address_id', '').strip()
        full_name = request.POST.get('full_name', '').strip()
        phone = request.POST.get('phone_number', '').strip()
        house = request.POST.get('house_number', '').strip()
        street = request.POST.get('street', '').strip()
        area = request.POST.get('area', '').strip()
        city = request.POST.get('city', 'Central City').strip()
        state = request.POST.get('state', 'State').strip()
        pin_code = request.POST.get('pin_code', '').strip()
        addr_type = request.POST.get('address_type', 'HOME')
        landmark = request.POST.get('landmark', '').strip()

        delivery_date_choice = request.POST.get('delivery_date', 'today')
        delivery_slot = request.POST.get('delivery_slot', '07:00 AM - 10:00 AM')
        instructions = request.POST.get('delivery_instructions', '').strip()
        payment_method = request.POST.get('payment_method', 'COD')

        # Auto-sync Name and Phone between Section 1 & Section 2 so the user never has to type twice
        if not customer_name and full_name:
            customer_name = full_name
        if not full_name and customer_name:
            full_name = customer_name

        if not customer_phone and phone:
            customer_phone = phone
        if not phone and customer_phone:
            phone = customer_phone

        # Authenticate / Register user
        if request.user.is_authenticated:
            customer_name = customer_name or request.user.get_full_name() or request.user.username
            customer_phone = customer_phone or getattr(request.user, 'phone_number', '') or ''
            customer_email = customer_email or request.user.email or ''
            buyer_user = request.user
        else:
            if not customer_name:
                messages.error(request, "Please enter your Name.")
                return render(request, 'buyer/checkout.html', build_checkout_context())
            if not customer_phone:
                messages.error(request, "Please enter your Mobile Number.")
                return render(request, 'buyer/checkout.html', build_checkout_context())

            # If guest email is omitted, generate fallback guest email from phone
            if not customer_email:
                clean_phone = ''.join(filter(str.isdigit, customer_phone)) or 'guest'
                customer_email = f"buyer_{clean_phone}@freshmart.local"

            # Find existing buyer or create
            matched_user = User.objects.filter(email__iexact=customer_email).first()
            if not matched_user and customer_phone:
                matched_user = User.objects.filter(phone_number=customer_phone).first()

            if matched_user:
                # NEVER auto-login or take over an Admin, Seller, or Delivery account through guest checkout!
                if matched_user.role in [User.Role.ADMIN, User.Role.SELLER, User.Role.DELIVERY] or matched_user.is_superuser:
                    messages.error(request, "This email belongs to a staff or administrator profile. Please sign in to continue.")
                    return redirect(f"{reverse('accounts:login')}?role=BUYER&next={request.path}")

                messages.info(request, "An account with this email/phone already exists. Please sign in to complete your checkout.")
                return redirect(f"{reverse('accounts:login')}?role=BUYER&next={request.path}")
            else:
                clean_email_prefix = slugify(customer_email.split('@')[0]) or 'customer'
                unique_username = f"{clean_email_prefix}_{uuid.uuid4().hex[:6]}"
                first_name = customer_name.split()[0] if customer_name else ''
                last_name = ' '.join(customer_name.split()[1:]) if customer_name and len(customer_name.split()) > 1 else ''
                buyer_user = User.objects.create(
                    username=unique_username,
                    email=customer_email,
                    phone_number=customer_phone,
                    first_name=first_name,
                    last_name=last_name,
                    role=User.Role.BUYER,
                    account_status=User.Status.ACTIVE
                )
                buyer_user.set_unusable_password()
                buyer_user.save()
                BuyerProfile.objects.get_or_create(user=buyer_user)

                login(request, buyer_user)
                cart.user = buyer_user
                cart.session_key = None
                cart.save(update_fields=['user', 'session_key'])

        # Retrieve or create delivery address
        if address_id and request.user.is_authenticated:
            selected_address = get_object_or_404(Address, id=address_id, user=buyer_user)
            recipient_name = selected_address.full_name
            recipient_phone = selected_address.phone_number
            address_text = selected_address.formatted
            pin = selected_address.pin_code
            address_type_name = selected_address.get_address_type_display()
        else:
            if not (full_name and phone and house and street and pin_code):
                messages.error(request, "Please fill in all required delivery address fields: House/Building No., Street, and PIN Code.")
                return render(request, 'buyer/checkout.html', build_checkout_context())
            
            selected_address = Address.objects.create(
                user=buyer_user,
                full_name=full_name,
                phone_number=phone,
                house_number=house,
                street=street,
                area=area,
                city=city,
                state=state,
                pin_code=pin_code,
                landmark=landmark,
                address_type=addr_type,
                is_default=(not Address.objects.filter(user=buyer_user).exists())
            )
            recipient_name = selected_address.full_name
            recipient_phone = selected_address.phone_number
            address_text = selected_address.formatted
            pin = selected_address.pin_code
            address_type_name = selected_address.get_address_type_display()

        delivery_date_choice = request.POST.get('delivery_date', 'today')
        today_date = timezone.now().date()
        delivery_date = today_date if delivery_date_choice == 'today' else today_date + timezone.timedelta(days=1)
        delivery_slot = request.POST.get('delivery_slot', '10:00 AM - 01:00 PM')
        instructions = request.POST.get('delivery_instructions', '').strip()
        payment_method = request.POST.get('payment_method', 'COD')

        # Recalculate all amounts on backend
        backend_subtotal = Decimal('0.00')
        backend_discount = Decimal('0.00')
        for item in cart_items:
            backend_subtotal += (item.product.mrp * item.quantity)
            backend_discount += ((item.product.mrp - item.product.selling_price) * item.quantity)
        
        selling_subtotal = cart.subtotal
        delivery_fee = cart.delivery_fee

        redeem_cashback_requested = request.POST.get('redeem_cashback') in ['1', 'true', 'on', 'yes']
        cashback_to_redeem = Decimal('0.00')
        if redeem_cashback_requested and buyer_user.is_authenticated:
            from apps.reports.services import get_buyer_cashback_balance
            user_balance = get_buyer_cashback_balance(buyer_user)
            cashback_to_redeem = min(user_balance, cart.subtotal)

        final_payable = max(Decimal('0.00'), cart.grand_total - cashback_to_redeem)

        checkout_token = request.POST.get('checkout_token', '').strip()
        if checkout_token:
            existing_token_order = Order.objects.filter(buyer=buyer_user, checkout_token=checkout_token).first()
            if existing_token_order:
                cart.items.all().delete()
                messages.info(request, f"Your order #{existing_token_order.order_id} has already been received.")
                return redirect('orders:confirmation', order_id=existing_token_order.order_id)

        # Prevent duplicate submissions (within 30s)
        recent_duplicate = Order.objects.filter(
            buyer=buyer_user,
            total_amount=final_payable,
            created_at__gte=timezone.now() - timezone.timedelta(seconds=30)
        ).first()
        if recent_duplicate:
            cart.items.all().delete()
            messages.info(request, f"Your order #{recent_duplicate.order_id} has already been received.")
            return redirect('orders:confirmation', order_id=recent_duplicate.order_id)

        # Atomic order placement & inventory reservation
        try:
            with transaction.atomic():
                # Re-verify and deduct stock safely with deadlock-free consistent lock ordering
                sorted_cart_items = sorted(list(cart_items), key=lambda it: it.product_id or 0)
                product_totals = {}
                for it in sorted_cart_items:
                    product_totals[it.product_id] = product_totals.get(it.product_id, 0) + it.quantity

                for item in sorted_cart_items:
                    prod = item.product
                    limit = prod.per_order_limit
                    total_ordered = product_totals.get(item.product_id, item.quantity)
                    if total_ordered > limit:
                        limit_unit = prod.get_unit_label(limit)
                        raise ValueError(
                            f"You can order a maximum of {limit} {limit_unit} of '{prod.name}' per order."
                        )
                    record = StockRecord.objects.select_for_update().get(product_id=item.product_id)
                    if record.current_stock < total_ordered:
                        avail_unit = prod.get_unit_label(record.current_stock)
                        req_unit = prod.get_unit_label(total_ordered)
                        raise ValueError(
                            f"Insufficient stock for {prod.name}. "
                            f"Available: {record.current_stock} {avail_unit}, Requested: {total_ordered} {req_unit}."
                        )
                    record.adjust_stock(
                        -item.quantity,
                        StockMovement.ChangeType.SALE,
                        user=buyer_user,
                        notes=f"Reserved for order checkout by {buyer_user.username}"
                    )

                is_cod = (payment_method == 'COD')
                
                # Create Order in PENDING status initially
                order = Order.objects.create(
                    order_id=Order.generate_order_id(),
                    buyer=buyer_user,
                    recipient_name=recipient_name,
                    recipient_phone=recipient_phone,
                    delivery_address_text=address_text,
                    pin_code=pin,
                    address_type=address_type_name,
                    delivery_date=delivery_date,
                    delivery_slot=delivery_slot,
                    delivery_instructions=instructions,
                    status=Order.Status.ORDER_CONFIRMED if is_cod else Order.Status.ORDER_PLACED,
                    payment_method=payment_method,
                    payment_status=Order.PaymentStatus.PENDING,  # PENDING for both COD (unpaid at checkout) and Online (unverified)
                    transaction_id='',
                    subtotal=selling_subtotal,
                    discount_amount=cart.total_discount,
                    delivery_fee=delivery_fee,
                    tax_amount=Decimal('0.00'),
                    total_amount=final_payable,
                    cashback_redeemed=cashback_to_redeem,
                    stock_deducted=True,
                    stock_restored=False,
                    checkout_token=checkout_token
                )

                # Create status history
                if is_cod:
                    order.add_status_history(
                        Order.Status.ORDER_CONFIRMED,
                        buyer_user,
                        "Cash on Delivery order placed. Payment to be collected at doorstep delivery."
                    )
                else:
                    order.add_status_history(
                        Order.Status.ORDER_PLACED,
                        buyer_user,
                        f"Order placed with {order.get_payment_method_display()}. Redirecting to payment gateway verification."
                    )

                # Group cart items by seller to create SellerSubOrders
                seller_items_map = {}
                for item in cart_items:
                    seller = item.product.seller
                    if seller not in seller_items_map:
                        seller_items_map[seller] = []
                    seller_items_map[seller].append(item)

                for seller, items_list in seller_items_map.items():
                    sub_subtotal = sum(i.line_total for i in items_list)
                    sub_order = SellerSubOrder.objects.create(
                        order=order,
                        seller=seller,
                        subtotal=sub_subtotal,
                        status=SellerSubOrder.Status.PENDING
                    )

                    for item in items_list:
                        OrderItem.objects.create(
                            order=order,
                            sub_order=sub_order,
                            seller=seller,
                            product=item.product,
                            product_name=item.product.name,
                            brand=item.product.brand,
                            unit_value=item.product.unit_value,
                            unit_price=item.product.selling_price,
                            mrp=item.product.mrp,
                            product_cost_snapshot=item.product.product_cost,
                            quantity=item.quantity,
                            total_price=item.line_total,
                            image_url=item.product.primary_image_url
                        )

                # Initialize financial snapshots & pending earnings
                from apps.reports.services import (
                    redeem_buyer_cashback,
                    create_order_financial_snapshots,
                    add_pending_seller_earnings,
                )
                if cashback_to_redeem > Decimal('0.00'):
                    redeem_buyer_cashback(order, cashback_to_redeem)

                create_order_financial_snapshots(order)
                add_pending_seller_earnings(order)

                # Clear cart now that items are reserved into the Order
                cart.items.all().delete()

                # If Cash on Delivery, notify immediately and go straight to confirmation
                if is_cod:
                    from apps.catalog.currency import format_inr
                    for seller, items_list in seller_items_map.items():
                        sub_subtotal = sum(i.line_total for i in items_list)
                        Notification.send(
                            user=seller,
                            title=f"New COD Order #{order.order_id}",
                            message=f"You have a new Cash on Delivery grocery order for {format_inr(sub_subtotal)}. Please prepare produce.",
                            category=Notification.Category.ORDER,
                            link=f"/seller/orders/{order.order_id}/"
                        )

                    Notification.send(
                        user=buyer_user,
                        title=f"Order Confirmed #{order.order_id}",
                        message=f"Thank you! Your COD order of {format_inr(order.total_amount)} is confirmed. Please keep cash/UPI ready at delivery.",
                        category=Notification.Category.ORDER,
                        link=f"/orders/track/{order.order_id}/"
                    )

                    messages.success(request, f"Cash on Delivery order #{order.order_id} placed successfully!")
                    return redirect('orders:confirmation', order_id=order.order_id)

                # If online payment method, redirect to test payment gateway for server verification
                return redirect('orders:payment_gateway', order_id=order.order_id)

        except Exception as e:
            messages.error(request, f"Order placement failed: {str(e)}")
            return render(request, 'buyer/checkout.html', build_checkout_context())

    return render(request, 'buyer/checkout.html', build_checkout_context())


@login_required
def payment_gateway_view(request, order_id):
    """Simulated Secure Payment Gateway for test-mode verification of UPI, Cards, Netbanking."""
    order = get_object_or_404(Order.objects.prefetch_related('items'), order_id=order_id, buyer=request.user)

    if order.payment_status == Order.PaymentStatus.PAID:
        messages.info(request, f"Order #{order.order_id} is already paid.")
        return redirect('orders:confirmation', order_id=order.order_id)

    if order.status == Order.Status.CANCELLED or order.payment_status == Order.PaymentStatus.FAILED:
        messages.error(request, f"Order #{order.order_id} payment has failed or was cancelled.")
        return render(request, 'buyer/payment_failed.html', {'order': order})

    return render(request, 'buyer/payment_gateway.html', {
        'order': order,
    })


@login_required
def payment_verify_view(request, order_id):
    """Server-side verification of payment gateway callback."""
    if request.method != 'POST':
        return redirect('orders:payment_gateway', order_id=order_id)

    order = get_object_or_404(Order.objects.prefetch_related('items', 'sub_orders'), order_id=order_id, buyer=request.user)
    action = request.POST.get('action')

    if action == 'success':
        # Server-side verification simulation
        txn_id = f"TXN-VERIFIED-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        
        with transaction.atomic():
            order.payment_status = Order.PaymentStatus.PAID
            order.status = Order.Status.ORDER_CONFIRMED
            order.transaction_id = txn_id
            order.save(update_fields=['payment_status', 'status', 'transaction_id', 'updated_at'])

            order.add_status_history(
                Order.Status.ORDER_CONFIRMED,
                request.user,
                f"Online Payment Verified Successfully via {order.get_payment_method_display()} (Txn Ref: {txn_id})"
            )

            # Notify sellers that payment is verified and order is ready for packing
            for sub in order.sub_orders.all():
                Notification.send(
                    user=sub.seller,
                    title=f"Paid Order Received #{order.order_id}",
                    message=f"Order #{order.order_id} has been PAID online (Rs. {sub.subtotal}). Please accept and pack the order.",
                    category=Notification.Category.ORDER,
                    link=f"/seller/orders/{order.order_id}/"
                )

            # Notify buyer
            Notification.send(
                user=request.user,
                title=f"Payment Verified #{order.order_id}",
                message=f"Your online payment of Rs. {order.total_amount} was successful. We are preparing your fresh groceries.",
                category=Notification.Category.ORDER,
                link=f"/orders/track/{order.order_id}/"
            )

        messages.success(request, f"Payment verified! Order #{order.order_id} confirmed.")
        return redirect('orders:confirmation', order_id=order.order_id)

    else:
        # Failure or cancellation
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(id=order.id)
            if locked_order.status != Order.Status.CANCELLED:
                locked_order.payment_status = Order.PaymentStatus.FAILED
                locked_order.status = Order.Status.CANCELLED
                locked_order.save(update_fields=['payment_status', 'status', 'updated_at'])

                locked_order.add_status_history(
                    Order.Status.CANCELLED,
                    request.user,
                    "Online payment authorization failed or was declined by user."
                )

                # Restore reserved inventory exactly once
                restore_stock_for_order(
                    locked_order,
                    actor=request.user,
                    reason=f"Restored from failed payment order #{locked_order.order_id}"
                )

                from apps.reports.services import restore_redeemed_cashback, reverse_seller_profit
                restore_redeemed_cashback(locked_order, reason=f"Restored from failed payment order #{locked_order.order_id}")
                reverse_seller_profit(locked_order, reason="Online payment authorization failed.")

                locked_order.sub_orders.update(status=SellerSubOrder.Status.CANCELLED, stock_restored=True)

        messages.error(request, f"Payment authorization failed for Order #{order.order_id}.")
        return render(request, 'buyer/payment_failed.html', {'order': order})


@login_required
def order_confirmation_view(request, order_id):
    """Order confirmation invoice page."""
    order = get_object_or_404(Order.objects.prefetch_related('items', 'items__seller'), order_id=order_id, buyer=request.user)
    return render(request, 'buyer/order_confirmation.html', {'order': order})


@login_required
def order_track_view(request, order_id):
    """Detailed visual order tracking page with milestone stepper and live status updates."""
    order = get_object_or_404(Order.objects.prefetch_related('items', 'status_history', 'delivery_assignment'), order_id=order_id)
    
    # Permission verification
    is_authorized = (
        order.buyer == request.user or
        request.user.role == 'ADMIN' or
        request.user.is_superuser or
        (request.user.role == 'DELIVERY' and hasattr(order, 'delivery_assignment') and order.delivery_assignment.delivery_person == request.user) or
        (request.user.role == 'SELLER' and order.items.filter(seller=request.user).exists())
    )

    if not is_authorized:
        messages.error(request, "You do not have permission to view that order tracking.")
        return redirect('catalog:home')

    status_order = [
        Order.Status.ORDER_PLACED,
        Order.Status.ORDER_CONFIRMED,
        Order.Status.PREPARING,
        Order.Status.PACKED,
        Order.Status.READY_FOR_PICKUP,
        Order.Status.OUT_FOR_DELIVERY,
        Order.Status.DELIVERED,
    ]

    current_idx = -1
    if order.status in status_order:
        current_idx = status_order.index(order.status)

    active_otp = None
    otp_expired = False
    if request.user == order.buyer or request.user.role == 'ADMIN' or request.user.is_superuser:
        from apps.delivery.models import DeliveryOTP
        latest_otp = DeliveryOTP.objects.filter(order=order, is_used=False).order_by('-created_at').first()
        if latest_otp:
            if latest_otp.expires_at > timezone.now():
                active_otp = latest_otp
            else:
                otp_expired = True

    return render(request, 'buyer/order_track.html', {
        'order': order,
        'status_order': status_order,
        'current_idx': current_idx,
        'active_otp': active_otp,
        'otp_expired': otp_expired,
    })


@login_required
def order_history_view(request):
    """Buyer order history page."""
    from apps.reports.services import get_buyer_cashback_balance
    orders = Order.objects.filter(buyer=request.user).prefetch_related('items').order_by('-created_at')
    cashback_balance = get_buyer_cashback_balance(request.user)
    return render(request, 'buyer/order_history.html', {
        'orders': orders,
        'cashback_balance': cashback_balance,
    })


@login_required
def cancel_order_view(request, order_id):
    """Buyer order cancellation (allowed if order not yet picked up / out for delivery)."""
    order = get_object_or_404(Order, order_id=order_id, buyer=request.user)
    
    cancellable_statuses = [Order.Status.ORDER_PLACED, Order.Status.ORDER_CONFIRMED, Order.Status.PREPARING]
    if order.status not in cancellable_statuses:
        messages.error(request, "This order is already packed or out for delivery and cannot be cancelled online. Please contact support.")
        return redirect('orders:track', order_id=order.order_id)

    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)
        if locked_order.status not in cancellable_statuses:
            messages.error(request, "This order is already packed or out for delivery and cannot be cancelled online. Please contact support.")
            return redirect('orders:track', order_id=locked_order.order_id)

        # Restore inventory exactly once
        restore_stock_for_order(
            locked_order,
            actor=request.user,
            reason=f"Restored from customer cancelled order #{locked_order.order_id}"
        )

        from apps.reports.services import (
            restore_redeemed_cashback,
            reverse_seller_profit,
            reverse_buyer_cashback,
            reverse_delivery_earning,
        )
        restore_redeemed_cashback(locked_order, reason=f"Restored from customer cancelled order #{locked_order.order_id}")
        reverse_seller_profit(locked_order, reason="Order cancelled by customer.")
        reverse_buyer_cashback(locked_order, reason="Order cancelled by customer.")
        reverse_delivery_earning(locked_order)

        locked_order.add_status_history(Order.Status.CANCELLED, request.user, "Order cancelled by customer.")
        locked_order.sub_orders.update(status=SellerSubOrder.Status.CANCELLED, stock_restored=True)

        # Notify sellers
        for sub in order.sub_orders.all():
            Notification.send(
                user=sub.seller,
                title=f"Order #{order.order_id} Cancelled",
                message=f"Buyer cancelled order #{order.order_id}. Items have been safely returned to inventory.",
                category=Notification.Category.ORDER,
                link=f"/seller/orders/{order.order_id}/"
            )

        messages.info(request, f"Order #{order.order_id} has been cancelled successfully.")
        return redirect('orders:order_history')
