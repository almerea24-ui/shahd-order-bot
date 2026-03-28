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
  "province": "المحافظة (MUST be one of: الأنبار, أربيل, البصرة, بابل, بغداد, دهوك, ديالى, ذي قار, كربلاء, كركوك, ميسان, المثنى, النجف, نينوى, القادسية, صلاح الدين, السليمانية, واسط)",
  "city": "المدينة/المنطقة/القضاء (e.g. مدينة الصدر, البلديات, الجزيرة, العلام, etc.)",
  "street": "الشارع أو العنوان التفصيلي (e.g. قطاع 68, شارع صقر, خلف جامع عمر)",
  "nearest_landmark": "أقرب نقطة دالة (if mentioned)",
  "products": [
    {"name": "product name - use the EXACT abbreviated name from the message", "quantity": 1, "is_gift": false}
  ],
  "total_price": 0,
  "instagram": "instagram handle only (without URL), empty string if not mentioned",
  "notes": "ANY additional notes or instructions from the customer (e.g. توصيل بوقت معين, ملاحظات خاصة, etc.). Do NOT include address, phone, name, or product info here."
}

CRITICAL RULES:
- Prices are in thousands of Iraqi Dinars (e.g., 51 means 51,000 IQD, 30 means 30,000 IQD)
- The total_price is ALWAYS the total including delivery
- Products marked as هدية (gift) must have is_gift=true
- For product names, keep them as-is from the message (e.g. "عسل العام", "مبيض العروسه", "بكج الكرسمس") - do NOT expand them
- A number right after a product name usually means quantity (e.g. "مرطب العروسه2" means quantity=2)
- Province MUST be extracted correctly from the address
- City/area MUST be extracted (e.g. from "بغداد مدينه الصدر قطاع 68", province=بغداد, city=مدينة الصدر, street=قطاع 68)
- Phone numbers should be in format 07xxxxxxxxx
- Instagram links should be extracted as handle only (e.g. "7.f_sa" from the URL)
- notes field should capture any delivery instructions or special requests"""

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
