import logging
import uuid
from apps.notifications.models import Notification

logger = logging.getLogger(__name__)


def send_delivery_otp_sms(order, raw_otp):
    """
    Simulated secure SMS dispatch service for delivery OTP.
    Sends SMS message to the customer's registered phone number and posts an in-app notification to the buyer.
    Never exposes plaintext OTP to delivery partner or seller endpoints.
    """
    recipient_phone = order.recipient_phone
    masked_phone = f"...{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
    sms_ref = f"SMS-{uuid.uuid4().hex[:8].upper()}"

    sms_body = (
        f"FreshMart OTP: Your secure delivery code for Order #{order.order_id} is {raw_otp}. "
        f"Valid for 5 minutes. Share this code ONLY with your delivery partner upon package handover."
    )

    # In production with SMS Gateway (e.g. Twilio, MSG91, Fast2SMS):
    # response = requests.post(SMS_GATEWAY_URL, json={'phone': recipient_phone, 'message': sms_body}, ...)
    
    # Audit log entry (audit record records dispatch event and reference)
    logger.info("Delivery OTP SMS dispatched: Order=%s, Recipient=%s, Ref=%s", order.order_id, masked_phone, sms_ref)

    # Create in-app buyer notification so buyer receives the code directly on their portal
    if order.buyer:
        Notification.send(
            user=order.buyer,
            title=f"Delivery OTP for #{order.order_id}",
            message=f"Your delivery verification code is {raw_otp} (valid for 5 minutes). Share this with your delivery agent upon doorstep arrival.",
            category=Notification.Category.DELIVERY,
            link=f"/orders/track/{order.order_id}/"
        )

    return {
        'success': True,
        'sms_reference': sms_ref,
        'recipient_masked': masked_phone
    }
