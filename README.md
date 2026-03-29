# بوت طلبات شهد بيوتي v2

بوت تيليجرام لإدخال طلبات الواتساب إلى نظام Odoo ERP — النسخة المحسّنة.

## التحسينات عن v1

### أمان
- إزالة كل التوكنات وكلمات السر من الكود — كلشي عبر Environment Variables
- نظام صلاحيات: AUTHORIZED_USERS + ADMIN_USERS
- كل يوزر يشوف الـ ID مالته إذا ما عنده صلاحية

### أداء
- OdooRPC Singleton: اتصال واحد يُعاد استخدامه
- Product Cache: كاش المنتجات لمدة 5 دقائق
- City Cache: كاش المدن لكل محافظة لمدة 10 دقائق
- Retry Logic: 3 محاولات + إعادة تسجيل دخول تلقائي

### دقة المطابقة
- Arabic Normalization: أ/إ/آ→ا، ة↔ه، ى↔ي، حركات، تطويل
- Fuzzy Matching مع SequenceMatcher + Jaccard Similarity
- البحث أولاً بالكاش ثم fallback لأودو

### فيتشرات جديدة
- `/report today` — تقرير مبيعات اليوم
- `/report week` — تقرير الأسبوع
- `/stock [اسم]` — فحص المخزون
- `/search [اسم/رقم]` — بحث عن طلب أو زبون
- كشف الطلبات المكررة تلقائياً
- تنبيه المخزون المنخفض عند كل طلب

## هيكل الملفات

- `bot.py` — الملف الرئيسي (handlers + order creation)
- `config.py` — كل الإعدادات من Environment Variables
- `odoo_client.py` — Singleton Odoo RPC + cache + retry
- `matching.py` — مطابقة المنتجات والمدن (fuzzy Arabic)
- `parse_order.py` — تحليل الطلبات بـ LLM
- `product_aliases.py` — قاموس أسماء المنتجات
- `reports.py` — تقارير المبيعات
- `duplicate_guard.py` — كشف الطلبات المكررة

## الإعداد

### 1. المتغيرات البيئية
انسخ `.env.example` إلى `.env` وعدّل القيم:
```
TELEGRAM_BOT_TOKEN=your_token
ODOO_URL=https://shahdbeauty.odoo.com
ODOO_DB=your_db_name
ODOO_USER=admin
ODOO_PASSWORD=your_password
OPENAI_API_KEY=sk-your-key
AUTHORIZED_USERS=123456789,987654321
ADMIN_USERS=123456789
```

### 2. التشغيل
```bash
pip install -r requirements.txt
python bot.py
```

### 3. Docker
```bash
docker build -t shahd-bot .
docker run --env-file .env shahd-bot
```

## الأوامر

| الأمر | الوصف | الصلاحية |
|-------|-------|----------|
| /start | بداية + عرض الأوامر | الكل |
| /help | المساعدة التفصيلية | الكل |
| /shahd | تحديد براند شهد بيوتي | الكل |
| /marlin | تحديد براند مارلين | الكل |
| /report today | تقرير مبيعات اليوم | مدراء |
| /report week | تقرير مبيعات الأسبوع | مدراء |
| /stock [اسم] | فحص المخزون | الكل |
| /search [بحث] | بحث عن طلب أو زبون | الكل |

## ملاحظات تطوير

- إضافة منتجات جديدة: عدّل `product_aliases.py`
- سعر الشحن ثابت 5000 دينار — لا تعدله
- إضافة محافظات: عدّل `PROVINCE_MAP` في `config.py`
- تغيير مدة كشف المكرر: `DUPLICATE_WINDOW_MINUTES` بالـ `.env`
