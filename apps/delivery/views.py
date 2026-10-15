from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from apps.accounts.decorators import role_required
from apps.accounts.models import User
from apps.delivery.models import DeliveryAssignment, DeliveryAttempt, DeliveryOTP
from apps.delivery.sms_service import send_delivery_otp_sms
from apps.orders.models import Order, OrderStatusHistory, SellerSubOrder
from apps.notifications.models import Notification


@role_required(['DELIVERY'])
def delivery_dashboard(request):
    """Delivery driver dashboard showing assigned jobs, pending pickups, COD dues, and metrics."""
    agent = request.user
    today = timezone.now().date()

    assignments = DeliveryAssignment.objects.filter(delivery_person=agent).select_related('order', 'order__buyer')
    
    # Metrics
    today_assigned = assignments.filter(assigned_at__date=today).count()
    pending_pickups = assignments.filter(status__in=[DeliveryAssignment.Status.ASSIGNED, DeliveryAssignment.Status.ACCEPTED]).count()
    active_deliveries = assignments.filter(status__in=[DeliveryAssignment.Status.PICKED_UP, DeliveryAssignment.Status.OUT_FOR_DELIVERY]).count()
    delivered_orders = assignments.filter(status=DeliveryAssignment.Status.DELIVERED).count()
    failed_deliveries = assignments.filter(status=DeliveryAssignment.Status.FAILED).count()

    total_closed = delivered_orders + failed_deliveries
    success_rate = int((delivered_orders / total_closed * 100)) if total_closed > 0 else 100

    # Current Active Assignments (not yet delivered/failed)
    active_jobs = assignments.filter(
        status__in=[
            DeliveryAssignment.Status.ASSIGNED,
            DeliveryAssignment.Status.ACCEPTED,
            DeliveryAssignment.Status.ARRIVED_AT_STORE,
            DeliveryAssignment.Status.PICKED_UP,
            DeliveryAssignment.Status.OUT_FOR_DELIVERY
        ]
    ).prefetch_related('order__sub_orders__seller__seller_profile').order_by('-assigned_at')

    return render(request, 'delivery/dashboard.html', {
        'today_assigned': today_assigned,
        'pending_pickups': pending_pickups,
        'active_deliveries': active_deliveries,
        'delivered_orders': delivered_orders,
        'failed_deliveries': failed_deliveries,
        'success_rate': success_rate,
        'active_jobs': active_jobs,
    })


@role_required(['DELIVERY'])
def delivery_order_detail(request, assignment_id):
    """
    Detailed delivery assignment view with strict backend privacy gating:
    - Pre-pickup: Customer address, phone, and name are masked and excluded from response.
      Only store pickup name, address, and verification action are accessible.
    - Shop Arrival & Pickup: Verified against Order ID and seller packed state.
    - Post-pickup: Customer delivery details unlocked.
    - Payment Gate: COD collection must be recorded before OTP generation.
    - Delivery OTP: Secure hashed verification to complete delivery.
    """
    agent = request.user
    assignment = get_object_or_404(
        DeliveryAssignment.objects.select_related('order', 'order__buyer').prefetch_related(
            'order__items', 'order__sub_orders', 'order__sub_orders__seller__seller_profile', 'attempts', 'otps'
        ),
        id=assignment_id,
        delivery_person=agent
    )
    order = assignment.order

    # POST Action Handlers
    if request.method == 'POST':
        action = request.POST.get('action')

        # 1. Accept Assignment
        if action == 'accept':
            if assignment.status == DeliveryAssignment.Status.ASSIGNED:
                assignment.status = DeliveryAssignment.Status.ACCEPTED
                assignment.save(update_fields=['status', 'updated_at'])
                messages.success(request, f"Assignment for Order #{order.order_id} accepted. Proceed to the seller store for pickup.")
            return redirect('delivery:order_detail', assignment_id=assignment.id)

        # 2. Arrived at Store
        elif action == 'arrive_at_store':
            if assignment.status in [DeliveryAssignment.Status.ASSIGNED, DeliveryAssignment.Status.ACCEPTED]:
                assignment.status = DeliveryAssignment.Status.ARRIVED_AT_STORE
                assignment.save(update_fields=['status', 'updated_at'])
                messages.info(request, f"Arrived at store for Order #{order.order_id}. Present or enter the Order ID to verify handover.")
            return redirect('delivery:order_detail', assignment_id=assignment.id)

        # 3. Verify Order ID & Confirm Pickup at Store
        elif action == 'verify_pickup':
            entered_order_id = request.POST.get('order_id_input', '').strip()
            
            # Prevent duplicate pickup
            if assignment.status in [DeliveryAssignment.Status.PICKED_UP, DeliveryAssignment.Status.OUT_FOR_DELIVERY, DeliveryAssignment.Status.DELIVERED]:
                messages.warning(request, f"Order #{order.order_id} has already been picked up.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Strict Order ID validation
            if not entered_order_id or entered_order_id.upper() != order.order_id.upper():
                messages.error(request, f"Pickup Verification Failed: Order ID '{entered_order_id}' does not match #{order.order_id}. Please confirm with the store owner.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Check if sellers have marked the order as packed
            unpacked_suborders = order.sub_orders.exclude(status__in=[
                SellerSubOrder.Status.PACKED,
                SellerSubOrder.Status.READY_FOR_PICKUP,
                SellerSubOrder.Status.HANDED_OVER,
                SellerSubOrder.Status.SHIPPED,
                SellerSubOrder.Status.DELIVERED
            ])
            if unpacked_suborders.exists():
                messages.error(request, f"Cannot pickup order #{order.order_id}: The seller has not marked the package as Packed yet. Please ask the store to finish packing.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            with transaction.atomic():
                assignment.status = DeliveryAssignment.Status.PICKED_UP
                assignment.pickup_time = timezone.now()
                assignment.shop_verified = True
                assignment.shop_verified_at = timezone.now()
                assignment.save()

                # Update seller sub-orders to HANDED_OVER
                now = timezone.now()
                for sub in order.sub_orders.all():
                    sub.status = SellerSubOrder.Status.HANDED_OVER
                    sub.handover_confirmed_by_seller = True
                    sub.handover_time = now
                    sub.save(update_fields=['status', 'handover_confirmed_by_seller', 'handover_time', 'updated_at'])

                order.status = Order.Status.OUT_FOR_DELIVERY
                order.save(update_fields=['status', 'updated_at'])
                order.add_status_history(
                    Order.Status.OUT_FOR_DELIVERY,
                    agent,
                    f"Order #{order.order_id} verified with Order ID and picked up from seller store by {agent.get_full_name() or agent.username}."
                )

                # Notify buyer that order has been picked up
                Notification.send(
                    user=order.buyer,
                    title=f"Order #{order.order_id} Picked Up!",
                    message=f"Delivery agent {agent.get_full_name() or agent.username} has verified and collected your package from the store.",
                    category=Notification.Category.DELIVERY,
                    link=f"/orders/track/{order.order_id}/"
                )

                # Notify sellers
                for sub in order.sub_orders.all():
                    Notification.send(
                        user=sub.seller,
                        title=f"Order #{order.order_id} Handed Over",
                        message=f"Delivery partner {agent.get_full_name() or agent.username} verified and collected package #{order.order_id}.",
                        category=Notification.Category.DELIVERY,
                        link=f"/seller/orders/{order.order_id}/"
                    )

            messages.success(request, f"Order #{order.order_id} verified and collected successfully! Customer delivery destination is now UNLOCKED.")
            return redirect('delivery:order_detail', assignment_id=assignment.id)

        # 4. Start Out for Delivery en route
        elif action == 'out_for_delivery':
            if assignment.status == DeliveryAssignment.Status.PICKED_UP:
                assignment.status = DeliveryAssignment.Status.OUT_FOR_DELIVERY
                assignment.save(update_fields=['status', 'updated_at'])
                order.status = Order.Status.OUT_FOR_DELIVERY
                order.save(update_fields=['status', 'updated_at'])
                order.add_status_history(Order.Status.OUT_FOR_DELIVERY, agent, "Delivery partner is en route to customer doorstep.")
                messages.info(request, f"Order #{order.order_id} is Out for Delivery.")
            return redirect('delivery:order_detail', assignment_id=assignment.id)

        # 5. Confirm Cash on Delivery Collection
        elif action == 'collect_cod':
            if order.payment_method != Order.PaymentMethod.COD:
                messages.error(request, "This order is not a Cash on Delivery order.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            if assignment.cod_collected or order.payment_status == Order.PaymentStatus.PAID:
                messages.warning(request, f"Cash of Rs. {order.total_amount} has already been collected for Order #{order.order_id}.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            with transaction.atomic():
                assignment.cod_collected = True
                assignment.cod_collected_amount = order.total_amount
                assignment.cod_collected_at = timezone.now()
                assignment.save(update_fields=['cod_collected', 'cod_collected_amount', 'cod_collected_at', 'updated_at'])

                order.payment_status = Order.PaymentStatus.PAID
                order.save(update_fields=['payment_status', 'updated_at'])

                order.add_status_history(
                    order.status,
                    agent,
                    f"Cash on Delivery amount of Rs. {order.total_amount} collected in cash by delivery agent {agent.get_full_name() or agent.username}."
                )

                # Record attempt log for cash collection
                DeliveryAttempt.objects.create(
                    assignment=assignment,
                    status="COD_COLLECTED",
                    proof_of_delivery=f"Cash Collected: Rs. {order.total_amount}",
                    cod_collected=True,
                    cod_amount=order.total_amount
                )

                Notification.send(
                    user=order.buyer,
                    title=f"Payment Received #{order.order_id}",
                    message=f"Cash payment of Rs. {order.total_amount} received by delivery partner.",
                    category=Notification.Category.ORDER,
                    link=f"/orders/track/{order.order_id}/"
                )

            messages.success(request, f"Cash on Delivery payment of Rs. {order.total_amount} recorded successfully. Delivery OTP verification is now UNLOCKED!")
            return redirect('delivery:order_detail', assignment_id=assignment.id)

        # 6. Generate & Send Secure Delivery OTP to Customer
        elif action == 'send_otp':
            # Validation: Must be picked up
            if assignment.status not in [DeliveryAssignment.Status.PICKED_UP, DeliveryAssignment.Status.OUT_FOR_DELIVERY]:
                messages.error(request, "Cannot generate OTP before order has been picked up from the seller store.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Validation: Payment requirement satisfied
            if order.payment_status != Order.PaymentStatus.PAID:
                if order.payment_method == Order.PaymentMethod.COD:
                    messages.error(request, f"Payment Required: Please collect Cash on Delivery of Rs. {order.total_amount} before sending delivery OTP.")
                else:
                    messages.error(request, "Payment Required: Online payment is not yet verified. OTP cannot be generated.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Rate limit / Cooldown check: 30 seconds
            recent_otp = DeliveryOTP.objects.filter(assignment=assignment, is_used=False).order_by('-created_at').first()
            if recent_otp and (timezone.now() - recent_otp.created_at).total_seconds() < 30:
                wait_sec = int(30 - (timezone.now() - recent_otp.created_at).total_seconds())
                messages.warning(request, f"Please wait {wait_sec} seconds before requesting a new OTP.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Generate cryptographically secure OTP and store salted hash (valid for 15 minutes)
            otp_obj, raw_code = DeliveryOTP.generate(assignment, expiry_minutes=15)
            
            # Send to customer's registered mobile number
            send_delivery_otp_sms(order, raw_code)

            masked_phone = f"...{order.recipient_phone[-4:]}" if len(order.recipient_phone) >= 4 else order.recipient_phone
            messages.success(
                request,
                f"Secure 6-digit Delivery OTP sent to customer's registered phone ({masked_phone}). "
                "Ask the customer for the OTP code upon doorstep package handover."
            )
            return redirect('delivery:order_detail', assignment_id=assignment.id)

        # 7. Verify Customer OTP & Complete Delivery
        elif action == 'verify_otp':
            entered_otp = request.POST.get('otp_code', '').strip()

            if assignment.status == DeliveryAssignment.Status.DELIVERED:
                messages.info(request, f"Order #{order.order_id} has already been marked Delivered.")
                return redirect('delivery:dashboard')

            if assignment.status not in [DeliveryAssignment.Status.PICKED_UP, DeliveryAssignment.Status.OUT_FOR_DELIVERY]:
                messages.error(request, "Order must be picked up and en route before completing delivery.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Payment must be paid
            if order.payment_status != Order.PaymentStatus.PAID:
                messages.error(request, "Delivery cannot be completed: Payment requirements are not satisfied.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            if not entered_otp:
                messages.error(request, "Please enter the 6-digit Delivery OTP provided by the customer.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Retrieve active OTP
            active_otp = DeliveryOTP.objects.filter(assignment=assignment, is_used=False).order_by('-created_at').first()
            if not active_otp:
                messages.error(request, "No active OTP found. Please click 'Send Delivery OTP to Customer' first.")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Verify OTP
            is_valid, verify_msg = active_otp.verify(entered_otp)
            if not is_valid:
                messages.error(request, f"OTP Verification Failed: {verify_msg}")
                return redirect('delivery:order_detail', assignment_id=assignment.id)

            # Successful OTP verification -> Mark Delivered atomically
            with transaction.atomic():
                assignment.status = DeliveryAssignment.Status.DELIVERED
                assignment.delivered_time = timezone.now()
                assignment.save(update_fields=['status', 'delivered_time', 'updated_at'])

                order.status = Order.Status.DELIVERED
                order.save(update_fields=['status', 'updated_at'])

                # Update seller sub orders
                order.sub_orders.update(status=SellerSubOrder.Status.DELIVERED)

                # Record delivery proof attempt
                proof_note = request.POST.get('proof_note', '').strip() or f"Verified via Customer Mobile OTP ({order.recipient_phone[-4:]})"
                DeliveryAttempt.objects.create(
                    assignment=assignment,
                    status="DELIVERED",
                    proof_of_delivery=proof_note,
                    cod_collected=assignment.cod_collected,
                    cod_amount=assignment.cod_collected_amount
                )

                order.add_status_history(
                    Order.Status.DELIVERED,
                    agent,
                    f"Delivered to customer doorstep. Verified via Customer OTP ({active_otp.recipient_phone}). Proof: {proof_note}"
                )

                # Update agent profile
                if hasattr(agent, 'delivery_profile'):
                    profile = agent.delivery_profile
                    profile.total_deliveries_completed += 1
                    profile.current_status = 'AVAILABLE'
                    profile.save()

                # Notify Buyer
                Notification.send(
                    user=order.buyer,
                    title=f"Order #{order.order_id} Delivered!",
                    message="Your grocery order has been successfully delivered and verified with OTP. Enjoy your fresh produce!",
                    category=Notification.Category.DELIVERY,
                    link=f"/orders/track/{order.order_id}/"
                )

                # Notify Sellers
                for sub in order.sub_orders.all():
                    Notification.send(
                        user=sub.seller,
                        title=f"Order #{order.order_id} Delivered",
                        message=f"Order #{order.order_id} has been delivered to customer and verified via OTP.",
                        category=Notification.Category.DELIVERY,
                        link=f"/seller/orders/{order.order_id}/"
                    )

            messages.success(request, f"SUCCESS! Order #{order.order_id} verified with OTP and marked as DELIVERED.")
            return redirect('delivery:dashboard')

        # 8. Report Delivery Failure
        elif action == 'failed':
            reason = request.POST.get('failure_reason', 'CUSTOMER_UNAVAILABLE')
            notes = request.POST.get('failure_notes', '')

            with transaction.atomic():
                assignment.status = DeliveryAssignment.Status.FAILED
                assignment.save(update_fields=['status', 'updated_at'])

                DeliveryAttempt.objects.create(
                    assignment=assignment,
                    status="FAILED",
                    failure_reason=reason,
                    notes=notes
                )

                order.status = Order.Status.FAILED_DELIVERY
                order.save(update_fields=['status', 'updated_at'])
                order.add_status_history(Order.Status.FAILED_DELIVERY, agent, f"Delivery attempt failed: {reason}. {notes}")

                Notification.send(
                    user=order.buyer,
                    title=f"Delivery Notice for #{order.order_id}",
                    message=f"Delivery partner could not complete delivery ({reason}). FreshMart support will contact you.",
                    category=Notification.Category.DELIVERY,
                    link=f"/orders/track/{order.order_id}/"
                )

            messages.warning(request, f"Marked delivery attempt as failed for Order #{order.order_id}.")
            return redirect('delivery:dashboard')

    # GET Request Processing - STRICT PRIVACY GATING
    customer_unlocked = assignment.is_customer_details_unlocked

    # Active OTP info for UI display
    active_otp = DeliveryOTP.objects.filter(
        assignment=assignment,
        is_used=False,
        expires_at__gt=timezone.now()
    ).order_by('-created_at').first()

    cooldown_remaining = 0
    if active_otp:
        elapsed = (timezone.now() - active_otp.created_at).total_seconds()
        if elapsed < 30:
            cooldown_remaining = int(30 - elapsed)

    # Privacy filter: If not unlocked, mask customer fields completely on the backend
    if customer_unlocked:
        customer_details = {
            'name': order.recipient_name,
            'phone': order.recipient_phone,
            'address': order.delivery_address_text,
            'pin_code': order.pin_code,
            'instructions': order.delivery_instructions,
            'slot': order.delivery_slot,
        }
    else:
        # Pre-pickup masked data: Zero customer PII leakage
        customer_details = {
            'name': "Protected Customer",
            'phone': None,
            'address': "LOCKED - Store Pickup Verification Required",
            'pin_code': None,
            'instructions': None,
            'slot': order.delivery_slot,
        }

    return render(request, 'delivery/order_detail.html', {
        'assignment': assignment,
        'order': order,
        'customer_unlocked': customer_unlocked,
        'customer_details': customer_details,
        'active_otp': active_otp,
        'cooldown_remaining': cooldown_remaining,
    })


@role_required(['DELIVERY'])
def delivery_history(request):
    """Log of past completed and failed deliveries for this delivery agent."""
    agent = request.user
    history = DeliveryAssignment.objects.filter(
        delivery_person=agent,
        status__in=[DeliveryAssignment.Status.DELIVERED, DeliveryAssignment.Status.FAILED, DeliveryAssignment.Status.CANCELLED]
    ).select_related('order', 'order__buyer').prefetch_related('attempts', 'order__items', 'order__sub_orders', 'order__sub_orders__seller__seller_profile').order_by('-updated_at')

    total_cod_collected = DeliveryAssignment.objects.filter(
        delivery_person=agent,
        cod_collected=True
    ).aggregate(Sum('cod_collected_amount'))['cod_collected_amount__sum'] or Decimal('0.00')

    return render(request, 'delivery/history.html', {
        'history': history,
        'total_cod_collected': total_cod_collected,
    })
