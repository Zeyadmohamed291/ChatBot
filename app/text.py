import re
import unicodedata

from app.models import Language

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC = str.maketrans("أإآىة", "ااايه")


def digits(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DIGITS)


def normalize(text: str) -> str:
    return re.sub(r"[\u064b-\u065f\u0670ـ]", "", digits(text)).lower().translate(_ARABIC)


def language_of(text: str, fallback: Language) -> Language:
    if re.search(r"[\u0621-\u063a\u0641-\u064a]", text):
        return "ar"
    if re.search(r"[a-zA-Z]", text):
        return "en"
    return fallback


def order_number_from_message(candidate: str | None, message: str) -> str | None:
    """A model may propose a number; only a literal customer-supplied token is accepted."""
    if not candidate:
        return None
    candidate = digits(candidate).upper().strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,38}[A-Z0-9]", candidate):
        return None
    if not any(c.isdigit() for c in candidate):
        return None
    if re.search(r"(?<![\w-])" + re.escape(candidate) + r"(?![\w-])", digits(message).upper()):
        return candidate
    return None


def extract_number(message: str) -> str | None:
    for token in re.findall(
        r"(?<![\w-])[A-Za-z0-9][A-Za-z0-9-]{1,38}[A-Za-z0-9](?![\w-])", digits(message)
    ):
        result = order_number_from_message(token, message)
        if result:
            return result
    return None


_GROUPS = [
    "عطر عطور perfume perfumes fragrance",
    "مكياج makeup cosmetics lipstick احمر شفاه",
    "هديه هدايا gift gifts",
    "شحن توصيل وصول shipping delivery deliver",
    "استرجاع ارجاع مرتجع returns return refund",
    "دفع سداد payment pay visa",
    "اكسسوارات اكسسوار accessories accessory jewelry سوار",
    "ملابس فستان ازياء fashion dress clothing",
    "منزل منزلي ديكور home decor",
    "الكترونيات سماعه سماعات electronics headphones",
]
_STOP = set(
    "i a an the want need please for me recommend suggest my is of to in and with looking some عايز عاوز ابغى ابي اريد ممكن من في علي عن لي انا لو سمحت رشحلي افضل عندكم كم هل ما هو اي ده دي".split()
)


def tokens(text: str) -> set[str]:
    result = set(re.findall(r"[\w]+", normalize(text))) - _STOP
    result |= {word[2:] for word in result if word.startswith("ال") and len(word) > 4}
    for group in _GROUPS:
        words = set(group.split())
        if result & words:
            result |= words
    return result


def score(query: str, document: str) -> int:
    return len(tokens(query) & tokens(document))
