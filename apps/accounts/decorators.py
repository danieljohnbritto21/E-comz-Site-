from functools import wraps
from urllib.parse import quote
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.utils.cache import add_never_cache_headers


def apply_cache_control_headers(response):
    """Adds strict anti-caching headers so protected data is never served from browser bfcache."""
    add_never_cache_headers(response)
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def role_required(allowed_roles):
    """
    Decorator for views that checks whether a user is logged in and has an allowed role.
    Strictly enforces server-side database authentication and role verification.
    Applies strict anti-caching headers to prevent back-button data leakage.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.warning(request, "Please log in to access this page.")
                role_param = allowed_roles[0] if allowed_roles else 'BUYER'
                next_url = request.get_full_path()
                redirect_target = f"{reverse('accounts:login')}?role={role_param}&next={quote(next_url)}"
                response = redirect(redirect_target)
                return apply_cache_control_headers(response)

            # Superusers always have admin access
            if request.user.is_superuser and 'ADMIN' in allowed_roles:
                response = view_func(request, *args, **kwargs)
                return apply_cache_control_headers(response)

            # Check account status
            if not request.user.is_active or request.user.account_status != 'ACTIVE':
                messages.error(request, f"Your account status is: {request.user.get_account_status_display()}. Please contact support.")
                logout(request)
                response = redirect('accounts:login')
                return apply_cache_control_headers(response)

            if request.user.role not in allowed_roles:
                messages.error(request, "You do not have permission to view that section.")
                response = redirect(get_role_redirect_url(request.user))
                return apply_cache_control_headers(response)

            # Specific check for seller approval
            if request.user.role == 'SELLER':
                if hasattr(request.user, 'seller_profile') and not request.user.seller_profile.is_approved:
                    messages.info(request, "Your seller application is currently pending admin review. You can view your store status.")

            response = view_func(request, *args, **kwargs)
            return apply_cache_control_headers(response)
        return _wrapped_view
    return decorator


def get_role_redirect_url(user):
    """Returns the primary dashboard URL for a user based on role."""
    if not user.is_authenticated:
        return 'catalog:home'
    if user.is_superuser or user.role == 'ADMIN':
        return 'admin_dashboard:dashboard'
    elif user.role == 'SELLER':
        return 'seller:dashboard'
    elif user.role == 'DELIVERY':
        return 'delivery:dashboard'
    else:
        return 'catalog:home'
