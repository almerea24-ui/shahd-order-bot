#!/usr/bin/env python3
"""
Product and city matching — uses cached Odoo data + improved Arabic fuzzy matching.
"""

import logging
from difflib import SequenceMatcher

from config import PROVINCE_MAP
from odoo_client import OdooRPC
from product_aliases import PRODUCT_ALIASES

logger = logging.getLogger(__name__)


# ============ Arabic Normalization ============

def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for comparison."""
    if not text:
        return ""
    t = text.strip()
    # Normalize ة/ه
    t = t.replace('ة', 'ه')
    # Normalize ى/ي
    t = t.replace('ى', 'ي')
    # Normalize أ/إ/آ -> ا
    t = t.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
    # Remove tatweel
    t = t.replace('ـ', '')
    # Remove diacritics (harakat)
    for c in 'ًٌٍَُِّْ':
        t = t.replace(c, '')
    return t


def strip_al(word: str) -> str:
    """Remove Arabic definite article ال."""
    if word.startswith('ال'):
        return word[2:]
    return word


def generate_variants(name: str) -> list[str]:
    """Generate spelling variants for an Arabic name."""
    if not name:
        return []
    variants = set()
    variants.add(name)
    variants.add(normalize_arabic(name))

    # With/without ال
    if name.startswith('ال'):
        variants.add(name[2:])
    else:
        variants.add('ال' + name)

    # Per-word ال toggle
    words = name.split()
    toggled = []
    for w in words:
        if w.startswith('ال'):
            toggled.append(w[2:])
        else:
            toggled.append('ال' + w)
    variants.add(' '.join(toggled))

    # ة <-> ه swap
    for v in list(variants):
        variants.add(v.replace('ة', 'ه'))
        variants.add(v.replace('ه', 'ة'))

    return list(variants)


def arabic_similarity(a: str, b: str) -> float:
    """Compute similarity between two Arabic strings (0.0 to 1.0)."""
    na = normalize_arabic(a).lower()
    nb = normalize_arabic(b).lower()

    # Exact match after normalization
    if na == nb:
        return 1.0

    # Strip ال and compare
    words_a = set(strip_al(w) for w in na.split())
    words_b = set(strip_al(w) for w in nb.split())

    if words_a == words_b:
        return 0.98

    # Jaccard similarity on words
    if words_a and words_b:
        intersection = words_a & words_b
        union = words_a | words_b
        jaccard = len(intersection) / len(union)

        # Bonus for subset match
        if words_a.issubset(words_b) or words_b.issubset(words_a):
            jaccard = max(jaccard, 0.85)

        # Also use SequenceMatcher for character-level similarity
        char_sim = SequenceMatcher(None, na, nb).ratio()

        return max(jaccard, char_sim)

    return SequenceMatcher(None, na, nb).ratio()


# ============ Product Matching ============

def resolve_product_name(name: str) -> str:
    """Resolve product name using aliases, return exact Odoo name."""
    name_clean = name.strip()

    # 1. Exact match
    if name_clean in PRODUCT_ALIASES:
        return PRODUCT_ALIASES[name_clean]

    # 2. Case-insensitive match
    name_lower = name_clean.lower()
    for alias, real_name in PRODUCT_ALIASES.items():
        if alias.lower() == name_lower:
            return real_name

    # 3. Normalized match (ال, ة/ه, etc.)
    name_norm = normalize_arabic(name_clean)
    for alias, real_name in PRODUCT_ALIASES.items():
        if normalize_arabic(alias) == name_norm:
            return real_name

    # 4. Fuzzy match on aliases
    best_match = None
    best_score = 0.0
    for alias, real_name in PRODUCT_ALIASES.items():
        score = arabic_similarity(name_clean, alias)
        if score > best_score:
            best_score = score
            best_match = real_name
    if best_match and best_score >= 0.6:
        logger.info(f"Alias fuzzy match: '{name_clean}' -> '{best_match}' (score: {best_score:.2f})")
        return best_match

    return name_clean


def find_product(rpc: OdooRPC, product_name: str):
    """Find product using cached catalog + fuzzy matching."""
    resolved_name = resolve_product_name(product_name)
    logger.info(f"Product lookup: '{product_name}' -> resolved: '{resolved_name}'")

    # Get cached products
    all_products = rpc.get_all_products()

    # 1. Exact match
    for p in all_products:
        if p['name'].strip() == resolved_name:
            logger.info(f"Exact cache match: '{p['name']}'")
            return p

    # 2. Normalized exact match
    resolved_norm = normalize_arabic(resolved_name)
    for p in all_products:
        if normalize_arabic(p['name']) == resolved_norm:
            logger.info(f"Normalized cache match: '{p['name']}'")
            return p

    # 3. Fuzzy match on cached products
    scored = []
    for p in all_products:
        score = arabic_similarity(resolved_name, p['name'])
        if score >= 0.5:
            scored.append((p, score))

    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        best = scored[0]
        if best[1] >= 0.6:
            logger.info(f"Fuzzy cache match: '{best[0]['name']}' (score: {best[1]:.2f})")
            return best[0]

    # 4. Keyword overlap (for long product names)
    resolved_words = set(normalize_arabic(w) for w in resolved_name.split() if len(w) > 1)
    generic_words = {'بكج', 'كريم', 'عطر', 'زيت', 'سيروم', 'غسول', 'مقشر',
                     'لوشن', 'شامبو', 'ماسك', 'مربي', 'عسل', 'كورس', 'صابونه',
                     'مرطب', 'هديه', 'للعنايه', 'بالجسم', 'للوجه'}

    best_kw = None
    best_kw_score = 0
    for p in all_products:
        p_words = set(normalize_arabic(w) for w in p['name'].split() if len(w) > 1)
        common = resolved_words & p_words
        specific_common = common - generic_words

        if len(common) >= 2 and len(specific_common) >= 1:
            score = len(common) / len(resolved_words | p_words)
            if specific_common:
                score += 0.3
            if score > best_kw_score:
                best_kw_score = score
                best_kw = p

    if best_kw and best_kw_score >= 0.4:
        logger.info(f"Keyword match: '{best_kw['name']}' (score: {best_kw_score:.2f})")
        return best_kw

    # 5. Fallback to Odoo search (for edge cases not in cache)
    products = rpc.search_read('product.product', [
        ['name', 'ilike', resolved_name], ['sale_ok', '=', True], ['active', '=', True]
    ], fields=['id', 'name', 'list_price', 'qty_available'], limit=5)

    if products:
        best_fallback = None
        best_fb_score = 0
        for p in products:
            score = arabic_similarity(resolved_name, p['name'])
            if score > best_fb_score:
                best_fb_score = score
                best_fallback = p
        if best_fallback and best_fb_score >= 0.4:
            logger.info(f"Odoo fallback match: '{best_fallback['name']}' (score: {best_fb_score:.2f})")
            return best_fallback

    logger.warning(f"No product found for: '{product_name}' (resolved: '{resolved_name}')")
    return None


# ============ City Matching ============

def find_city(rpc: OdooRPC, city_name: str, state_id: int):
    """Find city using cached city list + fuzzy matching."""
    if not city_name or not state_id:
        return None

    # Get cached cities
    all_cities = rpc.get_cities_for_state(state_id)
    variants = generate_variants(city_name)
    logger.info(f"City lookup: '{city_name}' in state {state_id} ({len(all_cities)} cached cities)")

    # 1. Exact match with variants
    for city in all_cities:
        cname = city.get('x_name', '')
        for variant in variants:
            if cname == variant:
                logger.info(f"City exact match: '{cname}'")
                return city

    # 2. Normalized exact match
    city_norm = normalize_arabic(city_name)
    for city in all_cities:
        if normalize_arabic(city.get('x_name', '')) == city_norm:
            logger.info(f"City normalized match: '{city['x_name']}'")
            return city

    # 3. Containment match
    for city in all_cities:
        cname = city.get('x_name', '')
        cname_norm = normalize_arabic(cname)
        for variant in variants:
            v_norm = normalize_arabic(variant)
            if v_norm in cname_norm or cname_norm in v_norm:
                logger.info(f"City containment match: '{cname}'")
                return city

    # 4. Fuzzy match
    best = None
    best_score = 0.0
    for city in all_cities:
        cname = city.get('x_name', '')
        score = arabic_similarity(city_name, cname)
        if score > best_score:
            best_score = score
            best = city

    if best and best_score >= 0.6:
        logger.info(f"City fuzzy match: '{best['x_name']}' (score: {best_score:.2f})")
        return best

    logger.warning(f"No city found for: '{city_name}' in state {state_id}")
    return None
