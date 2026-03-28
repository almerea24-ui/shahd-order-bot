#!/usr/bin/env python3
"""
Telegram Bot for entering WhatsApp orders into Odoo.
Standalone deployment version.
"""

import os
import json
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from parse_order import parse_with_llm

# --- Configuration from environment variables ---
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ODOO_URL = os.environ.get("ODOO_URL", "https://shahdbeauty.odoo.com")
ODOO_DB = os.environ.get("ODOO_DB", "1tarabut-shahdbeauty-main-26480069")
ODOO_USER = os.environ.get("ODOO_USER", "admin")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "2002")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

IRAQ_COUNTRY_ID = 106
WHATSAPP_TAG_ID = 4

PROVINCE_MAP = {
    "الأنبار": 1762, "الانبار": 1762, "الرمادي": 1762,
    "أربيل": 1782, "اربيل": 1782,
    "البصرة": 1764, "البصره": 1764,
    "بابل": 1772, "بغداد": 1774, "دهوك": 1776,
    "ديالى": 1780, "ديالي": 1780, "ذي قار": 1778,
    "كربلاء": 1784, "كربلا": 1784, "كركوك": 1786,
    "ميسان": 1788, "المثنى": 1766, "المثني": 1766,
    "النجف": 1794, "نينوى": 1790, "نينوي": 1790,
    "القادسية": 1768, "القادسيه": 1768, "الديوانية": 1768,
    "صلاح الدين": 1796, "السليمانية": 1770, "السليمانيه": 1770,
    "واسط": 1792,
}

CARRIER_MAP = {
    "shahd": {"carrier_id": 8, "name": "Albarq Delivery Shahd", "product_id": 51},
    "marlin": {"carrier_id": 11, "name": "Albarq Delivery Marlin", "product_id": 51},
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============ Odoo RPC ============

class OdooRPC:
    def __init__(self):
        self.url = ODOO_URL
        self.db = ODOO_DB
        self.session = requests.Session()
        self.uid = None
        self._login()

    def _login(self):
        result = self._jsonrpc('/web/session/authenticate', {
            'db': self.db, 'login': ODOO_USER, 'password': ODOO_PASSWORD
        })
        self.uid = result.get('uid')
        if not self.uid:
            raise Exception("Odoo authentication failed")

    def _jsonrpc(self, endpoint, params):
        payload = {'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': params}
        resp = self.session.post(f'{self.url}{endpoint}', json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get('error'):
            err = data['error']
            msg = err.get('data', {}).get('message', '') or err.get('message', '')
            raise Exception(f"Odoo Error: {msg}")
        return data.get('result', {})

    def call(self, model, method, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        return self._jsonrpc('/web/dataset/call_kw', {
            'model': model, 'method': method, 'args': args, 'kwargs': kwargs
        })

    def search_read(self, model, domain, fields=None, limit=None):
        kw = {'fields': fields or []}
        if limit:
            kw['limit'] = limit
        return self.call(model, 'search_read', [domain], kw)

    def create(self, model, vals):
        return self.call(model, 'create', [vals])

    def write(self, model, ids, vals):
        if not isinstance(ids, list):
            ids = [ids]
        return self.call(model, 'write', [ids, vals])

    def read(self, model, ids, fields=None):
        if not isinstance(ids, list):
            ids = [ids]
        return self.call(model, 'read', [ids], {'fields': fields or []})


# ============ Odoo Order Functions ============

def find_city(rpc, city_name, state_id):
    if not city_name or not state_id:
        return None
    cities = rpc.search_read('x_city', [
        ['x_name', '=', city_name], ['x_studio_state', '=', state_id], ['x_active', '=', True]
    ], fields=['id', 'x_name'])
    if cities:
        return cities[0]
    cities = rpc.search_read('x_city', [
        ['x_name', 'ilike', city_name], ['x_studio_state', '=', state_id], ['x_active', '=', True]
    ], fields=['id', 'x_name'], limit=10)
    if cities:
        best, best_score = None, -1
        for c in cities:
            cname = c['x_name']
            if cname == city_name:
                return c
            score = 100 if (city_name in cname or cname in city_name) else 0
            score += len(set(city_name.split()) & set(cname.split())) * 50
            if score > best_score:
                best_score, best = score, c
        return best
    return None


def find_product(rpc, product_name):
    exact = rpc.search_read('product.product', [
        ['name', '=', product_name], ['sale_ok', '=', True]
    ], fields=['id', 'name', 'list_price'], limit=1)
    if exact:
        return exact[0]
    products = rpc.search_read('product.product', [
        ['name', 'ilike', product_name], ['sale_ok', '=', True]
    ], fields=['id', 'name', 'list_price'], limit=10)
    if products:
        def score(p):
            pname = p['name'].lower().strip()
            search = product_name.lower().strip()
            search_words = set(search.split())
            pname_words = set(pname.split())
            if pname == search:
                return 10000
            if search_words.issubset(pname_words):
                return 5000 - len(pname)
            if search in pname:
                return 3000 - len(pname)
            return len(search_words & pname_words) * 100 - len(pname)
        products.sort(key=score, reverse=True)
        return products[0]
    keywords = [kw for kw in product_name.split() if len(kw) > 2]
    if len(keywords) >= 2:
        for i in range(len(keywords)):
            for j in range(i + 1, len(keywords)):
                products = rpc.search_read('product.product', [
                    ['name', 'ilike', keywords[i]], ['name', 'ilike', keywords[j]], ['sale_ok', '=', True]
                ], fields=['id', 'name', 'list_price'], limit=5)
                if products:
                    return products[0]
    for kw in keywords:
        products = rpc.search_read('product.product', [
            ['name', 'ilike', kw], ['sale_ok', '=', True]
        ], fields=['id', 'name', 'list_price'], limit=5)
        if products:
            return products[0]
    return None


def create_full_order(order_data, brand):
    """Create complete order in Odoo. Returns dict with result info."""
    rpc = OdooRPC()

    # 1. Create customer
    name = order_data.get("customer_name", "")
    phone = order_data.get("phone", "")
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
    if city_name and state_id:
        city = find_city(rpc, city_name, state_id)
        if city:
            customer_vals['x_studio_city'] = city['id']
        else:
            customer_vals['city'] = city_name
    elif city_name:
        customer_vals['city'] = city_name

    street = order_data.get("street", "")
    nearest = order_data.get("nearest_landmark", "")
    full_street = f"{street} - {nearest}" if street and nearest else street or nearest
    if full_street:
        customer_vals['street'] = full_street

    partner_id = rpc.create('res.partner', customer_vals)

    # 2. Create sale order
    carrier = CARRIER_MAP.get(brand, CARRIER_MAP["shahd"])

    notes_parts = []
    for key in ["province", "city", "street", "nearest_landmark"]:
        if order_data.get(key):
            notes_parts.append(order_data[key])
    if order_data.get("instagram"):
        notes_parts.append(f"Instagram: {order_data['instagram']}")

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
    product_lines = []
    matched_products = []

    for item in products_data:
        product = find_product(rpc, item["name"])
        if product:
            line_vals = {
                'order_id': order_id,
                'product_id': product['id'],
                'product_uom_qty': item.get('quantity', 1),
            }
            is_gift = item.get('is_gift', False)
            if is_gift:
                line_vals['price_unit'] = 0
                line_vals['name'] = f"{product['name']} (هدية)"
            line_id = rpc.create('sale.order.line', line_vals)
            product_lines.append(line_id)
            gift_label = " (هدية)" if is_gift else ""
            matched_products.append(f"{product['name']} x{item.get('quantity', 1)}{gift_label}")
        else:
            unmatched.append(item["name"])

    # 4. Add delivery via wizard
    try:
        wiz_id = rpc.create('choose.delivery.carrier', {
            'order_id': order_id, 'carrier_id': carrier['carrier_id'], 'delivery_price': 0,
        })
        rpc.call('choose.delivery.carrier', 'button_confirm', [[wiz_id]])
    except:
        rpc.write('sale.order', order_id, {'carrier_id': carrier['carrier_id']})
        rpc.create('sale.order.line', {
            'order_id': order_id, 'product_id': carrier['product_id'],
            'product_uom_qty': 1, 'price_unit': 0,
            'name': carrier['name'], 'is_delivery': True,
        })

    # 5. Adjust price
    order_info = rpc.read('sale.order', order_id, fields=['name', 'amount_total'])[0]
    current_total = order_info['amount_total']
    order_name = order_info['name']

    raw_total = order_data.get("total_price", 0)
    target_total = raw_total if raw_total >= 1000 else raw_total * 1000

    delivery_fee = 0
    if target_total > 0 and abs(current_total - target_total) > 100:
        delivery_lines = rpc.search_read('sale.order.line', [
            ['order_id', '=', order_id], ['is_delivery', '=', True]
        ], fields=['id', 'price_unit'])

        if delivery_lines and current_total < target_total:
            delivery_fee = target_total - current_total
            rpc.write('sale.order.line', delivery_lines[0]['id'], {'price_unit': delivery_fee})
        elif current_total > target_total and product_lines:
            diff = current_total - target_total
            first_line = rpc.read('sale.order.line', product_lines[0], fields=['price_unit'])
            old_price = first_line[0]['price_unit']
            rpc.write('sale.order.line', product_lines[0], {'price_unit': old_price - diff})

        order_info = rpc.read('sale.order', order_id, fields=['amount_total'])[0]
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
        "url": f"{ODOO_URL}/odoo/sales/{order_id}"
    }


# ============ Telegram Bot Handlers ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً! أنا بوت إدخال الطلبات لأودو 🛒\n\n"
        "أرسل لي رسالة الطلب من الواتساب وأنا أدخلها بأودو.\n\n"
        "الأوامر:\n"
        "/shahd - طلبات شهد بيوتي\n"
        "/marlin - طلبات مارلين\n"
        "/help - المساعدة"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "كيفية الاستخدام:\n\n"
        "1. أرسل /shahd أو /marlin لتحديد البراند\n"
        "2. ألصق رسالة الطلب من الواتساب\n"
        "3. راجع البيانات واضغط تأكيد\n\n"
        "أو ببساطة ألصق الطلب مباشرة وأنا أسألك عن البراند."
    )

async def set_shahd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['brand'] = 'shahd'
    await update.message.reply_text("✅ تم تحديد البراند: شهد بيوتي\nأرسل الطلب الحين...")

async def set_marlin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['brand'] = 'marlin'
    await update.message.reply_text("✅ تم تحديد البراند: مارلين\nأرسل الطلب الحين...")

async def handle_order_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming order text."""
    message_text = update.message.text
    if message_text.startswith('/'):
        return

    await update.message.reply_text("⏳ جاري تحليل الطلب...")

    try:
        parsed = parse_with_llm(message_text)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ بتحليل الطلب: {e}")
        return

    context.user_data['parsed_order'] = parsed
    context.user_data['raw_message'] = message_text

    # Build summary
    products_text = ""
    for p in parsed.get("products", []):
        gift = " (هدية)" if p.get("is_gift") else ""
        products_text += f"  • {p['name']} x{p.get('quantity', 1)}{gift}\n"

    raw_total = parsed.get("total_price", 0)
    total_display = f"{raw_total},000" if raw_total < 1000 else f"{raw_total:,}"

    summary = (
        f"👤 الاسم: {parsed.get('customer_name', '?')}\n"
        f"📱 الهاتف: {parsed.get('phone', '?')}\n"
        f"📍 المحافظة: {parsed.get('province', '?')}\n"
        f"🏘 المدينة: {parsed.get('city', '?')}\n"
        f"🛣 العنوان: {parsed.get('street', '?')}\n"
        f"📦 المنتجات:\n{products_text}"
        f"💰 الإجمالي: {total_display} د.ع"
    )

    brand = context.user_data.get('brand')
    if brand:
        brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"
        summary += f"\n🏷 البراند: {brand_name}"
        keyboard = [
            [InlineKeyboardButton("✅ تأكيد وإدخال", callback_data="confirm")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ]
        await update.message.reply_text(f"📋 بيانات الطلب:\n\n{summary}", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        context.user_data['pending_summary'] = summary
        keyboard = [
            [InlineKeyboardButton("شهد بيوتي", callback_data="brand_shahd")],
            [InlineKeyboardButton("مارلين", callback_data="brand_marlin")]
        ]
        await update.message.reply_text(
            f"📋 بيانات الطلب:\n\n{summary}\n\n🏷 اختر البراند:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("brand_"):
        brand = data.replace("brand_", "")
        context.user_data['brand'] = brand
        brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"

        summary = context.user_data.get('pending_summary', '')
        summary += f"\n🏷 البراند: {brand_name}"

        keyboard = [
            [InlineKeyboardButton("✅ تأكيد وإدخال", callback_data="confirm")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ]
        await query.edit_message_text(f"📋 بيانات الطلب:\n\n{summary}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "confirm":
        parsed = context.user_data.get('parsed_order')
        brand = context.user_data.get('brand', 'shahd')

        if not parsed:
            await query.edit_message_text("❌ خطأ: ما لقيت بيانات الطلب. أرسل الطلب مرة ثانية.")
            return

        await query.edit_message_text("⏳ جاري إدخال الطلب في أودو...")

        try:
            result = create_full_order(parsed, brand)

            products_list = "\n".join([f"  • {p}" for p in result['products']])
            unmatched_text = ""
            if result['unmatched']:
                unmatched_text = f"\n\n⚠️ منتجات غير موجودة: {', '.join(result['unmatched'])}"

            total_match = "✅" if abs(result['total'] - result['target']) < 100 else f"⚠️ (المطلوب: {result['target']:,.0f})"

            response = (
                f"✅ تم إدخال الطلب بنجاح!\n\n"
                f"🔢 رقم الطلب: {result['order_name']}\n"
                f"👤 العميل: {result['customer_name']}\n"
                f"📦 المنتجات:\n{products_list}\n"
                f"🚚 التوصيل: {result['delivery_fee']:,.0f} د.ع ({result['carrier']})\n"
                f"💰 الإجمالي: {result['total']:,.0f} د.ع {total_match}"
                f"{unmatched_text}\n\n"
                f"🔗 {result['url']}"
            )
            await query.edit_message_text(response)

        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            await query.edit_message_text(f"❌ خطأ بإدخال الطلب:\n{e}\n\nحاول مرة ثانية.")

        context.user_data.pop('parsed_order', None)
        context.user_data.pop('pending_summary', None)

    elif data == "cancel":
        await query.edit_message_text("❌ تم إلغاء الطلب.")
        context.user_data.pop('parsed_order', None)
        context.user_data.pop('pending_summary', None)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("shahd", set_shahd))
    app.add_handler(CommandHandler("marlin", set_marlin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_message))
    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
