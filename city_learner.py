"""
city_learner.py
---------------
Auto-learning module: when an employee selects the correct city from the
disambiguation list, this module saves the mapping as a new alias in
city_aliases_learned.json (separate from the static city_aliases.py).

On next lookup, city_aliases.py checks this file first.
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

LEARNED_FILE = os.path.join(os.path.dirname(__file__), "city_aliases_learned.json")


def _load_learned() -> dict:
    """Load learned aliases from JSON file."""
    if not os.path.exists(LEARNED_FILE):
        return {}
    try:
        with open(LEARNED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"city_learner: failed to load {LEARNED_FILE}: {e}")
        return {}


def _save_learned(data: dict):
    """Save learned aliases to JSON file."""
    try:
        with open(LEARNED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"city_learner: failed to save {LEARNED_FILE}: {e}")


def learn_city_alias(typed_name: str, correct_name: str, province: str):
    """
    Save a new alias: typed_name → correct_name for the given province.
    Called when an employee selects the correct city from the disambiguation list.
    """
    if not typed_name or not correct_name or not province:
        return
    if typed_name.strip() == correct_name.strip():
        return  # No need to save identical mappings

    data = _load_learned()
    if province not in data:
        data[province] = {}

    old = data[province].get(typed_name)
    if old == correct_name:
        return  # Already saved

    data[province][typed_name] = correct_name
    _save_learned(data)
    logger.info(f"city_learner: learned '{typed_name}' → '{correct_name}' in {province}")


def lookup_learned_alias(city_name: str, province: str) -> str | None:
    """
    Look up a learned alias.
    Returns the correct Odoo city name if found, else None.
    """
    if not city_name or not province:
        return None
    data = _load_learned()
    province_data = data.get(province, {})
    return province_data.get(city_name)
