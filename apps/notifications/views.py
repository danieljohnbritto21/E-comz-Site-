from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from apps.notifications.models import Notification


@login_required
def notification_list_view(request):
    """View all notifications for authenticated user."""
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')

    # Mark all as read action
    if request.method == 'POST' and request.POST.get('action') == 'mark_all_read':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return redirect('notifications:list')

    return render(request, 'notifications/list.html', {'notifications': notifications})


@login_required
def mark_notification_read(request, notification_id):
    """Mark a single notification as read and redirect to its link."""
    notif = get_object_or_404(Notification, id=notification_id, user=request.user)
    notif.is_read = True
    notif.save(update_fields=['is_read'])
    if notif.link:
        return redirect(notif.link)
    return redirect('notifications:list')
