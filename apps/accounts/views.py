from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils.text import slugify
from django.db import transaction
from apps.accounts.forms import LoginForm, BuyerRegistrationForm, SellerRegistrationForm
from apps.accounts.models import User, BuyerProfile, SellerProfile
from apps.accounts.decorators import get_role_redirect_url
from apps.notifications.models import Notification


def login_view(request):
    if request.user.is_authenticated:
        return redirect(get_role_redirect_url(request.user))

    selected_role = request.GET.get('role', 'BUYER').strip().upper()
    if selected_role not in ['BUYER', 'SELLER', 'DELIVERY', 'ADMIN']:
        selected_role = 'BUYER'

    if request.method == 'POST':
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

            # 1. Authenticate user credentials
            user = authenticate(request, username=username_or_email, password=password)
            if user is None:
                user_obj = User.objects.filter(email__iexact=username_or_email).first()
                if user_obj:
                    user = authenticate(request, username=user_obj.username, password=password)

            # 2. Reject if invalid username/email/password
            if user is None:
                messages.error(request, "Invalid username or password.")
                return render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})

            # 3. Check account active status
            if not user.is_active or user.account_status in [User.Status.SUSPENDED, User.Status.REJECTED]:
                messages.error(request, "Your account is inactive. Please contact support.")
                return render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})

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
                return render(request, 'auth/login.html', {'form': form, 'selected_role': selected_role})

            # 5. Create authenticated session only after verified credentials and matching role
            old_session_key = request.session.session_key
            login(request, user)
            
            # Merge items from guest shopping cart into user's basket
            if old_session_key:
                from apps.cart.views import merge_session_cart
                merge_session_cart(user, old_session_key)

            messages.success(request, f"Welcome back, {user.first_name or user.username}!")

            # 6. Redirect to requested next destination or role-specific dashboard
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect(get_role_redirect_url(user))
        else:
            messages.error(request, "Please enter all required fields with a valid role selection.")
    else:
        form = LoginForm(initial={'role': selected_role})

    return render(request, 'auth/login.html', {
        'form': form,
        'selected_role': selected_role
    })


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect('catalog:home')


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

                login(request, user)
                messages.success(request, f"Welcome to FreshMart, {user.first_name or user.username}! Your customer account is ready.")
                return redirect('catalog:home')
    else:
        form = BuyerRegistrationForm()

    return render(request, 'auth/register_buyer.html', {'form': form})


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
