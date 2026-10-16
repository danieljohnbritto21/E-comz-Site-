import csv
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Sum, Count, Q
from apps.accounts.decorators import role_required
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile
from apps.catalog.models import Product, Category
from apps.orders.models import Order, OrderItem, SellerSubOrder, OrderStatusHistory
from apps.delivery.models import DeliveryAssignment
from apps.delivery.services import auto_assign_delivery_partner
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
    """View and filter all platform orders with status filters and delivery assignment."""
    orders = Order.objects.select_related('buyer', 'delivery_assignment__delivery_person').prefetch_related('sub_orders', 'items').order_by('-created_at')
    
    status_filter = request.GET.get('status')
    if status_filter:
        orders = orders.filter(status=status_filter)

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
        'orders': orders,
        'delivery_agents': delivery_agents,
        'status_filter': status_filter,
    })


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
    elif status_filter == 'out_of_stock':
        products_qs = products_qs.filter(stock_record__current_stock__lte=0)

    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        prod = get_object_or_404(Product, id=product_id)
        prod.is_active = not prod.is_active
        prod.save(update_fields=['is_active'])
        status_name = "Activated" if prod.is_active else "Deactivated"
        messages.info(request, f"{prod.name} has been {status_name}.")
        return redirect(request.get_full_path())

    all_sellers = SellerProfile.objects.select_related('user').all().order_by('store_name')
    all_categories = Category.objects.filter(is_active=True).order_by('name')

    return render(request, 'admin_dashboard/products.html', {
        'products': products_qs,
        'total_products': total_products,
        'active_products': active_products,
        'inactive_products': inactive_products,
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
