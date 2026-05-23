#!/usr/bin/env python3
"""
Parse a WhatsApp order message and extract structured order data using LLM.
v4: Catalog-first product extraction — products are matched against real Odoo catalog
    before LLM, eliminating hallucination and wrong-product errors.
"""

import json
import os
import re
import logging

logger = logging.getLogger(__name__)

# Province name normalization map - covers common Iraqi dialect variations
PROVINCE_NORMALIZE = {
    "نجف": "النجف", "النجف": "النجف",
    "بغداد": "بغداد",
    "بصرة": "البصرة", "البصرة": "البصرة", "البصره": "البصرة", "بصره": "البصرة",
    "كربلاء": "كربلاء", "كربلا": "كربلاء",
    "ميسان": "ميسان", "العمارة": "ميسان", "العماره": "ميسان", "عمارة": "ميسان", "عماره": "ميسان",
    "ذي قار": "ذي قار", "الناصرية": "ذي قار", "الناصريه": "ذي قار", "ناصرية": "ذي قار", "ناصريه": "ذي قار",
    "واسط": "واسط", "الكوت": "واسط", "كوت": "واسط",
    "ديالى": "ديالى", "ديالي": "ديالى", "بعقوبة": "ديالى", "بعقوبه": "ديالى",
    "الأنبار": "الأنبار", "الانبار": "الأنبار", "انبار": "الأنبار", "الرمادي": "الأنبار", "رمادي": "الأنبار",
    "بابل": "بابل", "الحلة": "بابل", "الحله": "بابل", "حلة": "بابل", "حله": "بابل",
    "نينوى": "نينوى", "نينوي": "نينوى", "الموصل": "نينوى", "موصل": "نينوى",
    "صلاح الدين": "صلاح الدين", "تكريت": "صلاح الدين",
    "القادسية": "القادسية", "القادسيه": "القادسية", "الديوانية": "القادسية", "الديوانيه": "القادسية", "ديوانية": "القادسية", "ديوانيه": "القادسية",
    "المثنى": "المثنى", "المثني": "المثنى", "السماوة": "المثنى", "السماوه": "المثنى", "سماوة": "المثنى", "سماوه": "المثنى",
    "أربيل": "أربيل", "اربيل": "أربيل",
    "دهوك": "دهوك",
    "كركوك": "كركوك",
    "السليمانية": "السليمانية", "السليمانيه": "السليمانية", "سليمانية": "السليمانية", "سليمانيه": "السليمانية",
}

# Known cities/districts per province - to help validate
KNOWN_CITIES = {
    "النجف": ["الكوفة", "كوفة", "كوفه", "المناذرة", "المشخاب", "الحيرة", "الحيره", "ابو صخير"],
    "بغداد": ["الكرادة", "الكراده", "مدينة الصدر", "مدينه الصدر", "الصدر", "الكاظمية", "الكاظميه", "الاعظمية", "الاعظميه", "المنصور", "الشعلة", "الشعله", "البلديات", "بلديات", "حي اور", "اور", "الشعب", "الحرية", "الحريه", "زيونة", "زيونه", "البياع", "الجادرية", "الجادريه", "الدورة", "الدوره", "الغزالية", "الغزاليه", "حي الجهاد", "الجهاد", "الحسينية", "الحسينيه", "المعامل", "الطالبية", "الطالبيه", "الامين", "الأمين", "رصافة", "كرخ"],
    "البصرة": ["الزبير", "ابو الخصيب", "شط العرب", "الفاو", "القرنة", "القرنه", "المدينة", "المدينه", "الجزائر", "الابله", "الابلة"],
    "كربلاء": ["عين التمر", "الهندية", "الهنديه", "الحسينية", "الحسينيه"],
    "ميسان": ["المجر", "علي الغربي", "قلعة صالح"],
    "ذي قار": ["الشطرة", "الشطره", "الرفاعي", "سوق الشيوخ"],
    "واسط": ["الصويرة", "الصويره", "الحي", "النعمانية", "النعمانيه"],
    "ديالى": ["المقدادية", "المقداديه", "خانقين", "بلدروز"],
    "الأنبار": ["الفلوجة", "الفلوجه", "هيت", "حديثة", "حديثه", "القائم"],
    "بابل": ["المسيب", "الهاشمية", "الهاشميه", "المحاويل"],
}


def _detect_province_from_text(text: str) -> str:
    """Try to detect province from raw text using known province names."""
    text_clean = text.strip()
    sorted_names = sorted(PROVINCE_NORMALIZE.keys(), key=len, reverse=True)
    for name in sorted_names:
        if name in text_clean:
            return PROVINCE_NORMALIZE[name]
    return ""


def _detect_city_from_text(text: str, province: str) -> str:
    """Try to detect city/district from raw text."""
    if province in KNOWN_CITIES:
        for city in KNOWN_CITIES[province]:
            if city in text:
                return city
    return ""


# Arabic number words to digits mapping
ARABIC_NUMBER_WORDS = {
    'واحد': 1, 'وحده': 1, 'وحدة': 1, 'واحدة': 1, 'واحده': 1,
    'اثنين': 2, 'اثنينين': 2, 'ثنتين': 2, 'ثنين': 2, 'ثنتان': 2,
    'ثلاثة': 3, 'ثلاث': 3, 'ثلاثه': 3,
    'اربعة': 4, 'اربع': 4, 'اربعه': 4, 'أربعة': 4, 'أربع': 4,
    'خمسة': 5, 'خمس': 5, 'خمسه': 5,
    'ستة': 6, 'ست': 6, 'سته': 6,
    'سبعة': 7, 'سبع': 7, 'سبعه': 7,
    'ثمانية': 8, 'ثمان': 8, 'ثمانيه': 8,
    'تسعة': 9, 'تسع': 9, 'تسعه': 9,
    'عشرة': 10, 'عشر': 10, 'عشره': 10,
}


# All known product keywords - any line containing these is a product line
PRODUCT_LINE_KEYWORDS = [
    'بكج', 'عسل', 'كريم', 'غسول', 'لوشن', 'مقشر', 'مربى', 'عطر', 'كورس',
    'زيت', 'شامبو', 'سيروم', 'ماسك', 'تنت', 'صابونة', 'صابونه', 'حمرة', 'حمره', 'رموش',
    'اضافر', 'اظافر', 'اضاضر', 'ليفة', 'ليفه', 'سبلاش', 'قناع', 'مورد', 'واقي',
    'مخمرية', 'شاي', 'مكس', 'بياض', 'نيلة', 'كافيار', 'مسك', 'حنة', 'حنه',
    'مبيض', 'مبيضة', 'مرطب', 'كريمة', 'بودر', 'مسحوق', 'بخاخ', 'سكراب', 'صبغة',
    'مسكارا', 'مسكاره', 'قلم', 'اسنز', 'essence', 'بوند', 'كف', 'ليب', 'بلاشر', 'هايلايتر'
]

# Lines that are NOT products (address/phone/price/notes indicators)
NON_PRODUCT_PATTERNS = [
    r'\d{10,}',           # phone numbers
    r'07\d{8,}',          # Iraqi phone
    r'سعر\s*\d',            # price line
    r'الحساب\s*\d',         # price line
    r'السعر\s*\d',           # price line
    r'العنوان',           # address line
    r'بغداد|بصرة|نجف|كربلاء|موصل|اربيل|أربيل|السليمانية|دهوك|كركوك|ميسان|بابل|واسط|ديالى|الانبار|الأنبار|صلاح الدين|القادسية|المثنى|ذي قار',  # Iraqi provinces
    r'طابق|بناية|عمارة|شارع|زقاق',  # address details
    r'رقم التلفون|رقم التليفون|هذا رقم',  # phone label
    r'الاسم\s*:|\u0627لاسم\s*:',    # name label
    r'شوف الطلب|شسالفه|الله يخليك',  # notes
    r'^الاسم',              # starts with الاسم
]


def _is_product_line(line: str) -> bool:
    """Check if a text line looks like a product line."""
    line_clean = line.strip()
    if not line_clean:
        return False

    for pattern in NON_PRODUCT_PATTERNS:
        if re.search(pattern, line_clean):
            return False

    line_check = re.sub(r'^(\u0647\u062f\u064a\u0629|\u0647\u062f\u064a\u0647)\s*', '', line_clean).strip()

    for kw in PRODUCT_LINE_KEYWORDS:
        pattern = r'(?:^|\s)' + re.escape(kw) + r'(?:\s|$)'
        if re.search(pattern, line_check):
            return True

    return False


def _extract_product_lines_from_text(text: str) -> list:
    """
    Extract product lines from raw order text.
    Returns list of dicts: [{name, quantity, is_gift}]
    """
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    products = []

    for line in lines:
        if not _is_product_line(line):
            continue

        # Detect is_gift: هدية/هديه anywhere in line
        is_gift = bool(re.search(r'(^|\s)(هدية|هديه)(\s|$)', line))
        # Remove هدية/هديه from start, end, or middle (before/after quantity)
        name = re.sub(r'^(هدية|هديه)\s+', '', line).strip()
        name = re.sub(r'\s+(هدية|هديه)$', '', name).strip()
        name = re.sub(r'\s+(هدية|هديه)\s+', ' ', name).strip()

        quantity = 1
        adad_match = re.search(r'\s*عدد\s*(\d{1,2})$', name)
        if adad_match:
            quantity = int(adad_match.group(1))
            name = name[:adad_match.start()].strip()
        elif re.search(r'[xX×]\s*(\d{1,2})$', name):
            x_match = re.search(r'[xX×]\s*(\d{1,2})$', name)
            quantity = int(x_match.group(1))
            name = name[:x_match.start()].strip()
        else:
            digit_match = re.search(r'\s+(\d{1,2})$', name)
            if digit_match:
                num = int(digit_match.group(1))
                if 1 <= num <= 20:
                    quantity = num
                    name = name[:digit_match.start()].strip()
            else:
                for word, num in sorted(ARABIC_NUMBER_WORDS.items(), key=lambda x: len(x[0]), reverse=True):
                    if name.endswith(' ' + word):
                        quantity = num
                        name = name[:-(len(word)+1)].strip()
                        break

        products.append({'name': name, 'quantity': quantity, 'is_gift': is_gift})

    return products


def _merge_products(llm_products: list, extracted_products: list) -> list:
    """
    Merge LLM-extracted products with Python-extracted products.
    Python extraction is ground truth for WHICH products exist.
    """
    from product_aliases import PRODUCT_ALIASES

    if not extracted_products:
        return llm_products

    if not llm_products:
        return extracted_products

    def _resolve_alias(name: str) -> str:
        name_lower = name.strip().lower()
        for alias, resolved in PRODUCT_ALIASES.items():
            if alias.lower() == name_lower:
                return resolved.lower()
        return name_lower

    def _name_similar(a: str, b: str) -> bool:
        a_resolved = _resolve_alias(a)
        b_resolved = _resolve_alias(b)
        if a_resolved == b_resolved:
            return True

        a_clean = re.sub(r'[\s]', '', a_resolved)
        b_clean = re.sub(r'[\s]', '', b_resolved)
        if len(a_clean) >= 3 and len(b_clean) >= 3:
            if a_clean in b_clean or b_clean in a_clean:
                return True
        a_words = set(w for w in a.split() if len(w) > 1)
        b_words = set(w for w in b.split() if len(w) > 1)
        if a_words and b_words:
            overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
            if overlap >= 0.5:
                return True
        return False

    merged = []
    used_llm_indices = set()

    for ext_prod in extracted_products:
        best_match = None
        best_idx = -1
        for i, llm_prod in enumerate(llm_products):
            if i in used_llm_indices:
                continue
            if _name_similar(ext_prod['name'], llm_prod.get('name', '')):
                best_match = llm_prod
                best_idx = i
                break

        if best_match is not None:
            used_llm_indices.add(best_idx)
            best_match['is_gift'] = ext_prod['is_gift'] or best_match.get('is_gift', False)
            if best_match.get('quantity', 1) == 1 and ext_prod['quantity'] > 1:
                best_match['quantity'] = ext_prod['quantity']
            merged.append(best_match)
        else:
            merged.append(ext_prod)

    for i, llm_prod in enumerate(llm_products):
        if i not in used_llm_indices:
            already = any(_name_similar(llm_prod.get('name', ''), m.get('name', '')) for m in merged)
            if not already:
                merged.append(llm_prod)

    return merged


def _fix_quantities(parsed: dict, original_text: str) -> dict:
    """Fix product quantities by checking original text for Arabic number words."""
    products = parsed.get('products', [])
    lines = [l.strip() for l in original_text.strip().split('\n') if l.strip()]

    used_lines = set()
    product_line_map = []

    for product in products:
        pname = product.get('name', '').strip()
        pname_clean = re.sub(r'^(هدية|هديه)\s*', '', pname).strip()

        best_line = None
        best_score = 0

        for idx, line in enumerate(lines):
            if idx in used_lines:
                continue
            pwords = [w for w in pname_clean.split() if len(w) > 1]
            if not pwords:
                continue
            matches = sum(1 for w in pwords if w in line)
            score = matches / len(pwords) if pwords else 0

            if score > best_score:
                best_score = score
                best_line = idx

        if best_line is not None and best_score >= 0.5:
            used_lines.add(best_line)
            product_line_map.append((product, lines[best_line]))
        else:
            product_line_map.append((product, None))

    for product, line in product_line_map:
        if line is None:
            continue

        pname = product.get('name', '').strip()
        pname_clean = re.sub(r'^(هدية|هديه)\s*', '', pname).strip()

        remaining = line
        for w in pname_clean.split():
            remaining = remaining.replace(w, '', 1)
        remaining = re.sub(r'(هدية|هديه)', '', remaining).strip()

        found_qty = False
        for word, num in sorted(ARABIC_NUMBER_WORDS.items(), key=lambda x: len(x[0]), reverse=True):
            if word in remaining:
                product['quantity'] = num
                found_qty = True
                break

        if not found_qty:
            adad_match = re.search(r'عدد\s*(\d{1,2})', remaining)
            if adad_match:
                num = int(adad_match.group(1))
                if 1 <= num <= 20:
                    product['quantity'] = num
                    found_qty = True

        if not found_qty:
            x_match = re.search(r'[xX×]\s*(\d{1,2})', remaining)
            if x_match:
                num = int(x_match.group(1))
                if 1 <= num <= 20:
                    product['quantity'] = num
                    found_qty = True

        if not found_qty:
            digit_match = re.search(r'\b(\d{1,2})\b', remaining)
            if digit_match:
                num = int(digit_match.group(1))
                if 1 <= num <= 20:
                    product['quantity'] = num
                    found_qty = True

        if not found_qty:
            product['quantity'] = 1

    for product in products:
        name = product.get('name', '')
        name = re.sub(r'^(هدية|هديه)\s*', '', name).strip()
        for word in ARABIC_NUMBER_WORDS:
            if name.endswith(' ' + word):
                name = name[:-(len(word)+1)].strip()
        product['name'] = name

    return parsed


def _validate_and_fix(parsed: dict, original_text: str) -> dict:
    """Validate and fix province/city extraction using the original text."""

    # 0. Fix shorthand price (e.g., 75 → 75000, 50 → 50000)
    price = parsed.get('total_price', 0)
    if isinstance(price, (int, float)) and 0 < price < 1000:
        parsed['total_price'] = int(price * 1000)

    # 1. Fix province
    llm_province = parsed.get("province", "")
    detected_province = _detect_province_from_text(original_text)

    llm_province_normalized = PROVINCE_NORMALIZE.get(
        llm_province.replace("ال", "", 1) if llm_province.startswith("ال") else llm_province,
        PROVINCE_NORMALIZE.get(llm_province, "")
    )

    if detected_province and detected_province != llm_province_normalized:
        parsed["province"] = detected_province
    elif detected_province:
        parsed["province"] = detected_province
    elif llm_province_normalized:
        parsed["province"] = llm_province_normalized

    # 2. Fix city - if city is same as province name, it's wrong
    city = parsed.get("city", "")
    province = parsed.get("province", "")

    city_clean = city.replace("ال", "", 1) if city.startswith("ال") else city
    if city_clean in PROVINCE_NORMALIZE or city in PROVINCE_NORMALIZE:
        detected_city = _detect_city_from_text(original_text, province)
        if detected_city:
            parsed["city"] = detected_city
        else:
            street = parsed.get("street", "")
            if street:
                parsed["city"] = street
                parsed["street"] = ""

    # 3. Check for حي pattern
    hiy_match = re.search(r'حي\s+(\S+)', original_text)
    if hiy_match:
        hiy_name = "حي " + hiy_match.group(1)
        current_city = parsed.get("city", "")
        general_areas = ["رصافة", "رصافه", "رصافة أولى", "رصافة ثانية", "كرخ", "كرخ أولى", "كرخ ثانية"]
        if any(area in current_city for area in general_areas) or not current_city:
            old_city = current_city
            parsed["city"] = hiy_name
            if old_city and old_city not in (parsed.get("street", "") or ""):
                street = parsed.get("street", "")
                parsed["street"] = f"{old_city} {street}".strip() if street else old_city

    # 4. Ensure street has full address details
    if not parsed.get("street") and parsed.get("nearest_landmark"):
        parsed["street"] = parsed["nearest_landmark"]

    # 5. Check if LLM put a product name in customer_name by mistake
    PRODUCT_KEYWORDS = [
        'بكج', 'عسل', 'كريم', 'غسول', 'لوشن', 'مقشر', 'مربى', 'عطر', 'كورس',
        'زيت', 'شامبو', 'سيروم', 'ماسك', 'تنت', 'صابونة', 'حمرة', 'رموش', 'اضافر', 'ليفة',
        'سبلاش', 'قناع', 'مورد', 'سيروم', 'واقي', 'مخمرية', 'شاي'
    ]
    customer_name = parsed.get('customer_name', '')
    customer_name_lower = customer_name.strip().lower()
    is_product_name = any(kw in customer_name_lower for kw in PRODUCT_KEYWORDS)

    if is_product_name:
        products = parsed.get('products', [])
        already_in_list = any(
            customer_name.strip() in p.get('name', '') or p.get('name', '') in customer_name.strip()
            for p in products
        )
        if not already_in_list:
            products.insert(0, {'name': customer_name.strip(), 'quantity': 1, 'is_gift': False})
            parsed['products'] = products
        lines = [l.strip() for l in original_text.strip().split('\n') if l.strip()]
        for line in lines:
            line_has_product = any(kw in line for kw in PRODUCT_KEYWORDS)
            line_has_phone = bool(re.search(r'07\d{9}', line))
            line_has_price = bool(re.search(r'سعر|\d{4,}', line))
            if not line_has_product and not line_has_phone and not line_has_price:
                words = line.split()
                if 1 <= len(words) <= 4 and not re.search(r'\d', line):
                    parsed['customer_name'] = line
                    break

    return parsed


# ============ Catalog-First Product Extraction ============

def _normalize_ar(text: str) -> str:
    """Normalize Arabic text for comparison."""
    if not text:
        return ""
    t = text.strip()
    t = t.replace('ة', 'ه').replace('ى', 'ي')
    t = t.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
    t = t.replace('ـ', '')
    for c in 'ًٌٍَُِّْ':
        t = t.replace(c, '')
    # Normalize mini/مني/ميني → same token
    t = t.replace('ميني', 'مني').replace('mini', 'مني')
    return t.lower()


def _strip_al(w: str) -> str:
    """Strip Arabic definite article and preposition prefixes."""
    # Remove preposition + article: للـ، بالـ، وال، فال، كال
    for prefix in ('لل', 'بال', 'وال', 'فال', 'كال'):
        if w.startswith(prefix):
            return w[len(prefix):]
    # Remove plain article: ال
    if w.startswith('ال'):
        return w[2:]
    return w


def _catalog_match_score(query: str, product_name: str) -> float:
    """
    Score how well a query string matches a product name.
    Returns 0.0 to 1.0. Higher = better match.
    """
    q = _normalize_ar(query)
    p = _normalize_ar(product_name)

    if q == p:
        return 1.0

    q_words = set(_strip_al(w) for w in q.split() if len(w) > 1)
    p_words = set(_strip_al(w) for w in p.split() if len(w) > 1)

    if not q_words or not p_words:
        return 0.0

    # Generic words that shouldn't drive matching alone
    generic = {'بكج', 'كريم', 'عطر', 'زيت', 'سيروم', 'غسول', 'مقشر',
               'لوشن', 'شامبو', 'ماسك', 'مربي', 'عسل', 'كورس', 'صابونه',
               'مرطب', 'هديه', 'للعنايه', 'بالجسم', 'للوجه', 'صابون'}

    intersection = q_words & p_words
    specific = intersection - generic

    if not intersection:
        return 0.0

    # Require at least 1 specific word for a valid match
    if not specific:
        return 0.1

    jaccard = len(intersection) / len(q_words | p_words)

    # Bonus: all query words found in product name
    if q_words.issubset(p_words):
        jaccard = max(jaccard, 0.85)

    # Bonus: specific words match
    specific_score = len(specific) / max(len(q_words), 1)

    # Bonus: substring containment (handles partial names like 'كورس بياض وجه' in 'كورس بياض الثلج للوجه')
    q_no_space = re.sub(r'\s+', '', q)
    p_no_space = re.sub(r'\s+', '', p)
    if len(q_no_space) >= 4 and (q_no_space in p_no_space or p_no_space in q_no_space):
        jaccard = max(jaccard, 0.75)

    return max(jaccard, specific_score * 0.8)


def extract_products_from_catalog(text: str, brand: str, rpc=None) -> list:
    """
    Catalog-first product extraction:
    1. Extract candidate product lines from text (keyword-based)
    2. For each line, find best matching product in Odoo catalog
    3. Return list of {name (as written), quantity, is_gift, catalog_match, catalog_score}

    This replaces LLM-based product extraction for the product list.
    The LLM is still used for customer info (name, phone, address, price).
    """
    from product_aliases import PRODUCT_ALIASES, BRAND_ALIASES

    # Step 1: Extract candidate lines
    raw_lines = _extract_product_lines_from_text(text)
    if not raw_lines:
        return []

    # Step 2: Get catalog
    catalog = []
    if rpc is not None:
        try:
            all_products = rpc.get_all_products()
            # Filter by brand
            for p in all_products:
                categ = p.get('categ_complete_name', '')
                has_shahd = 'شهد' in categ
                has_marlin = 'مارلين' in categ
                if brand == 'shahd' and has_marlin and not has_shahd:
                    continue
                if brand == 'marlin' and has_shahd and not has_marlin:
                    continue
                catalog.append(p)
        except Exception as e:
            logger.warning(f"Could not load catalog for catalog-first extraction: {e}")

    results = []
    for item in raw_lines:
        raw_name = item['name']
        qty = item['quantity']
        is_gift = item['is_gift']

        # Resolve via brand aliases first
        resolved = raw_name
        rn = _normalize_ar(raw_name)
        if brand and brand in BRAND_ALIASES:
            bmap = BRAND_ALIASES[brand]
            # Sort by alias length descending: longer aliases match first (e.g. 'صابونة الكركم مني' before 'صابونة الكركم')
            for alias, real in sorted(bmap.items(), key=lambda x: len(x[0]), reverse=True):
                if _normalize_ar(alias) == rn:
                    resolved = real
                    break

        # Then general aliases (also sorted by length)
        if resolved == raw_name:
            for alias, real in sorted(PRODUCT_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
                if _normalize_ar(alias) == rn:
                    resolved = real
                    break

        # Find best catalog match
        best_product = None
        best_score = 0.0
        if catalog:
            for p in catalog:
                score = _catalog_match_score(resolved, p['name'])
                if score > best_score:
                    best_score = score
                    best_product = p

        entry = {
            'name': raw_name,          # original as written
            'resolved_name': resolved,  # after alias resolution
            'quantity': qty,
            'is_gift': is_gift,
            'catalog_product': best_product,  # matched Odoo product or None
            'catalog_score': best_score,
        }
        results.append(entry)
        if best_product:
            logger.info(f"Catalog match: '{raw_name}' → '{best_product['name']}' (score={best_score:.2f})")
        else:
            logger.warning(f"No catalog match for: '{raw_name}'")

    return results


# ============ Main Parse Function ============

def parse_with_llm(message_text: str, rpc=None, brand: str = None) -> dict:
    """
    Parse order message.
    - LLM: extracts customer info (name, phone, address, price, notes)
    - Catalog-first: extracts products from Odoo catalog directly
    - Fallback: if catalog unavailable, uses LLM products + Python merge
    """
    import requests as _requests
    from config import OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL

    _session = _requests.Session()
    _session.trust_env = False

    def _call_llm(messages, temperature=0, max_tokens=1500):
        r = _session.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://shahdbeauty.odoo.com",
                "X-Title": "Shahd Odoo Bot"
            },
            json={"model": LLM_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
            timeout=30
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    system_prompt = """You are an order parser for an Iraqi beauty products business.
Extract order details from WhatsApp messages in Arabic/Iraqi dialect.

Return ONLY valid JSON with these fields:
{
  "customer_name": "full name in Arabic",
  "phone": "phone number (Iraqi format 07xxxxxxxxx)",
  "province": "المحافظة",
  "city": "المنطقة/الحي/القضاء - the MOST SPECIFIC area name",
  "street": "العنوان التفصيلي (شارع, زقاق, قرب, خلف, etc.)",
  "nearest_landmark": "أقرب نقطة دالة",
  "products": [
    {"name": "product name exactly as written", "quantity": 1, "is_gift": false}
  ],
  "total_price": 0,
  "instagram": "instagram handle only, empty string if not mentioned",
  "notes": "additional notes only"
}

CRITICAL RULES FOR IRAQI ADDRESSES:
- Iraqi addresses follow this pattern: محافظة + منطقة/قضاء + حي + شارع + تفاصيل
- province is the GOVERNORATE (محافظة): بغداد, البصرة, النجف, كربلاء, نينوى, etc.
- city is the MOST SPECIFIC neighborhood/district/area (حي/منطقة/قضاء), NOT the province!

IMPORTANT PROVINCE MAPPINGS:
- نجف, النجف = المحافظة: النجف
- كوفة, كوفه, الكوفة = المنطقة في محافظة النجف (NOT a province!)
- بصرة, البصرة, البصره = المحافظة: البصرة
- كربلا, كربلاء = المحافظة: كربلاء
- موصل, الموصل = المحافظة: نينوى
- ناصرية, الناصرية = المحافظة: ذي قار
- عمارة, العمارة = المحافظة: ميسان
- كوت, الكوت = المحافظة: واسط
- حلة, الحلة = المحافظة: بابل
- ديوانية, الديوانية = المحافظة: القادسية
- سماوة, السماوة = المحافظة: المثنى
- تكريت = المحافظة: صلاح الدين
- رمادي, الرمادي = المحافظة: الأنبار

EXAMPLES:
- "نجف كوفه نهاية سايدين" → province=النجف, city=كوفه, street=نهاية سايدين
- "بغداد رصافة أولى حي اور شارع ابو عبير" → province=بغداد, city=حي اور, street=رصافة أولى شارع ابو عبير
- "بغداد مدينه الصدر قطاع 68" → province=بغداد, city=مدينة الصدر, street=قطاع 68
- "البصره جامعة الكرمه" → province=البصرة, city=جامعة الكرمه
- "كربلاء حي الغدير قرب الكفيل" → province=كربلاء, city=حي الغدير, street=قرب الكفيل
- "اربيل امباير رويال فيلا 230" → province=أربيل, city=امباير, street=رويال فيلا 230
- "بغداد باب شرجي" → province=بغداد, city=باب شرجي

IMPORTANT: The word RIGHT AFTER the province name is usually the city/area. NEVER set city to "غير محدد" - always extract it from the address text.

OTHER RULES:
- Prices can be short (e.g., 26, 50, 75) or full (e.g., 26000, 50000, 75000) - return the EXACT number as written
- NEVER divide or multiply the price - use the exact number written
- If price says "واصل" or "واصله" or "واصلة", set total_price to 0
- Products marked as هدية/هديه must have is_gift=true
- Keep product names as-is from the message (WITHOUT the quantity word/number)
- Phone numbers format: 07xxxxxxxxx

CRITICAL QUANTITY RULES:
- A quantity number/word ON THE SAME LINE as a product name = quantity for THAT product ONLY
- Each product line is independent
- Numbers can be Arabic digits or words (واحد, اثنين, ثلاثة, اربعة, خمسة, ستة, سبعة, ثمانية, تسعة, عشرة)
- Iraqi dialect: وحده=1, ثنتين=2, ثلاث=3, اربع=4, خمس=5
- "عدد" followed by a number means quantity. REMOVE "عدد" and the number from the product name.
- Examples:
  * "عطر ماي سول اثنين" → name="عطر ماي سول", quantity=2
  * "بكج الكافيار x2" → name="بكج الكافيار", quantity=2
  * "شامبو جوز الهند عدد2" → name="شامبو جوز الهند", quantity=2

CRITICAL PRODUCT NAME PRESERVATION:
- "مني" or "ميني" at the END of a product name is PART OF THE NAME (mini size). NEVER remove it.
- "صابونة الكركم مني" → name="صابونة الكركم مني"

GIFT PRODUCT NAME RULES:
- When a line starts with "هدية" or "هديه", set is_gift=true and REMOVE the word from the product name
- Example: "هدية مسك قريشي" → name="مسك قريشي", is_gift=true

PRICE SHORTHAND RULES:
- Prices are written as short numbers: 75 means 75,000 IQD
- ALWAYS return the price EXACTLY as written (do NOT multiply or convert)
- Examples: "سعر 75" → total_price=75, "الحساب 50" → total_price=50"""

    full_prompt = system_prompt + f"\n\nParse this order and return ONLY valid JSON (no markdown, no code blocks):\n\n{message_text}"

    raw = _call_llm(
        messages=[
            {"role": "system", "content": "You are an order parser. Return ONLY valid JSON, no markdown."},
            {"role": "user", "content": full_prompt}
        ],
        temperature=0,
        max_tokens=1500
    ).strip()

    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    parsed = json.loads(raw)

    # Validate and fix province/city/price
    parsed = _validate_and_fix(parsed, message_text)

    # ===== CATALOG-FIRST PRODUCT EXTRACTION =====
    # If rpc available, use catalog-first approach
    if rpc is not None:
        catalog_products = extract_products_from_catalog(message_text, brand or '', rpc)
        if catalog_products:
            # Convert catalog results to standard product format
            # catalog_product field is used later in discord_bot.py to skip find_product() call
            final_products = []
            for cp in catalog_products:
                prod_entry = {
                    'name': cp['name'],
                    'quantity': cp['quantity'],
                    'is_gift': cp['is_gift'],
                }
                if cp.get('catalog_product') and cp['catalog_score'] >= 0.5:
                    # Pre-resolved product — discord_bot.py can use directly
                    prod_entry['_odoo_product'] = cp['catalog_product']
                    prod_entry['_catalog_score'] = cp['catalog_score']
                final_products.append(prod_entry)
            parsed['products'] = final_products
            logger.info(f"Catalog-first: {len(final_products)} products extracted")
        else:
            # Fallback to LLM + Python merge
            extracted_products = _extract_product_lines_from_text(message_text)
            if extracted_products:
                llm_products = parsed.get('products', [])
                merged = _merge_products(llm_products, extracted_products)
                parsed['products'] = merged
    else:
        # No rpc — fallback to LLM + Python merge (original behavior)
        extracted_products = _extract_product_lines_from_text(message_text)
        if extracted_products:
            llm_products = parsed.get('products', [])
            merged = _merge_products(llm_products, extracted_products)
            parsed['products'] = merged

    # Fix quantities
    parsed = _fix_quantities(parsed, message_text)

    return parsed
