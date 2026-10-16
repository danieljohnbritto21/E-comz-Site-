from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.text import slugify
from django.db import transaction, models
from django.http import JsonResponse
from apps.accounts.forms import LoginForm, BuyerRegistrationForm, SellerRegistrationForm
from apps.accounts.models import User, BuyerProfile, SellerProfile, DeliveryProfile
from apps.accounts.decorators import get_role_redirect_url, apply_cache_control_headers
from apps.notifications.models import Notification


def get_client_ip(request):
    """Safely extract remote IP address from HTTP headers for rate limiting."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def login_view(request):
    if request.user.is_authenticated:
        return redirect(get_role_redirect_url(request.user))

    selected_role = request.GET.get('role', 'BUYER').strip().upper()
    if selected_role not in ['BUYER', 'SELLER', 'DELIVERY', 'ADMIN']:
        selected_role = 'BUYER'

    if request.method == 'POST':
        client_ip = get_client_ip(request)
        rate_limit_key = f"auth_lockout_{client_ip}"
        failed_attempts = cache.get(rate_limit_key, 0)
        if failed_attempts >= 10:
            messages.error(request, "Too many failed login attempts. Please wait 5 minutes before trying again.")
            form = LoginForm(request.POST)
            response = render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})
            return apply_cache_control_headers(response)

        # Track selected role from post to preserve active tab state on UI
        raw_posted_role = request.POST.get('role', selected_role).strip().upper()
        if raw_posted_role in ['CUSTOMER', 'BUYER']:
            selected_role = 'BUYER'
        elif raw_posted_role in ['SELLER', 'VENDOR']:
            selected_role = 'SELLER'
        elif raw_posted_role in ['DELIVERY', 'DELIVERY_PERSON', 'DRIVER', 'AGENT']:
            selected_role = 'DELIVERY'
        elif raw_posted_role in ['ADMIN', 'ADMINISTRATOR']:
            selected_role = 'ADMIN'

        form = LoginForm(request.POST)
        if form.is_valid():
            username_or_email = form.cleaned_data['username'].strip()
            password = form.cleaned_data['password']
            role_choice = form.cleaned_data['role']

            # 1. Authenticate user credentials against database
            user = authenticate(request, username=username_or_email, password=password)
            if user is None:
                user_obj = User.objects.filter(email__iexact=username_or_email).first()
                if user_obj:
                    user = authenticate(request, username=user_obj.username, password=password)

            # 2. Reject if invalid username/email/password
            if user is None:
                cache.set(rate_limit_key, failed_attempts + 1, 300)
                messages.error(request, "Invalid username or password.")
                response = render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})
                return apply_cache_control_headers(response)

            # 3. Check account active status
            if not user.is_active or user.account_status in [User.Status.SUSPENDED, User.Status.REJECTED]:
                messages.error(request, "Your account is inactive. Please contact support.")
                response = render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})
                return apply_cache_control_headers(response)

            # 4. Strict server-side role validation
            # Never trust browser role selection as proof of identity; compare with DB record
            is_role_matched = False
            if role_choice == 'ADMIN':
                if user.role == User.Role.ADMIN or user.is_superuser:
                    is_role_matched = True
            elif role_choice == 'SELLER':
                if user.role == User.Role.SELLER:
                    is_role_matched = True
            elif role_choice == 'DELIVERY':
                if user.role == User.Role.DELIVERY:
                    is_role_matched = True
            elif role_choice == 'BUYER':
                if user.role == User.Role.BUYER:
                    is_role_matched = True

            # Reject completely if user role does not match the selected tab
            if not is_role_matched:
                cache.set(rate_limit_key, failed_attempts + 1, 300)
                role_label = {
                    'BUYER': 'Customer',
                    'SELLER': 'Seller',
                    'DELIVERY': 'Delivery Person',
                    'ADMIN': 'Admin'
                }.get(role_choice, role_choice)

                messages.error(
                    request,
                    f"This account cannot sign in using the {role_label} role. Please select the correct role."
                )
                response = render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})
                return apply_cache_control_headers(response)

            # 5. Clear rate limit counter on successful credentials & matching role
            cache.delete(rate_limit_key)

            # Create authenticated session only after verified credentials and matching role
            old_session_key = request.session.session_key
            login(request, user)
            
            # Merge items from guest shopping cart into user's basket
            if old_session_key:
                from apps.cart.views import merge_session_cart
                merge_session_cart(user, old_session_key)

            # Process any preserved pending product addition from guest session
            pending_prod_id = request.POST.get('pending_product_id') or request.GET.get('pending_product_id') or request.session.get('pending_product_id')
            pending_qty = int(request.POST.get('pending_qty') or request.GET.get('pending_qty') or request.session.get('pending_qty') or 1)
            if pending_prod_id and user.role == User.Role.BUYER:
                try:
                    from apps.catalog.models import Product
                    from apps.cart.models import Cart, CartItem
                    p = Product.objects.filter(id=pending_prod_id, is_active=True).first()
                    if p and p.current_stock_count > 0:
                        user_cart, _ = Cart.objects.get_or_create(user=user)
                        item, created = CartItem.objects.get_or_create(
                            cart=user_cart,
                            product=p,
                            defaults={'quantity': min(pending_qty, p.current_stock_count)}
                        )
                        if created:
                            messages.success(request, f"Added {p.name} to your basket!")
                except Exception:
                    pass
                if 'pending_product_id' in request.session:
                    del request.session['pending_product_id']
                if 'pending_qty' in request.session:
                    del request.session['pending_qty']

            messages.success(request, f"Welcome back, {user.first_name or user.username}!")

            # 6. Redirect to requested next destination or role-specific dashboard
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                response = redirect(next_url)
            else:
                response = redirect(get_role_redirect_url(user))
            return apply_cache_control_headers(response)
        else:
            messages.error(request, "Please enter all required fields with a valid role selection.")
    else:
        form = LoginForm(initial={'role': selected_role})

    response = render(request, 'auth/login.html', {
        'form': form,
        'selected_role': selected_role,
        'next': request.GET.get('next', ''),
        'pending_product_id': request.GET.get('pending_product_id', ''),
        'pending_qty': request.GET.get('pending_qty', '1'),
    })
    return apply_cache_control_headers(response)


def logout_view(request):
    """
    Safely terminates authenticated session, invalidates session data on server,
    clears cookies, sets anti-caching headers, and redirects safely.
    Handles both POST (recommended with CSRF) and GET requests.
    """
    was_admin = request.user.is_authenticated and (request.user.is_superuser or request.user.role == 'ADMIN')
    was_seller = request.user.is_authenticated and request.user.role == 'SELLER'
    was_delivery = request.user.is_authenticated and request.user.role == 'DELIVERY'

    # Terminate server session completely
    logout(request)

    messages.info(request, "You have been logged out successfully.")

    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        response = redirect(next_url)
    elif was_admin:
        response = redirect(f"{reverse('accounts:login')}?role=ADMIN")
    elif was_seller:
        response = redirect(f"{reverse('accounts:login')}?role=SELLER")
    elif was_delivery:
        response = redirect(f"{reverse('accounts:login')}?role=DELIVERY")
    else:
        response = redirect('catalog:home')

    # Explicitly clear session cookie on client
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return apply_cache_control_headers(response)


def role_redirect_view(request):
    if not request.user.is_authenticated:
        return redirect('accounts:login')
    return redirect(get_role_redirect_url(request.user))


def register_buyer_view(request):
    if request.user.is_authenticated:
        return redirect(get_role_redirect_url(request.user))

    if request.method == 'POST':
        form = BuyerRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.role = User.Role.BUYER
                user.account_status = User.Status.ACTIVE
                user.set_password(form.cleaned_data['password'])
                user.save()

                BuyerProfile.objects.create(user=user)

                Notification.send(
                    user=user,
                    title="Welcome to FreshMart!",
                    message="Your customer account has been created successfully. Enjoy farm fresh grocery shopping!",
                    category=Notification.Category.ACCOUNT,
                    link='/'
                )

                old_session_key = request.session.session_key
                login(request, user)
                if old_session_key:
                    from apps.cart.views import merge_session_cart
                    merge_session_cart(user, old_session_key)

                # Process any preserved pending product addition from guest session
                pending_prod_id = request.POST.get('pending_product_id') or request.GET.get('pending_product_id') or request.session.get('pending_product_id')
                pending_qty = int(request.POST.get('pending_qty') or request.GET.get('pending_qty') or request.session.get('pending_qty') or 1)
                if pending_prod_id and user.role == User.Role.BUYER:
                    try:
                        from apps.catalog.models import Product
                        from apps.cart.models import Cart, CartItem
                        p = Product.objects.filter(id=pending_prod_id, is_active=True).first()
                        if p and p.current_stock_count > 0:
                            user_cart, _ = Cart.objects.get_or_create(user=user)
                            item, created = CartItem.objects.get_or_create(
                                cart=user_cart,
                                product=p,
                                defaults={'quantity': min(pending_qty, p.current_stock_count)}
                            )
                            if created:
                                messages.success(request, f"Added {p.name} to your basket!")
                    except Exception:
                        pass
                    if 'pending_product_id' in request.session:
                        del request.session['pending_product_id']
                    if 'pending_qty' in request.session:
                        del request.session['pending_qty']

                messages.success(request, f"Welcome to FreshMart, {user.first_name or user.username}! Your customer account is ready.")
                next_url = request.POST.get('next') or request.GET.get('next')
                if next_url and next_url.startswith('/'):
                    return redirect(next_url)
                return redirect('catalog:home')
    else:
        form = BuyerRegistrationForm()

    return render(request, 'auth/register_buyer.html', {
        'form': form,
        'next': request.GET.get('next', ''),
        'pending_product_id': request.GET.get('pending_product_id', ''),
        'pending_qty': request.GET.get('pending_qty', '1'),
    })


def register_seller_view(request):
    if request.user.is_authenticated:
        return redirect(get_role_redirect_url(request.user))

    if request.method == 'POST':
        form = SellerRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.role = User.Role.SELLER
                user.account_status = User.Status.ACTIVE # or PENDING
                user.set_password(form.cleaned_data['password'])
                user.save()

                store_name = form.cleaned_data['store_name']
                SellerProfile.objects.create(
                    user=user,
                    store_name=store_name,
                    store_slug=slugify(store_name),
                    pickup_address=form.cleaned_data['pickup_address'],
                    city=form.cleaned_data['city'],
                    pin_code=form.cleaned_data['pin_code'],
                    is_approved=True # Auto-approve initial seller for development convenience
                )

                Notification.send(
                    user=user,
                    title="Seller Store Created",
                    message=f"Congratulations! Store '{store_name}' has been created. You can now add grocery products and start selling.",
                    category=Notification.Category.ACCOUNT,
                    link='/seller/products/add/'
                )

                login(request, user)
                messages.success(request, f"Seller store '{store_name}' registered successfully! Welcome to your seller dashboard.")
                return redirect('seller:dashboard')
    else:
        form = SellerRegistrationForm()

    return render(request, 'auth/register_seller.html', {'form': form})


@login_required
def profile_view(request):
    user = request.user
    role = user.role

    # Handle profile updates
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        email = request.POST.get('email', '').strip()

        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        if phone_number:
            user.phone_number = phone_number
        if email:
            user.email = email
        user.save()

        # Specific role updates
        if user.is_delivery_role() and hasattr(user, 'delivery_profile'):
            vehicle_type = request.POST.get('vehicle_type')
            vehicle_number = request.POST.get('vehicle_number')
            license_number = request.POST.get('license_number')
            dp = user.delivery_profile
            if vehicle_type:
                dp.vehicle_type = vehicle_type
            if vehicle_number is not None:
                dp.vehicle_number = vehicle_number.strip()
            if license_number is not None:
                dp.license_number = license_number.strip()
            dp.save()
        elif user.is_seller_role() and hasattr(user, 'seller_profile'):
            sp = user.seller_profile
            pickup_address = request.POST.get('pickup_address')
            store_phone = request.POST.get('store_phone')
            if pickup_address:
                sp.pickup_address = pickup_address.strip()
            if store_phone:
                sp.store_phone = store_phone.strip()
            sp.save()

        messages.success(request, "Account details updated successfully.")
        return redirect('accounts:profile')

    context = {
        'account_user': user,
        'role': role,
    }

    # Delivery profile context
    if hasattr(user, 'delivery_profile'):
        context['delivery_profile'] = user.delivery_profile

    # Seller profile context
    if hasattr(user, 'seller_profile'):
        context['seller_profile'] = user.seller_profile
        context['seller_product_count'] = user.products.count()
        context['seller_order_count'] = user.seller_sub_orders.count()

    # Buyer profile context
    if hasattr(user, 'buyer_profile') or user.role == User.Role.BUYER:
        context['buyer_profile'] = getattr(user, 'buyer_profile', None)
        context['buyer_orders_count'] = user.orders.count()
        context['buyer_addresses'] = user.addresses.all()
        context['buyer_recent_orders'] = user.orders.prefetch_related('items').order_by('-created_at')[:10]

    # Admin context (directory of all accounts for all roles)
    if user.is_admin_role():
        context['all_sellers'] = SellerProfile.objects.select_related('user').all().order_by('id')
        context['all_drivers'] = DeliveryProfile.objects.select_related('user').all().order_by('id')
        context['all_buyers'] = User.objects.filter(role=User.Role.BUYER).select_related('buyer_profile').all().order_by('id')
        context['all_admins'] = User.objects.filter(models.Q(role=User.Role.ADMIN) | models.Q(is_superuser=True)).order_by('id')

    response = render(request, 'auth/profile.html', context)
    return apply_cache_control_headers(response)


@login_required
def api_account_details_view(request):
    user = request.user
    data = {
        'id': user.id,
        'account_id': user.account_id,
        'username': user.username,
        'full_name': user.get_full_name() or user.username,
        'email': user.email,
        'phone_number': user.phone_number or 'Not provided',
        'role': user.role,
        'role_display': user.get_role_display(),
        'account_status': user.get_account_status_display(),
        'date_joined': user.date_joined.strftime('%b %d, %Y') if user.date_joined else '',
    }

    if hasattr(user, 'delivery_profile'):
        dp = user.delivery_profile
        data['delivery'] = {
            'driver_account_id': dp.driver_account_id,
            'vehicle_type': dp.get_vehicle_type_display(),
            'vehicle_number': dp.vehicle_number or 'Not registered',
            'license_number': dp.license_number or 'Not registered',
            'current_status': dp.get_current_status_display(),
            'service_area': dp.service_area,
            'rating': str(dp.rating),
            'total_deliveries': dp.total_deliveries_completed,
            'max_active_orders': dp.max_active_orders,
        }
    if hasattr(user, 'seller_profile'):
        sp = user.seller_profile
        data['seller'] = {
            'seller_account_id': sp.seller_account_id,
            'store_name': sp.store_name,
            'store_slug': sp.store_slug,
            'store_phone': sp.store_phone or user.phone_number or 'Not provided',
            'store_email': sp.store_email or user.email,
            'pickup_address': sp.pickup_address,
            'city': sp.city,
            'pin_code': sp.pin_code,
            'serviceable_pincodes': sp.serviceable_pincodes,
            'commission_rate': str(sp.commission_rate),
            'preparation_time_minutes': sp.preparation_time_minutes,
            'is_approved': sp.is_approved,
            'product_count': user.products.count(),
            'order_count': user.seller_sub_orders.count(),
        }
    if hasattr(user, 'buyer_profile') or user.role == User.Role.BUYER:
        bp = getattr(user, 'buyer_profile', None)
        data['buyer'] = {
            'customer_account_id': bp.customer_account_id if bp else user.account_id,
            'loyalty_points': bp.loyalty_points if bp else 0,
            'order_count': user.orders.count(),
            'recent_orders': [
                {
                    'order_id': o.order_id,
                    'status': o.status,
                    'status_display': o.get_status_display(),
                    'total_amount': str(o.total_amount),
                    'created_at': o.created_at.strftime('%b %d, %Y %I:%M %p'),
                    'items_count': o.items.count(),
                    'track_url': f"/orders/track/{o.order_id}/"
                } for o in user.orders.prefetch_related('items').order_by('-created_at')[:5]
            ],
            'addresses': [
                {
                    'id': a.id,
                    'full_name': a.full_name,
                    'phone': a.phone_number,
                    'address_line': f"{a.house_number}, {a.street}, {a.area}, {a.city} - {a.pin_code}",
                    'is_default': a.is_default
                } for a in user.addresses.all()[:5]
            ]
        }
    if user.is_admin_role():
        data['admin'] = {
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
            'sellers_count': SellerProfile.objects.count(),
            'drivers_count': DeliveryProfile.objects.count(),
            'buyers_count': User.objects.filter(role=User.Role.BUYER).count(),
        }
    response = JsonResponse({'status': 'success', 'account': data})
    return apply_cache_control_headers(response)

