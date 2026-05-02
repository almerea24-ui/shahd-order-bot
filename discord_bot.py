#!/usr/bin/env python3
"""
Shahd Beauty / Marlen Discord Order Bot v3.
Supports: order entry, reports, stock check, search, duplicate detection, user access control.
Brand-aware: uses Discord channels to separate Shahd and Marlin orders.
"""

import time
import logging
import asyncio
from datetime import datetime

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
        product = find_product(rpc, item["name"], brand=brand)
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
    target_total = int(raw_total) if raw_total else 0

    order_info = rpc.read('sale.order', order_id, fields=['amount_total', 'name'])[0]
    current_total = order_info['amount_total']
    order_name = order_info['name']

    if target_total > 0 and abs(current_total - target_total) > 1:
        # discount_amount = current - target
        # موجب → يُضاف خصم سالب (تخفيض)
        # سالب → يُضاف تعديل موجب (تكملة، مثلاً عند وجود هدايا بسعر 0)
        discount_amount = current_total - target_total
        rpc.create('sale.order.line', {
            'order_id': order_id,
            'product_id': DISCOUNT_PRODUCT_ID,
            'product_uom_qty': 1,
            'price_unit': -discount_amount,
            'name': 'Discount' if discount_amount > 0 else 'Price Adjustment',
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
            await interaction.message.edit(content=response)

        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            await interaction.message.edit(content=f"❌ خطأ بإدخال الطلب:\n{e}\n\nحاول مرة ثانية.")

    @discord.ui.button(label="إلغاء ❌", style=discord.ButtonStyle.danger, custom_id="cancel_order")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="تم إلغاء الطلب. ❌", view=None)


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
    print(f"Discord Bot v3 is running... 🚀")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Check authorization
    if not is_authorized(message.author.id):
        return

    # Process commands if any
    await bot.process_commands(message)

    # If it's a command, don't process as order
    if message.content.startswith("!") or message.content.startswith("/"):
        return

    # Determine brand based on channel
    brand = None
    if message.channel.id == SHAHD_CHANNEL_ID:
        brand = "shahd"
    elif message.channel.id == MARLIN_CHANNEL_ID:
        brand = "marlin"
    
    if not brand:
        # Not in a designated order channel
        return

    text = message.content.strip()
    if len(text) < 15:
        return

    status_msg = await message.channel.send("⏳ جاري تحليل الطلب...")

    try:
        parsed = await asyncio.to_thread(parse_with_llm, text)
    except Exception as e:
        logger.error(f"LLM Parse Error: {e}")
        await status_msg.edit(content="❌ حدث خطأ أثناء تحليل الطلب. تأكد من صيغة الرسالة.")
        return

    if not parsed.get("products"):
        await status_msg.edit(content="❌ لم يتم العثور على منتجات في الرسالة.")
        return

    # Check duplicates
    is_dup, dup_msg = check_duplicate(parsed)
    dup_warning = f"\n\n⚠️ **تحذير:** {dup_msg}" if is_dup else ""

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

    brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"
    summary += f"\nالبراند: {brand_name}{dup_warning}"

    view = OrderConfirmView(parsed, brand, order_key)
    await status_msg.edit(content=f"📋 بيانات الطلب:\n\n{summary}", view=view)


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
        
        # Discord message limit is 2000 chars, split if necessary
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
        print("❌ DISCORD_BOT_TOKEN is missing in config.")
        return
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
