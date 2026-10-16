import uuid
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Sum, Count, Q
from django.core.paginator import Paginator
from apps.accounts.decorators import role_required
from apps.accounts.models import SellerProfile
from apps.catalog.models import Product, ProductImage, Category, SubCategory
from apps.inventory.models import StockRecord, StockMovement
from apps.orders.models import Order, OrderItem, SellerSubOrder, OrderStatusHistory
from apps.notifications.models import Notification
from apps.accounts.models import User
from apps.delivery.models import DeliveryAssignment
from apps.delivery.services import auto_assign_delivery_partner


@role_required(['SELLER'])
def seller_dashboard(request):
    """Seller dashboard with metrics, low-stock alerts, and recent fulfillment requests."""
    seller = request.user
    today = timezone.now().date()
    month_start = today.replace(day=1)

    # Products metrics
    products_qs = Product.objects.filter(seller=seller)
    total_products = products_qs.count()
    active_products = products_qs.filter(is_active=True).count()
    out_of_stock = products_qs.filter(stock_record__current_stock__lte=0).count()
    low_stock = products_qs.filter(
        stock_record__current_stock__gt=0,
        stock_record__current_stock__lte=10
    ).count()

    # Sub orders metrics
    sub_orders_qs = SellerSubOrder.objects.filter(seller=seller)
    today_orders = sub_orders_qs.filter(created_at__date=today).count()
    pending_orders = sub_orders_qs.filter(status__in=['PENDING', 'ACCEPTED', 'PREPARING']).count()
    
    # Financial metrics
    today_sales = sub_orders_qs.filter(created_at__date=today).exclude(status__in=['REJECTED', 'CANCELLED']).aggregate(Sum('subtotal'))['subtotal__sum'] or Decimal('0.00')
    monthly_revenue = sub_orders_qs.filter(created_at__date__gte=month_start).exclude(status__in=['REJECTED', 'CANCELLED']).aggregate(Sum('subtotal'))['subtotal__sum'] or Decimal('0.00')

    # Recent orders
    recent_sub_orders = sub_orders_qs.select_related('order', 'order__buyer').order_by('-created_at')[:8]

    # Critical low stock alerts
    low_stock_products = products_qs.filter(stock_record__current_stock__lte=10).select_related('stock_record')[:6]

    return render(request, 'seller/dashboard.html', {
        'total_products': total_products,
        'active_products': active_products,
        'out_of_stock': out_of_stock,
        'low_stock': low_stock,
        'today_orders': today_orders,
        'pending_orders': pending_orders,
        'today_sales': today_sales,
        'monthly_revenue': monthly_revenue,
        'recent_sub_orders': recent_sub_orders,
        'low_stock_products': low_stock_products,
    })


@role_required(['SELLER'])
def seller_product_list(request):
    """View and manage all products created by the seller with filters, search, and pagination."""
    seller = request.user
    products_qs = Product.objects.filter(seller=seller).select_related('category', 'stock_record').order_by('-created_at')

    # Aggregate counts for quick tabs
    total_count = products_qs.count()
    active_count = products_qs.filter(is_active=True).count()
    inactive_count = products_qs.filter(is_active=False).count()
    in_stock_count = products_qs.filter(stock_record__current_stock__gt=0).count()
    low_stock_count = products_qs.filter(
        stock_record__current_stock__gt=0,
        stock_record__current_stock__lte=10
    ).count()
    out_of_stock_count = products_qs.filter(stock_record__current_stock__lte=0).count()

    # Search filter
    search_query = request.GET.get('q', '').strip()
    if search_query:
        products_qs = products_qs.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query) |
            Q(brand__icontains=search_query)
        )

    # Category filter
    category_slug = request.GET.get('category', '').strip()
    if category_slug:
        products_qs = products_qs.filter(category__slug=category_slug)

    # Status filter
    status_filter = request.GET.get('status', '').strip()
    if status_filter == 'active':
        products_qs = products_qs.filter(is_active=True)
    elif status_filter == 'inactive':
        products_qs = products_qs.filter(is_active=False)

    # Stock filter
    stock_filter = request.GET.get('stock', '').strip()
    if stock_filter == 'in_stock':
        products_qs = products_qs.filter(stock_record__current_stock__gt=0)
    elif stock_filter == 'low_stock':
        products_qs = products_qs.filter(
            stock_record__current_stock__gt=0,
            stock_record__current_stock__lte=10
        )
    elif stock_filter == 'out_of_stock':
        products_qs = products_qs.filter(stock_record__current_stock__lte=0)

    # Pagination (10 per page)
    paginator = Paginator(products_qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    categories = Category.objects.filter(is_active=True)

    return render(request, 'seller/product_list.html', {
        'products': page_obj,
        'page_obj': page_obj,
        'total_count': total_count,
        'active_count': active_count,
        'inactive_count': inactive_count,
        'in_stock_count': in_stock_count,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'categories': categories,
        'search_query': search_query,
        'selected_category': category_slug,
        'status_filter': status_filter,
        'stock_filter': stock_filter,
    })


@role_required(['SELLER'])
def seller_product_add(request):
    """Add a new product with stock record, threshold, images, and full produce specifications."""
    categories = Category.objects.filter(is_active=True)
    subcategories = SubCategory.objects.filter(is_active=True)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        category_id = request.POST.get('category')
        subcategory_id = request.POST.get('subcategory')
        brand = request.POST.get('brand', 'FreshFarm').strip()
        description = request.POST.get('description', '').strip()
        unit = request.POST.get('unit', 'kg')
        unit_value = request.POST.get('unit_value', '1 kg').strip()
        origin = request.POST.get('origin', 'Local Organic Farm').strip()
        freshness_info = request.POST.get('freshness_info', 'Harvested fresh within 24h').strip()
        storage_instructions = request.POST.get('storage_instructions', 'Store in a cool, dry place or refrigerate').strip()
        nutritional_info = request.POST.get('nutritional_info', '').strip()
        return_policy = request.POST.get('return_policy', 'Same-day replacement for quality issues').strip()
        sku = request.POST.get('sku', '').strip()

        # Validations
        if not name:
            messages.error(request, "Please enter a valid product name.")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        if not category_id:
            messages.error(request, "Please select a product category.")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        try:
            mrp = Decimal(request.POST.get('mrp', '0').strip() or '0')
            selling_price = Decimal(request.POST.get('selling_price', '0').strip() or '0')
        except Exception:
            messages.error(request, "Please enter valid numeric values for MRP and Selling Price.")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        if mrp <= 0 or selling_price <= 0:
            messages.error(request, "MRP and Selling Price must be positive amounts.")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        if selling_price > mrp:
            messages.error(request, "Selling price cannot exceed Maximum Retail Price (MRP).")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        try:
            initial_stock = int(request.POST.get('initial_stock', '10').strip() or '10')
            low_stock_threshold = int(request.POST.get('low_stock_threshold', '5').strip() or '5')
        except Exception:
            messages.error(request, "Please enter valid integer values for stock quantities.")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        if initial_stock < 0 or low_stock_threshold < 0:
            messages.error(request, "Stock quantities cannot be negative.")
            return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})

        if not sku or Product.objects.filter(sku=sku).exists():
            clean_name = slugify(name)[:6].upper() or 'PROD'
            sku = f"SKU-{clean_name}-{request.user.id}-{uuid.uuid4().hex[:4].upper()}"

        is_active = 'is_active' in request.POST
        is_featured = 'is_featured' in request.POST or True
        is_best_seller = 'is_best_seller' in request.POST

        subcategory = None
        if subcategory_id:
            subcategory = SubCategory.objects.filter(id=subcategory_id, category_id=category_id).first()

        try:
            with transaction.atomic():
                category = get_object_or_404(Category, id=category_id)
                product = Product.objects.create(
                    seller=request.user,
                    category=category,
                    subcategory=subcategory,
                    name=name,
                    brand=brand or 'Farm Fresh',
                    description=description,
                    mrp=mrp,
                    selling_price=selling_price,
                    unit=unit,
                    unit_value=unit_value or '1 unit',
                    sku=sku,
                    origin=origin,
                    freshness_info=freshness_info,
                    storage_instructions=storage_instructions,
                    nutritional_info=nutritional_info,
                    return_policy=return_policy,
                    is_active=is_active,
                    is_approved=True,
                    is_featured=is_featured,
                    is_best_seller=is_best_seller
                )

                # Initialize StockRecord & Movement
                StockRecord.objects.create(
                    product=product,
                    current_stock=initial_stock,
                    low_stock_threshold=low_stock_threshold
                )

                StockMovement.objects.create(
                    product=product,
                    change_type=StockMovement.ChangeType.RESTOCK,
                    quantity=initial_stock,
                    stock_before=0,
                    stock_after=initial_stock,
                    notes="Initial product inventory setup",
                    created_by=request.user
                )

                # Handle images
                images = request.FILES.getlist('images')
                for i, img_file in enumerate(images):
                    ProductImage.objects.create(
                        product=product,
                        image=img_file,
                        is_primary=(i == 0)
                    )

                messages.success(request, f"Product '{product.name}' created and published successfully!")
                return redirect('seller:product_list')
        except Exception as e:
            messages.error(request, f"Failed to save product: {str(e)}")

    return render(request, 'seller/product_form.html', {'categories': categories, 'subcategories': subcategories})


@role_required(['SELLER'])
def seller_product_edit(request, product_id):
    """Edit existing product details, pricing, inventory stock, and produce specifications."""
    product = get_object_or_404(Product.objects.select_related('stock_record'), id=product_id, seller=request.user)
    if not hasattr(product, 'stock_record') or product.stock_record is None:
        StockRecord.objects.create(product=product, current_stock=0, low_stock_threshold=5)
        product.refresh_from_db()

    categories = Category.objects.filter(is_active=True)
    subcategories = SubCategory.objects.filter(is_active=True)

    if request.method == 'POST':
        product.name = request.POST.get('name', product.name).strip()
        category_id = request.POST.get('category')
        subcategory_id = request.POST.get('subcategory')

        if category_id:
            product.category = get_object_or_404(Category, id=category_id)
        if subcategory_id:
            product.subcategory = SubCategory.objects.filter(id=subcategory_id, category=product.category).first()
        else:
            product.subcategory = None

        product.brand = request.POST.get('brand', product.brand).strip()
        product.description = request.POST.get('description', product.description).strip()
        
        try:
            mrp = Decimal(request.POST.get('mrp', product.mrp))
            selling_price = Decimal(request.POST.get('selling_price', product.selling_price))
            if selling_price > mrp:
                messages.error(request, "Selling price cannot exceed MRP.")
                return render(request, 'seller/product_form.html', {'product': product, 'categories': categories, 'subcategories': subcategories})
            product.mrp = mrp
            product.selling_price = selling_price
        except Exception:
            messages.error(request, "Invalid price entered.")
            return render(request, 'seller/product_form.html', {'product': product, 'categories': categories, 'subcategories': subcategories})

        product.unit = request.POST.get('unit', product.unit)
        if 'origin' in request.POST:
            product.origin = request.POST.get('origin', '').strip() or product.origin
        if 'freshness_info' in request.POST:
            product.freshness_info = request.POST.get('freshness_info', '').strip() or product.freshness_info
        if 'storage_instructions' in request.POST:
            product.storage_instructions = request.POST.get('storage_instructions', '').strip() or product.storage_instructions
        if 'nutritional_info' in request.POST:
            product.nutritional_info = request.POST.get('nutritional_info', '').strip()
        product.return_policy = request.POST.get('return_policy', product.return_policy).strip()

        product.is_active = 'is_active' in request.POST
        product.is_featured = 'is_featured' in request.POST
        product.is_best_seller = 'is_best_seller' in request.POST
        product.save()

        # Update Stock Record (Stock Inward / Inventory increase or adjustment)
        stock_record = product.stock_record
        low_stock_threshold_input = request.POST.get('low_stock_threshold')
        if low_stock_threshold_input:
            try:
                stock_record.low_stock_threshold = max(1, int(low_stock_threshold_input))
                stock_record.save(update_fields=['low_stock_threshold'])
            except (ValueError, TypeError):
                pass

        current_stock_input = request.POST.get('current_stock')
        stock_reason = request.POST.get('stock_reason', '').strip()

        if current_stock_input is not None:
            try:
                new_stock = max(0, int(current_stock_input.strip() or '0'))
                diff = new_stock - stock_record.current_stock
                if diff != 0:
                    change_type = StockMovement.ChangeType.RESTOCK if diff > 0 else StockMovement.ChangeType.MANUAL
                    default_note = f"Restocked +{diff} units via product edit" if diff > 0 else f"Manual adjustment: {diff} units via product edit"
                    stock_record.adjust_stock(
                        quantity_change=diff,
                        change_type=change_type,
                        user=request.user,
                        notes=stock_reason or default_note
                    )
            except (ValueError, TypeError) as e:
                messages.warning(request, f"Stock quantity could not be updated: {e}")

        # Delete images marked for removal
        delete_image_ids = request.POST.getlist('delete_images')
        if delete_image_ids:
            ProductImage.objects.filter(id__in=delete_image_ids, product=product).delete()

        # Upload additional images if provided
        images = request.FILES.getlist('images')
        for img_file in images:
            ProductImage.objects.create(
                product=product,
                image=img_file,
                is_primary=False
            )

        # Ensure at least one image is primary if images exist
        if product.images.exists() and not product.images.filter(is_primary=True).exists():
            first_img = product.images.first()
            first_img.is_primary = True
            first_img.save(update_fields=['is_primary'])

        messages.success(request, f"Product '{product.name}' updated successfully!")
        return redirect('seller:product_list')

    return render(request, 'seller/product_form.html', {'product': product, 'categories': categories, 'subcategories': subcategories})


@role_required(['SELLER'])
def seller_product_toggle_status(request, product_id):
    """Toggle product active/inactive."""
    product = get_object_or_404(Product, id=product_id, seller=request.user)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active'])
    status_str = "Active" if product.is_active else "Inactive"
    messages.info(request, f"Product '{product.name}' is now {status_str}.")
    return redirect('seller:product_list')


@role_required(['SELLER'])
def seller_product_delete(request, product_id):
    """Safely delete or archive a product belonging to the seller."""
    product = get_object_or_404(Product, id=product_id, seller=request.user)
    name = product.name
    
    # If product has historical orders, deactivate/archive instead of hard deleting
    if product.sold_items.exists():
        product.is_active = False
        product.save(update_fields=['is_active'])
        messages.warning(request, f"Product '{name}' has existing customer purchase records and has been archived/deactivated to preserve historical order integrity.")
    else:
        product.delete()
        messages.success(request, f"Product '{name}' has been permanently removed.")
    
    return redirect('seller:product_list')


@role_required(['SELLER'])
def seller_inventory_view(request):
    """Seller inventory management with quick adjustment and movement audit trail."""
    seller = request.user
    products = Product.objects.filter(seller=seller).select_related('stock_record', 'category').order_by('stock_record__current_stock')

    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        qty_change = int(request.POST.get('quantity_change', 0))
        reason = request.POST.get('reason', 'Manual Inventory Update')
        change_type = request.POST.get('change_type', StockMovement.ChangeType.MANUAL)

        product = get_object_or_404(Product, id=product_id, seller=seller)
        try:
            product.stock_record.adjust_stock(
                qty_change,
                change_type=change_type,
                user=seller,
                notes=reason
            )
            messages.success(request, f"Adjusted stock for {product.name} ({qty_change:+d} units).")
        except Exception as e:
            messages.error(request, f"Adjustment failed: {str(e)}")
        return redirect('seller:inventory')

    recent_movements = StockMovement.objects.filter(product__seller=seller).select_related('product').order_by('-created_at')[:15]

    return render(request, 'seller/inventory.html', {
        'products': products,
        'recent_movements': recent_movements,
    })


@role_required(['SELLER'])
def seller_orders_view(request):
    """Seller order fulfillment view showing sub-orders and status workflow."""
    seller = request.user
    sub_orders = SellerSubOrder.objects.filter(seller=seller).select_related(
        'order', 'order__buyer', 'order__delivery_assignment', 'order__delivery_assignment__delivery_person'
    ).prefetch_related('items', 'items__product', 'items__product__images').order_by('-created_at')

    # Status filter
    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        sub_orders = sub_orders.filter(status=status_filter)

    # Payment status filter
    payment_filter = request.GET.get('payment', '').strip()
    if payment_filter == 'paid':
        sub_orders = sub_orders.filter(order__payment_status='PAID')
    elif payment_filter == 'cod':
        sub_orders = sub_orders.filter(order__payment_method='COD')
    elif payment_filter == 'pending':
        sub_orders = sub_orders.filter(order__payment_status='PENDING')

    # Search by order ID or customer name
    search_query = request.GET.get('q', '').strip()
    if search_query:
        clean_q = search_query.replace('#', '')
        sub_orders = sub_orders.filter(
            Q(order__order_id__icontains=clean_q) |
            Q(order__recipient_name__icontains=search_query) |
            Q(order__recipient_phone__icontains=search_query)
        )

    # Metrics for tabs
    total_sub_orders = SellerSubOrder.objects.filter(seller=seller).count()
    pending_count = SellerSubOrder.objects.filter(seller=seller, status='PENDING').count()
    accepted_count = SellerSubOrder.objects.filter(seller=seller, status='ACCEPTED').count()
    preparing_count = SellerSubOrder.objects.filter(seller=seller, status='PREPARING').count()
    packed_count = SellerSubOrder.objects.filter(seller=seller, status__in=['PACKED', 'READY_FOR_PICKUP']).count()
    handed_over_count = SellerSubOrder.objects.filter(seller=seller, status__in=['HANDED_OVER', 'SHIPPED']).count()
    delivered_count = SellerSubOrder.objects.filter(seller=seller, status='DELIVERED').count()

    return render(request, 'seller/orders.html', {
        'sub_orders': sub_orders,
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'search_query': search_query,
        'total_sub_orders': total_sub_orders,
        'pending_count': pending_count,
        'accepted_count': accepted_count,
        'preparing_count': preparing_count,
        'packed_count': packed_count,
        'handed_over_count': handed_over_count,
        'delivered_count': delivered_count,
    })


@role_required(['SELLER'])
def seller_order_detail_view(request, order_id):
    """Detailed view for a specific order's items belonging to this seller with state progression and handover verification."""
    seller = request.user
    sub_order = get_object_or_404(
        SellerSubOrder.objects.select_related(
            'order', 'order__buyer', 'order__delivery_assignment', 'order__delivery_assignment__delivery_person'
        ).prefetch_related('items'),
        order__order_id=order_id,
        seller=seller
    )
    order = sub_order.order

    if request.method == 'POST':
        action = request.POST.get('action')

        # Business rule check: If online payment was selected and is still unverified/pending, disallow fulfillment
        if order.payment_method != 'COD' and order.payment_status != 'PAID' and action in ['start_preparing', 'mark_packed', 'ready_pickup', 'confirm_handover']:
            messages.error(request, "Cannot fulfill order: Online payment has not yet been verified by payment gateway.")
            return redirect('seller:order_detail', order_id=order.order_id)

        with transaction.atomic():
            if action == 'accept':
                sub_order.status = SellerSubOrder.Status.ACCEPTED
                sub_order.save()
                order.add_status_history(Order.Status.ORDER_CONFIRMED, seller, f"Accepted by store {seller.seller_profile.store_name if hasattr(seller, 'seller_profile') else seller.username}")
                messages.success(request, f"Order #{order.order_id} marked as Accepted.")

            elif action == 'start_preparing':
                sub_order.status = SellerSubOrder.Status.PREPARING
                sub_order.save()
                order.add_status_history(Order.Status.PREPARING, seller, "Produce is being inspected, weighed and prepared.")
                messages.info(request, f"Order #{order.order_id} is now being prepared.")

            elif action == 'mark_packed':
                sub_order.status = SellerSubOrder.Status.PACKED
                sub_order.save()
                order.add_status_history(Order.Status.PACKED, seller, "Grocery items safely packed & temperature sealed.")

                # Directly check and assign an available delivery partner!
                assigned, driver, msg = auto_assign_delivery_partner(order, actor=seller)
                if assigned:
                    sub_order.status = SellerSubOrder.Status.READY_FOR_PICKUP
                    sub_order.save(update_fields=['status', 'updated_at'])
                    messages.success(request, f"Order #{order.order_id} packed and automatically dispatched to delivery partner {driver.get_full_name() or driver.username}!")
                else:
                    messages.info(request, f"Order #{order.order_id} marked as Packed. Searching for available delivery partners...")

            elif action in ['ready_pickup', 'auto_assign_driver']:
                sub_order.status = SellerSubOrder.Status.READY_FOR_PICKUP
                sub_order.save()
                assigned, driver, msg = auto_assign_delivery_partner(order, actor=seller)
                if assigned:
                    messages.success(request, f"Order #{order.order_id} dispatched to available delivery partner {driver.get_full_name() or driver.username}!")
                else:
                    order.add_status_history(Order.Status.READY_FOR_PICKUP, seller, "Produce package waiting at store counter for delivery dispatch.")
                    messages.warning(request, f"Order is ready for pickup, but all delivery riders are currently busy. Dispatch admin has been alerted.")

            elif action == 'confirm_handover':
                # Validations:
                if sub_order.status not in [SellerSubOrder.Status.PACKED, SellerSubOrder.Status.READY_FOR_PICKUP]:
                    messages.error(request, "Cannot hand over order: Order must be packed first.")
                    return redirect('seller:order_detail', order_id=order.order_id)

                if not hasattr(order, 'delivery_assignment') or not order.delivery_assignment or not order.delivery_assignment.delivery_person:
                    assigned, driver, _ = auto_assign_delivery_partner(order, actor=seller)
                    if not assigned:
                        messages.error(request, "Cannot hand over order: No delivery partner has been assigned yet.")
                        return redirect('seller:order_detail', order_id=order.order_id)
                    order.refresh_from_db()

                entered_id = request.POST.get('order_id_verification', '').strip()
                if entered_id and entered_id.upper() != order.order_id.upper():
                    messages.error(request, f"Handover Verification Failed: Entered ID '{entered_id}' does not match #{order.order_id}.")
                    return redirect('seller:order_detail', order_id=order.order_id)

                assignment = order.delivery_assignment
                now = timezone.now()

                sub_order.status = SellerSubOrder.Status.HANDED_OVER
                sub_order.handover_confirmed_by_seller = True
                sub_order.handover_time = now
                sub_order.save(update_fields=['status', 'handover_confirmed_by_seller', 'handover_time', 'updated_at'])

                # Update assignment pickup state
                if assignment.status in [DeliveryAssignment.Status.ASSIGNED, DeliveryAssignment.Status.ACCEPTED, DeliveryAssignment.Status.ARRIVED_AT_STORE]:
                    assignment.status = DeliveryAssignment.Status.PICKED_UP
                    assignment.pickup_time = now
                    assignment.shop_verified = True
                    assignment.shop_verified_at = now
                    assignment.save()

                order.status = Order.Status.OUT_FOR_DELIVERY
                order.save(update_fields=['status', 'updated_at'])
                order.add_status_history(
                    Order.Status.OUT_FOR_DELIVERY,
                    seller,
                    f"Package #{order.order_id} verified and handed over to delivery partner {assignment.delivery_person.get_full_name() or assignment.delivery_person.username}."
                )

                # Notify Delivery Agent
                Notification.send(
                    user=assignment.delivery_person,
                    title=f"Handover Confirmed #{order.order_id}",
                    message=f"Store has verified and handed over package #{order.order_id}. Customer destination details are now unlocked.",
                    category=Notification.Category.DELIVERY,
                    link=f"/delivery/jobs/{assignment.id}/"
                )

                # Notify Buyer
                Notification.send(
                    user=order.buyer,
                    title=f"Order #{order.order_id} On the Way",
                    message=f"Your package has been picked up by delivery partner {assignment.delivery_person.get_full_name() or assignment.delivery_person.username}.",
                    category=Notification.Category.DELIVERY,
                    link=f"/orders/track/{order.order_id}/"
                )

                messages.success(request, f"Order #{order.order_id} verified and handed over to {assignment.delivery_person.get_full_name() or assignment.delivery_person.username}!")

            elif action == 'reject':
                reason = request.POST.get('rejection_reason', 'Seller unable to fulfill')
                sub_order.status = SellerSubOrder.Status.REJECTED
                sub_order.rejection_reason = reason
                sub_order.save()
                order.add_status_history(Order.Status.CANCELLED, seller, f"Rejected by seller: {reason}")
                
                # Restore stock atomically
                for item in sub_order.items.all():
                    if item.product and hasattr(item.product, 'stock_record'):
                        item.product.stock_record.adjust_stock(
                            item.quantity,
                            StockMovement.ChangeType.CANCEL_RESTORE,
                            user=seller,
                            notes=f"Restored from rejected order #{order.order_id}"
                        )
                messages.warning(request, f"Order #{order.order_id} rejected.")

        return redirect('seller:order_detail', order_id=order.order_id)

    # Auto-dispatch check on GET if packed but not yet assigned to any driver
    if sub_order.status in [SellerSubOrder.Status.PACKED, SellerSubOrder.Status.READY_FOR_PICKUP]:
        if not hasattr(order, 'delivery_assignment') or not order.delivery_assignment or not order.delivery_assignment.delivery_person:
            auto_assign_delivery_partner(order, actor=seller)
            # Re-fetch order and sub_order to have the latest assignment in template context
            sub_order = get_object_or_404(
                SellerSubOrder.objects.select_related(
                    'order', 'order__buyer', 'order__delivery_assignment', 'order__delivery_assignment__delivery_person'
                ).prefetch_related('items'),
                order__order_id=order_id,
                seller=seller
            )

    return render(request, 'seller/order_detail.html', {'sub_order': sub_order})


@role_required(['SELLER'])
def seller_categories_view(request):
    """View active platform categories and product counts for this seller."""
    categories = Category.objects.filter(is_active=True).annotate(
        seller_product_count=Count('products', filter=Q(products__seller=request.user))
    )
    return render(request, 'seller/categories.html', {'categories': categories})


@role_required(['SELLER'])
def seller_offers_view(request):
    """View and manage discounted and featured products for this seller."""
    seller = request.user
    deals = Product.objects.filter(seller=seller, mrp__gt=models.F('selling_price')).select_related('category', 'stock_record')
    featured = Product.objects.filter(seller=seller, is_featured=True).select_related('category', 'stock_record')
    return render(request, 'seller/offers.html', {'deals': deals, 'featured': featured})


@role_required(['SELLER'])
def seller_customers_view(request):
    """
    Customer Order History: Displays orders containing products from this seller
    that have reached a completed / delivered status.
    
    Adheres strictly to seller data isolation:
    - Only retrieves products, quantities, and subtotal amounts belonging to request.user.
    - Groups multiple products purchased in the same customer order into a single unified row
      to prevent double counting or confusing duplicated order references.
    - Sorts most recent completed orders first.
    """
    seller = request.user

    # Fetch qualifying order items belonging to this seller from delivered/completed orders
    # Matching across seller, product__seller, or sub_order__seller guarantees full relationship integrity
    qualifying_items = OrderItem.objects.filter(
        Q(seller=seller) | Q(product__seller=seller) | Q(sub_order__seller=seller)
    ).filter(
        Q(sub_order__status=SellerSubOrder.Status.DELIVERED) |
        Q(order__status=Order.Status.DELIVERED) |
        Q(order__delivery_assignment__status=DeliveryAssignment.Status.DELIVERED)
    ).exclude(
        order__status__in=[Order.Status.CANCELLED, Order.Status.FAILED_DELIVERY, Order.Status.REFUNDED]
    ).exclude(
        sub_order__status__in=[SellerSubOrder.Status.REJECTED, SellerSubOrder.Status.CANCELLED]
    ).select_related(
        'order', 'order__buyer', 'order__delivery_assignment', 'sub_order', 'product'
    ).order_by('-order__created_at', '-id')

    # Group items by order so each customer order appears once with all items for this seller
    orders_map = {}
    for item in qualifying_items:
        order = item.order
        if order.id not in orders_map:
            # Determine relevant completion/delivery date
            completion_date = order.created_at
            if hasattr(order, 'delivery_assignment') and order.delivery_assignment and order.delivery_assignment.delivered_time:
                completion_date = order.delivery_assignment.delivered_time
            elif item.sub_order and item.sub_order.updated_at:
                completion_date = item.sub_order.updated_at
            elif order.updated_at:
                completion_date = order.updated_at

            orders_map[order.id] = {
                'order': order,
                'sub_order': item.sub_order,
                'completion_date': completion_date,
                'items': [],
                'total_quantity': 0,
                'total_amount': Decimal('0.00'),
            }

        orders_map[order.id]['items'].append(item)
        orders_map[order.id]['total_quantity'] += item.quantity
        orders_map[order.id]['total_amount'] += item.total_price

    # Build clean history entry objects
    history_entries = []
    for order_id, data in orders_map.items():
        items = data['items']
        order = data['order']

        if len(items) == 1:
            product_name_display = items[0].product_name
        else:
            product_name_display = ", ".join(f"{it.product_name} (x{it.quantity})" for it in items)

        history_entries.append({
            'order': order,
            'sub_order': data['sub_order'],
            'items': items,
            'product_name': product_name_display,
            'quantity': data['total_quantity'],
            'total_price': data['total_amount'],
            'created_at': data['completion_date'],
            'destination': order.delivery_address_text,
        })

    # Sort most recent completed first
    history_entries.sort(key=lambda x: x['created_at'], reverse=True)

    return render(request, 'seller/customers.html', {
        'recent_buyers': history_entries,
    })


@role_required(['SELLER'])
def seller_reports_view(request):
    """Sales, order analytics and financial breakdown for this seller."""
    seller = request.user
    sub_orders = SellerSubOrder.objects.filter(seller=seller).exclude(status__in=['REJECTED', 'CANCELLED'])
    
    total_revenue = sub_orders.aggregate(Sum('subtotal'))['subtotal__sum'] or Decimal('0.00')
    total_orders_count = sub_orders.count()
    completed_orders_count = sub_orders.filter(status='DELIVERED').count()
    
    top_items = OrderItem.objects.filter(seller=seller).values('product_name').annotate(
        units_sold=Sum('quantity'),
        total_sales=Sum('total_price')
    ).order_by('-units_sold')[:8]

    return render(request, 'seller/reports.html', {
        'total_revenue': total_revenue,
        'total_orders_count': total_orders_count,
        'completed_orders_count': completed_orders_count,
        'top_items': top_items,
    })


@role_required(['SELLER'])
def seller_settings_view(request):
    """Store profile and pickup settings."""
    profile = request.user.seller_profile

    if request.method == 'POST':
        profile.store_name = request.POST.get('store_name', profile.store_name)
        profile.store_description = request.POST.get('store_description', profile.store_description)
        profile.pickup_address = request.POST.get('pickup_address', profile.pickup_address)
        profile.city = request.POST.get('city', profile.city)
        profile.pin_code = request.POST.get('pin_code', profile.pin_code)
        profile.store_phone = request.POST.get('store_phone', profile.store_phone)
        profile.preparation_time_minutes = int(request.POST.get('preparation_time_minutes', profile.preparation_time_minutes))
        profile.save()

        messages.success(request, "Store settings saved successfully.")
        return redirect('seller:settings')

    return render(request, 'seller/settings.html', {'profile': profile})
