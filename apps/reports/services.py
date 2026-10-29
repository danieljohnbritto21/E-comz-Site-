"""
Fresh Mart — Financial Services.

All financial calculations happen here with Python Decimal.
Provides settlement, cashback, wallet, delivery earning, and commission functions.
"""
import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone


def get_seller_commission_rate(seller_user):
    """
    Get the commission rate for a seller.
    Uses per-seller rate from SellerProfile if available and not None,
    otherwise falls back to MarketplaceConfig default (which defaults to 0.00%).
    """
    if hasattr(seller_user, 'seller_profile') and seller_user.seller_profile:
        rate = seller_user.seller_profile.commission_rate
        if rate is not None:
            return rate
    from apps.reports.models import MarketplaceConfig
    config = MarketplaceConfig.get_config()
    return config.commission_percentage if config and config.commission_percentage is not None else Decimal('0.00')


def calculate_commission(selling_price, quantity, commission_rate, commission_fixed=Decimal('0.00')):
    """
    Calculate marketplace commission for an order item.
    Returns (commission_amount, commission_per_unit).
    """
    base_amount = selling_price * quantity
    percentage_commission = (base_amount * commission_rate / Decimal('100')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    fixed_commission = (commission_fixed * quantity).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    total_commission = percentage_commission + fixed_commission
    return total_commission


def calculate_cashback_for_spend(spend_amount, config=None):
    """
    Calculate customer cashback from eligible selling price spend.
    Rule: ₹1 per ₹10 of eligible spend (10% by default).
    Exact decimal calculation with configured rounding policy (proportional decimal by default).
    """
    if not spend_amount or Decimal(str(spend_amount)) <= Decimal('0.00'):
        return Decimal('0.00')
    if config is None:
        from apps.reports.models import MarketplaceConfig
        config = MarketplaceConfig.get_config()

    rate_per_10 = getattr(config, 'cashback_rate_per_10', Decimal('1.00')) or Decimal('1.00')
    rounding_policy = getattr(config, 'cashback_rounding_policy', 'PROPORTIONAL')

    spend = Decimal(str(spend_amount))
    if rounding_policy == 'FLOOR_10':
        blocks = (spend // Decimal('10'))
        cashback = (blocks * rate_per_10).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        # Default proportional decimal: spend / 10 * rate_per_10
        cashback = (spend / Decimal('10') * rate_per_10).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return max(Decimal('0.00'), cashback)


def calculate_seller_financials(selling_price, product_cost, quantity, commission_rate,
                                 commission_fixed=Decimal('0.00'), config=None):
    """
    Calculate complete seller financial breakdown for an order item.
    Supports:
    1. PRODUCT_COST_RECEIVABLE (Prompt Default): The seller's Product Cost is the seller's receivable
       per sold unit (Buy Price model). Commission is not deducted a second time from Product Cost.
       For ₹100 Selling Price and ₹90 Product Cost:
       - Selling Price = ₹100
       - Product Cost / Seller Receivable = ₹90 per unit (₹90 * qty)
       - Customer pays ₹100 (subtotal)
       - Seller receives ₹90 (seller_receivable)
       - Cashback earned = ₹10 (Selling Price spend / 10 * 1)
       - Platform gross margin = ₹10
    2. COMMISSION_DEDUCTION: Traditional commission deduction where seller_payable = revenue - commission.
    """
    if config is None:
        from apps.reports.models import MarketplaceConfig
        config = MarketplaceConfig.get_config()

    settlement_mode = getattr(config, 'seller_settlement_mode', 'PRODUCT_COST_RECEIVABLE')
    product_revenue = (selling_price * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    commission_amount = calculate_commission(selling_price, quantity, commission_rate, commission_fixed)

    result = {
        'product_revenue': product_revenue,
        'commission_rate': commission_rate,
        'commission_fixed': commission_fixed,
        'commission_amount': commission_amount,
        'product_cost_total': None,
        'seller_receivable': None,
        'seller_payable': None,
        'seller_profit': None,
        'profit_available': False,
    }

    if product_cost is not None:
        product_cost_total = (product_cost * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        result['product_cost_total'] = product_cost_total
        result['profit_available'] = True

        if settlement_mode == 'PRODUCT_COST_RECEIVABLE':
            # Prompt rule: Seller receives Product Cost per unit sold!
            seller_receivable = product_cost_total
            result['seller_receivable'] = seller_receivable
            result['seller_payable'] = seller_receivable
            # Seller's internal profit is NOT known/tracked under this model
            result['seller_profit'] = None
        else:
            # Traditional commission deduction
            seller_payable = (product_revenue - commission_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            seller_profit = (seller_payable - product_cost_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            result['seller_receivable'] = seller_payable
            result['seller_payable'] = seller_payable
            result['seller_profit'] = seller_profit
    else:
        # Fallback when cost is not recorded
        seller_payable = (product_revenue - commission_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        result['seller_receivable'] = seller_payable
        result['seller_payable'] = seller_payable
        result['seller_profit'] = None

    return result


def create_order_financial_snapshots(order):
    """
    Create immutable financial snapshots for all items in an order.
    Called during checkout after order is created.
    Uses central marketplace configuration, seller receivable rules, and product costs.
    """
    from apps.reports.models import MarketplaceConfig, OrderItemFinancialSnapshot

    config = MarketplaceConfig.get_config()

    created_snapshots = []
    for item in order.items.select_related('product', 'seller', 'seller__seller_profile'):
        # Skip if snapshot already exists (idempotent)
        existing = OrderItemFinancialSnapshot.objects.filter(order_item=item).first()
        if existing:
            created_snapshots.append(existing)
            continue

        seller = item.seller
        commission_rate = get_seller_commission_rate(seller)
        commission_fixed = config.commission_fixed_per_unit

        product_cost = item.product_cost_snapshot  # Already snapshotted on OrderItem
        selling_price = item.unit_price
        quantity = item.quantity

        financials = calculate_seller_financials(
            selling_price, product_cost, quantity,
            commission_rate, commission_fixed, config=config
        )

        calc_mode = getattr(config, 'cashback_calculation_mode', 'SPEND_BASED')
        if calc_mode == 'SPEND_BASED':
            cb_total = calculate_cashback_for_spend(selling_price * quantity, config)
            cb_per_unit = calculate_cashback_for_spend(selling_price, config)
        else:
            cb_per_unit = getattr(config, 'cashback_per_unit', Decimal('1.00'))
            cb_total = (cb_per_unit * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        snap = OrderItemFinancialSnapshot.objects.create(
            order_item=item,
            order=order,
            seller=seller,
            selling_price_per_unit=selling_price,
            mrp_per_unit=item.mrp,
            product_cost_per_unit=product_cost,
            quantity=quantity,
            product_revenue=financials['product_revenue'],
            product_cost_total=financials['product_cost_total'],
            discount_amount=(item.mrp - selling_price) * quantity if item.mrp > selling_price else Decimal('0.00'),
            commission_rate=commission_rate,
            commission_fixed=commission_fixed,
            commission_amount=financials['commission_amount'],
            seller_receivable=financials['seller_receivable'],
            seller_payable=financials['seller_payable'],
            seller_profit=financials['seller_profit'],
            cashback_per_unit=cb_per_unit,
            cashback_total=cb_total,
            cashback_funding=config.cashback_funding,
            settlement_status='PENDING',
        )
        created_snapshots.append(snap)

    return created_snapshots


def award_buyer_cashback(order):
    """
    Award cashback to buyer after order completion.
    Uses immutable financial snapshots.
    Idempotent: will not duplicate cashback for the same order.
    """
    from apps.reports.models import CashbackLedger, OrderItemFinancialSnapshot

    buyer = order.buyer

    # Check if cashback was already awarded for this order
    existing = CashbackLedger.objects.filter(
        buyer=buyer, order=order, entry_type='EARNED'
    ).exists()
    if existing:
        return Decimal('0.00')

    snapshots = OrderItemFinancialSnapshot.objects.filter(order=order)
    total_cashback = Decimal('0.00')

    with transaction.atomic():
        # Calculate current buyer cashback balance
        from django.db.models import Sum
        current_balance = CashbackLedger.objects.filter(
            buyer=buyer, status='CONFIRMED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        for snapshot in snapshots:
            if snapshot.cashback_total <= Decimal('0.00'):
                continue

            cb_amount = snapshot.cashback_total
            new_balance = current_balance + cb_amount

            CashbackLedger.objects.create(
                buyer=buyer,
                order=order,
                order_item=snapshot.order_item,
                entry_type='EARNED',
                amount=cb_amount,
                balance_before=current_balance,
                balance_after=new_balance,
                status='CONFIRMED',
                funding_source=snapshot.cashback_funding,
                description=f"Cashback earned: {snapshot.quantity} unit(s) × ₹{snapshot.cashback_per_unit} for order #{order.order_id}",
            )
            current_balance = new_balance
            total_cashback += cb_amount

        # Update order cashback_earned
        order.cashback_earned = total_cashback
        order.save(update_fields=['cashback_earned', 'updated_at'])

    return total_cashback


def reverse_buyer_cashback(order, reason=''):
    """
    Reverse cashback for a cancelled/refunded order.
    Creates REVERSED entries linked to original EARNED entries.
    """
    from apps.reports.models import CashbackLedger
    from django.db.models import Sum

    earned_entries = CashbackLedger.objects.filter(
        buyer=order.buyer, order=order, entry_type='EARNED', status='CONFIRMED'
    )

    if not earned_entries.exists():
        return Decimal('0.00')

    total_reversed = Decimal('0.00')

    with transaction.atomic():
        current_balance = CashbackLedger.objects.filter(
            buyer=order.buyer, status='CONFIRMED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        for earned in earned_entries:
            # Skip if already reversed
            if CashbackLedger.objects.filter(
                related_entry=earned, entry_type='REVERSED'
            ).exists():
                continue

            reverse_amount = -abs(earned.amount)
            new_balance = current_balance + reverse_amount

            CashbackLedger.objects.create(
                buyer=order.buyer,
                order=order,
                order_item=earned.order_item,
                entry_type='REVERSED',
                amount=reverse_amount,
                balance_before=current_balance,
                balance_after=new_balance,
                status='CONFIRMED',
                funding_source=earned.funding_source,
                description=reason or f"Cashback reversed for cancelled order #{order.order_id}",
                related_entry=earned,
            )
            earned.status = 'REVERSED'
            earned.save(update_fields=['status'])
            current_balance = new_balance
            total_reversed += abs(earned.amount)

    return total_reversed


def get_buyer_cashback_balance(buyer):
    """Get the current available (spendable) cashback balance for a buyer."""
    from apps.reports.models import CashbackLedger
    from django.db.models import Sum
    balance = CashbackLedger.objects.filter(
        buyer=buyer, status='CONFIRMED'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    return max(balance, Decimal('0.00'))


def get_buyer_cashback_details(buyer):
    """
    Get complete buyer cashback wallet details:
    - available_balance: Spendable balance from confirmed settlements
    - pending_balance: Earnings on placed/in-transit orders awaiting delivery OTP verification
    - total_earned: Cumulative settled cashback credits
    - total_redeemed: Cumulative cashback spent at checkout
    - total_reversed: Cumulative reversals (due to returns/cancellations)
    - recent_entries: Audit ledger transactions
    """
    from apps.reports.models import CashbackLedger, OrderItemFinancialSnapshot
    from apps.orders.models import Order
    from django.db.models import Sum

    available_balance = get_buyer_cashback_balance(buyer)

    # Pending cashback: Orders that are not yet delivered/cancelled, awaiting OTP completion
    pending_statuses = [
        Order.Status.ORDER_PLACED,
        Order.Status.ORDER_CONFIRMED,
        Order.Status.PREPARING,
        Order.Status.PACKED,
        Order.Status.READY_FOR_PICKUP,
        Order.Status.OUT_FOR_DELIVERY,
    ]
    pending_balance = OrderItemFinancialSnapshot.objects.filter(
        order__buyer=buyer,
        order__status__in=pending_statuses,
        settlement_status='PENDING'
    ).aggregate(total=Sum('cashback_total'))['total'] or Decimal('0.00')

    # Breakdown metrics from ledger
    confirmed_entries = CashbackLedger.objects.filter(buyer=buyer, status='CONFIRMED')
    total_earned = confirmed_entries.filter(entry_type='EARNED').aggregate(
        total=Sum('amount'))['total'] or Decimal('0.00')

    redeemed_entries = confirmed_entries.filter(entry_type='REDEEMED').aggregate(
        total=Sum('amount'))['total'] or Decimal('0.00')
    total_redeemed = abs(redeemed_entries)

    reversed_entries = confirmed_entries.filter(entry_type='REVERSED').aggregate(
        total=Sum('amount'))['total'] or Decimal('0.00')
    total_reversed = abs(reversed_entries)

    recent_entries = CashbackLedger.objects.filter(buyer=buyer).select_related('order').order_by('-created_at')[:15]

    return {
        'available_balance': available_balance,
        'pending_balance': max(pending_balance, Decimal('0.00')),
        'total_earned': total_earned,
        'total_redeemed': total_redeemed,
        'total_reversed': total_reversed,
        'recent_entries': recent_entries,
    }


def redeem_buyer_cashback(order, amount):
    """
    Redeem cashback towards order payment.
    Creates a REDEEMED entry in CashbackLedger.
    """
    from apps.reports.models import CashbackLedger
    if not amount or amount <= Decimal('0.00'):
        return Decimal('0.00')

    buyer = order.buyer
    with transaction.atomic():
        current_balance = get_buyer_cashback_balance(buyer)
        actual_redeem = min(current_balance, amount)
        if actual_redeem <= Decimal('0.00'):
            return Decimal('0.00')

        redeem_negative = -abs(actual_redeem)
        new_balance = current_balance + redeem_negative

        CashbackLedger.objects.create(
            buyer=buyer,
            order=order,
            entry_type='REDEEMED',
            amount=redeem_negative,
            balance_before=current_balance,
            balance_after=new_balance,
            status='CONFIRMED',
            funding_source='COMPANY',
            description=f"Cashback redeemed for order #{order.order_id}",
        )
        order.cashback_redeemed = actual_redeem
        order.save(update_fields=['cashback_redeemed', 'updated_at'])
        return actual_redeem


def restore_redeemed_cashback(order, reason=''):
    """
    Refund/restore redeemed cashback for a cancelled order.
    """
    from apps.reports.models import CashbackLedger
    redeemed_entries = CashbackLedger.objects.filter(
        buyer=order.buyer, order=order, entry_type='REDEEMED', status='CONFIRMED'
    )
    if not redeemed_entries.exists():
        return Decimal('0.00')

    total_restored = Decimal('0.00')
    with transaction.atomic():
        current_balance = get_buyer_cashback_balance(order.buyer)
        for entry in redeemed_entries:
            if CashbackLedger.objects.filter(related_entry=entry, entry_type='REFUNDED').exists():
                continue
            restore_amount = abs(entry.amount)
            new_balance = current_balance + restore_amount
            CashbackLedger.objects.create(
                buyer=order.buyer,
                order=order,
                entry_type='REFUNDED',
                amount=restore_amount,
                balance_before=current_balance,
                balance_after=new_balance,
                status='CONFIRMED',
                funding_source=entry.funding_source,
                description=reason or f"Restored redeemed cashback from cancelled order #{order.order_id}",
                related_entry=entry,
            )
            current_balance = new_balance
            total_restored += restore_amount
    return total_restored


def get_or_create_seller_wallet(seller):
    """Get or create a seller wallet."""
    from apps.reports.models import SellerWallet
    wallet, _ = SellerWallet.objects.get_or_create(seller=seller)
    return wallet


def settle_seller_profit(order):
    """
    Settle seller profit for all eligible items in an order.
    Called after delivery confirmation + payment verification.
    Idempotent: uses unique idempotency keys per order-item.
    Credits PROFIT (not full payable) to wallet.
    """
    from apps.reports.models import (
        OrderItemFinancialSnapshot, SellerWalletTransaction, MarketplaceConfig
    )

    snapshots = OrderItemFinancialSnapshot.objects.filter(
        order=order, settlement_status='PENDING'
    ).select_related('order_item', 'seller')

    total_settled = Decimal('0.00')
    for snapshot in snapshots:
        idem_key = f"SETTLE-{order.order_id}-OI-{snapshot.order_item_id}"

        # Check idempotency
        if SellerWalletTransaction.objects.filter(idempotency_key=idem_key).exists():
            snapshot.settlement_status = 'SETTLED'
            snapshot.settled_at = timezone.now()
            snapshot.save(update_fields=['settlement_status', 'settled_at'])
            continue

        # Determine credit amount based on seller settlement rule
        config = MarketplaceConfig.get_config()
        if getattr(config, 'seller_settlement_mode', 'PRODUCT_COST_RECEIVABLE') == 'PRODUCT_COST_RECEIVABLE':
            credit_amount = snapshot.effective_seller_receivable
        else:
            credit_amount = snapshot.seller_profit if snapshot.seller_profit is not None else snapshot.seller_payable

        if credit_amount <= Decimal('0.00'):
            snapshot.settlement_status = 'SETTLED'
            snapshot.settled_at = timezone.now()
            snapshot.save(update_fields=['settlement_status', 'settled_at'])
            continue

        with transaction.atomic():
            wallet = get_or_create_seller_wallet(snapshot.seller)
            # Lock wallet row
            wallet = type(wallet).objects.select_for_update().get(pk=wallet.pk)

            balance_before = wallet.available_balance
            balance_after = balance_before + credit_amount

            SellerWalletTransaction.objects.create(
                wallet=wallet,
                seller=snapshot.seller,
                order=order,
                order_item=snapshot.order_item,
                transaction_type='PROFIT_CREDIT',
                amount=credit_amount,
                balance_before=balance_before,
                balance_after=balance_after,
                status='COMPLETED',
                description=f"Seller receivable settlement for {snapshot.order_item.product_name} x{snapshot.quantity} in order #{order.order_id}",
                idempotency_key=idem_key,
                settled_at=timezone.now(),
            )

            wallet.available_balance = balance_after
            wallet.lifetime_credited += credit_amount
            # Decrease pending if it was tracked
            if wallet.pending_earnings >= credit_amount:
                wallet.pending_earnings -= credit_amount
            else:
                wallet.pending_earnings = Decimal('0.00')
            wallet.save()

            snapshot.settlement_status = 'SETTLED'
            snapshot.settled_at = timezone.now()
            snapshot.save(update_fields=['settlement_status', 'settled_at'])
            total_settled += credit_amount

    return total_settled


def add_pending_seller_earnings(order):
    """
    Record pending earnings for sellers when order is placed/confirmed.
    """
    from apps.reports.models import (
        OrderItemFinancialSnapshot, SellerWalletTransaction, MarketplaceConfig
    )

    snapshots = OrderItemFinancialSnapshot.objects.filter(
        order=order
    ).select_related('seller')

    for snapshot in snapshots:
        idem_key = f"PENDING-{order.order_id}-OI-{snapshot.order_item_id}"

        if SellerWalletTransaction.objects.filter(idempotency_key=idem_key).exists():
            continue

        config = MarketplaceConfig.get_config()
        if getattr(config, 'seller_settlement_mode', 'PRODUCT_COST_RECEIVABLE') == 'PRODUCT_COST_RECEIVABLE':
            credit_amount = snapshot.effective_seller_receivable
        else:
            credit_amount = snapshot.seller_profit if snapshot.seller_profit is not None else snapshot.seller_payable
        if credit_amount <= Decimal('0.00'):
            continue

        with transaction.atomic():
            wallet = get_or_create_seller_wallet(snapshot.seller)
            wallet = type(wallet).objects.select_for_update().get(pk=wallet.pk)

            SellerWalletTransaction.objects.create(
                wallet=wallet,
                seller=snapshot.seller,
                order=order,
                order_item=snapshot.order_item,
                transaction_type='EARNING_PENDING',
                amount=credit_amount,
                balance_before=wallet.available_balance,
                balance_after=wallet.available_balance,  # Pending doesn't change available
                status='PENDING',
                description=f"Pending earning for {snapshot.order_item.product_name} in order #{order.order_id}",
                idempotency_key=idem_key,
            )

            wallet.pending_earnings += credit_amount
            wallet.save(update_fields=['pending_earnings', 'updated_at'])


def reverse_seller_profit(order, reason=''):
    """
    Reverse settled seller profit for cancelled/refunded order.
    Creates REVERSAL transactions linked to original PROFIT_CREDIT.
    """
    from apps.reports.models import (
        OrderItemFinancialSnapshot, SellerWalletTransaction
    )

    snapshots = OrderItemFinancialSnapshot.objects.filter(
        order=order
    ).select_related('seller')

    for snapshot in snapshots:
        if snapshot.settlement_status == 'REVERSED':
            continue

        # Find the original credit transaction
        original_txn = SellerWalletTransaction.objects.filter(
            order=order,
            order_item=snapshot.order_item,
            transaction_type='PROFIT_CREDIT',
            status='COMPLETED',
        ).first()

        if not original_txn:
            # If not yet settled, just cancel the pending
            pending_txn = SellerWalletTransaction.objects.filter(
                order=order,
                order_item=snapshot.order_item,
                transaction_type='EARNING_PENDING',
                status='PENDING',
            ).first()
            if pending_txn:
                with transaction.atomic():
                    wallet = get_or_create_seller_wallet(snapshot.seller)
                    wallet = type(wallet).objects.select_for_update().get(pk=wallet.pk)
                    pending_txn.status = 'REVERSED'
                    pending_txn.save(update_fields=['status'])
                    wallet.pending_earnings = max(
                        Decimal('0.00'), wallet.pending_earnings - abs(pending_txn.amount)
                    )
                    wallet.save(update_fields=['pending_earnings', 'updated_at'])

            snapshot.settlement_status = 'CANCELLED'
            snapshot.save(update_fields=['settlement_status'])
            continue

        idem_key = f"REVERSE-{order.order_id}-OI-{snapshot.order_item_id}"
        if SellerWalletTransaction.objects.filter(idempotency_key=idem_key).exists():
            continue

        reversal_amount = -abs(original_txn.amount)

        with transaction.atomic():
            wallet = get_or_create_seller_wallet(snapshot.seller)
            wallet = type(wallet).objects.select_for_update().get(pk=wallet.pk)

            balance_before = wallet.available_balance
            balance_after = balance_before + reversal_amount

            SellerWalletTransaction.objects.create(
                wallet=wallet,
                seller=snapshot.seller,
                order=order,
                order_item=snapshot.order_item,
                transaction_type='REVERSAL',
                amount=reversal_amount,
                balance_before=balance_before,
                balance_after=balance_after,
                status='COMPLETED',
                description=reason or f"Profit reversal for cancelled order #{order.order_id}",
                idempotency_key=idem_key,
                related_transaction=original_txn,
                settled_at=timezone.now(),
            )

            wallet.available_balance = balance_after
            wallet.save(update_fields=['available_balance', 'updated_at'])

            snapshot.settlement_status = 'REVERSED'
            snapshot.save(update_fields=['settlement_status'])


def create_delivery_earning(order, assignment):
    """
    Create delivery earning record after successful delivery.
    Idempotent: uses unique key per order+delivery_person.
    """
    from apps.reports.models import MarketplaceConfig, DeliveryEarning

    config = MarketplaceConfig.get_config()
    idem_key = f"DE-{order.order_id}-{assignment.delivery_person_id}"

    if DeliveryEarning.objects.filter(idempotency_key=idem_key).exists():
        return None

    earning = DeliveryEarning.objects.create(
        delivery_person=assignment.delivery_person,
        order=order,
        assignment=assignment,
        earning_amount=config.delivery_person_earning_per_order,
        delivery_fee_charged=order.delivery_fee,
        status='SETTLED',
        idempotency_key=idem_key,
        description=f"Delivery earning for order #{order.order_id}",
        settled_at=timezone.now(),
    )
    return earning


def reverse_delivery_earning(order):
    """Reverse delivery earning for a cancelled order."""
    from apps.reports.models import DeliveryEarning

    earnings = DeliveryEarning.objects.filter(
        order=order, status='SETTLED'
    )
    for earning in earnings:
        earning.status = 'REVERSED'
        earning.save(update_fields=['status'])
