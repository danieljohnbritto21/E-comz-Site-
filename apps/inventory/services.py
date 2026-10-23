from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from apps.inventory.models import StockRecord, StockMovement


def validate_purchase_quantity(product, requested_quantity, current_cart_quantity=0):
    """
    Validates a requested purchase quantity against:
    1. Positive quantity, Decimal precision without floating point inaccuracies
    2. Available stock
    3. Per-order limit for bike delivery (default 5 for kg, litre, bunch, packet, piece, bottle, box)
    4. Actual permitted maximum = min(available_stock, per_order_limit)
    5. Minimum purchase quantity & quantity step
    6. Whole-unit validation for whole-unit items (packets, pieces, bunches, boxes, bottles, etc.)
    7. Prevents rounding down to exceed limits

    Returns (is_valid: bool, error_message: str, actual_max_order_quantity: int).
    """
    try:
        if isinstance(requested_quantity, str):
            dec_requested = Decimal(requested_quantity.strip())
        else:
            dec_requested = Decimal(str(requested_quantity))
    except (ValueError, TypeError, InvalidOperation):
        return False, "Requested quantity must be a valid whole number.", getattr(product, 'actual_max_order_quantity', 0)

    if dec_requested <= Decimal('0'):
        return False, "Quantity must be a positive number greater than zero.", getattr(product, 'actual_max_order_quantity', 0)

    available_stock = getattr(product, 'current_stock_count', 0)
    per_order_limit = getattr(product, 'per_order_limit', 5)
    actual_max = getattr(product, 'actual_max_order_quantity', min(available_stock, per_order_limit))

    dec_available_stock = Decimal(str(available_stock))
    dec_per_order_limit = Decimal(str(per_order_limit))
    dec_actual_max = Decimal(str(actual_max))
    dec_current_cart = Decimal(str(current_cart_quantity))

    unit_str = product.get_unit_label(available_stock)
    limit_unit = product.get_unit_label(per_order_limit)
    req_unit = product.get_unit_label(int(dec_requested) if dec_requested % Decimal('1') == Decimal('0') else 2)

    if available_stock <= 0:
        return False, f"'{product.name}' is currently out of stock.", 0

    dec_total_requested = dec_current_cart + dec_requested

    # Validate against actual permitted maximum using exact Decimal arithmetic (never round down)
    if dec_total_requested > dec_actual_max:
        if available_stock < per_order_limit:
            if current_cart_quantity > 0:
                return False, (
                    f"Cannot add {dec_requested} {req_unit}. You already have {current_cart_quantity} in your basket, "
                    f"and only {available_stock} {unit_str} of '{product.name}' are available in stock."
                ), actual_max
            else:
                return False, (
                    f"Cannot add {dec_requested} {req_unit}. Only {available_stock} {unit_str} of '{product.name}' available in stock."
                ), actual_max
        else:
            if current_cart_quantity > 0:
                return False, (
                    f"You can order a maximum of {per_order_limit} {limit_unit} of '{product.name}' per order. "
                    f"You already have {current_cart_quantity} in your basket."
                ), actual_max
            else:
                return False, (
                    f"You can order a maximum of {per_order_limit} {limit_unit} of '{product.name}' per order."
                ), actual_max

    # Precision & whole-unit rules
    u = (getattr(product, 'unit', '') or 'pcs').lower().strip()
    whole_units = {'packet', 'packets', 'pack', 'packs', 'piece', 'pieces', 'pc', 'pcs', 'bunch', 'bunches', 'box', 'boxes', 'bottle', 'bottles', 'dozen', 'dozens'}
    if dec_requested % Decimal('1') != Decimal('0'):
        if u in whole_units:
            return False, f"'{product.name}' is sold in whole units ({req_unit}). Fractional quantities are not permitted.", actual_max
        return False, f"Requested quantity for '{product.name}' must be a valid whole number.", actual_max

    requested_qty = int(dec_requested)
    total_requested = current_cart_quantity + requested_qty

    min_qty = 1
    if total_requested < min_qty:
        return False, f"Minimum purchase quantity for '{product.name}' is 1.", actual_max

    step = 1
    if (total_requested - min_qty) % step != 0:
        return False, f"Quantity for '{product.name}' must be purchased in increments of 1.", actual_max

    return True, "", actual_max


class InventoryError(Exception):
    """Custom exception raised for inventory validation or deduction failures."""
    pass


def deduct_stock_for_order(order, buyer_user=None):
    """
    Atomically and safely reserves/deducts stock for all items in an order.
    Ensures stock is deducted exactly once, preventing double deduction.
    Sorts product IDs to avoid database deadlocks during concurrent multi-item checkouts.
    Strictly verifies per-order bike delivery limits and available inventory.
    """
    if buyer_user is None:
        buyer_user = getattr(order, 'buyer', None)

    with transaction.atomic():
        # Lock the order row if saved
        if order.pk:
            locked_order = order.__class__.objects.select_for_update().get(pk=order.pk)
            if locked_order.stock_deducted:
                return locked_order

        # Sort items by product_id to ensure deadlock-free row locking
        items_list = sorted(list(order.items.select_related('product')), key=lambda it: it.product_id or 0)
        
        # Aggregate totals per product across order items to prevent bypassing via duplicate order lines
        product_totals = {}
        for item in items_list:
            if item.product_id:
                product_totals[item.product_id] = product_totals.get(item.product_id, 0) + item.quantity

        for item in items_list:
            if not item.product_id:
                continue
            total_ordered = product_totals[item.product_id]
            limit = item.product.per_order_limit
            if total_ordered > limit:
                limit_unit = item.product.get_unit_label(limit)
                raise InventoryError(
                    f"You can order a maximum of {limit} {limit_unit} of '{item.product_name}' per order."
                )

            # Row-level lock on the stock record
            record = StockRecord.objects.select_for_update().get(product_id=item.product_id)
            if record.current_stock < item.quantity:
                unit_avail = item.product.get_unit_label(record.current_stock)
                unit_req = item.product.get_unit_label(item.quantity)
                raise InventoryError(
                    f"Insufficient stock for {item.product_name}. "
                    f"Available: {record.current_stock} {unit_avail}, Requested: {item.quantity} {unit_req}."
                )
            record.adjust_stock(
                -item.quantity,
                StockMovement.ChangeType.SALE,
                user=buyer_user,
                notes=f"Reserved for order checkout #{order.order_id} by {getattr(buyer_user, 'username', 'Customer')}"
            )

        order.stock_deducted = True
        order.stock_restored = False
        if order.pk:
            order.save(update_fields=['stock_deducted', 'stock_restored', 'updated_at'])
        return order


def restore_stock_for_order(order, actor=None, reason="", cancelled_by=None):
    """
    Atomically restores stock for all items in a cancelled/failed order.
    Guarantees stock is restored ONLY ONCE and only if previously deducted.
    """
    if actor is None and cancelled_by is not None:
        actor = cancelled_by

    with transaction.atomic():
        locked_order = order.__class__.objects.select_for_update().get(pk=order.pk)
        if not locked_order.stock_deducted or locked_order.stock_restored:
            return locked_order  # Already restored or was never deducted

        items_list = sorted(list(locked_order.items.select_related('product')), key=lambda it: it.product_id or 0)
        for item in items_list:
            if not item.product_id:
                continue
            record = StockRecord.objects.select_for_update().filter(product_id=item.product_id).first()
            if record:
                record.adjust_stock(
                    item.quantity,
                    StockMovement.ChangeType.CANCEL_RESTORE,
                    user=actor,
                    notes=reason or f"Restored from cancelled order #{locked_order.order_id}"
                )

        locked_order.stock_restored = True
        locked_order.save(update_fields=['stock_restored', 'updated_at'])
        return locked_order


def restore_stock_for_sub_order(sub_order, actor=None, reason=""):
    """
    Atomically restores stock for a specific seller sub-order (e.g. seller rejection).
    Guarantees restoration happens only once.
    """
    with transaction.atomic():
        locked_sub = sub_order.__class__.objects.select_for_update().get(pk=sub_order.pk)
        if locked_sub.stock_restored:
            return locked_sub

        items_list = sorted(list(locked_sub.items.select_related('product')), key=lambda it: it.product_id or 0)
        for item in items_list:
            if not item.product_id:
                continue
            record = StockRecord.objects.select_for_update().filter(product_id=item.product_id).first()
            if record:
                record.adjust_stock(
                    item.quantity,
                    StockMovement.ChangeType.CANCEL_RESTORE,
                    user=actor,
                    notes=reason or f"Restored from rejected sub-order #{locked_sub.sub_order_id}"
                )

        locked_sub.stock_restored = True
        locked_sub.save(update_fields=['stock_restored', 'updated_at'])
        return locked_sub
