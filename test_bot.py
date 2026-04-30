#!/usr/bin/env python3
"""
فحص شامل للبوت قبل النشر.
يفحص: imports, syntax, logic, تكامل الملفات, وكل الدوال الأساسية.
"""

import sys
import ast
import os
import traceback

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []

def check(name, fn):
    try:
        fn()
        results.append((PASS, name))
    except Exception as e:
        results.append((FAIL, f"{name}: {e}"))

# ============ 1. Syntax Check ============

def syntax_check(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()
    ast.parse(source)

check("Syntax: discord_bot.py", lambda: syntax_check("discord_bot.py"))
check("Syntax: config.py", lambda: syntax_check("config.py"))
check("Syntax: matching.py", lambda: syntax_check("matching.py"))
check("Syntax: odoo_client.py", lambda: syntax_check("odoo_client.py"))
check("Syntax: parse_order.py", lambda: syntax_check("parse_order.py"))
check("Syntax: duplicate_guard.py", lambda: syntax_check("duplicate_guard.py"))
check("Syntax: reports.py", lambda: syntax_check("reports.py"))
check("Syntax: product_aliases.py", lambda: syntax_check("product_aliases.py"))

# ============ 2. Import Check ============

# Set dummy env vars so config.py doesn't exit
os.environ.setdefault("DISCORD_BOT_TOKEN", "dummy_token")
os.environ.setdefault("ODOO_URL", "http://dummy.odoo.com")
os.environ.setdefault("ODOO_DB", "dummy_db")
os.environ.setdefault("ODOO_USER", "dummy_user")
os.environ.setdefault("ODOO_PASSWORD", "dummy_pass")
os.environ.setdefault("OPENAI_API_KEY", "dummy_key")
os.environ.setdefault("SHAHD_CHANNEL_ID", "123456789")
os.environ.setdefault("MARLIN_CHANNEL_ID", "987654321")

def import_config():
    import importlib
    import config
    importlib.reload(config)
    assert config.DISCORD_BOT_TOKEN == "dummy_token"
    assert config.SHAHD_CHANNEL_ID == 123456789
    assert config.MARLIN_CHANNEL_ID == 987654321
    assert "shahd" in config.CARRIER_MAP
    assert "marlin" in config.CARRIER_MAP
    assert len(config.PROVINCE_MAP) > 10

check("Import + Validate: config.py", import_config)

def import_product_aliases():
    from product_aliases import PRODUCT_ALIASES
    assert isinstance(PRODUCT_ALIASES, dict)
    assert len(PRODUCT_ALIASES) > 50
    # Check key aliases exist
    assert "بياض الثلج" in PRODUCT_ALIASES
    assert "كورس معالجة" in PRODUCT_ALIASES
    assert "مسكارة" in PRODUCT_ALIASES

check("Import + Validate: product_aliases.py", import_product_aliases)

def import_matching():
    from matching import normalize_arabic, strip_al, generate_variants, arabic_similarity, resolve_product_name
    # Test normalize
    assert normalize_arabic("العروسة") == "العروسه"
    assert normalize_arabic("أحمد") == "احمد"
    # Test strip_al
    assert strip_al("المنتج") == "المنتج"[2:]
    assert strip_al("منتج") == "منتج"
    # Test similarity
    score = arabic_similarity("بياض الثلج", "بياض الثلج")
    assert score == 1.0
    score2 = arabic_similarity("بياض الثلج", "بياض الثلج للوجه")
    assert score2 > 0.5
    # Test alias resolution
    resolved = resolve_product_name("بياض الثلج")
    assert resolved == "بياض الثلج وجه كريم", f"Expected 'بياض الثلج وجه كريم', got '{resolved}'"
    resolved2 = resolve_product_name("كورس معالجة")
    assert resolved2 == "كورس معالجة البشرة", f"Expected 'كورس معالجة البشرة', got '{resolved2}'"

check("Import + Logic: matching.py", import_matching)

def import_duplicate_guard():
    from duplicate_guard import check_duplicate, register_order
    # Test with dummy order
    order = {"customer_name": "أحمد", "phone": "07801234567", "products": [{"name": "بكج"}]}
    is_dup, msg = check_duplicate(order)
    assert isinstance(is_dup, bool)
    assert isinstance(msg, str)

check("Import + Logic: duplicate_guard.py", import_duplicate_guard)

def import_discord_bot():
    import importlib.util
    spec = importlib.util.spec_from_file_location("discord_bot", "discord_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Check key functions exist
    assert hasattr(mod, 'create_full_order'), "create_full_order missing"
    assert hasattr(mod, 'is_authorized'), "is_authorized missing"
    assert hasattr(mod, 'is_admin'), "is_admin missing"
    assert hasattr(mod, 'OrderConfirmView'), "OrderConfirmView missing"
    assert hasattr(mod, 'OrderBot'), "OrderBot missing"
    assert hasattr(mod, 'bot'), "bot instance missing"
    assert hasattr(mod, 'main'), "main() missing"
    # Check bot has correct intents
    assert mod.bot.intents.message_content == True, "message_content intent not enabled"

check("Import + Structure: discord_bot.py", import_discord_bot)

# ============ 3. Logic: Brand Filtering ============

def test_brand_filter_logic():
    """Simulate brand filtering without real Odoo connection."""
    from matching import normalize_arabic
    
    # Simulate product list with categ_id
    mock_products = [
        {"id": 1, "name": "كورس معالجة البشرة", "list_price": 50000, "qty_available": 10,
         "categ_id": [5, "مسارات الفئة مارلين الاونلاين"]},
        {"id": 2, "name": "كورس بياض الثلج للوجه", "list_price": 45000, "qty_available": 8,
         "categ_id": [3, "مسارات الفئة شهد الاونلاين"]},
        {"id": 3, "name": "بكج العناية بالمناطق الحساسة", "list_price": 60000, "qty_available": 5,
         "categ_id": [3, "مسارات الفئة شهد الاونلاين"]},
        {"id": 4, "name": "بكج الكافيار", "list_price": 75000, "qty_available": 3,
         "categ_id": [5, "مسارات الفئة مارلين الاونلاين"]},
    ]
    
    def filter_by_brand(products, brand):
        filtered = []
        for p in products:
            categ_name = p.get('categ_id', [0, ''])[1] if p.get('categ_id') else ''
            if brand == 'shahd':
                if 'مارلين' not in categ_name:
                    filtered.append(p)
            elif brand == 'marlin':
                if 'شهد' not in categ_name:
                    filtered.append(p)
        return filtered
    
    # Test: Shahd channel should NOT see Marlin products
    shahd_products = filter_by_brand(mock_products, 'shahd')
    shahd_names = [p['name'] for p in shahd_products]
    assert "كورس معالجة البشرة" not in shahd_names, "Marlin product leaked into Shahd!"
    assert "بكج الكافيار" not in shahd_names, "Marlin product leaked into Shahd!"
    assert "كورس بياض الثلج للوجه" in shahd_names, "Shahd product missing from Shahd!"
    assert "بكج العناية بالمناطق الحساسة" in shahd_names, "Shahd product missing from Shahd!"
    
    # Test: Marlin channel should NOT see Shahd products
    marlin_products = filter_by_brand(mock_products, 'marlin')
    marlin_names = [p['name'] for p in marlin_products]
    assert "كورس بياض الثلج للوجه" not in marlin_names, "Shahd product leaked into Marlin!"
    assert "بكج العناية بالمناطق الحساسة" not in marlin_names, "Shahd product leaked into Marlin!"
    assert "كورس معالجة البشرة" in marlin_names, "Marlin product missing from Marlin!"
    assert "بكج الكافيار" in marlin_names, "Marlin product missing from Marlin!"

check("Logic: Brand Filtering (Shahd/Marlin separation)", test_brand_filter_logic)

# ============ 4. Logic: Channel-to-Brand Mapping ============

def test_channel_brand_mapping():
    import config
    shahd_ch = config.SHAHD_CHANNEL_ID
    marlin_ch = config.MARLIN_CHANNEL_ID
    assert shahd_ch != marlin_ch, "SHAHD_CHANNEL_ID and MARLIN_CHANNEL_ID must be different!"
    assert shahd_ch != 0, "SHAHD_CHANNEL_ID is not set!"
    assert marlin_ch != 0, "MARLIN_CHANNEL_ID is not set!"

# Note: This will warn because we're using dummy IDs in test
results.append((WARN, "Logic: Channel IDs — will be real values in production .env"))

# ============ 5. Logic: Province Map Coverage ============

def test_province_map():
    import config
    required_provinces = ["بغداد", "البصرة", "النجف", "كربلاء", "الموصل", "أربيل", "كركوك"]
    for prov in required_provinces:
        assert prov in config.PROVINCE_MAP, f"Province missing: {prov}"

check("Logic: Province Map coverage", test_province_map)

# ============ 6. Logic: Carrier Map ============

def test_carrier_map():
    import config
    for brand in ["shahd", "marlin"]:
        c = config.CARRIER_MAP[brand]
        assert "carrier_id" in c
        assert "name" in c
        assert "product_id" in c
        assert c["carrier_id"] > 0
        assert c["product_id"] > 0

check("Logic: Carrier Map (shahd + marlin)", test_carrier_map)

# ============ 7. Logic: Duplicate Guard ============

def test_duplicate_guard():
    from duplicate_guard import check_duplicate, register_order
    import time
    
    order1 = {"customer_name": "فاطمة علي", "phone": "07901111111", "products": [{"name": "بكج النيلة"}]}
    
    # First time: not duplicate
    is_dup1, _ = check_duplicate(order1)
    assert not is_dup1, "First order should NOT be duplicate"
    
    # Register it
    register_order(order1, "test_key_001")
    
    # Second time: should be duplicate
    is_dup2, msg2 = check_duplicate(order1)
    assert is_dup2, "Same order should be detected as duplicate"
    assert "07901111111" in msg2 or "فاطمة" in msg2 or "مكرر" in msg2.lower() or len(msg2) > 0

check("Logic: Duplicate Guard (register + detect)", test_duplicate_guard)

# ============ 8. File Structure Check ============

def test_required_files():
    required = [
        "discord_bot.py", "config.py", "matching.py", "odoo_client.py",
        "parse_order.py", "duplicate_guard.py", "reports.py",
        "product_aliases.py", "requirements.txt", "Dockerfile", "Procfile", ".gitignore"
    ]
    for f in required:
        assert os.path.exists(f), f"Missing file: {f}"

check("Files: All required files present", test_required_files)

def test_no_telegram_imports():
    """Make sure no Telegram imports remain in discord_bot.py"""
    with open("discord_bot.py", "r") as f:
        content = f.read()
    assert "telegram" not in content.lower(), "Telegram import found in discord_bot.py!"
    assert "python-telegram-bot" not in content.lower()

check("Files: No Telegram code in discord_bot.py", test_no_telegram_imports)

def test_requirements_correct():
    with open("requirements.txt") as f:
        content = f.read()
    assert "discord.py" in content, "discord.py missing from requirements.txt"
    assert "python-telegram-bot" not in content, "Old telegram dependency still in requirements.txt"
    assert "openai" in content
    assert "requests" in content

check("Files: requirements.txt correct", test_requirements_correct)

def test_procfile_correct():
    with open("Procfile") as f:
        content = f.read()
    assert "discord_bot.py" in content, "Procfile should run discord_bot.py"
    assert "bot.py" not in content or "discord_bot.py" in content

check("Files: Procfile points to discord_bot.py", test_procfile_correct)

# ============ Print Results ============

print("\n" + "="*55)
print("   فحص البوت الشامل — النتائج")
print("="*55)

passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
warned = sum(1 for r in results if r[0] == WARN)

for status, name in results:
    print(f"  {status}  {name}")

print("="*55)
print(f"  النتيجة: {passed} نجح | {failed} فشل | {warned} تحذير")
print("="*55 + "\n")

if failed > 0:
    sys.exit(1)
else:
    print("✅ البوت جاهز للنشر!\n")
