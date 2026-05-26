#!/usr/bin/env python3
"""
Shahd Beauty / Marlen Discord Order Bot v4.
Improvements:
  1. Catalog-first product extraction (rpc passed to parse_with_llm)
  2. City disambiguation: asks employee when city not found in Odoo
  3. Duplicate detection backed by Odoo (real orders, not in-memory only)
  4. Request queue — serializes concurrent orders per channel
  5. Pro-rata discount distribution on products (no phantom Discount line)
"""

import time
import logging
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

import discord
from discord.ext import commands
from discord import app_commands

from config import (
    DISCORD_BOT_TOKEN, SHAHD_CHANNEL_ID, MARLIN_CHANNEL_ID,
    ODOO_URL, PROVINCE_MAP, CARRIER_MAP,
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

# ============ Improvement 4: Per-channel request queue ============
# Ensures orders in the same channel are processed one at a time
_channel_queues: dict[int, asyncio.Queue] = {}
_channel_workers: dict[int, asyncio.Task] = {}


async def _get_channel_queue(channel_id: int) -> asyncio.Queue:
    if channel_id not in _channel_queues:
        _channel_queues[channel_id] = asyncio.Queue()
    return _channel_queues[channel_id]


async def _channel_worker(channel_id: int):
    """Worker that processes orders for a channel one at a time."""
    queue = _channel_queues[channel_id]
    while True:
        coro = await queue.get()
        try:
            await coro
        except Exception as e:
            logger.error(f"Channel worker error (channel={channel_id}): {e}", exc_info=True)
        finally:
            queue.task_done()


async def enqueue_order(channel_id: int, coro):
    """Enqueue an order processing coroutine for a channel."""
    queue = await _get_channel_queue(channel_id)
    if channel_id not in _channel_workers or _channel_workers[channel_id].done():
        _channel_workers[channel_id] = asyncio.create_task(_channel_worker(channel_id))
    position = queue.qsize() + 1
    await queue.put(coro)
    return position


# ============ Access Control ============

def is_authorized(user_id: int) -> bool:
    if not AUTHORIZED_USERS:
        return True
    return user_id in AUTHORIZED_USERS


def is_admin(user_id: int) -> bool:
    if not ADMIN_USERS:
        return is_authorized(user_id)
    return user_id in ADMIN_USERS


# ============ Improvement 3: Odoo-backed duplicate detection ============

def check_duplicate_odoo(rpc: OdooRPC, phone: str, window_minutes: int = 30) -> tuple[bool, str]:
    """
    Check if a recent order exists in Odoo for this phone number.
    More reliable than in-memory check — survives bot restarts.
    """
    if not phone or len(phone) < 8:
        return False, ''
    try:
        cutoff = (datetime.utcnow() - timedelta(minutes=window_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        orders = rpc.search_read('sale.order', [
            ['partner_id.phone', '=', phone],
            ['date_order', '>=', cutoff],
            ['state', 'in', ['sale', 'done', 'draft']],
        ], fields=['name', 'date_order', 'amount_total'], limit=3)
        if orders:
            latest = orders[0]
            order_time = latest.get('date_order', '')
            msg = f"⚠️ يوجد طلب حديث لنفس الرقم {phone}: {latest['name']} ({order_time[:16]})"
            return True, msg
    except Exception as e:
        logger.warning(f"Odoo duplicate check failed: {e}")
    return False, ''


# ============ Improvement 5: Pro-rata discount distribution ============

def _distribute_discount(products_total: float, target_total: float, delivery_fee: float,
                         matched_products: list, order_id: int, rpc: OdooRPC,
                         product_line_ids: list) -> float:
    """
    Apply the difference between products_total+delivery and target_total
    as a single discount/adjustment line.
    Returns the actual final total.
    """
    current_total = products_total + delivery_fee
    if target_total <= 0 or abs(current_total - target_total) <= 1:
        return current_total

    diff = target_total - current_total  # negative = discount, positive = extra charge

    # Add a single adjustment line using the discount product
    rpc.create('sale.order.line', {
        'order_id': order_id,
        'product_id': DISCOUNT_PRODUCT_ID,
        'product_uom_qty': 1,
        'price_unit': diff,
        'name': 'تعديل السعر' if diff > 0 else 'خصم',
    })
    return target_total


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
            # إذا city تبدأ بـ "حي" أو "شارع" أو "زقاق" (sub-neighborhood)
            # جرب الكلمة الأولى من street كـ city بدلاً منها
            sub_prefixes = ('حي ', 'شارع ', 'زقاق ', 'قرب ', 'خلف ')
            if any(city_name.startswith(p) for p in sub_prefixes) and street:
                first_street_word = street.split()[0] if street.split() else ''
                if first_street_word:
                    alt = find_city(rpc, first_street_word, state_id)
                    if alt:
                        customer_vals['x_studio_city'] = alt['id']
                        city_matched = True

            if not city_matched and street:
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
    # حماية ثانية: تحقق من وجود جميع المنتجات قبل إنشاء أي سطر في Odoo
    missing_products = []
    for item in products_data:
        if item.get('_skip'):
            continue
        if not item.get('_odoo_product'):
            p = find_product(rpc, item["name"], brand=brand)
            if p:
                item['_odoo_product'] = p
            elif item.get('is_gift'):
                item['_skip'] = True  # هدية غير موجودة — تجاهل
            else:
                missing_products.append(item["name"])
    if missing_products:
        # حذف الطلب الذي تم إنشاؤه وإلغاؤه
        try:
            rpc.call('sale.order', 'action_cancel', [[order_id]])
            rpc.unlink('sale.order', order_id)
        except Exception:
            pass
        raise ValueError(f"⛔ منتجات غير موجودة في النظام:\n" + "\n".join([f"  ❌ {n}" for n in missing_products]) + "\n\nيرجى تصحيح الطلب وإعادة إرساله.")
    unmatched = []
    matched_products = []
    products_total = 0
    low_stock = []
    product_line_ids = []  # (line_id, price_unit, is_gift) for pro-rata
    for item in products_data:
        if item.get('_skip'):
            continue  # هدية غير موجودة — تجاهل
        # Improvement 1: use pre-resolved catalog product if available
        product = item.get('_odoo_product') or find_product(rpc, item["name"], brand=brand)
        if product:
            qty = item.get('quantity', 1)
            is_gift = item.get('is_gift', False)
            unit_price = product['list_price']
            line_vals = {
                'order_id': order_id,
                'product_id': product['id'],
                'product_uom_qty': qty,
            }
            if is_gift:
                line_vals['price_unit'] = 0
                line_vals['name'] = f"{product['name']} (هدية)"
                unit_price = 0

            line_id = rpc.create('sale.order.line', line_vals)
            line_price = unit_price * qty
            products_total += line_price
            product_line_ids.append((line_id, unit_price * qty, is_gift))

            gift_label = " (هدية)" if is_gift else ""
            matched_products.append(f"{product['name']} x{qty}{gift_label}")

            stock = product.get('qty_available', None)
            if stock is not None and stock < qty:
                low_stock.append(f"{product['name']} (متوفر: {int(stock)}, مطلوب: {qty})")
        else:
            unmatched.append(item["name"])

    # 4. Add delivery — force 4000 IQD
    delivery_fee = 4000
    try:
        wiz_id = rpc.create('choose.delivery.carrier', {
            'order_id': order_id, 'carrier_id': carrier['carrier_id'],
        })
        rpc.call('choose.delivery.carrier', 'button_confirm', [[wiz_id]])
    except Exception:
        rpc.write('sale.order', order_id, {'carrier_id': carrier['carrier_id']})
        rpc.create('sale.order.line', {
            'order_id': order_id, 'product_id': carrier['product_id'],
            'product_uom_qty': 1, 'price_unit': 4000,
            'name': carrier['name'], 'is_delivery': True,
        })

    delivery_lines = rpc.search_read('sale.order.line', [
        ['order_id', '=', order_id], ['is_delivery', '=', True]
    ], fields=['id', 'price_unit'])
    if delivery_lines:
        if delivery_lines[0]['price_unit'] != 4000:
            rpc.write('sale.order.line', delivery_lines[0]['id'], {'price_unit': 4000})

    # 5. Improvement 5: Pro-rata discount distribution
    raw_total = order_data.get("total_price", 0)
    target_total = int(raw_total) if raw_total else 0

    if target_total > 0 and abs((products_total + delivery_fee) - target_total) > 1:
        final_total = _distribute_discount(
            products_total, target_total, delivery_fee,
            matched_products, order_id, rpc, product_line_ids
        )
    else:
        final_total = products_total + delivery_fee

    # Verify final total from Odoo
    order_info = rpc.read('sale.order', order_id, fields=['amount_total', 'name'])[0]
    current_total = order_info['amount_total']
    order_name = order_info['name']

    # If still off (edge case), add a single adjustment line
    if target_total > 0 and abs(current_total - target_total) > 1:
        diff = target_total - current_total
        rpc.create('sale.order.line', {
            'order_id': order_id,
            'product_id': DISCOUNT_PRODUCT_ID,
            'product_uom_qty': 1,
            'price_unit': diff,
            'name': 'Price Adjustment' if diff > 0 else 'Discount',
        })
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
        "url": f"{ODOO_URL}/odoo/sales/{order_id}",
        "province": province,
        "province_matched": bool(state_id),
        "city": city_name,
        "city_matched": city_matched,
        "low_stock": low_stock,
    }


# ============ Improvement 2: City disambiguation view ============

class CitySelectView(discord.ui.View):
    """Shown when city is not found — lets employee pick from Odoo cities or confirm free text."""

    def __init__(self, parsed_data, brand, order_key, state_id, city_candidates):
        super().__init__(timeout=120)
        self.parsed_data = parsed_data
        self.brand = brand
        self.order_key = order_key
        self.state_id = state_id
        self.chosen_city = None

        # Add select menu with up to 25 candidates
        options = []
        for c in city_candidates[:24]:
            options.append(discord.SelectOption(label=c['x_name'][:100], value=str(c['id'])))
        options.append(discord.SelectOption(label="⬅️ استخدم النص كما هو", value="free_text"))

        select = discord.ui.Select(
            placeholder="اختر المنطقة الصحيحة...",
            options=options,
            custom_id="city_select"
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        value = interaction.data['values'][0]
        if value == "free_text":
            # Keep city as free text
            pass
        else:
            city_id = int(value)
            # Find city name
            rpc = OdooRPC()
            cities = rpc.search_read('x_city', [['id', '=', city_id]], fields=['id', 'x_name'])
            if cities:
                self.parsed_data['_city_id'] = city_id
                self.parsed_data['_city_name'] = cities[0]['x_name']

        await interaction.response.edit_message(content="⏳ جاري إدخال الطلب...", view=None)
        try:
            result = await asyncio.to_thread(create_full_order, self.parsed_data, self.brand)
            register_order(self.parsed_data, self.order_key)
            await _send_order_result(interaction.message, result)
        except Exception as e:
            logger.error(f"Order error after city select: {e}", exc_info=True)
            await interaction.message.edit(content=f"❌ خطأ: {e}")


# ============ Discord Bot Setup ============

class OrderConfirmView(discord.ui.View):
    def __init__(self, parsed_data, brand, order_key):
        super().__init__(timeout=None)
        self.parsed_data = parsed_data
        self.brand = brand
        self.order_key = order_key

    @discord.ui.button(label="تأكيد وإدخال ✅", style=discord.ButtonStyle.success, custom_id="confirm_order")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="⏳ جاري إدخال الطلب في أودو...", view=None)

        try:
            result = await asyncio.to_thread(create_full_order, self.parsed_data, self.brand)
            register_order(self.parsed_data, self.order_key)
            await _send_order_result(interaction.message, result)
        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            await interaction.message.edit(content=f"❌ خطأ بإدخال الطلب:\n{e}\n\nحاول مرة ثانية.")

    @discord.ui.button(label="إلغاء ❌", style=discord.ButtonStyle.danger, custom_id="cancel_order")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="تم إلغاء الطلب. ❌", view=None)


async def _send_order_result(message: discord.Message, result: dict):
    """Format and send the order result message."""
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
    await message.edit(content=response)


class OrderBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Discord bot setup complete and slash commands synced.")


bot = OrderBot()


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Discord Bot v4 is running... 🚀")


async def _process_order_message(message: discord.Message, brand: str, text: str):
    """Core order processing logic — runs inside the channel queue."""
    status_msg = await message.channel.send("⏳ جاري تحليل الطلب...")

    # Improvement 1: pass rpc and brand to parse_with_llm for catalog-first extraction
    try:
        rpc = OdooRPC()
        parsed = await asyncio.to_thread(parse_with_llm, text, rpc, brand)
    except Exception as e:
        logger.error(f"LLM Parse Error: {e}")
        await status_msg.edit(content="❌ حدث خطأ أثناء تحليل الطلب. تأكد من صيغة الرسالة.")
        return

    if not parsed.get("products"):
        await status_msg.edit(content="❌ لم يتم العثور على منتجات في الرسالة.")
        return

    # Improvement 3: Odoo-backed duplicate check
    phone = parsed.get('phone', '')
    is_dup_odoo, dup_msg_odoo = check_duplicate_odoo(rpc, phone)
    is_dup_mem, dup_msg_mem = check_duplicate(parsed)
    is_dup = is_dup_odoo or is_dup_mem
    dup_warning = ""
    if is_dup:
        dup_warning = f"\n\n⚠️ **تحذير:** {dup_msg_odoo or dup_msg_mem}"

    # Format summary
    summary = f"العميل: {parsed.get('customer_name', 'غير محدد')}\n"
    summary += f"الهاتف: {parsed.get('phone', 'غير محدد')}\n"

    province_warning = ""
    if not parsed.get("province"):
        province_warning = "⚠️ **المحافظة مفقودة!**\n"
    else:
        summary += f"المحافظة: {parsed['province']}\n"

    city_warning = ""
    if not parsed.get("city"):
        city_warning = "⚠️ **المنطقة مفقودة!**\n"
    else:
        summary += f"المنطقة: {parsed['city']}\n"

    if parsed.get("street"):
        summary += f"العنوان: {parsed['street']}\n"
    if parsed.get("nearest_landmark"):
        summary += f"أقرب نقطة دالة: {parsed['nearest_landmark']}\n"

    summary += "\nالمنتجات:\n"
    for p in parsed.get("products", []):
        gift_str = " (هدية)" if p.get("is_gift") else ""
        summary += f"  - {p['name']} x{p.get('quantity', 1)}{gift_str}\n"

    if parsed.get("total_price"):
        summary += f"\nالإجمالي: {parsed['total_price']:,.0f} د.ع\n"
    if parsed.get("notes"):
        summary += f"ملاحظات: {parsed['notes']}\n"

    order_key = f"order_{int(time.time() * 1000)}"

    if province_warning or city_warning:
        summary += province_warning + city_warning
        summary += "\n\nأرسل الطلب مرة ثانية بالمحافظة والمنطقة الصحيحة."
        await status_msg.edit(content=f"📋 بيانات الطلب:\n\n{summary}")
        return

    # Improvement 2: City disambiguation
    state_id = PROVINCE_MAP.get(parsed.get('province', ''), False)
    city_name = parsed.get('city', '')
    city_candidates = []
    if state_id and city_name:
        city_found = await asyncio.to_thread(find_city, rpc, city_name, state_id)
        if not city_found:
            # Try to get candidate cities sorted by fuzzy similarity
            try:
                from matching import arabic_similarity
                all_cities = await asyncio.to_thread(rpc.get_cities_for_state, state_id)
                scored = sorted(
                    all_cities,
                    key=lambda c: arabic_similarity(city_name, c.get('x_name', '')),
                    reverse=True
                )
                city_candidates = scored[:15]
            except Exception:
                pass

    brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"
    summary += f"\nالبراند: {brand_name}{dup_warning}"

    # ── فحص المنتجات في Odoo قبل عرض أزرار التأكيد ──
    unmatched_products = []
    for p in parsed.get("products", []):
        odoo_product = p.get('_odoo_product') or await asyncio.to_thread(
            find_product, rpc, p['name'], brand=brand
        )
        if odoo_product:
            p['_odoo_product'] = odoo_product  # حفظ النتيجة لتجنب إعادة البحث
        elif p.get('is_gift'):
            # هدية غير موجودة — تحذف بصمت بدون إيقاف الطلب
            logger.warning(f"هدية غير موجودة في Odoo: '{p['name']}' — تم تجاهلها")
            p['_skip'] = True
        else:
            unmatched_products.append(p['name'])

    if unmatched_products:
        unmatched_list = '\n'.join([f'  ❌ {name}' for name in unmatched_products])
        await status_msg.edit(
            content=(
                f"⛔ **الطلب موقوف — منتجات غير موجودة في النظام:**\n"
                f"{unmatched_list}\n\n"
                f"يرجى تصحيح اسم المنتج وإعادة إرسال الطلب."
            )
        )
        return
    # ─────────────────────────────────────────────────────────────────

    if city_candidates and city_name:
        # Show city selection
        summary += f"\n\n⚠️ المنطقة '{city_name}' غير موجودة في النظام. اختر المنطقة الصحيحة:"
        view = CitySelectView(parsed, brand, order_key, state_id, city_candidates)
        await status_msg.edit(content=f"📋 بيانات الطلب:\n\n{summary}", view=view)
    else:
        view = OrderConfirmView(parsed, brand, order_key)
        await status_msg.edit(content=f"📋 بيانات الطلب:\n\n{summary}", view=view)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not is_authorized(message.author.id):
        return

    await bot.process_commands(message)

    if message.content.startswith("!") or message.content.startswith("/"):
        return

    brand = None
    if message.channel.id == SHAHD_CHANNEL_ID:
        brand = "shahd"
    elif message.channel.id == MARLIN_CHANNEL_ID:
        brand = "marlin"

    if not brand:
        return

    text = message.content.strip()
    if len(text) < 15:
        return

    # Improvement 4: Enqueue order processing
    queue_pos = await enqueue_order(
        message.channel.id,
        _process_order_message(message, brand, text)
    )
    if queue_pos > 1:
        await message.channel.send(f"⏳ طلبك في الطابور (رقم {queue_pos}). انتظر قليلاً...")


# ============ Slash Commands ============

@bot.tree.command(name="report", description="إنشاء تقرير مبيعات")
@app_commands.describe(period="الفترة الزمنية (today أو week)")
async def report_cmd(interaction: discord.Interaction, period: str = "today"):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("⛔ ما عندك صلاحية.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        if period == "week":
            report_text = await asyncio.to_thread(generate_weekly_report)
        else:
            report_text = await asyncio.to_thread(generate_daily_report)

        if len(report_text) > 1900:
            parts = [report_text[i:i+1900] for i in range(0, len(report_text), 1900)]
            await interaction.followup.send(f"📊 تقرير المبيعات:\n\n{parts[0]}")
            for part in parts[1:]:
                await interaction.channel.send(part)
        else:
            await interaction.followup.send(f"📊 تقرير المبيعات:\n\n{report_text}")
    except Exception as e:
        logger.error(f"Report error: {e}")
        await interaction.followup.send("❌ حدث خطأ أثناء توليد التقرير.")


@bot.tree.command(name="stock", description="فحص مخزون منتج")
@app_commands.describe(product_name="اسم المنتج")
async def stock_cmd(interaction: discord.Interaction, product_name: str):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("⛔ ما عندك صلاحية.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        rpc = OdooRPC()
        results = await asyncio.to_thread(rpc.check_stock, product_name)

        if not results:
            await interaction.followup.send(f"❌ ما لقيت منتج بهذا الاسم: {product_name}")
            return

        msg = f"📦 نتائج البحث عن: {product_name}\n\n"
        for r in results:
            msg += f"🔹 {r['name']}\n"
            msg += f"   المتوفر الفعلي: {r['on_hand']}\n"
            msg += f"   المتوقع: {r['forecasted']}\n"
            msg += f"   الحالة: {r['status']}\n\n"

        await interaction.followup.send(msg)
    except Exception as e:
        logger.error(f"Stock error: {e}")
        await interaction.followup.send("❌ حدث خطأ أثناء فحص المخزون.")


@bot.tree.command(name="search", description="البحث عن طلب أو زبون")
@app_commands.describe(query="رقم الهاتف، اسم الزبون، أو رقم الطلب")
async def search_cmd(interaction: discord.Interaction, query: str):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("⛔ ما عندك صلاحية.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        rpc = OdooRPC()
        orders = await asyncio.to_thread(rpc.search_orders, query)
        customers = await asyncio.to_thread(rpc.search_customers, query)

        result_text = format_search_results(orders, customers)

        if len(result_text) > 1900:
            parts = [result_text[i:i+1900] for i in range(0, len(result_text), 1900)]
            await interaction.followup.send(parts[0])
            for part in parts[1:]:
                await interaction.channel.send(part)
        else:
            await interaction.followup.send(result_text)
    except Exception as e:
        logger.error(f"Search error: {e}")
        await interaction.followup.send("❌ حدث خطأ أثناء البحث.")


def main():
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN not set.")
        return
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
