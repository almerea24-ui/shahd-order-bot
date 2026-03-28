#!/usr/bin/env python3
"""
Telegram Bot for entering WhatsApp orders into Odoo.
Supports multiple concurrent orders, notes, correct product matching,
and mandatory province/city validation.
"""

import os
import sys
import json
import logging
import asyncio
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from parse_order import parse_with_llm

# --- Configuration ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8773451402:AAGD3-4QY1HKK3RZ8ze4cyXw1a3H96Keqsw")
ODOO_URL = os.environ.get("ODOO_URL", "https://shahdbeauty.odoo.com")
ODOO_DB = os.environ.get("ODOO_DB", "1tarabut-shahdbeauty-main-26480069")
ODOO_USER = os.environ.get("ODOO_USER", "admin")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "2002")

IRAQ_COUNTRY_ID = 106
WHATSAPP_TAG_ID = 4

PROVINCE_MAP = {
    "الأنبار": 1762, "الانبار": 1762, "الرمادي": 1762, "انبار": 1762,
    "أربيل": 1782, "اربيل": 1782,
    "البصرة": 1764, "البصره": 1764, "بصرة": 1764, "بصره": 1764,
    "بابل": 1772, "الحلة": 1772, "حلة": 1772, "الحله": 1772,
    "بغداد": 1774,
    "دهوك": 1776,
    "ديالى": 1780, "ديالي": 1780, "بعقوبة": 1780, "بعقوبه": 1780,
    "ذي قار": 1778, "الناصرية": 1778, "الناصريه": 1778,
    "كربلاء": 1784, "كربلا": 1784,
    "كركوك": 1786,
    "ميسان": 1788, "العمارة": 1788, "العماره": 1788,
    "المثنى": 1766, "المثني": 1766, "السماوة": 1766, "السماوه": 1766,
    "النجف": 1794, "نجف": 1794,
    "نينوى": 1790, "نينوي": 1790, "الموصل": 1790,
    "القادسية": 1768, "القادسيه": 1768, "الديوانية": 1768, "الديوانيه": 1768,
    "صلاح الدين": 1796, "تكريت": 1796,
    "السليمانية": 1770, "السليمانيه": 1770,
    "واسط": 1792, "الكوت": 1792,
}

CARRIER_MAP = {
    "shahd": {"carrier_id": 8, "name": "Albarq Delivery Shahd", "product_id": 51},
    "marlin": {"carrier_id": 11, "name": "Albarq Delivery Marlin", "product_id": 51},
}

# Product name mapping: common abbreviations -> exact Odoo product names
PRODUCT_ALIASES = {
    "بكج جوز الهند": "بكج جوز الهند للعناية بالشعر",
    "صابونة كركم": "صابونة الكركم",
    "ص كركم": "صابونة الكركم",
    "صابونة كركم مني": "صابونة الكركم مني",
    "ص كركم ميني": "صابونة الكركم مني",
    "غسول عروسة": "غسول العروسة للوجه والجسم",
    "غسول العروسة": "غسول العروسة للوجه والجسم",
    "غسول العروسه": "غسول العروسة للوجه والجسم",
    "كريم تبيض": "كريم  تبيض العروسة للوجه و الجسم",
    "كريم تبيض العروسة": "كريم  تبيض العروسة للوجه و الجسم",
    "كريم تبيض العروسه": "كريم  تبيض العروسة للوجه و الجسم",
    "مبيض العروسة": "كريم  تبيض العروسة للوجه و الجسم",
    "مبيض العروسه": "كريم  تبيض العروسة للوجه و الجسم",
    "مبيضة العروسة": "كريم  تبيض العروسة للوجه و الجسم",
    "مبيضه العروسه": "كريم  تبيض العروسة للوجه و الجسم",
    "مقشر عروسة": "مقشر العروسة للوجه والجسم",
    "مقشر العروسة": "مقشر العروسة للوجه والجسم",
    "مقشر العروسه": "مقشر العروسة للوجه والجسم",
    "لوشن عروسة": "لوشن العروسة",
    "لوشن العروسه": "لوشن العروسة",
    "مرطب العروسة": "لوشن العروسة",
    "مرطب العروسه": "لوشن العروسة",
    "مورد العروسة": "مورد العروسة لتوريد  الوجه والجسم",
    "مورد العروسه": "مورد العروسة لتوريد  الوجه والجسم",
    "بكج عروسة": "بكج العروسة",
    "بكج العروسه": "بكج العروسة",
    "بكج كافيار": "بكج الكافيار",
    "بكج نيلة": "بكج النيلة الثلاثي",
    "بكج النيلة": "بكج النيلة الثلاثي",
    "بكج النيله": "بكج النيلة الثلاثي",
    "بكج انوثة": "كورس الأنوثة للعناية بالجسم",
    "بكج أنوثة": "كورس الأنوثة للعناية بالجسم",
    "بكج الانوثة": "كورس الأنوثة للعناية بالجسم",
    "بكج الانوثه": "كورس الأنوثة للعناية بالجسم",
    "بكج الكرسمس": "بكج الكرسمس الحصري – الإصدار المحدود (4 منتجات + 2 هدايا وتوصيل مجاني )",
    "بكج كرسمس": "بكج الكرسمس الحصري – الإصدار المحدود (4 منتجات + 2 هدايا وتوصيل مجاني )",
    "بكج الكريسمس": "بكج الكرسمس الحصري – الإصدار المحدود (4 منتجات + 2 هدايا وتوصيل مجاني )",
    "كورس بياض": "كورس بياض الثلج للوجه",
    "كورس بياض وجه": "كورس بياض الثلج للوجه",
    "كورس بياض الثلج": "كورس بياض الثلج للوجه",
    "بياض الثلج": "بياض الثلج وجه كريم",
    "بكج تشيز": "بكج التشيز كيك للعناية الفاخرة بالجسم",
    "بكج التشيز كيك": "بكج التشيز كيك للعناية الفاخرة بالجسم",
    "بكج تشيز كيك": "بكج التشيز كيك للعناية الفاخرة بالجسم",
    "بكج حساسة": "بكج العناية بالمناطق الحساسة",
    "بكج مناطق حساسة": "بكج العناية بالمناطق الحساسة",
    "كورس انوثة": "كورس الأنوثة للعناية بالجسم",
    "كورس الانوثة": "كورس الأنوثة للعناية بالجسم",
    "كورس الانوثه": "كورس الأنوثة للعناية بالجسم",
    "كورس معالجة": "كورس معالجة البشرة",
    "عسل الانوثه": "عسل مسمن مناطق انثوية",
    "عسل الانوثة": "عسل مسمن مناطق انثوية",
    "عسل انوثة": "عسل مسمن مناطق انثوية",
    "عسل انوثه": "عسل مسمن مناطق انثوية",
    "عسل العام": "عسل مسمن عام",
    "عسل عام": "عسل مسمن عام",
    "عسل وجه": "عسل مسمن وجه",
    "عسل الوجه": "عسل مسمن وجه",
    "عسل رجالي": "عسل رجالي",
    "كريم الانوثة": "كريم  الأنةثه للعناية بالمناطق الحساسة",
    "كريم الانوثه": "كريم  الأنةثه للعناية بالمناطق الحساسة",
    "كريم انوثة": "كريم  الأنةثه للعناية بالمناطق الحساسة",
    "كريم انوثه": "كريم  الأنةثه للعناية بالمناطق الحساسة",
    "كريم معالجة": "كريم معالجة البشرة",
    "كريم معالجه": "كريم معالجة البشرة",
    "كريم التشققات": "كريم التشققات",
    "كريم تشققات": "كريم التشققات",
    "واقي شمس": "واقي شمس شهد بيوتي",
    "حمرة": "حمرة  كولدن روز",
    "حمره": "حمرة  كولدن روز",
    "اضافر": "اضافر هديه",
    "ليفة سلكونية": "ليفة سلكونية",
    "ليفه سلكونيه": "ليفة سلكونية",
    "ليفة مغربية": "ليفة مغربية",
    "ليفه مغربيه": "ليفة مغربية",
    "مخمرية": "مخمرية",
    "مكس الزيوت": "مكس الزيوت",
    "مكس زيوت": "مكس الزيوت",
    "شاي الماجا": "شاي الماجا",
    "شاي ماجا": "شاي الماجا",
    "سيروم فيتامين": "سيروم فياتمين c",
    "سيروم فيتامين سي": "سيروم فياتمين c",
    "مقشر كاندي": "مقشر كاندي",
    "تنت": "تنت الخدود والشفايف",
    "تنت سائل": "تنت سائل",
    "زيت التطويل": "زيت التطويل",
    "زيت الكثافة": "زيت الكثافة",
    "زيت الكثافه": "زيت الكثافة",
    "زيت الكافيار": "زيت الكافيار",
    "زيت ايقاف التساقط": "زيت ايقاف التساقط",
    "زيت جوز الهند": "زيت جوز الهند للشعر",
    "شامبو الكافيار": "شامبو الكافيار",
    "شامبو جوز الهند": "شامبو جوز الهند للشعر",
    "سيروم جوز الهند": "سيروم جوز الهند للشعر",
    "ماسك جوز الهند": "ماسك جوز الهند للشعر",
    "سيروم الشعر": "سيروم الشعر بخلاصة الكافيار",
    "سيروم الاضافر": "سيروم الأضافر",
    "سيروم الرموش": "سيروم الرومش",
    "ماسك الكافيار": "ماسك الكافيار",
    "مربى شكولا": "مربى الشكولا",
    "مربى فروتي": "مربى الفروتي",
    "مربى فريز": "مربى الفريز",
    "مربى كرز": "مربى الكرز",
    "مربى كرميل": "مربى الكرميل",
    "مربى موز": "مربى الموز",
    "مرطب التشيز كيك": "مرطب التشيز كيك للجسم",
    "غسول التشيز كيك": "غسول التشيز كيك",
    "مقشر التشيز كيك": "مقشر التشيز كيك",
    "بكج النيلة المغربية": "مجموعة العناية بالجسم من النيلة المغربية",
    "مجموعة النيلة": "مجموعة العناية بالجسم من النيلة المغربية",
    "غسول النيلة": "غسول النيلة",
    "غسول النيله": "غسول النيلة",
    "لوشن النيلة": "لوشن النيلة",
    "لوشن النيله": "لوشن النيلة",
    "مقشر النيلة": "مقشر النيلة",
    "مقشر النيله": "مقشر النيلة",
    "سبلاش النيلة": "سبلاش النيلة",
    "قناع النيلة": "قناع النيلة الطيني",
    "كريم تبيض الانوثة": "كريم تبيض بكج الأنوثة",
    "كريم تبيض الانوثه": "كريم تبيض بكج الأنوثة",
    "مورد الانوثة": "مورد الأنوثة",
    "مورد الانوثه": "مورد الأنوثة",
    "حنة": "حنة هدية",
    "حنه": "حنة هدية",
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
        return self._jsonrpc('/web/dataset/call_kw', {
            'model': model, 'method': method,
            'args': args or [], 'kwargs': kwargs or {}
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

def resolve_product_name(name):
    """Resolve product name using aliases, return exact Odoo name."""
    name_clean = name.strip()
    # Try exact match in aliases
    if name_clean in PRODUCT_ALIASES:
        return PRODUCT_ALIASES[name_clean]
    # Try lowercase match
    name_lower = name_clean.lower()
    for alias, real_name in PRODUCT_ALIASES.items():
        if alias.lower() == name_lower:
            return real_name
    # Try word-based matching: score by how many words match
    name_words = set(name_clean.split())
    best_match = None
    best_score = 0
    for alias, real_name in PRODUCT_ALIASES.items():
        alias_words = set(alias.split())
        common = name_words & alias_words
        # Require at least 2 common words, or all words of the shorter one match
        if len(common) >= 2 or (len(common) >= 1 and (common == name_words or common == alias_words) and len(common) > 0 and len(name_words) <= 2 and len(alias_words) <= 2):
            # Score: common words / total unique words (Jaccard similarity)
            score = len(common) / len(name_words | alias_words)
            # Bonus for exact subset match
            if name_words.issubset(alias_words) or alias_words.issubset(name_words):
                score += 0.5
            if score > best_score:
                best_score = score
                best_match = real_name
    if best_match and best_score >= 0.4:
        return best_match
    return name_clean


def _normalize_al(name):
    """Generate variants with/without Arabic 'ال' prefix for each word."""
    if not name:
        return []
    variants = set()
    variants.add(name)
    # Try removing 'ال' from beginning
    if name.startswith('ال'):
        variants.add(name[2:])
    else:
        variants.add('ال' + name)
    # Try for each word
    words = name.split()
    new_words = []
    for w in words:
        if w.startswith('ال'):
            new_words.append(w[2:])
        else:
            new_words.append('ال' + w)
    variants.add(' '.join(new_words))
    # Also try: ة -> ه and ه -> ة
    for v in list(variants):
        variants.add(v.replace('ة', 'ه'))
        variants.add(v.replace('ه', 'ة'))
    return list(variants)


def find_city(rpc, city_name, state_id):
    """Find city in x_city model by name and state."""
    if not city_name or not state_id:
        return None
    
    # Generate all name variants (with/without ال, ة/ه)
    name_variants = _normalize_al(city_name)
    logger.info(f"City lookup: '{city_name}' -> variants: {name_variants}")
    
    # Try exact match with all variants
    for variant in name_variants:
        cities = rpc.search_read('x_city', [
            ['x_name', '=', variant], ['x_studio_state', '=', state_id], ['x_active', '=', True]
        ], fields=['id', 'x_name'])
        if cities:
            logger.info(f"City exact match: '{cities[0]['x_name']}'")
            return cities[0]
    
    # Try ilike match with all variants
    all_candidates = []
    for variant in name_variants:
        cities = rpc.search_read('x_city', [
            ['x_name', 'ilike', variant], ['x_studio_state', '=', state_id], ['x_active', '=', True]
        ], fields=['id', 'x_name'], limit=20)
        all_candidates.extend(cities)
    
    # Remove duplicates
    seen_ids = set()
    unique_candidates = []
    for c in all_candidates:
        if c['id'] not in seen_ids:
            seen_ids.add(c['id'])
            unique_candidates.append(c)
    
    if unique_candidates:
        best = None
        best_score = -1
        for c in unique_candidates:
            cname = c['x_name']
            # Check exact match with any variant
            for variant in name_variants:
                if cname == variant:
                    logger.info(f"City variant match: '{cname}'")
                    return c
            score = 0
            # Check containment with original and variants
            for variant in name_variants:
                if variant in cname or cname in variant:
                    score = max(score, 100)
            words1 = set(city_name.split())
            words2 = set(cname.split())
            # Also compare without ال
            words1_no_al = set(w[2:] if w.startswith('ال') else w for w in words1)
            words2_no_al = set(w[2:] if w.startswith('ال') else w for w in words2)
            common = len(words1_no_al & words2_no_al)
            score += common * 50
            if score > best_score:
                best_score = score
                best = c
        if best:
            logger.info(f"City fuzzy match: '{best['x_name']}' (score: {best_score})")
        return best
    
    logger.warning(f"No city found for: '{city_name}'")
    return None


def find_product(rpc, product_name):
    """Find product in Odoo by name, using aliases first."""
    resolved_name = resolve_product_name(product_name)
    logger.info(f"Product lookup: '{product_name}' -> resolved: '{resolved_name}'")

    # Try exact match with resolved name
    exact = rpc.search_read('product.product', [
        ['name', '=', resolved_name], ['sale_ok', '=', True], ['active', '=', True]
    ], fields=['id', 'name', 'list_price'], limit=1)
    if exact:
        logger.info(f"Exact match found: '{exact[0]['name']}'")
        return exact[0]

    # Try ilike with resolved name
    products = rpc.search_read('product.product', [
        ['name', 'ilike', resolved_name], ['sale_ok', '=', True], ['active', '=', True]
    ], fields=['id', 'name', 'list_price'], limit=10)
    if products:
        def score(p):
            pname = p['name'].strip()
            search = resolved_name.strip()
            if pname == search:
                return 10000
            search_words = set(search.lower().split())
            pname_words = set(pname.lower().split())
            # All search words found in product name
            if search_words.issubset(pname_words):
                return 5000 - len(pname)
            # Search string contained in product name
            if search.lower() in pname.lower():
                return 3000 - len(pname)
            # Word overlap - require significant overlap
            common = len(search_words & pname_words)
            total = len(search_words | pname_words)
            if total > 0 and common / total >= 0.4:
                return common * 100 - len(pname)
            return -10000  # Poor match, penalize heavily
        products.sort(key=score, reverse=True)
        best = products[0]
        best_score = score(best)
        if best_score > -10000:
            logger.info(f"ilike match found: '{best['name']}' (score: {best_score})")
            return best

    # Try original name if different from resolved
    if resolved_name != product_name:
        products = rpc.search_read('product.product', [
            ['name', 'ilike', product_name], ['sale_ok', '=', True], ['active', '=', True]
        ], fields=['id', 'name', 'list_price'], limit=10)
        if products:
            def score2(p):
                pname = p['name'].strip()
                search = product_name.strip()
                search_words = set(search.lower().split())
                pname_words = set(pname.lower().split())
                if search_words.issubset(pname_words):
                    return 5000 - len(pname)
                common = len(search_words & pname_words)
                total = len(search_words | pname_words)
                if total > 0 and common / total >= 0.4:
                    return common * 100 - len(pname)
                return -10000
            products.sort(key=score2, reverse=True)
            best = products[0]
            if score2(best) > -10000:
                logger.info(f"Original name match: '{best['name']}'")
                return best

    # Try multi-keyword search - require ALL important keywords to match
    keywords = [kw for kw in resolved_name.split() if len(kw) > 2]
    if len(keywords) >= 2:
        # Build domain with all keywords
        domain = [['sale_ok', '=', True], ['active', '=', True]]
        for kw in keywords:
            domain.append(['name', 'ilike', kw])
        products = rpc.search_read('product.product', domain, fields=['id', 'name', 'list_price'], limit=5)
        if products:
            logger.info(f"Multi-keyword match: '{products[0]['name']}'")
            return products[0]

    # Try with Arabic normalization variants (ال, ة/ه)
    name_variants = _normalize_al(resolved_name)
    for variant in name_variants:
        if variant == resolved_name:
            continue
        products = rpc.search_read('product.product', [
            ['name', 'ilike', variant], ['sale_ok', '=', True], ['active', '=', True]
        ], fields=['id', 'name', 'list_price'], limit=10)
        if products:
            logger.info(f"Arabic variant match: '{products[0]['name']}' (variant: '{variant}')")
            return products[0]

    # Try single keyword search - search by each word individually and score results
    keywords = [kw for kw in resolved_name.split() if len(kw) > 1]
    # Also add variants without ال and with ة/ه swap
    expanded_keywords = set()
    for kw in keywords:
        expanded_keywords.add(kw)
        if kw.startswith('ال'):
            expanded_keywords.add(kw[2:])
        else:
            expanded_keywords.add('ال' + kw)
        expanded_keywords.add(kw.replace('ة', 'ه'))
        expanded_keywords.add(kw.replace('ه', 'ة'))
        # Handle common Arabic spelling variations
        expanded_keywords.add(kw.replace('ي', 'ى'))
        expanded_keywords.add(kw.replace('ى', 'ي'))
    
    all_single_results = []
    seen_ids = set()
    for kw in expanded_keywords:
        if len(kw) < 2:
            continue
        products = rpc.search_read('product.product', [
            ['name', 'ilike', kw], ['sale_ok', '=', True], ['active', '=', True]
        ], fields=['id', 'name', 'list_price'], limit=10)
        for p in products:
            if p['id'] not in seen_ids:
                seen_ids.add(p['id'])
                all_single_results.append(p)
    
    if all_single_results:
        # Score each result by how many of the original keywords appear in its name
        def fuzzy_score(p):
            pname = p['name'].lower()
            pname_normalized = pname.replace('ة', 'ه').replace('ى', 'ي')
            # Remove ال from product name words for comparison
            pname_words = set()
            for w in pname.split():
                pname_words.add(w)
                if w.startswith('ال'):
                    pname_words.add(w[2:])
            pname_words_normalized = set(w.replace('ة', 'ه').replace('ى', 'ي') for w in pname_words)
            
            score = 0
            for kw in keywords:
                kw_lower = kw.lower()
                kw_normalized = kw_lower.replace('ة', 'ه').replace('ى', 'ي')
                kw_no_al = kw_lower[2:] if kw_lower.startswith('ال') else kw_lower
                kw_no_al_normalized = kw_normalized[2:] if kw_normalized.startswith('ال') else kw_normalized
                
                # Check if keyword (or its variant) appears in product name
                if kw_lower in pname or kw_no_al in pname:
                    score += 100
                elif kw_normalized in pname_normalized or kw_no_al_normalized in pname_normalized:
                    score += 90
                # Check partial match (keyword is start of a word in product name)
                elif any(w.startswith(kw_no_al[:3]) for w in pname_words if len(kw_no_al) >= 3):
                    score += 50
                elif any(w.startswith(kw_no_al_normalized[:3]) for w in pname_words_normalized if len(kw_no_al_normalized) >= 3):
                    score += 40
            return score
        
        all_single_results.sort(key=fuzzy_score, reverse=True)
        best = all_single_results[0]
        best_sc = fuzzy_score(best)
        if best_sc >= 50:  # At least one reasonable keyword match
            logger.info(f"Fuzzy single-keyword match: '{best['name']}' (score: {best_sc})")
            return best

    logger.warning(f"No product found for: '{product_name}' (resolved: '{resolved_name}')")
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
    city_matched = False
    if city_name and state_id:
        city = find_city(rpc, city_name, state_id)
        if city:
            customer_vals['x_studio_city'] = city['id']
            city_matched = True
        else:
            customer_vals['city'] = city_name
    elif city_name:
        customer_vals['city'] = city_name

    street = order_data.get("street", "")
    nearest = order_data.get("nearest_landmark", "")
    if street:
        customer_vals['street'] = street
    if nearest:
        customer_vals['street2'] = nearest

    partner_id = rpc.create('res.partner', customer_vals)

    # 2. Create sale order
    carrier = CARRIER_MAP.get(brand, CARRIER_MAP["shahd"])

    # Build shipment notes (without Instagram link)
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
    product_lines = []
    matched_products = []
    products_total = 0

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
            line_id = rpc.create('sale.order.line', line_vals)
            product_lines.append(line_id)

            price = 0 if is_gift else product['list_price'] * qty
            products_total += price
            gift_label = " (هدية)" if is_gift else ""
            matched_products.append(f"{product['name']} x{qty}{gift_label}")
        else:
            unmatched.append(item["name"])

    # 4. Add delivery via wizard
    try:
        wiz_id = rpc.create('choose.delivery.carrier', {
            'order_id': order_id, 'carrier_id': carrier['carrier_id'], 'delivery_price': 0,
        })
        rpc.call('choose.delivery.carrier', 'button_confirm', [[wiz_id]])
    except Exception:
        rpc.write('sale.order', order_id, {'carrier_id': carrier['carrier_id']})
        rpc.create('sale.order.line', {
            'order_id': order_id, 'product_id': carrier['product_id'],
            'product_uom_qty': 1, 'price_unit': 0,
            'name': carrier['name'], 'is_delivery': True,
        })

    # 5. Adjust price - delivery fee = target - products total
    raw_total = order_data.get("total_price", 0)
    target_total = raw_total if raw_total >= 1000 else raw_total * 1000

    delivery_fee = 0
    if target_total > 0:
        delivery_fee = max(0, target_total - products_total)
        # Find delivery line and set its price
        delivery_lines = rpc.search_read('sale.order.line', [
            ['order_id', '=', order_id], ['is_delivery', '=', True]
        ], fields=['id', 'price_unit'])
        if delivery_lines:
            rpc.write('sale.order.line', delivery_lines[0]['id'], {'price_unit': delivery_fee})

        # If products total > target (discount needed), adjust first product line
        if products_total > target_total and product_lines:
            diff = products_total - target_total
            first_line = rpc.read('sale.order.line', product_lines[0], fields=['price_unit'])[0]
            rpc.write('sale.order.line', product_lines[0], {
                'price_unit': first_line['price_unit'] - diff
            })
            delivery_fee = 0

    # Read final total and force-adjust if needed
    order_info = rpc.read('sale.order', order_id, fields=['amount_total', 'name'])[0]
    current_total = order_info['amount_total']
    order_name = order_info['name']

    # Force-adjust: if target_total is set and current_total doesn't match, fix it
    if target_total > 0 and abs(current_total - target_total) > 1:
        diff = target_total - current_total
        logger.info(f"Price adjustment needed: current={current_total}, target={target_total}, diff={diff}")
        if product_lines:
            # Adjust first product line price to make total match
            first_line_data = rpc.read('sale.order.line', product_lines[0], fields=['price_unit', 'product_uom_qty'])[0]
            old_price = first_line_data['price_unit']
            qty = first_line_data['product_uom_qty'] or 1
            # diff is spread across qty
            new_price = old_price + (diff / qty)
            rpc.write('sale.order.line', product_lines[0], {'price_unit': new_price})
            logger.info(f"Adjusted first product line price: {old_price} -> {new_price}")
            # Re-read total
            order_info = rpc.read('sale.order', order_id, fields=['amount_total', 'name'])[0]
            current_total = order_info['amount_total']
            logger.info(f"New total after adjustment: {current_total}")

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
    }


# ============ Telegram Bot Handlers ============

BRAND_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("🟣 شهد بيوتي"), KeyboardButton("🔵 مارلين")]],
    resize_keyboard=True,
    is_persistent=True
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً! أنا بوت إدخال الطلبات لأودو.\n\n"
        "اختر المتجر من الأزرار بالأسفل، ثم أرسل الطلبات.\n"
        "تقدر تغير المتجر بأي وقت بالضغط على الزر الثاني.",
        reply_markup=BRAND_KEYBOARD
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "كيفية الاستخدام:\n\n"
        "1. أرسل /shahd أو /marlin لتحديد البراند\n"
        "2. ألصق رسالة الطلب من الواتساب\n"
        "3. راجع البيانات واضغط تأكيد\n\n"
        "يمكنك إرسال عدة طلبات متتالية بدون مشاكل.\n"
        "البراند يبقى محدد حتى تغيره."
    )

async def set_shahd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['brand'] = 'shahd'
    await update.message.reply_text("✅ تم تحديد البراند: شهد بيوتي\nأرسل الطلبات...", reply_markup=BRAND_KEYBOARD)

async def set_marlin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['brand'] = 'marlin'
    await update.message.reply_text("✅ تم تحديد البراند: مارلين\nأرسل الطلبات...", reply_markup=BRAND_KEYBOARD)


async def handle_order_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming order text - supports multiple concurrent orders."""
    message_text = update.message.text
    if message_text.startswith('/'):
        return

    # Handle brand selection buttons
    if message_text == "🟣 شهد بيوتي":
        context.user_data['brand'] = 'shahd'
        await update.message.reply_text("✅ تم تحديد البراند: شهد بيوتي\nأرسل الطلبات...", reply_markup=BRAND_KEYBOARD)
        return
    elif message_text == "🔵 مارلين":
        context.user_data['brand'] = 'marlin'
        await update.message.reply_text("✅ تم تحديد البراند: مارلين\nأرسل الطلبات...", reply_markup=BRAND_KEYBOARD)
        return

    msg = await update.message.reply_text("جاري تحليل الطلب...")

    try:
        parsed = parse_with_llm(message_text)
    except Exception as e:
        await msg.edit_text(f"خطأ بتحليل الطلب: {e}")
        return

    # Validate province
    province = parsed.get("province", "")
    state_id = PROVINCE_MAP.get(province)
    province_warning = ""
    if not province or not state_id:
        province_warning = "\n\n⚠️ المحافظة غير محددة أو غير موجودة! لازم تحدد المحافظة الصحيحة."

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
    total_display = f"{raw_total},000" if raw_total < 1000 else f"{raw_total:,}"

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

    # Generate unique order key for this specific order
    import time
    order_key = f"order_{int(time.time() * 1000)}"
    
    # Store order data with unique key
    if 'pending_orders' not in context.user_data:
        context.user_data['pending_orders'] = {}
    context.user_data['pending_orders'][order_key] = parsed

    brand = context.user_data.get('brand')

    # If province or city missing, show warning and ask to fix
    if province_warning or city_warning:
        summary += province_warning + city_warning
        summary += "\n\nأرسل الطلب مرة ثانية بالمحافظة والمنطقة الصحيحة."
        await msg.edit_text(f"بيانات الطلب:\n\n{summary}")
        # Remove pending order
        context.user_data['pending_orders'].pop(order_key, None)
        return

    if brand:
        brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"
        summary += f"\nالبراند: {brand_name}"

        keyboard = [
            [InlineKeyboardButton("تأكيد وإدخال ✅", callback_data=f"confirm_{order_key}")],
            [InlineKeyboardButton("إلغاء ❌", callback_data=f"cancel_{order_key}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(f"بيانات الطلب:\n\n{summary}", reply_markup=reply_markup)
    else:
        keyboard = [
            [InlineKeyboardButton("شهد بيوتي", callback_data=f"brand_shahd_{order_key}")],
            [InlineKeyboardButton("مارلين", callback_data=f"brand_marlin_{order_key}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(
            f"بيانات الطلب:\n\n{summary}\n\nاختر البراند:",
            reply_markup=reply_markup
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks - supports multiple orders."""
    query = update.callback_query
    await query.answer()
    data = query.data

    pending = context.user_data.get('pending_orders', {})

    if data.startswith("brand_"):
        # Format: brand_shahd_orderkey or brand_marlin_orderkey
        parts = data.split("_", 2)  # brand, shahd/marlin, orderkey
        if len(parts) >= 3:
            brand = parts[1]
            order_key = parts[2]
        else:
            brand = parts[1]
            order_key = None

        context.user_data['brand'] = brand
        brand_name = "شهد بيوتي" if brand == "shahd" else "مارلين"

        # Get current message text and append brand
        current_text = query.message.text
        # Remove "اختر البراند:" line
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
        # Format: confirm_orderkey
        order_key = data.replace("confirm_", "") if "_" in data else None
        parsed = pending.get(order_key) if order_key else context.user_data.get('parsed_order')
        brand = context.user_data.get('brand', 'shahd')

        if not parsed:
            await query.edit_message_text("خطأ: ما لقيت بيانات الطلب. أرسل الطلب مرة ثانية.")
            return

        await query.edit_message_text("جاري إدخال الطلب في أودو... ⏳")

        try:
            result = await asyncio.to_thread(create_full_order, parsed, brand)

            products_list = "\n".join([f"  - {p}" for p in result['products']])
            unmatched_text = ""
            if result['unmatched']:
                unmatched_text = f"\n\n⚠️ منتجات غير موجودة: {', '.join(result['unmatched'])}"

            total_match = "✅" if abs(result['total'] - result['target']) < 100 else f"⚠️ (المطلوب: {result['target']:,.0f})"

            warnings = ""
            if not result.get('province_matched'):
                warnings += "\n⚠️ المحافظة لم يتم ربطها بالنظام"
            if not result.get('city_matched'):
                warnings += "\n⚠️ المنطقة لم يتم ربطها بالنظام"

            response = (
                f"تم إدخال الطلب بنجاح! ✅\n\n"
                f"رقم الطلب: {result['order_name']}\n"
                f"العميل: {result['customer_name']}\n"
                f"المحافظة: {result.get('province', '?')}\n"
                f"المنطقة: {result.get('city', '?')}\n"
                f"المنتجات:\n{products_list}\n"
                f"التوصيل: {result['delivery_fee']:,.0f} د.ع ({result['carrier']})\n"
                f"الإجمالي: {result['total']:,.0f} د.ع {total_match}"
                f"{unmatched_text}{warnings}\n\n"
                f"الرابط: {result['url']}"
            )
            await query.edit_message_text(response)

        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            await query.edit_message_text(f"خطأ بإدخال الطلب:\n{e}\n\nحاول مرة ثانية.")

        # Remove this specific order from pending
        if order_key:
            pending.pop(order_key, None)

    elif data.startswith("cancel"):
        order_key = data.replace("cancel_", "") if "_" in data else None
        if order_key:
            pending.pop(order_key, None)
        await query.edit_message_text("تم إلغاء الطلب. ❌")


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
