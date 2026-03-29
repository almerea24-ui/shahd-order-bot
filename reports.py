#!/usr/bin/env python3
"""
Sales reports — daily, weekly, and custom date range reports from Odoo.
"""

import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from config import CARRIER_MAP
from odoo_client import OdooRPC

logger = logging.getLogger(__name__)


def _format_number(n):
    """Format number with commas for Iraqi dinar display."""
    if n >= 1000:
        return f"{n:,.0f}"
    return str(int(n))


def generate_daily_report(brand: str = None) -> str:
    """Generate today's sales report."""
    rpc = OdooRPC()
    today = datetime.now().strftime('%Y-%m-%d')
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    carrier_id = None
    if brand and brand in CARRIER_MAP:
        carrier_id = CARRIER_MAP[brand]['carrier_id']

    orders = rpc.get_orders_by_date(
        f"{today} 00:00:00", f"{tomorrow} 00:00:00",
        brand_carrier_id=carrier_id
    )

    if not orders:
        brand_name = _brand_display(brand)
        return f"📊 تقرير اليوم {today}\n{brand_name}\n\nلا توجد طلبات اليوم."

    return _format_report(orders, f"تقرير اليوم {today}", brand, rpc)


def generate_weekly_report(brand: str = None) -> str:
    """Generate this week's sales report (Saturday to today)."""
    rpc = OdooRPC()
    today = datetime.now()
    # Saturday as week start (Iraqi business week)
    days_since_sat = (today.weekday() + 2) % 7
    week_start = today - timedelta(days=days_since_sat)
    tomorrow = today + timedelta(days=1)

    carrier_id = None
    if brand and brand in CARRIER_MAP:
        carrier_id = CARRIER_MAP[brand]['carrier_id']

    orders = rpc.get_orders_by_date(
        week_start.strftime('%Y-%m-%d 00:00:00'),
        tomorrow.strftime('%Y-%m-%d 00:00:00'),
        brand_carrier_id=carrier_id
    )

    period = f"{week_start.strftime('%Y-%m-%d')} إلى {today.strftime('%Y-%m-%d')}"
    if not orders:
        brand_name = _brand_display(brand)
        return f"📊 تقرير الأسبوع\n{period}\n{brand_name}\n\nلا توجد طلبات هذا الأسبوع."

    return _format_report(orders, f"تقرير الأسبوع ({period})", brand, rpc)


def generate_custom_report(date_from: str, date_to: str, brand: str = None) -> str:
    """Generate report for a custom date range."""
    rpc = OdooRPC()
    carrier_id = None
    if brand and brand in CARRIER_MAP:
        carrier_id = CARRIER_MAP[brand]['carrier_id']

    orders = rpc.get_orders_by_date(
        f"{date_from} 00:00:00", f"{date_to} 23:59:59",
        brand_carrier_id=carrier_id
    )

    period = f"{date_from} إلى {date_to}"
    if not orders:
        brand_name = _brand_display(brand)
        return f"📊 تقرير مخصص\n{period}\n{brand_name}\n\nلا توجد طلبات في هذه الفترة."

    return _format_report(orders, f"تقرير ({period})", brand, rpc)


def _brand_display(brand):
    if brand == 'shahd':
        return "🟣 شهد بيوتي"
    elif brand == 'marlin':
        return "🔵 مارلين"
    return "🔘 الكل"


def _format_report(orders, title, brand, rpc):
    """Format a report from a list of orders."""
    total_revenue = sum(o.get('amount_total', 0) for o in orders)
    order_count = len(orders)
    avg_order = total_revenue / order_count if order_count else 0

    # Count by state
    state_counter = Counter()
    for o in orders:
        # Try to get state from partner
        partner = o.get('partner_id')
        if partner and isinstance(partner, (list, tuple)):
            state_counter[partner[1] if len(partner) > 1 else 'غير محدد'] += 1

    # Product frequency
    product_counter = Counter()
    for o in orders:
        try:
            lines = rpc.get_order_lines(o['id'])
            for line in lines:
                if not line.get('is_delivery', False):
                    product_name = line.get('product_id', [0, '?'])
                    if isinstance(product_name, (list, tuple)):
                        product_name = product_name[1]
                    qty = line.get('product_uom_qty', 1)
                    product_counter[product_name] += int(qty)
        except Exception as e:
            logger.warning(f"Error reading order lines for {o.get('name')}: {e}")

    brand_name = _brand_display(brand)

    # Build report
    report = f"📊 {title}\n{brand_name}\n\n"
    report += f"📦 عدد الطلبات: {order_count}\n"
    report += f"💰 إجمالي المبيعات: {_format_number(total_revenue)} د.ع\n"
    report += f"📈 متوسط الطلب: {_format_number(avg_order)} د.ع\n"

    # Top products
    if product_counter:
        report += "\n🏆 أكثر المنتجات مبيعاً:\n"
        for product, qty in product_counter.most_common(10):
            report += f"  {qty}x {product}\n"

    # Top provinces (from customer names - simplified)
    if state_counter:
        report += "\n🗺 التوزيع الجغرافي:\n"
        for state, count in state_counter.most_common(10):
            pct = (count / order_count) * 100
            report += f"  {state}: {count} ({pct:.0f}%)\n"

    return report


def format_search_results(orders) -> str:
    """Format order search results for display."""
    if not orders:
        return "لم يتم العثور على نتائج."

    text = f"🔍 تم العثور على {len(orders)} نتيجة:\n\n"
    for o in orders:
        partner = o.get('partner_id', [0, '?'])
        name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else str(partner)
        state = '✅' if o.get('state') in ('sale', 'done') else '⏳'
        date = o.get('date_order', '')[:10] if o.get('date_order') else '?'
        total = _format_number(o.get('amount_total', 0))

        text += f"{state} {o.get('name', '?')} — {name}\n"
        text += f"   💰 {total} د.ع | 📅 {date}\n\n"

    return text
