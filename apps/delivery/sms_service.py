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
    recipient_phone = getattr(order, 'recipient_phone', '') or ''
    masked_phone = f"...{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
    sms_ref = f"SMS-{uuid.uuid4().hex[:8].upper()}"

    if not recipient_phone or not recipient_phone.strip():
        logger.warning("Delivery OTP SMS dispatch failed: Order=%s has no phone number", order.order_id)
        return {
            'success': False,
            'sms_reference': sms_ref,
            'recipient_masked': '',
            'error': 'Missing customer phone number',
            'provider': 'LOCAL_SIMULATOR',
        }

    sms_body = (
        f"FreshMart OTP: Your secure delivery code for Order #{order.order_id} is {raw_otp}. "
        f"Valid for 5 minutes. Share this code ONLY with your delivery partner upon package handover."
    )

    sms_sent = True
    sms_error = None
    provider_used = 'LOCAL_SIMULATOR'

    # Live SMS Gateway Integration (Fast2SMS for India, or Twilio)
    import os
    import json
    import urllib.request
    from django.conf import settings

    fast2sms_api_key = os.getenv('FAST2SMS_API_KEY') or getattr(settings, 'FAST2SMS_API_KEY', None)
    if fast2sms_api_key:
        provider_used = 'Fast2SMS'
        try:
            clean_digits = ''.join(c for c in recipient_phone if c.isdigit())
            if len(clean_digits) > 10:
                clean_digits = clean_digits[-10:]
            req = urllib.request.Request(
                "https://www.fast2sms.com/dev/bulkV2",
                data=json.dumps({
                    "route": "otp",
                    "variables_values": str(raw_otp),
                    "numbers": clean_digits
                }).encode('utf-8'),
                headers={
                    "authorization": fast2sms_api_key,
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp_data = resp.read().decode('utf-8')
                logger.info("Fast2SMS API Response: %s", resp_data)
                parsed = json.loads(resp_data)
                if not parsed.get('return', False):
                    sms_sent = False
                    sms_error = parsed.get('message', ['SMS delivery not acknowledged by provider'])[0] if isinstance(parsed.get('message'), list) else str(parsed.get('message', 'SMS gateway error'))
        except Exception as exc:
            logger.error("Fast2SMS dispatch failed: %s", exc)
            sms_sent = False
            sms_error = str(exc)

    # Audit log and console terminal dispatch simulation
    logger.info("Delivery OTP SMS dispatched: Order=%s, Recipient=%s, Ref=%s, Provider=%s, Sent=%s",
                order.order_id, masked_phone, sms_ref, provider_used, sms_sent)
    print("\n" + "="*70)
    print(f"[FRESHMART SMS GATEWAY DISPATCH - {provider_used}]")
    print(f"Recipient Phone : {order.recipient_phone}")
    print(f"Recipient Name  : {order.recipient_name}")
    print(f"Order Reference : #{order.order_id}")
    print(f"Dispatched OTP  : >>> {raw_otp} <<< (Valid for 15 minutes)")
    print(f"SMS Content     : \"{sms_body}\"")
    print(f"Delivery Status : {'SUCCESS' if sms_sent else 'FAILED: ' + str(sms_error)}")
    print("="*70 + "\n")

    # In-app buyer notification so buyer receives the code directly on their portal
    if order.buyer:
        Notification.send(
            user=order.buyer,
            title=f"Delivery OTP for #{order.order_id}",
            message=f"Your delivery verification code is {raw_otp} (valid for 15 minutes). Share this with your delivery agent upon doorstep arrival.",
            category=Notification.Category.DELIVERY,
            link=f"/orders/track/{order.order_id}/"
        )

    return {
        'success': sms_sent,
        'sms_reference': sms_ref,
        'recipient_masked': masked_phone,
        'error': sms_error,
        'provider': provider_used,
    }
