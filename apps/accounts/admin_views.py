import csv
from decimal import Decimal
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
            return redirect(request.get_full_path())

        elif action == 'reject_product':
            from apps.reports.models import ProductApprovalHistory
            reason = request.POST.get('rejection_reason', 'Rejected by admin.').strip() or 'Rejected by admin.'
            prev = prod.approval_status
            prod.approval_status = Product.ApprovalStatus.REJECTED
            prod.is_approved = False
            prod.rejection_reason = reason
            prod.save(update_fields=['approval_status', 'is_approved', 'rejection_reason', 'updated_at'])
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
            return redirect(request.get_full_path())

        elif action == 'suspend_product':
            from apps.reports.models import ProductApprovalHistory
            reason = request.POST.get('rejection_reason', 'Suspended by admin.').strip() or 'Suspended by admin.'
            prev = prod.approval_status
            prod.approval_status = Product.ApprovalStatus.SUSPENDED
            prod.is_approved = False
            prod.rejection_reason = reason
            prod.save(update_fields=['approval_status', 'is_approved', 'rejection_reason', 'updated_at'])
            ProductApprovalHistory.objects.create(
                product=prod, action='SUSPENDED', admin_user=request.user,
                reason=reason, previous_status=prev, new_status='SUSPENDED'
            )
            messages.warning(request, f"Product '{prod.name}' suspended.")
            return redirect(request.get_full_path())

        else:
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
    Company Profit & Loss dashboard.
    Revenue = commission + delivery fee charged to customers.
    Expenses = delivery person earnings + company-funded cashback.
    Seller wallet balances and delivery liabilities are NOT company profit.
    """
    from apps.reports.models import (
        OrderItemFinancialSnapshot, DeliveryEarning, CashbackLedger,
        SellerWallet, MarketplaceConfig
    )

    config = MarketplaceConfig.get_config()

    # Date filtering
    period = request.GET.get('period', 'all')
    today = timezone.now().date()
    date_filter = {}
    if period == 'today':
        date_filter = {'created_at__date': today}
    elif period == 'month':
        date_filter = {'created_at__date__gte': today.replace(day=1)}
    elif period == 'year':
        date_filter = {'created_at__date__year': today.year}

    # ---- REVENUE ----
    # 1. Commission earned
    settled_snapshots = OrderItemFinancialSnapshot.objects.filter(
        settlement_status='SETTLED', **date_filter
    )
    total_commission = settled_snapshots.aggregate(
        t=Sum('commission_amount'))['t'] or Decimal('0.00')

    # 2. Delivery fees collected from customers (order level)
    delivered_orders = Order.objects.filter(status='DELIVERED', **date_filter)
    total_delivery_fees = delivered_orders.aggregate(
        t=Sum('delivery_fee'))['t'] or Decimal('0.00')

    total_revenue = total_commission + total_delivery_fees

    # ---- EXPENSES ----
    # 1. Delivery person earnings (what we pay delivery partners)
    delivery_earnings_filter = {'status': 'SETTLED'}
    if date_filter:
        delivery_earnings_filter.update(date_filter)
    total_delivery_expenses = DeliveryEarning.objects.filter(
        **delivery_earnings_filter
    ).aggregate(t=Sum('earning_amount'))['t'] or Decimal('0.00')

    # 2. Company-funded cashback
    cashback_filter = {'entry_type': 'EARNED', 'status': 'CONFIRMED', 'funding_source': 'COMPANY'}
    if date_filter:
        cashback_filter.update(date_filter)
    total_cashback_expense = CashbackLedger.objects.filter(
        **cashback_filter
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    total_expenses = total_delivery_expenses + total_cashback_expense

    # ---- PROFIT ----
    company_profit = total_revenue - total_expenses

    # ---- LIABILITIES (NOT profit) ----
    total_seller_wallet_balance = SellerWallet.objects.aggregate(
        t=Sum('available_balance'))['t'] or Decimal('0.00')
    total_seller_pending = SellerWallet.objects.aggregate(
        t=Sum('pending_earnings'))['t'] or Decimal('0.00')
    total_delivery_unsettled = DeliveryEarning.objects.filter(
        status='PENDING'
    ).aggregate(t=Sum('earning_amount'))['t'] or Decimal('0.00')

    # Order metrics
    total_orders = delivered_orders.count()
    total_gmv = delivered_orders.aggregate(t=Sum('total_amount'))['t'] or Decimal('0.00')

    return render(request, 'admin_dashboard/company_pnl.html', {
        'config': config,
        'period': period,
        'total_commission': total_commission,
        'total_delivery_fees': total_delivery_fees,
        'total_revenue': total_revenue,
        'total_delivery_expenses': total_delivery_expenses,
        'total_cashback_expense': total_cashback_expense,
        'total_expenses': total_expenses,
        'company_profit': company_profit,
        'total_seller_wallet_balance': total_seller_wallet_balance,
        'total_seller_pending': total_seller_pending,
        'total_delivery_unsettled': total_delivery_unsettled,
        'total_orders': total_orders,
        'total_gmv': total_gmv,
    })
