import csv
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Sum, Count, Q, F
from apps.accounts.decorators import role_required
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile
from apps.catalog.models import Product, Category
from apps.orders.models import Order, OrderItem, SellerSubOrder, OrderStatusHistory
from apps.delivery.models import DeliveryAssignment
from apps.delivery.services import auto_assign_delivery_partner
from apps.inventory.models import StockMovement, StockRecord
from apps.notifications.models import Notification


@role_required(['ADMIN'])
def admin_dashboard_view(request):
    """Central administrator control center with live KPI metric cards and tables."""
    today = timezone.now().date()
    month_start = today.replace(day=1)

    # User counts
    total_buyers = User.objects.filter(role=User.Role.BUYER).count()
    total_sellers = User.objects.filter(role=User.Role.SELLER).count()
    total_delivery = User.objects.filter(role=User.Role.DELIVERY).count()
    pending_sellers_count = SellerProfile.objects.filter(is_approved=False).count()

    # Product metrics
    total_products = Product.objects.count()
    active_products = Product.objects.filter(is_active=True).count()
    out_of_stock_products = Product.objects.filter(stock_record__current_stock__lte=0).count()

    # Orders metrics
    total_orders = Order.objects.count()
    pending_orders = Order.objects.filter(status__in=['ORDER_PLACED', 'ORDER_CONFIRMED', 'PREPARING', 'PACKED', 'READY_FOR_PICKUP']).count()
    delivered_orders = Order.objects.filter(status=Order.Status.DELIVERED).count()
    cancelled_orders = Order.objects.filter(status=Order.Status.CANCELLED).count()

    # Financials
    today_sales = Order.objects.filter(created_at__date=today).exclude(status__in=['CANCELLED', 'REFUNDED']).aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
    monthly_revenue = Order.objects.filter(created_at__date__gte=month_start).exclude(status__in=['CANCELLED', 'REFUNDED']).aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')

    # Tables
    recent_orders = Order.objects.select_related('buyer').order_by('-created_at')[:8]
    recent_users = User.objects.order_by('-created_at')[:6]
    pending_sellers = SellerProfile.objects.filter(is_approved=False).select_related('user')[:5]

    return render(request, 'admin_dashboard/dashboard.html', {
        'total_buyers': total_buyers,
        'total_sellers': total_sellers,
        'total_delivery': total_delivery,
        'pending_sellers_count': pending_sellers_count,
        'total_products': total_products,
        'active_products': active_products,
        'out_of_stock_products': out_of_stock_products,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'delivered_orders': delivered_orders,
        'cancelled_orders': cancelled_orders,
        'today_sales': today_sales,
        'monthly_revenue': monthly_revenue,
        'recent_orders': recent_orders,
        'recent_users': recent_users,
        'pending_sellers': pending_sellers,
    })


@role_required(['ADMIN'])
def admin_orders_view(request):
    """
    Comprehensive marketplace order management console.
    Supports 17 status categories/tabs with live counts, multi-field search,
    advanced filters (seller, delivery agent, payment method/status, date range, cashback),
    backend pagination, and delivery assignment.
    """
    from django.core.paginator import Paginator

    all_orders = Order.objects.all()

    # Live counts across all 17 supported marketplace status categories
    status_counts = {
        'all': all_orders.count(),
        'pending_confirmation': all_orders.filter(status=Order.Status.ORDER_PLACED).count(),
        'confirmed': all_orders.filter(status=Order.Status.ORDER_CONFIRMED).count(),
        'processing': all_orders.filter(status=Order.Status.PREPARING).count(),
        'packed_ready': all_orders.filter(status__in=[Order.Status.PACKED, Order.Status.READY_FOR_PICKUP]).count(),
        'awaiting_assignment': all_orders.filter(
            status__in=[Order.Status.ORDER_CONFIRMED, Order.Status.PREPARING, Order.Status.PACKED, Order.Status.READY_FOR_PICKUP]
        ).filter(Q(delivery_assignment__isnull=True) | Q(delivery_assignment__delivery_person__isnull=True)).count(),
        'assigned': all_orders.filter(
            delivery_assignment__status__in=[
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.ACCEPTED,
                DeliveryAssignment.Status.ARRIVED_AT_STORE
            ]
        ).count(),
        'out_for_delivery': all_orders.filter(status=Order.Status.OUT_FOR_DELIVERY).count(),
        'awaiting_payment': all_orders.filter(
            payment_method=Order.PaymentMethod.COD,
            payment_status=Order.PaymentStatus.PENDING
        ).exclude(status__in=[Order.Status.CANCELLED, Order.Status.REFUNDED]).count(),
        'awaiting_otp': all_orders.filter(
            payment_status=Order.PaymentStatus.PAID,
            delivery_assignment__status__in=[DeliveryAssignment.Status.PICKED_UP, DeliveryAssignment.Status.OUT_FOR_DELIVERY]
        ).exclude(status=Order.Status.DELIVERED).count(),
        'delivered': all_orders.filter(status=Order.Status.DELIVERED).count(),
        'cancelled': all_orders.filter(status=Order.Status.CANCELLED).count(),
        'return_requested': all_orders.filter(status=Order.Status.RETURN_REQUESTED).count(),
        'returned': all_orders.filter(status=Order.Status.RETURNED).count(),
        'refund_pending': all_orders.filter(status=Order.Status.REFUND_PENDING).count(),
        'refunded': all_orders.filter(status=Order.Status.REFUNDED).count(),
        'delivery_failed': all_orders.filter(
            Q(status=Order.Status.FAILED_DELIVERY) | Q(delivery_assignment__status=DeliveryAssignment.Status.FAILED)
        ).count(),
    }

    orders = Order.objects.select_related(
        'buyer',
        'delivery_assignment',
        'delivery_assignment__delivery_person'
    ).prefetch_related(
        'sub_orders',
        'sub_orders__seller',
        'sub_orders__seller__seller_profile',
        'items',
        'items__seller',
        'items__seller__seller_profile',
        'financial_snapshots'
    ).order_by('-created_at')

    # Status tab filter
    active_tab = request.GET.get('tab', 'all')
    if active_tab == 'pending_confirmation':
        orders = orders.filter(status=Order.Status.ORDER_PLACED)
    elif active_tab == 'confirmed':
        orders = orders.filter(status=Order.Status.ORDER_CONFIRMED)
    elif active_tab == 'processing':
        orders = orders.filter(status=Order.Status.PREPARING)
    elif active_tab == 'packed_ready':
        orders = orders.filter(status__in=[Order.Status.PACKED, Order.Status.READY_FOR_PICKUP])
    elif active_tab == 'awaiting_assignment':
        orders = orders.filter(
            status__in=[Order.Status.ORDER_CONFIRMED, Order.Status.PREPARING, Order.Status.PACKED, Order.Status.READY_FOR_PICKUP]
        ).filter(Q(delivery_assignment__isnull=True) | Q(delivery_assignment__delivery_person__isnull=True))
    elif active_tab == 'assigned':
        orders = orders.filter(
            delivery_assignment__status__in=[
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.ACCEPTED,
                DeliveryAssignment.Status.ARRIVED_AT_STORE
            ]
        )
    elif active_tab == 'out_for_delivery':
        orders = orders.filter(status=Order.Status.OUT_FOR_DELIVERY)
    elif active_tab == 'awaiting_payment':
        orders = orders.filter(
            payment_method=Order.PaymentMethod.COD,
            payment_status=Order.PaymentStatus.PENDING
        ).exclude(status__in=[Order.Status.CANCELLED, Order.Status.REFUNDED])
    elif active_tab == 'awaiting_otp':
        orders = orders.filter(
            payment_status=Order.PaymentStatus.PAID,
            delivery_assignment__status__in=[DeliveryAssignment.Status.PICKED_UP, DeliveryAssignment.Status.OUT_FOR_DELIVERY]
        ).exclude(status=Order.Status.DELIVERED)
    elif active_tab == 'delivered':
        orders = orders.filter(status=Order.Status.DELIVERED)
    elif active_tab == 'cancelled':
        orders = orders.filter(status=Order.Status.CANCELLED)
    elif active_tab == 'return_requested':
        orders = orders.filter(status=Order.Status.RETURN_REQUESTED)
    elif active_tab == 'returned':
        orders = orders.filter(status=Order.Status.RETURNED)
    elif active_tab == 'refund_pending':
        orders = orders.filter(status=Order.Status.REFUND_PENDING)
    elif active_tab == 'refunded':
        orders = orders.filter(status=Order.Status.REFUNDED)
    elif active_tab == 'delivery_failed':
        orders = orders.filter(
            Q(status=Order.Status.FAILED_DELIVERY) | Q(delivery_assignment__status=DeliveryAssignment.Status.FAILED)
        )
    elif request.GET.get('status'):
        orders = orders.filter(status=request.GET.get('status'))

    # Advanced Search Query across Order ID, Customer Name/Phone/ID, Product Name, SKU, Seller Store
    q = request.GET.get('q', '').strip()
    if q:
        orders = orders.filter(
            Q(order_id__icontains=q) |
            Q(recipient_name__icontains=q) |
            Q(recipient_phone__icontains=q) |
            Q(buyer__username__icontains=q) |
            Q(items__product_name__icontains=q) |
            Q(items__brand__icontains=q) |
            Q(items__product__sku__icontains=q) |
            Q(items__seller__seller_profile__store_name__icontains=q)
        ).distinct()

    # Filter by specific Seller Store
    seller_name = request.GET.get('seller', '').strip()
    if seller_name:
        orders = orders.filter(items__seller__seller_profile__store_name__icontains=seller_name).distinct()

    # Filter by Delivery Agent ID
    delivery_agent_id = request.GET.get('delivery_agent', '').strip()
    if delivery_agent_id:
        orders = orders.filter(delivery_assignment__delivery_person_id=delivery_agent_id)

    # Filter by Payment Method
    payment_method = request.GET.get('payment_method', '').strip()
    if payment_method:
        orders = orders.filter(payment_method=payment_method)

    # Filter by Payment Status
    payment_status = request.GET.get('payment_status', '').strip()
    if payment_status:
        orders = orders.filter(payment_status=payment_status)

    # Filter by Order Status dropdown
    order_status = request.GET.get('order_status', '').strip()
    if order_status:
        orders = orders.filter(status=order_status)

    # Filter by Date Range
    date_from = request.GET.get('date_from', '').strip()
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    date_to = request.GET.get('date_to', '').strip()
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    # Filter by Cashback Status
    cashback_filter = request.GET.get('cashback', '').strip()
    if cashback_filter == 'earned':
        orders = orders.filter(cashback_earned__gt=0)
    elif cashback_filter == 'redeemed':
        orders = orders.filter(cashback_redeemed__gt=0)

    # Backend Pagination (20 orders per page)
    paginator = Paginator(orders, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Delivery agents list with active assignments count for assignment dropdowns
    delivery_agents = User.objects.filter(role=User.Role.DELIVERY, is_active=True).annotate(
        active_assignments_count=Count(
            'deliveries',
            filter=Q(deliveries__status__in=[
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.ACCEPTED,
                DeliveryAssignment.Status.ARRIVED_AT_STORE,
                DeliveryAssignment.Status.PICKED_UP,
                DeliveryAssignment.Status.OUT_FOR_DELIVERY
            ])
        )
    )

    return render(request, 'admin_dashboard/orders.html', {
        'orders': page_obj,
        'page_obj': page_obj,
        'status_counts': status_counts,
        'active_tab': active_tab,
        'delivery_agents': delivery_agents,
        'filters': {
            'q': q,
            'seller': seller_name,
            'delivery_agent': delivery_agent_id,
            'payment_method': payment_method,
            'payment_status': payment_status,
            'order_status': order_status,
            'date_from': date_from,
            'date_to': date_to,
            'cashback': cashback_filter,
            'status': request.GET.get('status', ''),
        }
    })


@role_required(['ADMIN'])
def admin_order_detail_view(request, order_id):
    """
    Dedicated comprehensive order details view for marketplace administrators.
    Displays:
    - Customer details (name, phone, address, placement time)
    - Product breakdown (MRP, unit selling price, discount, subtotal, cost price snapshot, cashback)
    - Payment breakdown (method, status, transaction ref, collected amount, collection audit trail)
    - Delivery breakdown (assigned rider, status, slot, OTP status, proof of delivery, failure attempts)
    - Financial accounting summary (GMV, commission, seller payable, seller profit, delivery earnings, company revenue)
    - Role-protected action triggers (Confirm, Dispatch, Cancel, Refund)
    """
    order = get_object_or_404(
        Order.objects.select_related(
            'buyer',
            'delivery_assignment',
            'delivery_assignment__delivery_person'
        ).prefetch_related(
            'items',
            'items__seller',
            'items__seller__seller_profile',
            'items__product',
            'items__financial_snapshot',
            'sub_orders',
            'sub_orders__seller',
            'sub_orders__seller__seller_profile',
            'status_history',
            'status_history__updated_by',
            'delivery_otps',
            'delivery_assignment__attempts'
        ),
        order_id=order_id
    )

    assignment = getattr(order, 'delivery_assignment', None)
    latest_otp = order.delivery_otps.order_by('-created_at').first()
    attempts = assignment.attempts.all() if assignment else []
    status_history = order.status_history.select_related('updated_by').order_by('created_at')

    # Financial summary calculation
    snapshots = list(order.financial_snapshots.all())
    total_commission = sum(s.commission_amount for s in snapshots)
    total_seller_payable = sum(s.seller_payable for s in snapshots)
    total_seller_profit = sum(s.seller_profit for s in snapshots if s.seller_profit is not None)
    all_costs_recorded = all(s.product_cost_per_unit is not None for s in snapshots) if snapshots else False

    delivery_earning = order.delivery_earnings.filter(status__in=['SETTLED', 'PENDING']).first()
    delivery_expense = delivery_earning.earning_amount if delivery_earning else Decimal('0.00')

    company_revenue = total_commission + order.delivery_fee
    company_expenses = delivery_expense + order.cashback_earned
    company_net_contribution = company_revenue - company_expenses

    delivery_agents = User.objects.filter(role=User.Role.DELIVERY, is_active=True).annotate(
        active_assignments_count=Count(
            'deliveries',
            filter=Q(deliveries__status__in=[
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.ACCEPTED,
                DeliveryAssignment.Status.ARRIVED_AT_STORE,
                DeliveryAssignment.Status.PICKED_UP,
                DeliveryAssignment.Status.OUT_FOR_DELIVERY
            ])
        )
    )

    return render(request, 'admin_dashboard/order_detail.html', {
        'order': order,
        'assignment': assignment,
        'latest_otp': latest_otp,
        'attempts': attempts,
        'status_history': status_history,
        'snapshots': snapshots,
        'total_commission': total_commission,
        'total_seller_payable': total_seller_payable,
        'total_seller_profit': total_seller_profit,
        'all_costs_recorded': all_costs_recorded,
        'delivery_expense': delivery_expense,
        'company_revenue': company_revenue,
        'company_expenses': company_expenses,
        'company_net_contribution': company_net_contribution,
        'delivery_agents': delivery_agents,
    })


@role_required(['ADMIN'])
def admin_order_action_view(request, order_id):
    """Role-protected administrator order lifecycle and financial actions."""
    if request.method != 'POST':
        return redirect('admin_dashboard:order_detail', order_id=order_id)

    order = get_object_or_404(Order, order_id=order_id)
    action = request.POST.get('action')

    if action == 'confirm_order':
        if order.status == Order.Status.ORDER_PLACED:
            with transaction.atomic():
                order.status = Order.Status.ORDER_CONFIRMED
                order.save(update_fields=['status', 'updated_at'])
                order.add_status_history(Order.Status.ORDER_CONFIRMED, request.user, "Confirmed by platform administrator.")
                messages.success(request, f"Order #{order.order_id} confirmed.")
        else:
            messages.warning(request, f"Cannot confirm order in status {order.get_status_display()}.")

    elif action == 'cancel_order':
        if order.status in [Order.Status.DELIVERED, Order.Status.CANCELLED, Order.Status.REFUNDED]:
            messages.error(request, f"Order #{order.order_id} is in status {order.get_status_display()} and cannot be cancelled.")
        else:
            reason = request.POST.get('reason', '').strip() or "Cancelled by platform administrator."
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(id=order.id)
                locked_order.status = Order.Status.CANCELLED
                locked_order.save(update_fields=['status', 'updated_at'])

                # Restore inventory safely
                from apps.inventory.services import restore_stock_for_order
                restore_stock_for_order(locked_order, actor=request.user, reason=f"Admin cancelled: {reason}")

                # Financial reversals
                from apps.reports.services import (
                    restore_redeemed_cashback,
                    reverse_seller_profit,
                    reverse_buyer_cashback,
                    reverse_delivery_earning,
                )
                restore_redeemed_cashback(locked_order, reason=reason)
                reverse_seller_profit(locked_order, reason=reason)
                reverse_buyer_cashback(locked_order, reason=reason)
                reverse_delivery_earning(locked_order)

                locked_order.add_status_history(Order.Status.CANCELLED, request.user, f"Admin cancelled: {reason}")
                locked_order.sub_orders.update(status=SellerSubOrder.Status.CANCELLED, stock_restored=True)

                if hasattr(locked_order, 'delivery_assignment'):
                    locked_order.delivery_assignment.status = DeliveryAssignment.Status.CANCELLED
                    locked_order.delivery_assignment.save(update_fields=['status', 'updated_at'])

                messages.success(request, f"Order #{order.order_id} cancelled and inventory/financials safely restored.")

    elif action == 'process_refund':
        if order.status in [Order.Status.CANCELLED, Order.Status.REFUNDED]:
            messages.info(request, f"Order #{order.order_id} is already {order.get_status_display()}.")
        else:
            reason = request.POST.get('reason', '').strip() or "Refund processed by platform administrator."
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(id=order.id)
                locked_order.status = Order.Status.REFUNDED
                locked_order.payment_status = Order.PaymentStatus.REFUNDED
                locked_order.save(update_fields=['status', 'payment_status', 'updated_at'])

                # Restore stock if items were physically returned
                restore_stock = request.POST.get('restore_stock') in ['1', 'true', 'on', 'yes']
                if restore_stock:
                    from apps.inventory.services import restore_stock_for_order
                    restore_stock_for_order(locked_order, actor=request.user, reason=f"Admin refund return: {reason}")

                from apps.reports.services import (
                    restore_redeemed_cashback,
                    reverse_seller_profit,
                    reverse_buyer_cashback,
                    reverse_delivery_earning,
                )
                restore_redeemed_cashback(locked_order, reason=reason)
                reverse_seller_profit(locked_order, reason=reason)
                reverse_buyer_cashback(locked_order, reason=reason)
                reverse_delivery_earning(locked_order)

                locked_order.add_status_history(Order.Status.REFUNDED, request.user, f"Refund completed: {reason}")
                messages.success(request, f"Refund successfully recorded for Order #{order.order_id}.")

    return redirect('admin_dashboard:order_detail', order_id=order.order_id)


@role_required(['ADMIN'])
def admin_assign_delivery(request, order_id):
    """Assign or reassign an order to a delivery partner."""
    if request.method == 'POST':
        order = get_object_or_404(Order.objects.prefetch_related('sub_orders'), order_id=order_id)
        
        if order.status == Order.Status.DELIVERED:
            messages.error(request, f"Order #{order.order_id} is already DELIVERED and cannot be reassigned.")
            return redirect('admin_dashboard:orders')

        agent_id = request.POST.get('delivery_person_id')
        agent = get_object_or_404(User, id=agent_id, role=User.Role.DELIVERY)

        with transaction.atomic():
            assignment, created = DeliveryAssignment.objects.get_or_create(
                order=order,
                defaults={
                    'delivery_person': agent,
                    'assigned_by': request.user,
                    'status': DeliveryAssignment.Status.ASSIGNED
                }
            )

            if not created:
                assignment.delivery_person = agent
                assignment.status = DeliveryAssignment.Status.ASSIGNED
                assignment.save()

            order.add_status_history(order.status, request.user, f"Assigned to delivery agent {agent.get_full_name() or agent.username}")

            # Notify delivery agent
            Notification.send(
                user=agent,
                title=f"New Delivery Assignment #{order.order_id}",
                message=f"You have been assigned to deliver order #{order.order_id} ({order.delivery_slot}).",
                category=Notification.Category.DELIVERY,
                link=f"/delivery/jobs/{assignment.id}/"
            )

            # Notify sellers
            for sub in order.sub_orders.all():
                Notification.send(
                    user=sub.seller,
                    title=f"Delivery Agent Assigned #{order.order_id}",
                    message=f"Delivery agent {agent.get_full_name() or agent.username} has been assigned to collect order #{order.order_id}.",
                    category=Notification.Category.DELIVERY,
                    link=f"/seller/orders/{order.order_id}/"
                )

            # Notify buyer
            Notification.send(
                user=order.buyer,
                title=f"Delivery Agent Assigned #{order.order_id}",
                message=f"Delivery partner {agent.get_full_name() or agent.username} has been assigned to deliver your order.",
                category=Notification.Category.DELIVERY,
                link=f"/orders/track/{order.order_id}/"
            )

            messages.success(request, f"Order #{order.order_id} assigned to {agent.get_full_name() or agent.username}!")

    return redirect('admin_dashboard:orders')


@role_required(['ADMIN'])
def admin_auto_assign_all(request):
    """Auto-assign all unassigned orders to available delivery fleet riders."""
    unassigned_orders = Order.objects.filter(
        status__in=[Order.Status.ORDER_CONFIRMED, Order.Status.PREPARING, Order.Status.PACKED, Order.Status.READY_FOR_PICKUP]
    ).filter(
        Q(delivery_assignment__isnull=True) | Q(delivery_assignment__delivery_person__isnull=True)
    ).order_by('created_at')

    assigned_count = 0
    for ord_obj in unassigned_orders:
        assigned, driver, _ = auto_assign_delivery_partner(ord_obj, actor=request.user)
        if assigned:
            assigned_count += 1

    if assigned_count > 0:
        messages.success(request, f"Successfully auto-dispatched {assigned_count} order(s) to available delivery fleet partners!")
    else:
        messages.info(request, "No eligible unassigned orders found, or all delivery drivers are currently occupied.")

    return redirect('admin_dashboard:orders')


@role_required(['ADMIN'])
def admin_buyers_view(request):
    """List and manage registered buyers."""
    buyers = User.objects.filter(role=User.Role.BUYER).prefetch_related('orders').order_by('-created_at')
    return render(request, 'admin_dashboard/buyers.html', {'buyers': buyers})


@role_required(['ADMIN'])
def admin_sellers_view(request):
    """List sellers with approval status controls."""
    sellers = SellerProfile.objects.select_related('user').order_by('-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        # Create New Seller
        if action == 'create_seller':
            store_name = request.POST.get('store_name', '').strip()
            username = request.POST.get('username', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            phone = request.POST.get('phone_number', '').strip()
            password = request.POST.get('password', '').strip() or 'seller123'
            pickup_address = request.POST.get('pickup_address', '').strip()
            city = request.POST.get('city', 'Central City').strip() or 'Central City'
            pin_code = request.POST.get('pin_code', '110001').strip() or '110001'
            is_approved = 'is_approved' in request.POST or request.POST.get('is_approved') == '1'

            if not store_name:
                messages.error(request, "Please enter a valid store name.")
                return redirect('admin_dashboard:sellers')

            if not username:
                messages.error(request, "Please enter a valid username.")
                return redirect('admin_dashboard:sellers')

            if User.objects.filter(username__iexact=username).exists():
                messages.error(request, f"Username '{username}' is already taken. Please choose another username.")
                return redirect('admin_dashboard:sellers')

            if email and User.objects.filter(email__iexact=email).exists():
                messages.error(request, f"An account with email '{email}' already exists.")
                return redirect('admin_dashboard:sellers')

            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    phone_number=phone,
                    password=password,
                    role=User.Role.SELLER,
                    account_status=User.Status.ACTIVE if is_approved else User.Status.PENDING_APPROVAL
                )
                slug = slugify(store_name)
                if not slug:
                    slug = f"store-{user.id}"
                if SellerProfile.objects.filter(store_slug=slug).exists():
                    slug = f"{slug}-{user.id}"

                profile = SellerProfile.objects.create(
                    user=user,
                    store_name=store_name,
                    store_slug=slug,
                    store_phone=phone or user.phone_number,
                    store_email=email or user.email,
                    pickup_address=pickup_address or 'Store Counter Pickup Address',
                    city=city,
                    pin_code=pin_code,
                    is_approved=is_approved
                )
                messages.success(request, f"Seller store '{store_name}' created successfully with Account ID {profile.seller_account_id} (Username: {username})!")
                return redirect('admin_dashboard:sellers')

        # Edit Existing Seller
        if action == 'edit_seller':
            profile_id = request.POST.get('profile_id')
            profile = get_object_or_404(SellerProfile, id=profile_id)
            user = profile.user

            store_name = request.POST.get('store_name', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            phone = request.POST.get('phone_number', '').strip()
            pickup_address = request.POST.get('pickup_address', '').strip()
            city = request.POST.get('city', 'Central City').strip() or 'Central City'
            pin_code = request.POST.get('pin_code', '110001').strip() or '110001'
            new_password = request.POST.get('password', '').strip()
            is_approved = 'is_approved' in request.POST or request.POST.get('is_approved') == '1'

            if not store_name:
                messages.error(request, "Please enter a valid store name.")
                return redirect('admin_dashboard:sellers')

            if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                messages.error(request, f"An account with email '{email}' already exists.")
                return redirect('admin_dashboard:sellers')

            with transaction.atomic():
                user.first_name = first_name
                user.last_name = last_name
                user.email = email
                user.phone_number = phone
                if is_approved:
                    user.account_status = User.Status.ACTIVE
                else:
                    user.account_status = User.Status.SUSPENDED
                if new_password:
                    user.set_password(new_password)
                user.save()

                profile.store_name = store_name
                profile.store_phone = phone or user.phone_number
                profile.store_email = email or user.email
                profile.pickup_address = pickup_address or profile.pickup_address
                profile.city = city
                profile.pin_code = pin_code
                profile.is_approved = is_approved
                profile.save()

            messages.success(request, f"Seller store '{store_name}' updated successfully!")
            return redirect('admin_dashboard:sellers')

        profile_id = request.POST.get('profile_id')
        profile = get_object_or_404(SellerProfile, id=profile_id)

        if action == 'approve':
            profile.is_approved = True
            profile.user.account_status = User.Status.ACTIVE
            profile.user.save()
            profile.save()
            Notification.send(
                user=profile.user,
                title="Seller Application Approved!",
                message="Your store has been approved by platform administration. You can now publish products.",
                category=Notification.Category.ACCOUNT,
                link='/seller/dashboard/'
            )
            messages.success(request, f"Store '{profile.store_name}' approved successfully.")
        elif action == 'suspend':
            profile.is_approved = False
            profile.user.account_status = User.Status.SUSPENDED
            profile.user.save()
            profile.save()
            messages.warning(request, f"Store '{profile.store_name}' suspended.")
        return redirect('admin_dashboard:sellers')

    return render(request, 'admin_dashboard/sellers.html', {'sellers': sellers})


@role_required(['ADMIN'])
def admin_delivery_view(request):
    """List delivery personnel and create new delivery accounts."""
    drivers = DeliveryProfile.objects.select_related('user').order_by('-created_at')

    if request.method == 'POST':
        # Create a new delivery agent account
        username = request.POST.get('username')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        phone = request.POST.get('phone_number')
        password = request.POST.get('password')
        vehicle_type = request.POST.get('vehicle_type', 'BIKE')
        vehicle_number = request.POST.get('vehicle_number', '')

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already in use.")
        else:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    phone_number=phone,
                    password=password,
                    role=User.Role.DELIVERY,
                    account_status=User.Status.ACTIVE
                )
                DeliveryProfile.objects.create(
                    user=user,
                    vehicle_type=vehicle_type,
                    vehicle_number=vehicle_number,
                    current_status='AVAILABLE'
                )
                messages.success(request, f"Created delivery account for {username}!")
                return redirect('admin_dashboard:delivery_team')

    return render(request, 'admin_dashboard/delivery_team.html', {'drivers': drivers})


@role_required(['ADMIN'])
def admin_products_view(request):
    """Admin platform-wide view of all products across sellers with filtering, searching, and metric totals."""
    products_qs = Product.objects.select_related('category', 'seller', 'seller__seller_profile', 'stock_record').order_by('-created_at')

    # Platform metric totals
    total_products = Product.objects.count()
    active_products = Product.objects.filter(is_active=True).count()
    inactive_products = Product.objects.filter(is_active=False).count()
    pending_approval_products = Product.objects.filter(approval_status=Product.ApprovalStatus.PENDING).count()
    in_stock_products = Product.objects.filter(stock_record__current_stock__gt=0).count()
    low_stock_products = Product.objects.filter(
        stock_record__current_stock__gt=0,
        stock_record__current_stock__lte=F('stock_record__low_stock_threshold')
    ).count()
    out_of_stock_products = Product.objects.filter(stock_record__current_stock__lte=0).count()
    total_sellers = User.objects.filter(role=User.Role.SELLER).count()

    # Search filter
    search_query = request.GET.get('q', '').strip()
    if search_query:
        if search_query.isdigit():
            products_qs = products_qs.filter(Q(id=int(search_query)) | Q(name__icontains=search_query) | Q(sku__icontains=search_query))
        else:
            products_qs = products_qs.filter(Q(name__icontains=search_query) | Q(sku__icontains=search_query) | Q(brand__icontains=search_query))

    # Seller filter
    seller_id = request.GET.get('seller_id', '').strip()
    selected_seller = None
    if seller_id and seller_id.isdigit():
        products_qs = products_qs.filter(seller_id=int(seller_id))
        selected_seller = User.objects.filter(id=int(seller_id)).first()

    # Category filter
    category_id = request.GET.get('category_id', '').strip()
    if category_id and category_id.isdigit():
        products_qs = products_qs.filter(category_id=int(category_id))

    # Status filter
    status_filter = request.GET.get('status', '').strip()
    if status_filter == 'active':
        products_qs = products_qs.filter(is_active=True)
    elif status_filter == 'inactive':
        products_qs = products_qs.filter(is_active=False)
    elif status_filter == 'in_stock':
        products_qs = products_qs.filter(stock_record__current_stock__gt=0)
    elif status_filter == 'low_stock':
        products_qs = products_qs.filter(
            stock_record__current_stock__gt=0,
            stock_record__current_stock__lte=F('stock_record__low_stock_threshold')
        )
    elif status_filter == 'out_of_stock':
        products_qs = products_qs.filter(stock_record__current_stock__lte=0)
    elif status_filter == 'pending_approval':
        products_qs = products_qs.filter(approval_status=Product.ApprovalStatus.PENDING)
    elif status_filter == 'rejected':
        products_qs = products_qs.filter(approval_status=Product.ApprovalStatus.REJECTED)
    elif status_filter == 'suspended':
        products_qs = products_qs.filter(approval_status=Product.ApprovalStatus.SUSPENDED)

    if request.method == 'POST':
        action = request.POST.get('action')
        product_id = request.POST.get('product_id')
        prod = get_object_or_404(Product, id=product_id)

        if action == 'adjust_stock':
            try:
                new_stock_val = request.POST.get('new_stock')
                if new_stock_val is not None and new_stock_val != '':
                    new_stk = int(new_stock_val)
                    if new_stk < 0:
                        messages.error(request, "Stock quantity cannot be negative.")
                        return redirect(request.get_full_path())
                    current_stk = prod.current_stock_count
                    qty_change = new_stk - current_stk
                else:
                    qty_change = int(request.POST.get('quantity_change', 0))
                reason = request.POST.get('reason', 'Admin Inventory Adjustment').strip() or 'Admin Inventory Adjustment'
                prod.stock_record.adjust_stock(
                    quantity_change=qty_change,
                    change_type=StockMovement.ChangeType.MANUAL,
                    user=request.user,
                    notes=f"Admin {request.user.username}: {reason}"
                )
                unit_label = prod.get_unit_label(abs(qty_change))
                messages.success(request, f"Updated stock for '{prod.name}' by {qty_change:+d} {unit_label}.")
            except Exception as e:
                messages.error(request, f"Could not adjust stock: {e}")
            return redirect(request.get_full_path())
        elif action == 'set_order_limit':
            limit_val = request.POST.get('max_quantity_per_order', '').strip()
            if limit_val == '' or limit_val.lower() == 'default':
                prod.max_quantity_per_order = None
                prod.save(update_fields=['max_quantity_per_order'])
                messages.success(request, f"Reset per-order limit for '{prod.name}' to unit default ({prod.per_order_limit} {prod.get_unit_label(prod.per_order_limit)}).")
            else:
                try:
                    new_limit = int(limit_val)
                    if new_limit <= 0:
                        messages.error(request, "Maximum quantity per order must be greater than zero.")
                    else:
                        prod.max_quantity_per_order = new_limit
                        prod.save(update_fields=['max_quantity_per_order'])
                        messages.success(request, f"Updated maximum per order for '{prod.name}' to {new_limit} {prod.get_unit_label(new_limit)}.")
                except (ValueError, TypeError):
                    messages.error(request, "Please enter a valid whole number for maximum quantity per order.")
            return redirect(request.get_full_path())
        elif action == 'approve_product':
            try:
                # Validate pricing before approval: approved products must satisfy MRP > Selling Price > Product Cost
                if prod.mrp is not None and prod.selling_price is not None and prod.mrp <= prod.selling_price:
                    messages.error(request, f"Cannot approve '{prod.name}': MRP (₹{prod.mrp}) must be greater than Selling Price (₹{prod.selling_price}).")
                    return redirect(request.get_full_path())
                if prod.selling_price is not None and prod.product_cost is not None and prod.selling_price <= prod.product_cost:
                    messages.error(request, f"Cannot approve '{prod.name}': Selling Price (₹{prod.selling_price}) must be greater than Product Cost (₹{prod.product_cost}).")
                    return redirect(request.get_full_path())

                from apps.reports.models import ProductApprovalHistory
                prev = prod.approval_status
                prod.approval_status = Product.ApprovalStatus.APPROVED
                prod.is_approved = True
                prod.rejection_reason = ''
                prod.save(update_fields=['approval_status', 'is_approved', 'rejection_reason', 'updated_at'])
                ProductApprovalHistory.objects.create(
                    product=prod, action='APPROVED', admin_user=request.user,
                    reason='Approved by admin.', previous_status=prev, new_status='APPROVED'
                )
                Notification.send(
                    user=prod.seller, title=f"Product Approved: {prod.name}",
                    message=f"Your product '{prod.name}' has been approved and is now visible to customers.",
                    category=Notification.Category.ACCOUNT, link=f"/seller/products/edit/{prod.id}/"
                )
                messages.success(request, f"Product '{prod.name}' approved.")
            except Exception as e:
                messages.error(request, f"Could not approve product: {e}")
            return redirect(request.get_full_path())

        elif action == 'reject_product':
            try:
                from apps.reports.models import ProductApprovalHistory
                reason = request.POST.get('rejection_reason', 'Rejected by admin.').strip() or 'Rejected by admin.'
                prev = prod.approval_status
                prod.approval_status = Product.ApprovalStatus.REJECTED
                prod.is_approved = False
                prod.is_active = False
                prod.rejection_reason = reason
                prod.save(update_fields=['approval_status', 'is_approved', 'is_active', 'rejection_reason', 'updated_at'])
                ProductApprovalHistory.objects.create(
                    product=prod, action='REJECTED', admin_user=request.user,
                    reason=reason, previous_status=prev, new_status='REJECTED'
                )
                Notification.send(
                    user=prod.seller, title=f"Product Rejected: {prod.name}",
                    message=f"Your product '{prod.name}' was rejected. Reason: {reason}",
                    category=Notification.Category.ACCOUNT, link=f"/seller/products/edit/{prod.id}/"
                )
                messages.warning(request, f"Product '{prod.name}' rejected.")
            except Exception as e:
                messages.error(request, f"Could not reject product: {e}")
            return redirect(request.get_full_path())

        elif action == 'suspend_product':
            try:
                from apps.reports.models import ProductApprovalHistory
                reason = request.POST.get('rejection_reason', 'Suspended by admin.').strip() or 'Suspended by admin.'
                prev = prod.approval_status
                prod.approval_status = Product.ApprovalStatus.SUSPENDED
                prod.is_approved = False
                prod.is_active = False
                prod.rejection_reason = reason
                prod.save(update_fields=['approval_status', 'is_approved', 'is_active', 'rejection_reason', 'updated_at'])
                ProductApprovalHistory.objects.create(
                    product=prod, action='SUSPENDED', admin_user=request.user,
                    reason=reason, previous_status=prev, new_status='SUSPENDED'
                )
                Notification.send(
                    user=prod.seller, title=f"Product Suspended: {prod.name}",
                    message=f"Your product '{prod.name}' was suspended. Reason: {reason}",
                    category=Notification.Category.ACCOUNT, link=f"/seller/products/edit/{prod.id}/"
                )
                messages.warning(request, f"Product '{prod.name}' suspended.")
            except Exception as e:
                messages.error(request, f"Could not suspend product: {e}")
            return redirect(request.get_full_path())

        else:
            try:
                prod.is_active = not prod.is_active
                prod.save(update_fields=['is_active'])
                status_name = "Activated" if prod.is_active else "Deactivated"
                messages.info(request, f"{prod.name} has been {status_name}.")
            except Exception as e:
                messages.error(request, f"Could not update status: {e}")
            return redirect(request.get_full_path())

    all_sellers = SellerProfile.objects.select_related('user').all().order_by('store_name')
    all_categories = Category.objects.filter(is_active=True).order_by('name')

    return render(request, 'admin_dashboard/products.html', {
        'products': products_qs,
        'total_products': total_products,
        'active_products': active_products,
        'inactive_products': inactive_products,
        'pending_approval_products': pending_approval_products,
        'in_stock_products': in_stock_products,
        'low_stock_products': low_stock_products,
        'out_of_stock_products': out_of_stock_products,
        'total_sellers': total_sellers,
        'all_sellers': all_sellers,
        'all_categories': all_categories,
        'search_query': search_query,
        'selected_seller_id': seller_id,
        'selected_seller': selected_seller,
        'selected_category_id': category_id,
        'status_filter': status_filter,
    })


@role_required(['ADMIN'])
def admin_categories_view(request):
    """View, add, edit, and delete grocery categories."""
    categories = Category.objects.all().order_by('sort_order', 'name')

    if request.method == 'POST':
        action = request.POST.get('action')

        # Delete Category Action
        if action == 'delete_category':
            category_id = request.POST.get('category_id')
            cat = get_object_or_404(Category, id=category_id)
            product_count = cat.products.count()
            if product_count > 0:
                messages.error(request, f"Cannot delete category '{cat.name}' because it contains {product_count} product(s). Please reassign or delete these products first, or mark this category as Inactive.")
                return redirect('admin_dashboard:categories')
            try:
                cat_name = cat.name
                cat.delete()
                messages.success(request, f"Category '{cat_name}' deleted successfully.")
            except Exception as e:
                messages.error(request, f"Could not delete category: {e}")
            return redirect('admin_dashboard:categories')

        # Edit Category Action
        if action == 'edit_category':
            category_id = request.POST.get('category_id')
            cat = get_object_or_404(Category, id=category_id)
            name = request.POST.get('name', '').strip()
            icon = request.POST.get('icon', 'fa-basket-shopping').strip() or 'fa-basket-shopping'
            description = request.POST.get('description', '').strip()
            is_active = 'is_active' in request.POST or request.POST.get('is_active') == '1'

            if not name:
                messages.error(request, "Please enter a valid category name.")
                return redirect('admin_dashboard:categories')

            if Category.objects.filter(name__iexact=name).exclude(pk=cat.pk).exists():
                messages.error(request, f"A category with name '{name}' already exists.")
                return redirect('admin_dashboard:categories')

            cat.name = name
            cat.slug = slugify(name)
            cat.icon = icon
            cat.description = description
            cat.is_active = is_active
            cat.save()
            messages.success(request, f"Category '{name}' updated successfully.")
            return redirect('admin_dashboard:categories')

        # Create Category Action (Default)
        name = request.POST.get('name', '').strip()
        icon = request.POST.get('icon', 'fa-basket-shopping').strip() or 'fa-basket-shopping'
        description = request.POST.get('description', '').strip()
        if name:
            try:
                Category.objects.create(name=name, icon=icon, description=description)
                messages.success(request, f"Category '{name}' created successfully.")
            except Exception:
                messages.error(request, f"A category with the name '{name}' already exists.")
            return redirect('admin_dashboard:categories')

    return render(request, 'admin_dashboard/categories.html', {'categories': categories})


@role_required(['ADMIN'])
def admin_reports_view(request):
    """Financial & sales reports summary."""
    orders_qs = Order.objects.exclude(status__in=['CANCELLED', 'REFUNDED'])

    gross_sales = orders_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
    total_orders = orders_qs.count()
    cancelled_count = Order.objects.filter(status='CANCELLED').count()
    delivered_count = Order.objects.filter(status='DELIVERED').count()

    # Seller breakdown
    seller_sales = SellerSubOrder.objects.exclude(status__in=['REJECTED', 'CANCELLED']).values('seller__username', 'seller__seller_profile__store_name').annotate(
        total_subtotal=Sum('subtotal'),
        order_count=Count('id')
    ).order_by('-total_subtotal')

    return render(request, 'admin_dashboard/reports.html', {
        'gross_sales': gross_sales,
        'total_orders': total_orders,
        'cancelled_count': cancelled_count,
        'delivered_count': delivered_count,
        'seller_sales': seller_sales,
    })


@role_required(['ADMIN'])
def export_sales_csv(request):
    """Export orders and sales metrics to CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="freshmart_sales_{timezone.now().strftime("%Y%m%d")}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Order ID', 'Date', 'Customer', 'Phone', 'Slot', 'Payment Method', 'Payment Status', 'Status', 'Total Amount (INR)'])

    orders = Order.objects.all().order_by('-created_at')
    for o in orders:
        writer.writerow([
            o.order_id,
            o.created_at.strftime('%Y-%m-%d %H:%M'),
            o.recipient_name,
            o.recipient_phone,
            o.delivery_slot,
            o.get_payment_method_display(),
            o.get_payment_status_display(),
            o.get_status_display(),
            f"{o.total_amount:.2f}"
        ])

    return response


@role_required(['ADMIN'])
def admin_marketplace_config_view(request):
    """View and edit marketplace configuration (singleton)."""
    from apps.reports.models import MarketplaceConfig, MarketplaceConfigHistory

    config = MarketplaceConfig.get_config()

    if request.method == 'POST':
        field_map = {
            'commission_percentage': 'commission_percentage',
            'commission_fixed_per_unit': 'commission_fixed_per_unit',
            'commission_base': 'commission_base',
            'cashback_per_unit': 'cashback_per_unit',
            'cashback_funding': 'cashback_funding',
            'delivery_fee_default': 'delivery_fee_default',
            'delivery_fee_free_above': 'delivery_fee_free_above',
            'delivery_person_earning_per_order': 'delivery_person_earning_per_order',
            'return_period_days': 'return_period_days',
        }
        boolean_fields = ['seller_settlement_require_delivery', 'seller_settlement_require_payment']
        changes = []

        with transaction.atomic():
            for post_key, model_field in field_map.items():
                new_val = request.POST.get(post_key, '').strip()
                if not new_val:
                    continue
                old_val = str(getattr(config, model_field))
                if model_field in ['commission_base', 'cashback_funding']:
                    if new_val != old_val:
                        MarketplaceConfigHistory.objects.create(
                            changed_by=request.user, field_name=model_field,
                            old_value=old_val, new_value=new_val
                        )
                        setattr(config, model_field, new_val)
                        changes.append(model_field)
                elif model_field == 'return_period_days':
                    try:
                        new_int = int(new_val)
                        if str(getattr(config, model_field)) != str(new_int):
                            MarketplaceConfigHistory.objects.create(
                                changed_by=request.user, field_name=model_field,
                                old_value=old_val, new_value=str(new_int)
                            )
                            setattr(config, model_field, new_int)
                            changes.append(model_field)
                    except (ValueError, TypeError):
                        pass
                else:
                    try:
                        new_dec = Decimal(new_val)
                        if str(getattr(config, model_field)) != str(new_dec):
                            MarketplaceConfigHistory.objects.create(
                                changed_by=request.user, field_name=model_field,
                                old_value=old_val, new_value=str(new_dec)
                            )
                            setattr(config, model_field, new_dec)
                            changes.append(model_field)
                    except Exception:
                        pass

            for bf in boolean_fields:
                new_bool = bf in request.POST
                old_bool = getattr(config, bf)
                if new_bool != old_bool:
                    MarketplaceConfigHistory.objects.create(
                        changed_by=request.user, field_name=bf,
                        old_value=str(old_bool), new_value=str(new_bool)
                    )
                    setattr(config, bf, new_bool)
                    changes.append(bf)

            if changes:
                config.updated_by = request.user
                config.save()
                messages.success(request, f"Marketplace settings updated: {', '.join(changes)}")
            else:
                messages.info(request, "No settings were changed.")

        return redirect('admin_dashboard:marketplace_config')

    history = MarketplaceConfigHistory.objects.all()[:20]
    return render(request, 'admin_dashboard/marketplace_config.html', {
        'config': config,
        'history': history,
    })


@role_required(['ADMIN'])
def admin_seller_wallets_view(request):
    """Overview of all seller wallets and their balances."""
    from apps.reports.models import SellerWallet, SellerWalletTransaction, SellerPayout

    wallets = SellerWallet.objects.select_related('seller', 'seller__seller_profile').order_by('-available_balance')

    total_available = wallets.aggregate(t=Sum('available_balance'))['t'] or Decimal('0.00')
    total_pending = wallets.aggregate(t=Sum('pending_earnings'))['t'] or Decimal('0.00')
    total_lifetime = wallets.aggregate(t=Sum('lifetime_credited'))['t'] or Decimal('0.00')
    total_withdrawn = wallets.aggregate(t=Sum('total_withdrawn'))['t'] or Decimal('0.00')

    # Payout requests
    payouts = SellerPayout.objects.select_related('seller', 'seller__seller_profile').order_by('-requested_at')[:20]

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve_payout':
            payout_id = request.POST.get('payout_id')
            payout = get_object_or_404(SellerPayout, id=payout_id, status='REQUESTED')
            with transaction.atomic():
                wallet = payout.wallet
                wallet = type(wallet).objects.select_for_update().get(pk=wallet.pk)
                if wallet.available_balance < payout.amount:
                    messages.error(request, f"Insufficient wallet balance for payout (Available: ₹{wallet.available_balance}).")
                    return redirect('admin_dashboard:seller_wallets')

                balance_before = wallet.available_balance
                balance_after = balance_before - payout.amount
                SellerWalletTransaction.objects.create(
                    wallet=wallet, seller=payout.seller, order=None,
                    transaction_type='WITHDRAWAL_CONFIRMED',
                    amount=-payout.amount,
                    balance_before=balance_before, balance_after=balance_after,
                    status='COMPLETED',
                    description=f"Withdrawal payout approved by admin {request.user.username}",
                    idempotency_key=f"PAYOUT-{payout.id}-APPROVED",
                    settled_at=timezone.now(),
                )
                wallet.available_balance = balance_after
                wallet.total_withdrawn += payout.amount
                wallet.save()
                payout.status = 'PAID'
                payout.approved_by = request.user
                payout.processed_at = timezone.now()
                payout.save()
                messages.success(request, f"Payout of ₹{payout.amount} approved for {payout.seller.username}.")
            return redirect('admin_dashboard:seller_wallets')

        elif action == 'reject_payout':
            payout_id = request.POST.get('payout_id')
            payout = get_object_or_404(SellerPayout, id=payout_id, status='REQUESTED')
            payout.status = 'REJECTED'
            payout.approved_by = request.user
            payout.processed_at = timezone.now()
            payout.description = request.POST.get('reason', 'Rejected by admin.')
            payout.save()
            messages.warning(request, f"Payout rejected for {payout.seller.username}.")
            return redirect('admin_dashboard:seller_wallets')

    return render(request, 'admin_dashboard/seller_wallets.html', {
        'wallets': wallets,
        'total_available': total_available,
        'total_pending': total_pending,
        'total_lifetime': total_lifetime,
        'total_withdrawn': total_withdrawn,
        'payouts': payouts,
    })


@role_required(['ADMIN'])
def admin_delivery_earnings_view(request):
    """Overview of all delivery earnings and payouts."""
    from apps.reports.models import DeliveryEarning, DeliveryPayout

    earnings = DeliveryEarning.objects.select_related('delivery_person', 'order').order_by('-created_at')[:50]

    total_earnings = DeliveryEarning.objects.filter(status='SETTLED').aggregate(
        t=Sum('earning_amount'))['t'] or Decimal('0.00')
    total_pending_earnings = DeliveryEarning.objects.filter(status='PENDING').aggregate(
        t=Sum('earning_amount'))['t'] or Decimal('0.00')

    payouts = DeliveryPayout.objects.select_related('delivery_person').order_by('-requested_at')[:20]

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve_delivery_payout':
            payout_id = request.POST.get('payout_id')
            payout = get_object_or_404(DeliveryPayout, id=payout_id, status='REQUESTED')
            payout.status = 'PAID'
            payout.approved_by = request.user
            payout.processed_at = timezone.now()
            payout.save()
            messages.success(request, f"Delivery payout of ₹{payout.amount} approved for {payout.delivery_person.username}.")
            return redirect('admin_dashboard:delivery_earnings')

    return render(request, 'admin_dashboard/delivery_earnings.html', {
        'earnings': earnings,
        'total_earnings': total_earnings,
        'total_pending_earnings': total_pending_earnings,
        'payouts': payouts,
    })


@role_required(['ADMIN'])
def admin_company_pnl_view(request):
    """
    Comprehensive dynamic Company Profit & Loss and Marketplace Accounting Dashboard.
    Supports:
    - Global Date Filtering: All Time, Today, This Month, This Year, Custom Date Range
    - 18 Standard Financial Summary KPIs with explicit formula tracking
    - Third-party merchant commission vs Company-owned inventory separation
    - Delivery economics (fees collected vs fleet payouts)
    - Multi-source cashback attribution (Company vs Seller funded)
    - Full item-level marketplace pricing and financial audit table
    - Escrow liability isolation (Seller wallets, pending earnings, buyer wallet liability)
    """
    from apps.reports.models import (
        OrderItemFinancialSnapshot, DeliveryEarning, CashbackLedger,
        SellerWallet, MarketplaceConfig
    )
    from django.core.paginator import Paginator

    config = MarketplaceConfig.get_config()
    today = timezone.now().date()

    # 1. Global Date Filters
    period = request.GET.get('period', 'all')
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()

    order_date_filter = {}
    snapshot_date_filter = {}
    delivery_date_filter = {}
    cashback_date_filter = {}
    refund_date_filter = {}

    date_label = "All Time"

    if date_from_str or date_to_str:
        period = 'custom'
        if date_from_str:
            order_date_filter['created_at__date__gte'] = date_from_str
            snapshot_date_filter['created_at__date__gte'] = date_from_str
            delivery_date_filter['created_at__date__gte'] = date_from_str
            cashback_date_filter['created_at__date__gte'] = date_from_str
            refund_date_filter['created_at__date__gte'] = date_from_str
        if date_to_str:
            order_date_filter['created_at__date__lte'] = date_to_str
            snapshot_date_filter['created_at__date__lte'] = date_to_str
            delivery_date_filter['created_at__date__lte'] = date_to_str
            cashback_date_filter['created_at__date__lte'] = date_to_str
            refund_date_filter['created_at__date__lte'] = date_to_str
        date_label = f"Custom ({date_from_str or 'Earliest'} to {date_to_str or 'Latest'})"
    elif period == 'today':
        order_date_filter = {'created_at__date': today}
        snapshot_date_filter = {'created_at__date': today}
        delivery_date_filter = {'created_at__date': today}
        cashback_date_filter = {'created_at__date': today}
        refund_date_filter = {'created_at__date': today}
        date_label = f"Today ({today.strftime('%d %b %Y')})"
    elif period == 'month':
        month_start = today.replace(day=1)
        order_date_filter = {'created_at__date__gte': month_start}
        snapshot_date_filter = {'created_at__date__gte': month_start}
        delivery_date_filter = {'created_at__date__gte': month_start}
        cashback_date_filter = {'created_at__date__gte': month_start}
        refund_date_filter = {'created_at__date__gte': month_start}
        date_label = f"This Month ({today.strftime('%B %Y')})"
    elif period == 'year':
        order_date_filter = {'created_at__date__year': today.year}
        snapshot_date_filter = {'created_at__date__year': today.year}
        delivery_date_filter = {'created_at__date__year': today.year}
        cashback_date_filter = {'created_at__date__year': today.year}
        refund_date_filter = {'created_at__date__year': today.year}
        date_label = f"This Year ({today.year})"

    # 2. Orders & GMV
    period_orders_qs = Order.objects.filter(**order_date_filter).select_related(
        'buyer', 'delivery_assignment', 'delivery_assignment__delivery_person'
    ).prefetch_related(
        'items',
        'items__seller',
        'items__seller__seller_profile',
        'financial_snapshots',
        'financial_snapshots__order_item',
        'financial_snapshots__seller',
        'financial_snapshots__seller__seller_profile',
        'delivery_earnings',
        'cashback_entries',
    ).order_by('-created_at')

    # Ensure all orders in period have immutable financial snapshots
    for ord_obj in period_orders_qs:
        if not ord_obj.financial_snapshots.exists():
            from apps.reports.services import create_order_financial_snapshots
            create_order_financial_snapshots(ord_obj)

    period_orders = Order.objects.filter(**order_date_filter)
    delivered_orders = period_orders.filter(status=Order.Status.DELIVERED)
    total_gmv = period_orders.exclude(status__in=[Order.Status.CANCELLED, Order.Status.REFUNDED]).aggregate(
        t=Sum('total_amount'))['t'] or Decimal('0.00')

    total_orders_count = period_orders.count()
    delivered_orders_count = delivered_orders.count()

    # 3. Product Sales & Customer Discounts
    period_snapshots = OrderItemFinancialSnapshot.objects.filter(**snapshot_date_filter)
    total_customer_product_sales = period_snapshots.aggregate(
        t=Sum('product_revenue'))['t'] or Decimal('0.00')
    total_customer_discounts = period_snapshots.aggregate(
        t=Sum('discount_amount'))['t'] or Decimal('0.00')

    # 4. Third-Party Marketplace Commission vs Company-Owned Product Revenue
    # Company products: seller is ADMIN role or superuser
    settled_snapshots = period_snapshots.filter(settlement_status='SETTLED')
    
    # 3rd-party marketplace commission
    third_party_settled = settled_snapshots.filter(seller__role=User.Role.SELLER)
    marketplace_commission_revenue = third_party_settled.aggregate(
        t=Sum('commission_amount'))['t'] or Decimal('0.00')

    # Company-owned product sales & COGS (No artificial commission charged)
    company_owned_settled = settled_snapshots.filter(Q(seller__role=User.Role.ADMIN) | Q(seller__is_superuser=True))
    company_owned_revenue = company_owned_settled.aggregate(
        t=Sum('product_revenue'))['t'] or Decimal('0.00')
    company_owned_cogs = company_owned_settled.filter(product_cost_total__isnull=False).aggregate(
        t=Sum('product_cost_total'))['t'] or Decimal('0.00')

    # 5. Delivery Economics
    delivery_fees_charged = delivered_orders.aggregate(
        t=Sum('delivery_fee'))['t'] or Decimal('0.00')

    delivery_settled_earnings = DeliveryEarning.objects.filter(
        status='SETTLED', **delivery_date_filter
    )
    delivery_payout_expenses = delivery_settled_earnings.aggregate(
        t=Sum('earning_amount'))['t'] or Decimal('0.00')
    company_delivery_margin = delivery_fees_charged - delivery_payout_expenses

    # 6. Cashback Accounting & Multi-source Attribution
    cashback_entries = CashbackLedger.objects.filter(**cashback_date_filter)
    
    # Company-funded cashback recognized as expense
    company_cashback_expense = cashback_entries.filter(
        entry_type='EARNED', status='CONFIRMED', funding_source='COMPANY'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    seller_funded_cashback = cashback_entries.filter(
        entry_type='EARNED', status='CONFIRMED', funding_source='SELLER'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    total_cashback_earned = cashback_entries.filter(
        entry_type='EARNED', status='CONFIRMED'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    total_cashback_redeemed = abs(cashback_entries.filter(
        entry_type='REDEEMED', status='CONFIRMED'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00'))

    total_cashback_reversed = abs(cashback_entries.filter(
        entry_type='REVERSED', status='CONFIRMED'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00'))

    pending_cashback_total = OrderItemFinancialSnapshot.objects.filter(
        settlement_status='PENDING',
        order__status__in=[
            Order.Status.ORDER_PLACED, Order.Status.ORDER_CONFIRMED,
            Order.Status.PREPARING, Order.Status.PACKED, Order.Status.READY_FOR_PICKUP,
            Order.Status.OUT_FOR_DELIVERY
        ]
    ).aggregate(t=Sum('cashback_total'))['t'] or Decimal('0.00')

    # 7. Gateway fees, Operating Expenses, and Refunds
    gateway_fees = Decimal('0.00')
    other_operating_expenses = Decimal('0.00')
    refunds_returns = period_orders.filter(status=Order.Status.REFUNDED).aggregate(
        t=Sum('total_amount'))['t'] or Decimal('0.00')

    # 8. Net Company Profit Calculation Formula
    company_revenue_total = marketplace_commission_revenue + company_owned_revenue + delivery_fees_charged
    company_expenses_total = (
        delivery_payout_expenses +
        company_cashback_expense +
        company_owned_cogs +
        refunds_returns +
        gateway_fees +
        other_operating_expenses
    )
    net_company_profit = company_revenue_total - company_expenses_total

    # 9. Liabilities & Escrow (CRITICAL: NOT company profit)
    pending_seller_liabilities = OrderItemFinancialSnapshot.objects.filter(
        settlement_status='PENDING'
    ).aggregate(t=Sum('seller_payable'))['t'] or Decimal('0.00')

    available_seller_wallet_balances = SellerWallet.objects.aggregate(
        t=Sum('available_balance'))['t'] or Decimal('0.00')

    pending_seller_earnings = SellerWallet.objects.aggregate(
        t=Sum('pending_earnings'))['t'] or Decimal('0.00')

    unsettled_delivery_earnings = DeliveryEarning.objects.filter(
        status='PENDING'
    ).aggregate(t=Sum('earning_amount'))['t'] or Decimal('0.00')

    customer_cashback_liability = CashbackLedger.objects.filter(
        status='CONFIRMED'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    # 10. Order-wise Profit Breakdown & Dynamic Calculations
    order_profit_rows = []
    total_completed_company_profit = Decimal('0.00')
    total_completed_seller_profit = Decimal('0.00')
    completed_orders_with_cost_count = 0

    for ord_obj in period_orders_qs:
        snaps = list(ord_obj.financial_snapshots.all())

        # Product Revenue & Costs
        order_prod_revenue = sum(s.product_revenue for s in snaps) if snaps else ord_obj.subtotal
        comm_amount = sum(s.commission_amount for s in snaps)
        seller_payable = sum(s.seller_payable for s in snaps)

        # Cost audit completeness
        has_cost_data = all(s.product_cost_per_unit is not None for s in snaps) if snaps else False
        total_product_cost = sum(s.product_cost_total for s in snaps if s.product_cost_total is not None) if has_cost_data else None

        # Seller profit calculation (Revenue - Commission - Product Cost = Seller Payable - Product Cost)
        if has_cost_data and total_product_cost is not None:
            order_seller_profit = (seller_payable - total_product_cost).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            order_seller_profit = None

        # Delivery economics
        del_fee = ord_obj.delivery_fee
        del_earning = next((de for de in ord_obj.delivery_earnings.all() if de.status in ['SETTLED', 'PENDING']), None)
        rider_payout = del_earning.earning_amount if del_earning else Decimal('0.00')

        # Company-funded cashback
        company_cb = sum(
            cb.amount for cb in ord_obj.cashback_entries.all()
            if cb.entry_type == 'EARNED' and cb.status == 'CONFIRMED' and cb.funding_source == 'COMPANY'
        )
        if not company_cb and ord_obj.status == Order.Status.DELIVERED:
            company_cb = sum(s.cashback_total for s in snaps if s.cashback_funding == 'COMPANY')

        # Company-owned product COGS & Revenue
        company_owned_rev = sum(s.product_revenue for s in snaps if s.seller.role != User.Role.SELLER or s.seller.is_superuser)
        company_owned_cost = sum(s.product_cost_total for s in snaps if (s.seller.role != User.Role.SELLER or s.seller.is_superuser) and s.product_cost_total is not None)

        # Net Company Profit for this order
        comp_rev = comm_amount + company_owned_rev + del_fee
        comp_exp = rider_payout + company_cb + company_owned_cost
        comp_profit = (comp_rev - comp_exp).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # Store names
        store_names = sorted(set(
            s.seller.seller_profile.store_name if hasattr(s.seller, 'seller_profile') and s.seller.seller_profile and s.seller.seller_profile.store_name else s.seller.username
            for s in snaps
        ))
        seller_display = ", ".join(store_names) if store_names else "FreshMart Direct"

        # Settlement status
        if snaps and all(s.settlement_status == 'SETTLED' for s in snaps):
            order_settlement = 'SETTLED'
        elif any(s.settlement_status == 'REVERSED' for s in snaps):
            order_settlement = 'REVERSED'
        elif any(s.settlement_status == 'CANCELLED' for s in snaps):
            order_settlement = 'CANCELLED'
        else:
            order_settlement = 'PENDING'

        is_completed = (ord_obj.status == Order.Status.DELIVERED)
        if is_completed:
            total_completed_company_profit += comp_profit
            if order_seller_profit is not None:
                total_completed_seller_profit += order_seller_profit
                completed_orders_with_cost_count += 1

        order_profit_rows.append({
            'order': ord_obj,
            'order_id': ord_obj.order_id,
            'created_at': ord_obj.created_at,
            'status': ord_obj.status,
            'status_display': ord_obj.get_status_display(),
            'status_badge_class': ord_obj.status_badge_class,
            'is_completed': is_completed,
            'seller_display': seller_display,
            'order_revenue': order_prod_revenue,
            'has_cost_data': has_cost_data,
            'product_cost': total_product_cost,
            'company_commission': comm_amount,
            'delivery_revenue': del_fee,
            'rider_payout': rider_payout,
            'company_cashback': company_cb,
            'company_expenses': comp_exp,
            'company_revenue': comp_rev,
            'company_profit': comp_profit,
            'seller_payable': seller_payable,
            'seller_profit': order_seller_profit,
            'settlement_status': order_settlement,
            'items_count': len(snaps),
            'snapshots': snaps,
        })

    completed_orders_count = delivered_orders_count
    avg_company_profit_per_order = (
        (total_completed_company_profit / completed_orders_count).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if completed_orders_count > 0 else Decimal('0.00')
    )
    avg_seller_profit_per_order = (
        (total_completed_seller_profit / completed_orders_with_cost_count).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if completed_orders_with_cost_count > 0 else Decimal('0.00')
    )

    # Filter and pagination for order-wise table
    order_search = request.GET.get('order_q', '').strip()
    order_status_filter = request.GET.get('order_status', '').strip()

    filtered_order_rows = order_profit_rows
    if order_search:
        q_lower = order_search.lower()
        filtered_order_rows = [
            r for r in filtered_order_rows
            if q_lower in r['order_id'].lower() or q_lower in r['seller_display'].lower()
        ]
    if order_status_filter:
        filtered_order_rows = [
            r for r in filtered_order_rows
            if r['status'] == order_status_filter
        ]

    order_paginator = Paginator(filtered_order_rows, 15)
    order_page_num = request.GET.get('order_page', 1)
    order_profit_page = order_paginator.get_page(order_page_num)

    # 11. Detailed Item-Level Financial Table
    table_snapshots = OrderItemFinancialSnapshot.objects.select_related(
        'order', 'order_item', 'seller', 'seller__seller_profile', 'order__delivery_assignment'
    ).filter(**snapshot_date_filter).order_by('-order__created_at')

    paginator = Paginator(table_snapshots, 25)
    page_number = request.GET.get('page', 1)
    pnl_items_page = paginator.get_page(page_number)

    # 12. Per-Seller Summary
    seller_summaries = []
    seller_profiles = SellerProfile.objects.select_related('user').all()
    for sp in seller_profiles:
        s_user = sp.user
        s_snaps = settled_snapshots.filter(seller=s_user)
        s_gross = s_snaps.aggregate(t=Sum('product_revenue'))['t'] or Decimal('0.00')
        s_comm = s_snaps.aggregate(t=Sum('commission_amount'))['t'] or Decimal('0.00')
        s_payable = s_snaps.aggregate(t=Sum('seller_payable'))['t'] or Decimal('0.00')
        s_cogs = s_snaps.filter(product_cost_total__isnull=False).aggregate(t=Sum('product_cost_total'))['t']
        s_profit = s_snaps.filter(seller_profit__isnull=False).aggregate(t=Sum('seller_profit'))['t']
        wallet = getattr(s_user, 'seller_wallet', None)
        seller_summaries.append({
            'seller': s_user,
            'store_name': sp.store_name,
            'gross_sales': s_gross,
            'commission_paid': s_comm,
            'seller_payable': s_payable,
            'cost_recorded': s_cogs,
            'estimated_profit': s_profit,
            'available_balance': wallet.available_balance if wallet else Decimal('0.00'),
            'pending_earnings': wallet.pending_earnings if wallet else Decimal('0.00'),
        })

    # 13. Per-Delivery Driver Summary
    driver_summaries = []
    delivery_users = User.objects.filter(role=User.Role.DELIVERY, is_active=True).select_related('delivery_profile')
    for d_user in delivery_users:
        d_earnings = DeliveryEarning.objects.filter(delivery_person=d_user, **delivery_date_filter)
        settled_amt = d_earnings.filter(status='SETTLED').aggregate(t=Sum('earning_amount'))['t'] or Decimal('0.00')
        pending_amt = d_earnings.filter(status='PENDING').aggregate(t=Sum('earning_amount'))['t'] or Decimal('0.00')
        driver_summaries.append({
            'driver': d_user,
            'profile': getattr(d_user, 'delivery_profile', None),
            'settled_earnings': settled_amt,
            'pending_earnings': pending_amt,
            'total_earnings': settled_amt + pending_amt,
        })

    return render(request, 'admin_dashboard/company_pnl.html', {
        'config': config,
        'period': period,
        'date_from': date_from_str,
        'date_to': date_to_str,
        'date_label': date_label,

        # 18 Standard Financial KPI Metrics
        'total_gmv': total_gmv,
        'total_customer_product_sales': total_customer_product_sales,
        'total_customer_discounts': total_customer_discounts,
        'marketplace_commission_revenue': marketplace_commission_revenue,
        'company_owned_revenue': company_owned_revenue,
        'company_owned_cogs': company_owned_cogs,
        'delivery_fees_charged': delivery_fees_charged,
        'delivery_payout_expenses': delivery_payout_expenses,
        'company_cashback_expense': company_cashback_expense,
        'gateway_fees': gateway_fees,
        'other_operating_expenses': other_operating_expenses,
        'refunds_returns': refunds_returns,
        'net_company_profit': net_company_profit,
        'pending_seller_liabilities': pending_seller_liabilities,
        'available_seller_wallet_balances': available_seller_wallet_balances,
        'pending_seller_earnings': pending_seller_earnings,
        'unsettled_delivery_earnings': unsettled_delivery_earnings,
        'customer_cashback_liability': max(customer_cashback_liability, Decimal('0.00')),

        # Intermediate totals for formula explanation
        'company_revenue_total': company_revenue_total,
        'company_expenses_total': company_expenses_total,
        'company_delivery_margin': company_delivery_margin,
        'seller_funded_cashback': seller_funded_cashback,

        # Order volume counts
        'total_orders_count': total_orders_count,
        'delivered_orders_count': delivered_orders_count,

        # Order-Wise Dynamic Profit Metrics (New Feature)
        'completed_orders_count': completed_orders_count,
        'total_completed_company_profit': total_completed_company_profit,
        'total_completed_seller_profit': total_completed_seller_profit,
        'completed_orders_with_cost_count': completed_orders_with_cost_count,
        'avg_company_profit_per_order': avg_company_profit_per_order,
        'avg_seller_profit_per_order': avg_seller_profit_per_order,
        'order_profit_page': order_profit_page,
        'order_search': order_search,
        'order_status_filter': order_status_filter,

        # Cashback attribution reconciliation
        'cashback_summary': {
            'earned': total_cashback_earned,
            'company_funded': company_cashback_expense,
            'seller_funded': seller_funded_cashback,
            'pending': pending_cashback_total,
            'redeemed': total_cashback_redeemed,
            'reversed': total_cashback_reversed,
        },

        # Detailed item ledger table
        'pnl_items_page': pnl_items_page,

        # Sub-ledgers
        'seller_summaries': seller_summaries,
        'driver_summaries': driver_summaries,
    })
