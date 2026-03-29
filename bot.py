#!/usr/bin/env python3
"""
Shahd Beauty / Marlen Telegram Order Bot v2.
Supports: order entry, reports, stock check, search, duplicate detection, user access control.
"""

import time
import logging
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from config import (
    BOT_TOKEN, ODOO_URL, PROVINCE_MAP, CARRIER_MAP,
    AUTHORIZED_USERS, ADMIN_USERS, IRAQ_COUNTRY_ID, WHATSAPP_TAG_ID,
    DISCOUNT_PRODUCT_ID,
)
from odoo_client import OdooRPC
from matching import resolve_product_name, find_product, find_city
from parse_order import parse_with_llm
from duplicate_guard import check_duplicate, register_order
from reports import (
    generate_daily_report, generate_weekly_report,
    format_search_results,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============ Access Control ============

def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not AUTHORIZED_USERS:
        return True  # No whitelist = open access
    return user_id in AUTHORIZED_USERS

def is_admin(user_id: int) -> bool:
    """Check if user has admin access."""
    if not ADMIN_USERS:
        return is_authorized(user_id)  # No admin list = all authorized are admins
    return user_id in ADMIN_USERS

async def check_access(update: Update) -> bool:
    """Check access and reply with error if not authorized."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(
            "⛔ ما عندك صلاحية تستخدم هذا البوت.\n"
            f"الـ ID مالتك: {update.effective_user.id}\n"
            "تواصل مع المدير لإضافتك."
        )
        return False
    return True


# ============ Odoo Order Functions ============

def create_full_order(order_data, brand):
    """Create complete order in Odoo. Returns dict with result info."""
    rpc = OdooRPC()

    # 1. Create customer
    name = order_data.get("customer_name", "").strip()
    phone = order_data.get("phone", "")
    if not name:
        name = phone if phone else "عميل غير محدد"
    province = order_data.get("province", "")
    state_id = PROVINCE_MAP.get(province, False)

    customer_vals = {
        'name': name, 'phone': phone, 'is_company': False,
        'customer_rank': 1, 'country_id': IRAQ_COUNTRY_ID,
        'category_id': [(4, WHATSAPP_TAG_ID)],
    }
    if state_id:
        customer_vals['state_id'] = state_id

    city_name = order_data.get("city", "")
    street = order_data.get("street", "")
    city_matched = False

    if city_name and state_id:
        city = find_city(rpc, city_name, state_id)
        if city:
            customer_vals['x_studio_city'] = city['id']
            city_matched = True
        else:
            # Fallback: try each word/phrase from street
            if street:
                street_words = street.split()
                for i in range(len(street_words)):
                    for length in [2, 1]:
                        if i + length <= len(street_words):
                            candidate = ' '.join(street_words[i:i+length])
                            city_result = find_city(rpc, candidate, state_id)
                            if city_result:
                                customer_vals['x_studio_city'] = city_result['id']
                                city_matched = True
                                break
                    if city_matched:
                        break

            # Try combining city + first word of street
            if not city_matched and street:
                combined = f"{city_name} {street.split()[0]}"
                city_result = find_city(rpc, combined, state_id)
                if city_result:
                    customer_vals['x_studio_city'] = city_result['id']
                    city_matched = True

            if not city_matched:
                customer_vals['city'] = city_name
    elif city_name:
        customer_vals['city'] = city_name

    nearest = order_data.get("nearest_landmark", "")
    if street:
        customer_vals['street'] = street
    if nearest:
        customer_vals['street2'] = nearest

    partner_id = rpc.create('res.partner', customer_vals)

    # 2. Create sale order
    carrier = CARRIER_MAP.get(brand, CARRIER_MAP["shahd"])
    notes_parts = []
    order_notes = order_data.get("notes", "")
    if order_notes:
        notes_parts.append(order_notes)

    order_vals = {
        'partner_id': partner_id,
        'partner_shipping_id': partner_id,
    }
    if notes_parts:
        order_vals['x_shipping_notes'] = " / ".join(notes_parts)

    order_id = rpc.create('sale.order', order_vals)

    # 3. Add products
    products_data = order_data.get("products", [])
    unmatched = []
    matched_products = []
    products_total = 0
    low_stock = []

    for item in products_data:
        product = find_product(rpc, item["name"])
        if product:
            qty = item.get('quantity', 1)
            is_gift = item.get('is_gift', False)
            line_vals = {
                'order_id': order_id,
                'product_id': product['id'],
                'product_uom_qty': qty,
            }
            if is_gift:
                line_vals['price_unit'] = 0
                line_vals['name'] = f"{product['name']} (هدية)"
            rpc.create('sale.order.line', line_vals)

            price = 0 if is_gift else product['list_price'] * qty
            products_total += price
            gift_label = " (هدية)" if is_gift else ""
            matched_products.append(f"{product['name']} x{qty}{gift_label}")

            # Check stock
            stock = product.get('qty_available', None)
            if stock is not None and stock < qty:
                low_stock.append(f"{product['name']} (متوفر: {int(stock)}, مطلوب: {qty})")
        else:
            unmatched.append(item["name"])

    # 4. Add delivery
    delivery_fee = 0
    try:
        wiz_id = rpc.create('choose.delivery.carrier', {
            'order_id': order_id, 'carrier_id': carrier['carrier_id'],
        })
        rpc.call('choose.delivery.carrier', 'button_confirm', [[wiz_id]])
    except Exception:
        rpc.write('sale.order', order_id, {'carrier_id': carrier['carrier_id']})
        rpc.create('sale.order.line', {
            'order_id': order_id, 'product_id': carrier['product_id'],
            'product_uom_qty': 1, 'price_unit': 5000,
            'name': carrier['name'], 'is_delivery': True,
        })

    delivery_lines = rpc.search_read('sale.order.line', [
        ['order_id', '=', order_id], ['is_delivery', '=', True]
    ], fields=['id', 'price_unit'])
    if delivery_lines:
        delivery_fee = delivery_lines[0]['price_unit']
        if delivery_fee <= 1:
            delivery_fee = 5000
            rpc.write('sale.order.line', delivery_lines[0]['id'], {'price_unit': 5000})

    # 5. Adjust price with Discount line
    raw_total = order_data.get("total_price", 0)
    target_total = raw_total if raw_total >= 1000 else raw_total * 1000

    order_info = rpc.read('sale.order', order_id, fields=['amount_total', 'name'])[0]
    current_total = order_info['amount_total']
    order_name = order_info['name']

    if target_total > 0 and abs(current_total - target_total) > 1:
        discount_amount = current_total - target_total
        if discount_amount > 0:
            rpc.create('sale.order.line', {
                'order_id': order_id,
                'product_id': DISCOUNT_PRODUCT_ID,
                'product_uom_qty': 1,
                'price_unit': -discount_amount,
                'name': 'Discount',
            })

        order_info = rpc.read('sale.order', order_id, fields=['amount_total', 'name'])[0]
        current_total = order_info['amount_total']

    # 6. Confirm order
    rpc.call('sale.order', 'action_confirm', [[order_id]])

    return {
        "order_id": order_id,
        "order_name": order_name,
        "partner_id": partner_id,
        "customer_name": name,
        "products": matched_products,
        "unmatched": unmatched,
        "total": current_total,
        "target": target_total,
        "delivery_fee": delivery_fee,
        "carrier": carrier['name'],
        "url": f"{ODOO_URL}/odoo/sales/{order_id}",
        "province": province,
        "province_matched": bool(state_id),
        "city": city_name,
        "city_matched": city_matched,
        "low_stock": low_stock,
    }


# ============ Telegram Bot Handlers ============

BRAND_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("🟣 شهد بيوتي"), KeyboardButton("🔵 مارلين")]],
    resize_keyboard=True,
    is_persistent=True
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text(
        "مرحباً! أنا بوت إدخال الطلبات لأودو v2 🚀\n\n"
        "📦 اختر المتجر من الأزرار بالأسفل، ثم ألصق طلب الواتساب\n\n"
        "الأوامر المتوفرة:\n"
        "/report — تقرير المبيعات\n"
        "/stock — فحص المخزون\n"
        "/search — بحث عن طلب أو زبون\n"
        "/help — المساعدة",
        reply_markup=BRAND_KEYBOARD
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text(
        "📖 كيفية الاستخدام:\n\n"
        "1️⃣ أرسل /shahd أو /marlin لتحديد البراند\n"
        "2️⃣ ألصق رسالة الطلب من الواتساب\n"
        "3️⃣ راجع البيانات واضغط تأكيد\n\n"
        "📊 /report — تقارير المبيعات\n"
        "  /report today — تقرير اليوم\n"
        "  /report week — تقرير الأسبوع\n\n"
        "📦 /stock [اسم المنتج] — فحص المخزون\n"
        "  مثال: /stock بكج النيلة\n\n"
        "🔍 /search [اسم أو رقم] — بحث\n"
        "  مثال: /search 07801234567\n"
        "  مثال: /search أحمد\n\n"
        "💡 يمكنك إرسال عدة طلبات متتالية بدون مشاكل.\n"
        "البراند يبقى محدد حتى تغيره."
    )


async def set_shahd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    context.user_data['brand'] = 'shahd'
    await update.message.reply_text("✅ تم تحديد البراند: شهد بيوتي", reply_markup=BRAND_KEYBOARD)


async def set_marlin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    context.user_data['brand'] = 'marlin'
    await update.message.reply_text("✅ تم تحديد البراند: مارلين", reply_markup=BRAND_KEYBOARD)


# ============ Report Command ============

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    if ADMIN_USERS and not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ التقارير متاحة للمدراء فقط.")
        return

    args = context.args
    brand = context.user_data.get('brand')

    if not args or args[0] == 'today':
        msg = await update.message.reply_text("⏳ جاري تجهيز تقرير اليوم...")
        try:
            report = await asyncio.to_thread(generate_daily_report, brand)
            await msg.edit_text(report)
        except Exception as e:
            await msg.edit_text(f"❌ خطأ: {e}")

    elif args[0] == 'week':
        msg = await update.message.reply_text("⏳ جاري تجهيز تقرير الأسبوع...")
        try:
            report = await asyncio.to_thread(generate_weekly_report, brand)
            await msg.edit_text(report)
        except Exception as e:
            await msg.edit_text(f"❌ خطأ: {e}")

    else:
        await update.message.reply_text(
            "استخدام التقارير:\n"
            "/report today — تقرير اليوم\n"
            "/report week — تقرير الأسبوع"
        )


# ============ Stock Command ============

async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return

    query = ' '.join(context.args) if context.args else ''
    if not query:
        await update.message.reply_text(
            "📦 فحص المخزون\n\n"
            "استخدم: /stock [اسم المنتج]\n"
            "مثال: /stock بكج النيلة\n"
            "مثال: /stock عسل"
        )
        return

    msg = await update.message.reply_text("⏳ جاري فحص المخزون...")
    try:
        rpc = OdooRPC()
        results = await asyncio.to_thread(rpc.check_stock, query)

        if not results:
            await msg.edit_text(f"❌ لم يتم العثور على منتج: {query}")
            return

        if isinstance(results, dict):
            results = [results]

        text = f"📦 نتائج المخزون لـ \"{query}\":\n\n"
        for r in results[:10]:
            status_icon = "✅" if r.get('on_hand', 0) > 0 else "❌"
            text += (
                f"{status_icon} {r['name']}\n"
                f"   بالمخزن: {int(r.get('on_hand', 0))} | متوقع: {int(r.get('forecasted', 0))}\n\n"
            )

        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"❌ خطأ: {e}")


# ============ Search Command ============

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return

    query = ' '.join(context.args) if context.args else ''
    if not query:
        await update.message.reply_text(
            "🔍 بحث عن طلب أو زبون\n\n"
            "استخدم: /search [اسم أو رقم هاتف أو رقم طلب]\n"
            "مثال: /search 07801234567\n"
            "مثال: /search أحمد\n"
            "مثال: /search S00123"
        )
        return

    msg = await update.message.reply_text("⏳ جاري البحث...")
    try:
        rpc = OdooRPC()
        orders = await asyncio.to_thread(rpc.search_orders, query)
        text = format_search_results(orders)
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"❌ خطأ: {e}")


# ============ Order Message Handler ============

async def handle_order_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return

    message_text = update.message.text
    if message_text.startswith('/'):
        return

    # Handle brand selection buttons
    if message_text == "🟣 شهد بيوتي":
        context.user_data['brand'] = 'shahd'
        await update.message.reply_text("✅ البراند: شهد بيوتي", reply_markup=BRAND_KEYBOARD)
        return
    elif message_text == "🔵 مارلين":
        context.user_data['brand'] = 'marlin'
        await update.message.reply_text("✅ البراند: مارلين", reply_markup=BRAND_KEYBOARD)
        return

    # Minimum length check
    if len(message_text.strip()) < 15:
        await update.message.reply_text("⚠️ الرسالة قصيرة جداً. ألصق رسالة الطلب كاملة من الواتساب.")
        return

    msg = await update.message.reply_text("⏳ جاري تحليل الطلب...")

    try:
        parsed = await asyncio.to_thread(parse_with_llm, message_text)
    except Exception as e:
        logger.error(f"Parse error: {e}", exc_info=True)
        await msg.edit_text(f"❌ خطأ بتحليل الطلب: {e}")
        return

    # Validate province
    province = parsed.get("province", "")
    state_id = PROVINCE_MAP.get(province)
    province_warning = ""
    if not province or not state_id:
        province_warning = "\n\n⚠️ المحافظة غير محددة أو غير موجودة!"

    # Validate city
    city = parsed.get("city", "")
    city_warning = ""
    if not city:
        city_warning = "\n⚠️ المنطقة/المدينة غير محددة!"

    # Build summary
    products_text = ""
    for p in parsed.get("products", []):
        resolved = resolve_product_name(p['name'])
        gift = " (هدية)" if p.get("is_gift") else ""
        products_text += f"  - {resolved} x{p.get('quantity', 1)}{gift}\n"

    raw_total = parsed.get("total_price", 0)
    if raw_total and raw_total > 0:
        total_display = f"{raw_total},000" if raw_total < 1000 else f"{raw_total:,}"
    else:
        total_display = "واصل (سعر المنتجات)"

    notes = parsed.get("notes", "")
    notes_text = f"\nملاحظات: {notes}" if notes else ""

    summary = (
        f"الاسم: {parsed.get('customer_name', '?')}\n"
        f"الهاتف: {parsed.get('phone', '?')}\n"
        f"المحافظة: {province or '❌ غير محددة'}\n"
        f"المنطقة: {city or '❌ غير محددة'}\n"
        f"العنوان: {parsed.get('street', '?')}\n"
        f"المنتجات:\n{products_text}"
        f"الإجمالي: {total_display} د.ع"
        f"{notes_text}"
    )

    # --- Duplicate Detection ---
    dup = check_duplicate(parsed)
    dup_warning = ""
    if dup:
        dup_warning = (
            f"\n\n🔴 تنبيه: يوجد طلب مشابه لنفس الرقم ({dup['phone']}) "
            f"قبل {dup['minutes_ago']} دقيقة (تطابق {dup['overlap_pct']}%)\n"
            f"⚠️ تأكد أن هذا طلب جديد وليس مكرر!"
        )

    # Generate unique order key
    order_key = f"order_{int(time.time() * 1000)}"

    if 'pending_orders' not in context.user_data:
        context.user_data['pending_orders'] = {}
    context.user_data['pending_orders'][order_key] = parsed

    brand = context.user_data.get('brand')

    # If province or city missing, show warning
    if province_warning or city_warning:
        summary += province_warning + city_warning
        summary += "\n\nأرسل الطلب مرة ثانية بالمحافظة والمنطقة الصحيحة."
        await msg.edit_text(f"📋 بيانات الطلب:\n\n{summary}")
        context.user_data['pending_orders'].pop(order_key, None)
        return

    if brand:
        brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"
        summary += f"\nالبراند: {brand_name}{dup_warning}"

        keyboard = [
            [InlineKeyboardButton("تأكيد وإدخال ✅", callback_data=f"confirm_{order_key}")],
            [InlineKeyboardButton("إلغاء ❌", callback_data=f"cancel_{order_key}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(f"📋 بيانات الطلب:\n\n{summary}", reply_markup=reply_markup)
    else:
        summary += dup_warning
        keyboard = [
            [InlineKeyboardButton("شهد بيوتي", callback_data=f"brand_shahd_{order_key}")],
            [InlineKeyboardButton("مارلين", callback_data=f"brand_marlin_{order_key}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(
            f"📋 بيانات الطلب:\n\n{summary}\n\nاختر البراند:",
            reply_markup=reply_markup
        )


# ============ Callback Handler ============

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    pending = context.user_data.get('pending_orders', {})

    if data.startswith("brand_"):
        parts = data.split("_", 2)
        brand = parts[1] if len(parts) >= 2 else 'shahd'
        order_key = parts[2] if len(parts) >= 3 else None

        context.user_data['brand'] = brand
        brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"

        current_text = query.message.text
        current_text = current_text.replace("\n\nاختر البراند:", "")
        current_text += f"\nالبراند: {brand_name}"

        confirm_data = f"confirm_{order_key}" if order_key else "confirm"
        cancel_data = f"cancel_{order_key}" if order_key else "cancel"

        keyboard = [
            [InlineKeyboardButton("تأكيد وإدخال ✅", callback_data=confirm_data)],
            [InlineKeyboardButton("إلغاء ❌", callback_data=cancel_data)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(current_text, reply_markup=reply_markup)

    elif data.startswith("confirm"):
        order_key = data.replace("confirm_", "") if "_" in data else None
        parsed = pending.get(order_key) if order_key else context.user_data.get('parsed_order')
        brand = context.user_data.get('brand', 'shahd')

        if not parsed:
            await query.edit_message_text("❌ ما لقيت بيانات الطلب. أرسل الطلب مرة ثانية.")
            return

        await query.edit_message_text("⏳ جاري إدخال الطلب في أودو...")

        try:
            result = await asyncio.to_thread(create_full_order, parsed, brand)

            # Register for duplicate detection
            register_order(parsed, order_key or 'unknown')

            products_list = "\n".join([f"  - {p}" for p in result['products']])
            unmatched_text = ""
            if result['unmatched']:
                unmatched_text = f"\n\n⚠️ منتجات غير موجودة: {', '.join(result['unmatched'])}"

            if result['target'] == 0:
                total_match = "✅"
            else:
                total_match = "✅" if abs(result['total'] - result['target']) < 100 else f"⚠️ (المطلوب: {result['target']:,.0f})"

            warnings = ""
            if not result.get('province_matched'):
                warnings += "\n⚠️ المحافظة لم يتم ربطها بالنظام"
            if not result.get('city_matched'):
                warnings += "\n⚠️ المنطقة لم يتم ربطها بالنظام"

            # Low stock warning
            low_stock_text = ""
            if result.get('low_stock'):
                low_stock_text = "\n\n📦 تحذير مخزون منخفض:\n"
                for item in result['low_stock']:
                    low_stock_text += f"  ⚠️ {item}\n"

            response = (
                f"تم إدخال الطلب بنجاح! ✅\n\n"
                f"رقم الطلب: {result['order_name']}\n"
                f"العميل: {result['customer_name']}\n"
                f"المحافظة: {result.get('province', '?')}\n"
                f"المنطقة: {result.get('city', '?')}\n"
                f"المنتجات:\n{products_list}\n"
                f"التوصيل: {result['delivery_fee']:,.0f} د.ع ({result['carrier']})\n"
                f"الإجمالي: {result['total']:,.0f} د.ع {total_match}"
                f"{unmatched_text}{warnings}{low_stock_text}\n\n"
                f"🔗 {result['url']}"
            )
            await query.edit_message_text(response)

        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            await query.edit_message_text(f"❌ خطأ بإدخال الطلب:\n{e}\n\nحاول مرة ثانية.")

        if order_key:
            pending.pop(order_key, None)

    elif data.startswith("cancel"):
        order_key = data.replace("cancel_", "") if "_" in data else None
        if order_key:
            pending.pop(order_key, None)
        await query.edit_message_text("تم إلغاء الطلب. ❌")


# ============ Main ============

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("shahd", set_shahd))
    app.add_handler(CommandHandler("marlin", set_marlin))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("stock", stock_cmd))
    app.add_handler(CommandHandler("search", search_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_message))

    logger.info("Bot v2 is running...")
    print("Bot v2 is running... 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
