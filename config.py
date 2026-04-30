#!/usr/bin/env python3
"""
Centralized configuration — all secrets from environment variables.
NEVER hardcode tokens, passwords, or API keys.
"""

import os
import sys

# --- Required Environment Variables ---
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
SHAHD_CHANNEL_ID = int(os.environ.get("SHAHD_CHANNEL_ID", "0"))
MARLIN_CHANNEL_ID = int(os.environ.get("MARLIN_CHANNEL_ID", "0"))
ODOO_URL = os.environ.get("ODOO_URL", "")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USER = os.environ.get("ODOO_USER", "")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

# --- Validate on startup ---
_REQUIRED = {
    "DISCORD_BOT_TOKEN": DISCORD_BOT_TOKEN,
    "ODOO_URL": ODOO_URL,
    "ODOO_DB": ODOO_DB,
    "ODOO_USER": ODOO_USER,
    "ODOO_PASSWORD": ODOO_PASSWORD,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}

_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    print(f"❌ Missing required environment variables: {', '.join(_missing)}")
    print("Set them in your .env file or hosting platform.")
    sys.exit(1)

# --- Authorized Users ---
# Comma-separated Telegram user IDs: "123456,789012"
# If empty, bot is open to everyone (NOT recommended for production)
AUTHORIZED_USERS_RAW = os.environ.get("AUTHORIZED_USERS", "")
AUTHORIZED_USERS: set[int] = set()
if AUTHORIZED_USERS_RAW:
    AUTHORIZED_USERS = {int(uid.strip()) for uid in AUTHORIZED_USERS_RAW.split(",") if uid.strip()}

# Admin users can access /report and /admin commands
ADMIN_USERS_RAW = os.environ.get("ADMIN_USERS", "")
ADMIN_USERS: set[int] = set()
if ADMIN_USERS_RAW:
    ADMIN_USERS = {int(uid.strip()) for uid in ADMIN_USERS_RAW.split(",") if uid.strip()}

# --- Odoo Constants ---
IRAQ_COUNTRY_ID = 106
WHATSAPP_TAG_ID = 4
DISCOUNT_PRODUCT_ID = 176

# --- Performance ---
PRODUCT_CACHE_TTL = int(os.environ.get("PRODUCT_CACHE_TTL", "300"))  # 5 minutes
CITY_CACHE_TTL = int(os.environ.get("CITY_CACHE_TTL", "600"))  # 10 minutes
ODOO_RETRY_ATTEMPTS = 3
ODOO_RETRY_DELAY = 1  # seconds

# --- Duplicate Detection ---
DUPLICATE_WINDOW_MINUTES = int(os.environ.get("DUPLICATE_WINDOW_MINUTES", "30"))

# --- LLM ---
LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")

# --- Carrier Map ---
CARRIER_MAP = {
    "shahd": {"carrier_id": 8, "name": "Albarq Delivery Shahd", "product_id": 51},
    "marlin": {"carrier_id": 11, "name": "Albarq Delivery Marlin", "product_id": 51},
}

# --- Province Map ---
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
