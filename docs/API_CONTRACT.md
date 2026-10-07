# عقد الربط المقترح مع Laravel والواجهة

الإصدار 1. عقد مبدئي قابل للمواءمة مع الفريق. خدمة AI لا تتصل بقاعدة البيانات مباشرة ولا تستدعي أي API للكتابة.

ملف `docs/openapi.json` يصف واجهة FastAPI آليًا، ويمكن استيراده في Postman أو Swagger. أعِد توليده بأمر `python scripts/export_openapi.py` بعد تغيير نماذج الـAPI.

## Laravel → AI

### POST /v1/chat

الرؤوس التالية يُنشئها Laravel، ولا يأخذها من body أو headers المستخدم:

```http
Authorization: Bearer <AI_SERVICE_TOKEN>
X-Session-Subject: <opaque-random-laravel-session-id>
X-Customer-ID: <authenticated-customer-id>
Content-Type: application/json
```

`X-Customer-ID` يُحذف للضيف. `X-Session-Subject` من 8 إلى 128 حرفًا من أحرف ASCII والأرقام و`_` و`-`. تتغير جلسة AI بعد تسجيل الدخول/الخروج/تغيير الحساب؛ مفتاحها مرتبط بالهويتين.

```json
{
  "message": "عايز عطر تحت 200 ريال",
  "session_id": null,
  "locale": "ar",
  "current_store_id": "store-1"
}
```

- `message`: مطلوب، من 1 إلى 300 حرف بعد حذف الفراغات الطرفية.
- `session_id`: `null` للمحادثة الجديدة، ثم UUID من الرد السابق.
- `locale`: `ar` أو `en`، افتراضي `ar`. لغة رسالة العميل الواضحة لها الأولوية.
- `current_store_id`: سياق اختياري، **لا يقيد** الترشيح؛ البحث عبر كل المتاجر.
- هوية المستخدم لا تُقبل داخل JSON، ولا يُقبل تاريخ محادثة يرسله المتصفح.

```json
{
  "request_id": "uuid",
  "session_id": "uuid",
  "language": "ar",
  "answer": "دي اختيارات متاحة من متاجرنا ممكن تناسب طلبك:",
  "product_cards": [{
    "id": "product-1", "store_id": "store-1", "name": "اسم المنتج",
    "description": "وصف من الـbackend", "price": "150.00",
    "original_price": "180.00", "currency": "SAR",
    "url": "https://shop.example/products/1", "image_url": null,
    "updated_at": "2026-10-07T09:00:00Z"
  }],
  "store_cards": [], "order": null, "faq_ids": [],
  "quick_replies": ["رشحلي هدية", "اختار متجر", "الشحن والتوصيل", "حالة طلبي"],
  "handoff": {"requested": false, "url": null},
  "requires_login": false, "requires_order_number": false,
  "demo": false, "degraded": false, "elapsed_ms": 500
}
```

`price` و`original_price` أرقام عشرية بصيغة نص للحفاظ على دقة المال. `demo` يوضح أن أحد الوضعين تجريبي. `degraded` يشير إلى تعذر مصدر خارجي. استجابة fallback المتوقعة تظل HTTP 200 مع رسالة مناسبة؛ لا تعرض الواجهة أخطاء تقنية للمستخدم.

### DELETE /v1/sessions/{session_id}

نفس الرؤوس. يعيد 204 عند مسح الجلسة. استدعوه قبل الخروج مع الهوية القديمة، وعند إنهاء المحادثة. إذا أغلقت الصفحة دون وصول الطلب، ينتهي التاريخ تلقائيًا حسب TTL.

### أخطاء HTTP

| الحالة | التصرف |
|---|---|
| 400 | سياق جلسة/هوية غير صحيح؛ أصلح الربط |
| 401 | مفتاح خدمة AI غير صحيح |
| 404 | الجلسة غير متاحة/انتهت؛ ابدأ بجلسة جديدة. لا تكشف هل تخص مستخدمًا آخر |
| 409 | رسالة أخرى لنفس الجلسة ما زالت تُعالج، أو سعة الجلسات ممتلئة؛ احترم Retry-After |
| 413 / 422 | مدخلات كبيرة/غير صحيحة؛ لا تعاود بنفس المحتوى |
| 429 | تجاوز الحد؛ احترم Retry-After |
| 503 | السعة أو Redis غير متاح؛ رسالة انتظار مناسبة |

الأخطاء تأتي في `error` ومعها `request_id`؛ استخدم status code لاتخاذ القرار، ولا تعرض النص الداخلي للعميل.

## AI → Laravel: أربعة endpoints للقراءة

كل طلب GET يحمل `Authorization: Bearer <BACKEND_API_TOKEN>` و`Accept: application/json`. المفتاح بصلاحية قراءة فقط. Laravel يتحقق منه في middleware مخصص. القيم القادمة من النموذج تُمرر كمعاملات بحث، ولا تنفذ كـSQL أو أسماء دوال.

### 1. GET /api/ai/stores

معاملات: `query` و`active=1`. رد `{"data": [Store, ...]}`. query فارغ يعيد كل المتاجر النشطة (حاليًا سبعة).

`Store`: `id`, `name_ar`, `name_en`, `description_ar`, `description_en`, `url`, `active`.

### 2. GET /api/ai/products/search

معاملات: `query`, `min_price?`, `max_price?`, `limit` (افتراضي 3)، `in_stock=1`, `active=1`.

يبحث عبر كل المتاجر، في الأسماء والأوصاف العربية والإنجليزية. يرجع النتائج **مرتبة حسب الصلة**، ضمن السعر المطلوب، بمنتجات منشورة ومتاجر نشطة ومخزون متاح وأسعار محدثة. لا يرجع catalog كاملًا. خدمة AI تعيد فحص الحدود والتوفر والروابط.

رد `{"data": [Product, ...]}`. `Product` يحتوي:

```json
{
  "id": "product-1", "store_id": "store-1",
  "name_ar": "عطر", "name_en": "Perfume",
  "description_ar": "وصف معتمد", "description_en": "Approved description",
  "price": "150.00", "original_price": "180.00", "currency": "SAR",
  "in_stock": true, "active": true, "store_active": true,
  "url": "https://shop.example/products/1", "image_url": null,
  "updated_at": "2026-10-07T09:00:00Z"
}
```

`original_price` اختياري/null عند عدم وجود خصم. `updated_at` وقت تحديث بيانات المنتج مع timezone. الصفحات والصور HTTPS وعلى hosts المسموح بها في إعدادات AI. لا تعمل روابط `demo.example` كروابط بيع حقيقية.

### 3. GET /api/ai/faqs/search

معاملات: `query`, `limit=2`. يجب إرجاع إجابات **مرتبطة بالسؤال ومعتمدة فقط**، أو قائمة فارغة. لا يتطلب العميل تسجيل الدخول.

رد `{"data": [FAQ, ...]}`. `FAQ`: `id`, `question_ar`, `question_en`, `answer_ar`, `answer_en`, `active`. الإجابات نص عادي. توفروا الترجمتين حتى لا نخمن السياسات أثناء الترجمة.

### 4. GET /api/ai/orders/{order_number}

رأس إضافي `X-Customer-ID` من هوية Laravel الموثوقة. **لا يُستدعى حتى يكتب العميل رقم الطلب ويسجل الدخول. لا يوجد استدعاء لقائمة طلباته.** الرقم المقبول حروف لاتينية/أرقام/شرطات بطول 3–40 ويحتوي رقمًا؛ الأرقام العربية تُحوّل للشكل الإنجليزي. إذا كانت صيغة أرقامكم مختلفة، تُعدّل أداة التحقق قبل الربط.

Laravel يبحث عن الطلب بشرطي الرقم **والمستخدم** في نفس الاستعلام قبل إرجاعه. لا يثق في الرقم وحده. 403 أو 404 يعطيان نفس رد العميل داخل AI.

```json
{
  "data": {
    "order_number": "ORDER-1001",
    "customer_id": "123",
    "status": "shipped",
    "tracking_url": "https://carrier.example/track/1001",
    "updated_at": "2026-10-07T09:00:00Z"
  }
}
```

الحالات: `processing`, `synced`, `shipped`, `delivered`, `cancelled`, `sync_error`, `refund_pending`, `refunded`. تحتاج أسماء الحالات المختلفة mapping في طبقة Laravel أو `app/gateway.py`. رابط التتبع nullable وعلى host موثوق. لا ترجع العنوان أو الهاتف أو معلومات الدفع؛ ليست مطلوبة لعرض الحالة.

عند طلب الحالة دون رقم، الرد `requires_order_number=true` ولن يُستدعى Laravel. عند وجود الرقم دون تسجيل الدخول، `requires_login=true`. بعد نجاح الدخول، تُنشأ جلسة AI جديدة ويعيد العميل إرسال رقم الطلب.

## ما يجب الاتفاق عليه قبل الدمج

المسارات وطريقة مصادقة الخدمات، أمثلة حقيقية مطابقة للعقد، أسماء حالات الطلب، hosts المنتجات والصور والتتبع، وطريقة إنهاء جلسة AI مع Laravel. مجرد وضع endpoints يكفي فقط إذا كانت الاستجابات توافق العقد. خلاف ذلك تعديل الـadapter مع إعادة اختبارات التكامل مطلوب.
