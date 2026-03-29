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
    
    return parsed


def parse_with_llm(message_text: str) -> dict:
    """Use OpenAI-compatible LLM to parse the order message."""
    from openai import OpenAI
    client = OpenAI()

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
- Prices are in thousands of Iraqi Dinars (e.g., 50 means 50,000 IQD)
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
- If no quantity is specified on a line, default to 1
- DO NOT confuse the final price number with a product quantity
- DO NOT carry over quantities from one product line to another

GIFT PRODUCT NAME RULES:
- When a line starts with "هدية" or "هديه", set is_gift=true and REMOVE the word "هدية"/"هديه" from the product name
- Example: "هدية مسك قريشي" → name="مسك قريشي", is_gift=true
- Example: "هدية بكج الرموش" → name="بكج الرموش", is_gift=true
- Example: "هدية ليفة سلكونيه" → name="ليفة سلكونيه", is_gift=true"""

    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Parse this order:\n\n{message_text}"}
        ],
        temperature=0,
        response_format={"type": "json_object"}
    )

    parsed = json.loads(response.choices[0].message.content)
    
    # Validate and fix using our own logic
    parsed = _validate_and_fix(parsed, message_text)
    
    # Fix quantities using Arabic number words detection
    parsed = _fix_quantities(parsed, message_text)
    
    return parsed
