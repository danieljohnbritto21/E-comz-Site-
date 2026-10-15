from django.db import transaction
from django.db.models import Count, Q, Case, When, Value, IntegerField
from django.utils import timezone
from apps.accounts.models import User, DeliveryProfile
from apps.delivery.models import DeliveryAssignment
from apps.orders.models import Order
from apps.notifications.models import Notification


def find_available_driver():
    """
    Find the best available delivery driver:
    1. Role must be DELIVERY and user must be active.
    2. Prefer drivers marked as AVAILABLE in DeliveryProfile over ON_DELIVERY.
    3. Exclude drivers who are OFFLINE.
    4. Annotate with current active assignments count.
    5. Prioritize the driver with the lowest number of active jobs.
    """
    active_statuses = [
        DeliveryAssignment.Status.ASSIGNED,
        DeliveryAssignment.Status.ACCEPTED,
        DeliveryAssignment.Status.ARRIVED_AT_STORE,
        DeliveryAssignment.Status.PICKED_UP,
        DeliveryAssignment.Status.OUT_FOR_DELIVERY,
    ]

    drivers = (
        User.objects.filter(role=User.Role.DELIVERY, is_active=True)
        .select_related('delivery_profile')
        .annotate(
            active_jobs=Count('deliveries', filter=Q(deliveries__status__in=active_statuses)),
            availability_rank=Case(
                When(delivery_profile__current_status=DeliveryProfile.Status.AVAILABLE, then=Value(1)),
                When(delivery_profile__current_status=DeliveryProfile.Status.ON_DELIVERY, then=Value(2)),
                When(delivery_profile__isnull=True, then=Value(3)),
                default=Value(99),  # OFFLINE
                output_field=IntegerField(),
            )
        )
        .exclude(delivery_profile__current_status=DeliveryProfile.Status.OFFLINE)
        .order_by('availability_rank', 'active_jobs', '-delivery_profile__rating', 'id')
    )

    for driver in drivers:
        max_jobs = 5
        if hasattr(driver, 'delivery_profile') and driver.delivery_profile and driver.delivery_profile.max_active_orders:
            max_jobs = driver.delivery_profile.max_active_orders
        
        if driver.active_jobs < max_jobs:
            # Ensure DeliveryProfile exists
            DeliveryProfile.objects.get_or_create(
                user=driver,
                defaults={'current_status': DeliveryProfile.Status.AVAILABLE}
            )
            return driver

    return None


def auto_assign_delivery_partner(order, actor=None):
    """
    Automatically search for an available delivery driver and assign the order.
    Returns (assigned: bool, driver: User or None, message: str)
    """
    if hasattr(order, 'delivery_assignment') and order.delivery_assignment and order.delivery_assignment.delivery_person:
        return True, order.delivery_assignment.delivery_person, "Delivery partner already assigned."

    free_driver = find_available_driver()
    if not free_driver:
        return False, None, "No delivery partners are currently available or online."

    with transaction.atomic():
        assignment, created = DeliveryAssignment.objects.get_or_create(
            order=order,
            defaults={
                'delivery_person': free_driver,
                'assigned_by': actor if (actor and actor.is_authenticated and actor.role == User.Role.ADMIN) else None,
                'status': DeliveryAssignment.Status.ASSIGNED,
            }
        )

        if not created and not assignment.delivery_person:
            assignment.delivery_person = free_driver
            assignment.status = DeliveryAssignment.Status.ASSIGNED
            assignment.save()

        # Update order history
        driver_name = free_driver.get_full_name() or free_driver.username
        actor_name = actor.get_full_name() or actor.username if actor else "System Auto-Dispatch"
        order.add_status_history(
            Order.Status.READY_FOR_PICKUP if order.status in [Order.Status.PACKED, Order.Status.READY_FOR_PICKUP] else order.status,
            actor if (actor and actor.is_authenticated) else None,
            f"Auto-assigned to delivery partner {driver_name} by {actor_name}."
        )

        # Notify delivery agent
        Notification.send(
            user=free_driver,
            title=f"New Delivery Assignment #{order.order_id}",
            message=f"Order #{order.order_id} has been packed and assigned to you for pickup ({order.delivery_slot}).",
            category=Notification.Category.DELIVERY,
            link=f"/delivery/jobs/{assignment.id}/"
        )

        # Notify sellers
        for sub in order.sub_orders.all():
            Notification.send(
                user=sub.seller,
                title=f"Delivery Partner Assigned #{order.order_id}",
                message=f"Delivery partner {driver_name} has been assigned to pick up order #{order.order_id}.",
                category=Notification.Category.DELIVERY,
                link=f"/seller/orders/{order.order_id}/"
            )

        # Notify buyer
        Notification.send(
            user=order.buyer,
            title=f"Delivery Partner Assigned #{order.order_id}",
            message=f"Delivery partner {driver_name} is assigned to deliver your order.",
            category=Notification.Category.DELIVERY,
            link=f"/orders/track/{order.order_id}/"
        )

        # Notify admin
        for admin in User.objects.filter(role=User.Role.ADMIN):
            Notification.send(
                user=admin,
                title=f"Order #{order.order_id} Auto-Assigned",
                message=f"Order #{order.order_id} was automatically assigned to driver {driver_name}.",
                category=Notification.Category.DELIVERY,
                link="/admin-dashboard/orders/"
            )

        return True, free_driver, f"Order #{order.order_id} automatically assigned to {driver_name}."
