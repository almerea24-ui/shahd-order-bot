#!/usr/bin/env python3
"""
Duplicate order detection — prevents the same order from being entered twice.
Checks by phone number + similar products within a time window.
"""

import time
import logging
from collections import defaultdict
from config import DUPLICATE_WINDOW_MINUTES

logger = logging.getLogger(__name__)

# In-memory store: phone -> [(timestamp, products_set, order_key), ...]
_recent_orders: dict[str, list] = defaultdict(list)


def _cleanup_old(phone: str):
    """Remove entries older than the duplicate window."""
    cutoff = time.time() - (DUPLICATE_WINDOW_MINUTES * 60)
    _recent_orders[phone] = [
        entry for entry in _recent_orders[phone]
        if entry[0] > cutoff
    ]


def _products_fingerprint(products: list) -> frozenset:
    """Create a fingerprint from product list for comparison."""
    items = []
    for p in products:
        name = p.get('name', '').strip().lower()
        qty = p.get('quantity', 1)
        items.append(f"{name}:{qty}")
    return frozenset(items)


def check_duplicate(parsed_order: dict) -> tuple[bool, str]:
    """
    Check if this order looks like a duplicate.
    Returns (True, warning_message) if duplicate, (False, '') if OK.
    """
    phone = parsed_order.get('phone', '').strip()
    if not phone or len(phone) < 8:
        return False, ''  # Can't check without phone

    _cleanup_old(phone)
    new_products = _products_fingerprint(parsed_order.get('products', []))
    now = time.time()

    for timestamp, old_products, old_key in _recent_orders[phone]:
        # Check product overlap
        if new_products and old_products:
            overlap = len(new_products & old_products) / max(len(new_products), len(old_products))
            if overlap >= 0.7:  # 70%+ overlap
                minutes_ago = int((now - timestamp) / 60)
                msg = f"طلب مكرر محتمل! نفس الرقم {phone} أرسل طلباً مشابهاً قبل {minutes_ago} دقيقة ({int(overlap * 100)}% تطابق)."
                return True, msg

    return False, ''


def register_order(parsed_order: dict, order_key: str):
    """Register a confirmed order to the duplicate tracker."""
    phone = parsed_order.get('phone', '').strip()
    if not phone:
        return
    products = _products_fingerprint(parsed_order.get('products', []))
    _recent_orders[phone].append((time.time(), products, order_key))
    logger.info(f"Registered order for duplicate tracking: phone={phone}, key={order_key}")


def get_recent_count(phone: str) -> int:
    """Get number of recent orders for this phone number."""
    _cleanup_old(phone)
    return len(_recent_orders[phone])
