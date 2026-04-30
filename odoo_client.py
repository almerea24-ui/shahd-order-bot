#!/usr/bin/env python3
"""
Odoo RPC client — singleton with retry logic, product/city caching.
"""

import time
import logging
import requests
from functools import lru_cache
from threading import Lock

from config import (
    ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD,
    ODOO_RETRY_ATTEMPTS, ODOO_RETRY_DELAY,
    PRODUCT_CACHE_TTL, CITY_CACHE_TTL,
)

logger = logging.getLogger(__name__)


class OdooRPC:
    """Thread-safe Odoo JSON-RPC client with automatic retry."""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.url = ODOO_URL
        self.db = ODOO_DB
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.uid = None
        self._login()
        # Caches
        self._product_cache = []
        self._product_cache_time = 0
        self._city_cache: dict[int, list] = {}  # state_id -> cities
        self._city_cache_time: dict[int, float] = {}
        self._initialized = True

    def _login(self):
        result = self._jsonrpc('/web/session/authenticate', {
            'db': self.db, 'login': ODOO_USER, 'password': ODOO_PASSWORD
        })
        self.uid = result.get('uid')
        if not self.uid:
            raise Exception("Odoo authentication failed")
        logger.info("Odoo login successful (uid=%s)", self.uid)

    def _jsonrpc(self, endpoint, params):
        payload = {'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': params}
        resp = self.session.post(f'{self.url}{endpoint}', json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get('error'):
            err = data['error']
            msg = err.get('data', {}).get('message', '') or err.get('message', '')
            raise Exception(f"Odoo Error: {msg}")
        return data.get('result', {})

    def _retry(self, func, *args, **kwargs):
        """Execute with retry on failure, re-login if session expired."""
        last_err = None
        for attempt in range(ODOO_RETRY_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if "session" in err_str or "expired" in err_str or "denied" in err_str:
                    logger.warning("Session expired, re-logging in (attempt %d)", attempt + 1)
                    try:
                        self._login()
                    except Exception:
                        pass
                if attempt < ODOO_RETRY_ATTEMPTS - 1:
                    time.sleep(ODOO_RETRY_DELAY * (attempt + 1))
                    logger.warning("Retry %d/%d: %s", attempt + 1, ODOO_RETRY_ATTEMPTS, e)
        raise last_err

    def call(self, model, method, args=None, kwargs=None):
        def _do():
            return self._jsonrpc('/web/dataset/call_kw', {
                'model': model, 'method': method,
                'args': args or [], 'kwargs': kwargs or {}
            })
        return self._retry(_do)

    def search_read(self, model, domain, fields=None, limit=None):
        kw = {'fields': fields or []}
        if limit:
            kw['limit'] = limit
        return self.call(model, 'search_read', [domain], kw)

    def create(self, model, vals):
        return self.call(model, 'create', [vals])

    def write(self, model, ids, vals):
        if not isinstance(ids, list):
            ids = [ids]
        return self.call(model, 'write', [ids, vals])

    def read(self, model, ids, fields=None):
        if not isinstance(ids, list):
            ids = [ids]
        return self.call(model, 'read', [ids], {'fields': fields or []})

    # ============ Cached Product Search ============

    def get_all_products(self, force=False):
        """Return cached list of all saleable products."""
        now = time.time()
        if not force and self._product_cache and (now - self._product_cache_time) < PRODUCT_CACHE_TTL:
            return self._product_cache
        logger.info("Refreshing product cache...")
        products = self.search_read('product.product', [
            ['sale_ok', '=', True], ['active', '=', True]
        ], fields=['id', 'name', 'list_price', 'qty_available', 'categ_id'], limit=500)
        self._product_cache = products
        self._product_cache_time = now
        logger.info("Product cache refreshed: %d products", len(products))
        return products

    def get_cities_for_state(self, state_id, force=False):
        """Return cached list of cities for a given state."""
        now = time.time()
        cache_time = self._city_cache_time.get(state_id, 0)
        if not force and state_id in self._city_cache and (now - cache_time) < CITY_CACHE_TTL:
            return self._city_cache[state_id]
        logger.info("Refreshing city cache for state %d...", state_id)
        cities = self.search_read('x_city', [
            ['x_studio_state', '=', state_id], ['x_active', '=', True]
        ], fields=['id', 'x_name'], limit=500)
        self._city_cache[state_id] = cities
        self._city_cache_time[state_id] = now
        logger.info("City cache for state %d: %d cities", state_id, len(cities))
        return cities

    def check_stock(self, product_name_or_id):
        """Check stock for a product. Returns dict with name, qty, status."""
        if isinstance(product_name_or_id, int):
            products = self.read('product.product', product_name_or_id,
                                 fields=['name', 'qty_available', 'virtual_available'])
            if products:
                p = products[0]
                return {
                    'name': p['name'],
                    'on_hand': p.get('qty_available', 0),
                    'forecasted': p.get('virtual_available', 0),
                    'status': 'متوفر' if p.get('qty_available', 0) > 0 else 'غير متوفر'
                }
        else:
            # Search by name
            products = self.search_read('product.product', [
                ['name', 'ilike', product_name_or_id],
                ['sale_ok', '=', True], ['active', '=', True]
            ], fields=['id', 'name', 'qty_available', 'virtual_available', 'categ_id'], limit=5)
            results = []
            for p in products:
                results.append({
                    'id': p['id'],
                    'name': p['name'],
                    'on_hand': p.get('qty_available', 0),
                    'forecasted': p.get('virtual_available', 0),
                    'status': 'متوفر' if p.get('qty_available', 0) > 0 else 'غير متوفر'
                })
            return results
        return None

    # ============ Search Orders/Customers ============

    def search_orders(self, query, limit=10):
        """Search orders by name, phone, or order number."""
        domain = ['|', '|',
                  ['name', 'ilike', query],
                  ['partner_id.name', 'ilike', query],
                  ['partner_id.phone', 'ilike', query]]
        return self.search_read('sale.order', domain,
                                fields=['id', 'name', 'partner_id', 'amount_total',
                                        'state', 'date_order', 'x_shipping_notes'],
                                limit=limit)

    def search_customers(self, query, limit=10):
        """Search customers by name or phone."""
        domain = ['|',
                  ['name', 'ilike', query],
                  ['phone', 'ilike', query]]
        return self.search_read('res.partner', domain,
                                fields=['id', 'name', 'phone', 'state_id',
                                        'city', 'x_studio_city', 'customer_rank'],
                                limit=limit)

    # ============ Reports ============

    def get_orders_by_date(self, date_from, date_to, brand_carrier_id=None):
        """Get orders within a date range, optionally filtered by carrier."""
        domain = [
            ['date_order', '>=', date_from],
            ['date_order', '<=', date_to],
            ['state', 'in', ['sale', 'done']],
        ]
        if brand_carrier_id:
            domain.append(['carrier_id', '=', brand_carrier_id])

        return self.search_read('sale.order', domain,
                                fields=['id', 'name', 'partner_id', 'amount_total',
                                        'state', 'date_order', 'carrier_id',
                                        'order_line'],
                                limit=500)

    def get_order_lines(self, order_id):
        """Get order lines for a specific order."""
        return self.search_read('sale.order.line', [
            ['order_id', '=', order_id]
        ], fields=['product_id', 'product_uom_qty', 'price_unit',
                   'price_subtotal', 'is_delivery', 'name'])
