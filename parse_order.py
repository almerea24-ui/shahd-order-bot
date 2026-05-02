#!/usr/bin/env python3
"""
Parse a WhatsApp order message and extract structured order data using LLM.
"""

import json
import os
import re

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
    # Try each province name (longer names first to avoid partial matches)
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
    'مبيض', 'مبيضة', 'مرطب', 'كريمة', 'بودر', 'مسحوق', 'بخاخ', 'سكراب', 'صبغة'
]

# Lines that are NOT products (address/phone/price/notes indicators)
NON_PRODUCT_PATTERNS = [
    r'\d{10,}',           # phone numbers
    r'07\d{8,}',          # Iraqi phone
    r'سعر\s*\d',            # price line
    r'الحساب\s*\d',         # price line
    r'السعر\s*\d',           # price line
    r'العنوان',           # address line
    r'بغداد|\u0628صرة|نجف|كربلاء|موصل',  # cities
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
    
    # Check non-product patterns first
    for pattern in NON_PRODUCT_PATTERNS:
        if re.search(pattern, line_clean):
            return False
    
    # Remove هدية/هديه prefix for keyword check
    line_check = re.sub(r'^(\u0647\u062f\u064a\u0629|\u0647\u062f\u064a\u0647)\s*', '', line_clean).strip()
    
    # Check if line contains any product keyword
    for kw in PRODUCT_LINE_KEYWORDS:
        if kw in line_check:
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
        
        is_gift = bool(re.match(r'^(\u0647\u062f\u064a\u0629|\u0647\u062f\u064a\u0647)\s+', line))
        # Remove هدية/هديه prefix
        name = re.sub(r'^(\u0647\u062f\u064a\u0629|\u0647\u062f\u064a\u0647)\s+', '', line).strip()
        
        # Extract quantity from line
        quantity = 1
        # Check for عدد2 / عدد 2 pattern (Iraqi dialect for quantity)
        adad_match = re.search(r'\s*عدد\s*(\d{1,2})$', name)
        if adad_match:
            quantity = int(adad_match.group(1))
            name = name[:adad_match.start()].strip()
        # Check for x2/X3 pattern
        elif re.search(r'[xX×]\s*(\d{1,2})$', name):
            x_match = re.search(r'[xX×]\s*(\d{1,2})$', name)
            quantity = int(x_match.group(1))
            name = name[:x_match.start()].strip()
        else:
            # Check for trailing digit
            digit_match = re.search(r'\s+(\d{1,2})$', name)
            if digit_match:
                num = int(digit_match.group(1))
                if 1 <= num <= 20:
                    quantity = num
                    name = name[:digit_match.start()].strip()
            else:
                # Check for Arabic number words at end
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
    If Python found more products, add the missing ones.
    The Python extraction is the ground truth for WHICH products exist.
    """
    from product_aliases import PRODUCT_ALIASES
    
    if not extracted_products:
        return llm_products
    
    # If LLM returned nothing or fewer products, use extracted as base
    if not llm_products:
        return extracted_products
    
    def _resolve_alias(name: str) -> str:
        """Resolve a product name through aliases."""
        name_lower = name.strip().lower()
        for alias, resolved in PRODUCT_ALIASES.items():
            if alias.lower() == name_lower:
                return resolved.lower()
        return name_lower
    
    # Check if each extracted product is represented in LLM products
    def _name_similar(a: str, b: str) -> bool:
        """Check if two product names are similar enough."""
        # First try alias resolution
        a_resolved = _resolve_alias(a)
        b_resolved = _resolve_alias(b)
        if a_resolved == b_resolved:
            return True
        
        a_clean = re.sub(r'[\s]', '', a_resolved)
        b_clean = re.sub(r'[\s]', '', b_resolved)
        # Check if one contains the other (at least 3 chars)
        if len(a_clean) >= 3 and len(b_clean) >= 3:
            if a_clean in b_clean or b_clean in a_clean:
                return True
        # Check word overlap
        a_words = set(w for w in a.split() if len(w) > 1)
        b_words = set(w for w in b.split() if len(w) > 1)
        if a_words and b_words:
            overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
            if overlap >= 0.5:
                return True
        return False
    
    # Build final merged list - use extracted order as the canonical order
    merged = []
    used_llm_indices = set()
    
    for ext_prod in extracted_products:
        # Find matching LLM product
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
            # Use LLM product but ensure gift flag and quantity are correct
            used_llm_indices.add(best_idx)
            # Trust Python for is_gift (more reliable)
            best_match['is_gift'] = ext_prod['is_gift'] or best_match.get('is_gift', False)
            # Trust Python for quantity if LLM says 1 but Python found more
            if best_match.get('quantity', 1) == 1 and ext_prod['quantity'] > 1:
                best_match['quantity'] = ext_prod['quantity']
            merged.append(best_match)
        else:
            # Product not found in LLM output - add from Python extraction
            merged.append(ext_prod)
    
    # Add any LLM products not matched (edge case)
    for i, llm_prod in enumerate(llm_products):
        if i not in used_llm_indices:
            # Check if it's not already in merged
            already = any(_name_similar(llm_prod.get('name', ''), m.get('name', '')) for m in merged)
            if not already:
                merged.append(llm_prod)
    
    return merged


def _fix_quantities(parsed: dict, original_text: str) -> dict:
    """Fix product quantities by checking original text for Arabic number words.
    Also ensures each product's quantity comes from its OWN line only."""
    products = parsed.get('products', [])
    lines = [l.strip() for l in original_text.strip().split('\n') if l.strip()]
    
    # Build a mapping: for each product, find its BEST matching line
    used_lines = set()
    product_line_map = []
    
    for product in products:
        pname = product.get('name', '').strip()
        # Remove هدية/هديه prefix for matching
        pname_clean = re.sub(r'^(هدية|هديه)\s*', '', pname).strip()
        
        best_line = None
        best_score = 0
        
        for idx, line in enumerate(lines):
            if idx in used_lines:
                continue
            # Count how many significant words from product name appear in this line
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
    
    # Now fix quantities based on each product's own line
    for product, line in product_line_map:
        if line is None:
            continue
        
        pname = product.get('name', '').strip()
        pname_clean = re.sub(r'^(هدية|هديه)\s*', '', pname).strip()
        
        # Get remaining text after removing product name words
        remaining = line
        for w in pname_clean.split():
            remaining = remaining.replace(w, '', 1)
        # Also remove هدية/هديه
        remaining = re.sub(r'(هدية|هديه)', '', remaining).strip()
        
        # Check for Arabic number words in remaining text
        found_qty = False
        for word, num in sorted(ARABIC_NUMBER_WORDS.items(), key=lambda x: len(x[0]), reverse=True):
            if word in remaining:
                product['quantity'] = num
                found_qty = True
                break
        
        if not found_qty:
            # Check for x2/X3 pattern (e.g., "بكج الكافيار x2")
            x_match = re.search(r'[xX×]\s*(\d{1,2})', remaining)
            if x_match:
                num = int(x_match.group(1))
                if 1 <= num <= 20:
                    product['quantity'] = num
                    found_qty = True
        
        if not found_qty:
            # Check for digit in remaining text
            digit_match = re.search(r'\b(\d{1,2})\b', remaining)
            if digit_match:
                num = int(digit_match.group(1))
                if 1 <= num <= 20:
                    product['quantity'] = num
                    found_qty = True
        
        if not found_qty:
            # No quantity found on this line - default to 1
            product['quantity'] = 1
    
    # Clean product names: remove هدية/هديه prefix if present
    for product in products:
        name = product.get('name', '')
        name = re.sub(r'^(هدية|هديه)\s*', '', name).strip()
        # Remove any Arabic number words from the name
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
    
    # 1. Fix province - use our own detection as backup/override
    llm_province = parsed.get("province", "")
    detected_province = _detect_province_from_text(original_text)
    
    # Normalize LLM province
    llm_province_normalized = PROVINCE_NORMALIZE.get(
        llm_province.replace("ال", "", 1) if llm_province.startswith("ال") else llm_province,
        PROVINCE_NORMALIZE.get(llm_province, "")
    )
    
    if detected_province and detected_province != llm_province_normalized:
        # Our detection disagrees with LLM - trust ours
        parsed["province"] = detected_province
    elif detected_province:
        parsed["province"] = detected_province
    elif llm_province_normalized:
        parsed["province"] = llm_province_normalized
    
    # 2. Fix city - if city is same as province name, it's wrong
    city = parsed.get("city", "")
    province = parsed.get("province", "")
    
    # Check if city is actually a province name
    city_clean = city.replace("ال", "", 1) if city.startswith("ال") else city
    if city_clean in PROVINCE_NORMALIZE or city in PROVINCE_NORMALIZE:
        # City is a province name - try to find real city
        detected_city = _detect_city_from_text(original_text, province)
        if detected_city:
            parsed["city"] = detected_city
        else:
            # Try to extract from street or use LLM's street as city
            street = parsed.get("street", "")
            if street:
                parsed["city"] = street
                parsed["street"] = ""
    
    # 3. Check if city should be more specific (e.g., "رصافة أولى" -> "حي اور")
    # Look for "حي" pattern in original text
    hiy_match = re.search(r'حي\s+(\S+)', original_text)
    if hiy_match:
        hiy_name = "حي " + hiy_match.group(1)
        current_city = parsed.get("city", "")
        # If current city is a general area and we found a specific حي, use it
        general_areas = ["رصافة", "رصافه", "رصافة أولى", "رصافة ثانية", "كرخ", "كرخ أولى", "كرخ ثانية"]
        if any(area in current_city for area in general_areas) or not current_city:
            # Move current city info to street and use حي as city
            old_city = current_city
            parsed["city"] = hiy_name
            if old_city and old_city not in (parsed.get("street", "") or ""):
                street = parsed.get("street", "")
                parsed["street"] = f"{old_city} {street}".strip() if street else old_city
    
    # 4. Ensure street has full address details
    # Combine nearest_landmark into street if street is empty
    if not parsed.get("street") and parsed.get("nearest_landmark"):
        parsed["street"] = parsed["nearest_landmark"]
    
    # 5. Check if LLM put a product name in customer_name by mistake
    # Product indicator words - if customer_name contains these, it's probably a product
    PRODUCT_KEYWORDS = [
        'بكج', 'عسل', 'كريم', 'غسول', 'لوشن', 'مقشر', 'مربى', 'عطر', 'كورس',
        'زيت', 'شامبو', 'سيروم', 'ماسك', 'تنت', 'صابونة', 'حمرة', 'رموش', 'اضافر', 'ليفة',
        'سبلاش', 'قناع', 'مورد', 'سيروم', 'واقي', 'مخمرية', 'شاي'
    ]
    customer_name = parsed.get('customer_name', '')
    customer_name_lower = customer_name.strip().lower()
    is_product_name = any(kw in customer_name_lower for kw in PRODUCT_KEYWORDS)
    
    if is_product_name:
        # LLM put a product in customer_name - move it to products list as first item
        products = parsed.get('products', [])
        # Check if this product is already in the list
        already_in_list = any(
            customer_name.strip() in p.get('name', '') or p.get('name', '') in customer_name.strip()
            for p in products
        )
        if not already_in_list:
            products.insert(0, {'name': customer_name.strip(), 'quantity': 1, 'is_gift': False})
            parsed['products'] = products
        # Try to find the real customer name from the original text
        lines = [l.strip() for l in original_text.strip().split('\n') if l.strip()]
        for line in lines:
            line_has_product = any(kw in line for kw in PRODUCT_KEYWORDS)
            line_has_phone = bool(re.search(r'07\d{9}', line))
            line_has_price = bool(re.search(r'سعر|\d{4,}', line))
            if not line_has_product and not line_has_phone and not line_has_price:
                # Could be customer name - check if it looks like a name (2+ Arabic words, no numbers)
                words = line.split()
                if 1 <= len(words) <= 4 and not re.search(r'\d', line):
                    parsed['customer_name'] = line
                    break
    
    return parsed


def parse_with_llm(message_text: str) -> dict:
    """Use LLM via OpenRouter to parse the order message."""
    import requests as _requests
    from config import OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL
    
    # Use a session with trust_env=False to bypass any proxy interference
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

IMPORTANT: The word RIGHT AFTER the province name is usually the city/area. NEVER set city to "غير محدد" - always extract it from the address text. The first area/place name after the province IS the city.

OTHER RULES:
- Prices are written as FULL numbers in Iraqi Dinars (e.g., 50000 means 50,000 IQD, 35000 means 35,000 IQD, 120000 means 120,000 IQD)
- NEVER divide or multiply the price - use the exact number written
- If price says "واصل" or "واصله" or "واصلة", set total_price to 0 (means delivery included, use product prices as-is)
- Products marked as هدية/هديه must have is_gift=true
- Keep product names as-is from the message (WITHOUT the quantity word/number)
- Phone numbers format: 07xxxxxxxxx

CRITICAL QUANTITY RULES:
- A quantity number/word ON THE SAME LINE as a product name = quantity for THAT product ONLY
- Each product line is independent - a quantity on one line does NOT apply to other lines
- Numbers can be in Arabic digits (1, 2, 3...) or Arabic words (واحد, اثنين, ثلاثة, اربعة, خمسة, ستة, سبعة, ثمانية, تسعة, عشرة)
- Iraqi dialect numbers: وحده=1, ثنتين=2, ثلاث=3, اربع=4, خمس=5
- The quantity word/number should NOT be included in the product name
- Examples:
  * "عطر ماي سول اثنين" → name="عطر ماي سول", quantity=2
  * "عطر ماي سول 2" → name="عطر ماي سول", quantity=2
  * "عسل العام ثلاثة" → name="عسل العام", quantity=3
  * "مربى كرميل 3" → name="مربى كرميل", quantity=3
  * "عسل عام ثنتين" → name="عسل عام", quantity=2
  * "بكج الكافيار x2" → name="بكج الكافيار", quantity=2
  * "عطر فانيلا X3" → name="عطر فانيلا", quantity=3
  * "شامبو جوز الهند عدد2" → name="شامبو جوز الهند", quantity=2
  * "زيت الكافيار عدد 3" → name="زيت الكافيار", quantity=3
- The word "عدد" followed by a number means quantity (Iraqi dialect). REMOVE "عدد" and the number from the product name.
- If no quantity is specified on a line, default to 1
- DO NOT confuse the final price number with a product quantity
- DO NOT carry over quantities from one product line to another

CRITICAL PRODUCT NAME PRESERVATION:
- The word "مني" or "ميني" at the END of a product name is PART OF THE PRODUCT NAME (it means "mini size"). NEVER remove it.
- "صابونة الكركم مني" → name="صابونة الكركم مني" (NOT "صابونة الكركم")
- "مقشر نيلة مني" → name="مقشر نيلة مني" (NOT "مقشر نيلة")
- "صابونه كركم مني" → name="صابونه كركم مني" (keep as-is)
- Keep ALL words in the product name exactly as written

GIFT PRODUCT NAME RULES:
- When a line starts with "هدية" or "هديه", set is_gift=true and REMOVE the word "هدية"/"هديه" from the product name
- Example: "هدية مسك قريشي" → name="مسك قريشي", is_gift=true
- Example: "هدية بكج الرموش" → name="بكج الرموش", is_gift=true
- Example: "هدية ليفة سلكونيه" → name="ليفة سلكونيه", is_gift=true

CRITICAL: NEVER SKIP ANY PRODUCT LINE!
- ALL lines that look like product names MUST be included in the products list
- The first line of the message is often the FIRST PRODUCT, not the customer name
- Customer name is usually a PERSON'S NAME (e.g., سراب صبري, أميرة غنام, ام محمد)
- Product lines are things like: بكج العروسة, عطر ماي سول, عسل الانوثه, مربى كرميل, etc.
- If the first line is a product name (not a person's name), include it as the FIRST product
- Example: if message starts with "بكج العروسة" then "بكج العروسة" is a product, NOT the customer name
- Example order:
  أميرة غنام        ← customer_name
  بكج العروسه      ← product 1 (موجود في products list!)
  عسل الانوثه 2    ← product 2, quantity=2
  هدية اضافر 2     ← product 3, is_gift=true, quantity=2

DO NOT put a product name in customer_name field!
- If a line contains words like: بكج, عسل, كريم, غسول, لوشن, مقشر, مربى, عطر, كورس, زيت, شامبو, سيروم, ماسك, تنت, صابونة, حمرة, رموش, اضافر, ليفة = it is a PRODUCT, not a customer name!
- Customer names are human names: أميرة, سراب, محمد, احمد, زينب, فاطمة, ام فلان, ابو فلان, etc.
- ALWAYS count the products: if the order has N product lines, the products array MUST have N items

PRICE SHORTHAND RULES:
- Prices can be written as short numbers: 75 means 75,000 IQD, 50 means 50,000 IQD, 45 means 45,000 IQD
- If the price is a small number (less than 1000), multiply by 1000 to get the real price
- Examples: "سعر 75" → total_price=75000, "الحساب 50" → total_price=50000, "128" → total_price=128000
- If price is already large (e.g., 75000, 50000), use as-is
- The word "سعر" or "الحساب" or "السعر" before a number means it's the price"""

    full_prompt = system_prompt + f"\n\nParse this order and return ONLY valid JSON (no markdown, no code blocks):\n\n{message_text}"

    raw = _call_llm(
        messages=[
            {"role": "system", "content": "You are an order parser. Return ONLY valid JSON, no markdown."},
            {"role": "user", "content": full_prompt}
        ],
        temperature=0,
        max_tokens=1500
    ).strip()
    # Remove markdown code blocks if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    parsed = json.loads(raw)
    
    # Validate and fix using our own logic
    parsed = _validate_and_fix(parsed, message_text)
    
    # CRITICAL: Extract products directly from text and merge with LLM output
    # This ensures no product line is ever skipped by the LLM
    extracted_products = _extract_product_lines_from_text(message_text)
    if extracted_products:
        llm_products = parsed.get('products', [])
        merged = _merge_products(llm_products, extracted_products)
        parsed['products'] = merged
    
    # Fix quantities using Arabic number words detection
    parsed = _fix_quantities(parsed, message_text)
    
    return parsed
