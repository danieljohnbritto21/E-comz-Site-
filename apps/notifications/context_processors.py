from apps.notifications.models import Notification


def notifications_context(request):
    """Provides unread notifications and count for authenticated user."""
    if request.user.is_authenticated:
        unread_qs = Notification.objects.filter(user=request.user, is_read=False)
        unread_count = unread_qs.count()
        recent_notifications = unread_qs[:5]
        return {
            'unread_notifications_count': unread_count,
            'recent_notifications': recent_notifications,
        }
    return {
        'unread_notifications_count': 0,
        'recent_notifications': [],
    }
