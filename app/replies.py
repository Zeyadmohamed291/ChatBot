from urllib.parse import urlencode

from app.models import Handoff, Language

TEXT = {
    "greeting": (
        "أهلًا! أقدر أساعدك تختار منتج من متاجرنا، أو أجاوبك عن الشحن وحالة طلبك. بتدور على إيه؟",
        "Hi! I can help you find a product across our stores, answer shopping questions, or check your order. What are you looking for?",
    ),
    "thanks": (
        "العفو! أنا معاك لو محتاج تختار حاجة تانية.",
        "You're welcome! I'm here if you need help finding anything else.",
    ),
    "goodbye": (
        "مع السلامة! أتمنى تلاقي اللي يناسبك.",
        "Take care! Hope you find something you love.",
    ),
    "capabilities": (
        "أقدر أرشح منتجات ومتاجر، أجاوب عن السياسات، وأتابع حالة طلب برقم الطلب بعد تسجيل الدخول.",
        "I can suggest products and stores, answer policy questions, and check an order by its number once you're signed in.",
    ),
    "action": (
        "أقدر أوضح لك الخطوات أو السياسة المعتمدة، لكن تنفيذ الشراء والدفع والإلغاء بيكون من صفحات الموقع. تحب أساعدك في إيه؟",
        "I can explain the steps or approved policy. Purchases, payments and cancellations are handled on the website. What would you like help with?",
    ),
    "unsupported": (
        "أقدر أساعدك في منتجات المتاجر والتسوق وحالة الطلبات. السؤال ده محتاج مساعدة من فريق الدعم.",
        "I can help with our stores, shopping and order status. Our support team can help with this question.",
    ),
    "human": (
        "تقدر تكمل استفسارك مع فريق الدعم على واتساب.",
        "You can continue your question with our support team on WhatsApp.",
    ),
    "no_support": (
        "محتاج مساعدة من فريق الدعم، لكن رابط التواصل مش متاح حاليًا. جرّب صفحة التواصل في الموقع.",
        "Our support team can help, but the contact link is currently unavailable. Please try the website's contact page.",
    ),
    "unavailable": (
        "آسف، مش قادر أجيب المعلومات دلوقتي. جرّب بعد شوية، أو تواصل مع الدعم.",
        "Sorry, I can't retrieve that information right now. Please try again shortly or contact support.",
    ),
    "products": (
        "دي اختيارات متاحة من متاجرنا ممكن تناسب طلبك:",
        "Here are available options from our stores that may suit your request:",
    ),
    "no_products": (
        "ما لقيتش منتجات متاحة مطابقة للطلب ده. تحب نغيّر النوع أو نطاق السعر؟",
        "I couldn't find available products matching that request. Would you like to change the category or price range?",
    ),
    "stores": ("دي المتاجر المناسبة لبحثك:", "Here are stores relevant to your search:"),
    "all_stores": (
        "دي المتاجر المتاحة؛ اختار اللي يناسبك ونكمل البحث فيه.",
        "Here are the available stores. You can choose one to explore.",
    ),
    "no_stores": (
        "مش قادر أحدد متجر متاح مناسب دلوقتي.",
        "I couldn't find a suitable active store right now.",
    ),
    "no_faq": (
        "مش عندي إجابة معتمدة عن النقطة دي حاليًا. تقدر تتأكد من فريق الدعم.",
        "I don't have an approved answer for that question yet. Our support team can confirm it.",
    ),
    "order_number": (
        "أكيد، ابعتلي رقم الطلب الأول عشان أقدر أتابع حالته.",
        "Of course. Please send your order number first so I can check its status.",
    ),
    "login": (
        "معايا رقم الطلب. سجّل الدخول الأول عشان أقدر أتحقق إن الطلب يخصك وأعرض حالته.",
        "I have the order number. Please sign in first so I can verify that the order belongs to you and show its status.",
    ),
    "order_unavailable": (
        "مش قادر أعرض الطلب ده لحسابك. تأكد من الرقم وإنك داخل بالحساب اللي عمل الطلب.",
        "I can't display this order for your account. Please check the number and sign in with the account that placed it.",
    ),
    "budget": ("تحب يكون في حدود ميزانية كام؟", "What budget would you like to stay within?"),
    "recipient": ("الهدية لمين؟", "Who is the gift for?"),
    "category": ("تحب أي نوع من المنتجات؟", "What kind of product would you prefer?"),
}

STATUSES = {
    "processing": ("قيد التجهيز", "Processing"),
    "synced": ("تم إرساله للمورّد", "Sent to supplier"),
    "shipped": ("تم الشحن", "Shipped"),
    "delivered": ("تم التوصيل", "Delivered"),
    "cancelled": ("تم الإلغاء", "Cancelled"),
    "sync_error": ("توجد مشكلة في مزامنة الطلب", "Order synchronization issue"),
    "refund_pending": ("استرداد المبلغ قيد المعالجة", "Refund pending"),
    "refunded": ("تم استرداد المبلغ", "Refunded"),
}


def text(key: str, language: Language) -> str:
    return TEXT[key][0 if language == "ar" else 1]


def quick_replies(language: Language) -> list[str]:
    return (
        ["رشحلي هدية", "اختار متجر", "الشحن والتوصيل", "حالة طلبي"]
        if language == "ar"
        else ["Recommend a gift", "Choose a store", "Shipping and delivery", "My order status"]
    )


def handoff(phone: str, language: Language) -> Handoff:
    message = (
        "مرحبًا، أحتاج مساعدة بخصوص المتجر."
        if language == "ar"
        else "Hello, I need help with the store."
    )
    # Do not copy the transcript, order number or account details into a third-party URL.
    return Handoff(
        requested=True,
        url=f"https://wa.me/{phone}?{urlencode({'text': message})}" if phone else None,
    )
