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

OTHER RULES:
- Prices are in thousands of Iraqi Dinars (e.g., 50 means 50,000 IQD)
- Products marked as هدية/هديه must have is_gift=true
- Keep product names as-is from the message
- A number after product name = quantity (e.g. "عسل العام 2" means quantity=2)
- Phone numbers format: 07xxxxxxxxx"""

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
    
    return parsed
