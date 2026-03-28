#!/usr/bin/env python3
"""
Parse a WhatsApp order message and extract structured order data using LLM.
"""

import json
import os

def parse_with_llm(message_text: str) -> dict:
    """Use OpenAI-compatible LLM to parse the order message."""
    from openai import OpenAI
    client = OpenAI()

    system_prompt = """You are an order parser for an Iraqi beauty products business (Shahd Beauty).
Extract order details from WhatsApp messages in Arabic/Iraqi dialect.

Return ONLY valid JSON with these fields:
{
  "customer_name": "full name in Arabic",
  "phone": "phone number (Iraqi format 07xxxxxxxxx)",
  "province": "المحافظة (one of: الأنبار, أربيل, البصرة, بابل, بغداد, دهوك, ديالى, ذي قار, كربلاء, كركوك, ميسان, المثنى, النجف, نينوى, القادسية, صلاح الدين, السليمانية, واسط)",
  "city": "المدينة/القضاء",
  "street": "الشارع أو العنوان التفصيلي",
  "nearest_landmark": "أقرب نقطة دالة (if mentioned)",
  "products": [
    {"name": "product name expanded to full name", "quantity": 1, "notes": "any special notes like هدية or ميني", "is_gift": false}
  ],
  "total_price": 0,
  "delivery_fee": 0,
  "instagram": "instagram URL or handle if mentioned",
  "notes": "any additional notes"
}

Rules:
- Prices are in thousands of Iraqi Dinars (e.g., 51 means 51,000 IQD)
- If delivery fee is not mentioned, set to 0
- If total_price includes delivery, try to separate them
- Products may include free items marked as هدية (gift) - set is_gift=true for these
- EXPAND abbreviated product names to their full names. Common abbreviations:
  - "بكج جوز الهند" = "بكج جوز الهند للعناية بالشعر"
  - "ص كركم" or "صابونة كركم" = "صابونة الكركم"
  - "ص كركم ميني" = "صابونة الكركم مني"
  - "غسول عروسة" = "غسول العروسة للوجه والجسم"
  - "كريم تبيض" = "كريم تبيض العروسة للوجه و الجسم"
  - "مقشر عروسة" = "مقشر العروسة للوجه والجسم"
  - "ماسك عروسة" = "ماسك العروسة للوجه والجسم"
  - "لوشن عروسة" = "لوشن العروسة"
  - "بكج عروسة" = "بكج العروسة"
  - "بكج كافيار" = "بكج الكافيار"
  - "بكج نيلة" = "بكج النيلة الثلاثي"
  - "بكج انوثة" or "بكج أنوثة" = "كورس الأنوثة للعناية بالجسم"
  - "بكج الكرسمس" or "بكج كرسمس" or "بكج الكريسمس" = "بكج الكرسمس الحصري"
  - "كورس بياض" or "كورس بياض وجه" = "كورس بياض الثلج للوجه"
  - "بكج تشيز" or "بكج التشيز كيك" = "بكج التشيز كيك للعناية الفاخرة بالجسم"
  - "بكج حساسة" or "بكج مناطق حساسة" = "بكج العناية بالمناطق الحساسة"
  - "كورس انوثة" = "كورس الأنوثة للعناية بالجسم"
  - "كورس معالجة" = "كورس معالجة البشرة"
  - "مبيض العروسة" or "مبيضة العروسة" or "مبيض العروسه" or "مبيضه العروسه" = "كريم  تبيض العروسة للوجه و الجسم"
  - "مرطب العروسة" or "مرطب العروسه" = "لوشن العروسة"
  - "عسل الانوثه" or "عسل الانوثة" or "عسل انوثة" = "عسل مسمن مناطق انثوية"
  - "عسل العام" or "عسل عام" = "عسل مسمن عام"
  - "عسل وجه" or "عسل الوجه" = "عسل مسمن وجه"
  - "عسل رجالي" = "عسل رجالي"
- Phone numbers should be in format 07xxxxxxxxx"""

    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Parse this order:\n\n{message_text}"}
        ],
        temperature=0,
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)
