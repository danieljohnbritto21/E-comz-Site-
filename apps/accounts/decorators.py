from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied


def role_required(allowed_roles):
    """
    Decorator for views that checks whether a user is logged in and has an allowed role.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.warning(request, "Please log in to access this page.")
                return redirect('accounts:login')
            
            # Superusers always have admin access
            if request.user.is_superuser and 'ADMIN' in allowed_roles:
                return view_func(request, *args, **kwargs)

            # Check account status
            if request.user.account_status != 'ACTIVE':
                messages.error(request, f"Your account status is: {request.user.get_account_status_display()}. Please contact support.")
                return redirect('accounts:login')

            if request.user.role not in allowed_roles:
                messages.error(request, "You do not have permission to view that section.")
                return redirect(get_role_redirect_url(request.user))

            # Specific check for seller approval
            if request.user.role == 'SELLER':
                if hasattr(request.user, 'seller_profile') and not request.user.seller_profile.is_approved:
                    messages.info(request, "Your seller application is currently pending admin review. You can view your store status.")
            
            return view_func(request, *args, **kwargs)
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
