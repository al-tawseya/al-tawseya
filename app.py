#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
التوصية — Jordanian Recommendation Engine
Single-file local web application.

Run:
    python app.py

Optional Gemini extraction:
    pip install -U google-generativeai
    export GOOGLE_API_KEY="..."
    python app.py

The legacy google-generativeai package is optional at runtime. The local
Arabic/Jordanian extractor always remains available as a deterministic
fallback, so the application still works without a Gemini API key.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

try:
    import google.generativeai as genai  # type: ignore
except Exception:  # Optional dependency.
    genai = None


HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = Path(__file__).with_name("decision_engine.db")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("al-tawseya")
DB_LOCK = threading.RLock()

PAYMENT_BANK = "Arab Bank"
PAYMENT_BANK_AR = "البنك العربي"
PAYMENT_ALIAS = "MQRB"
DEAL_PRICE_JOD = 1


# ---------------------------------------------------------------------------
# Data model and seed inventory
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Offer:
    id: int
    merchant_name: str
    title: str
    category: str
    price_jod: float
    tags: List[str]
    colors: List[str]
    style: List[str]
    city: str
    description: str
    image_url: str
    whatsapp_url: str
    instagram_url: str


SEED_OFFERS: Sequence[Offer] = (
    Offer(1, "Luxe Amman", "أحمر شفاه مات أسود Velvet Noir", "makeup", 9.0,
          ["lipstick", "matte", "black", "velvet", "حومرة", "روج"],
          ["black", "أسود"], ["luxury", "evening"], "عمّان",
          "أحمر شفاه مات بدرجة سوداء عميقة وثبات طويل للمناسبات والتنسيقات الجريئة.",
          "https://images.unsplash.com/photo-1586495777744-4413f21062fa?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000001", "https://instagram.com/luxe.amman"),
    Offer(2, "Nude House JO", "روج سائل أسود Black Ink", "makeup", 7.5,
          ["liquid lipstick", "lip", "black", "حومرة", "روج", "أسود"],
          ["black", "أسود"], ["minimal", "bold"], "عمّان",
          "روج سائل بتركيبة مرنة ولمعة مطفّية ناعمة.",
          "https://images.unsplash.com/photo-1596462502278-27bfdc403348?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000002", "https://instagram.com/nudehousejo"),
    Offer(3, "Amman Beauty Lab", "روج مخملي أسود مع محدد شفاه", "makeup", 12.0,
          ["lipstick", "liner", "black", "makeup", "حومرة", "مكياج"],
          ["black", "أسود"], ["professional", "night"], "عمّان",
          "ثنائية روج ومحدد بدرجة أسود كلاسيكية لإطلالة مسائية متكاملة.",
          "https://images.unsplash.com/photo-1512496015851-a90fb38ba796?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000003", "https://instagram.com/ammanbeautylab"),
    Offer(4, "The Makeup Room", "باقة مكياج العيون Black Smoke", "makeup", 18.0,
          ["eyeshadow", "smokey", "black", "makeup", "مكياج"],
          ["black", "grey", "أسود", "رمادي"], ["party", "night"], "الزرقاء",
          "لوحة ظلال دخانية بدرجات سوداء ورمادية مناسبة للسهرات.",
          "https://images.unsplash.com/photo-1522335789203-aabd1fc54bc9?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000004", "https://instagram.com/themakeuproomjo"),
    Offer(5, "Maison 7", "فستان مخمل أسود للحفلات", "clothes", 34.0,
          ["dress", "party", "black", "velvet", "فستان", "حفلة", "أسود"],
          ["black", "أسود"], ["party", "velvet", "elegant"], "عمّان",
          "فستان سهرة مخمل أسود بقصة أنيقة ومناسبة للحفلات والمناسبات.",
          "https://images.unsplash.com/photo-1566174053879-31528523f8ae?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000005", "https://instagram.com/maison7jo"),
    Offer(6, "Luna Amman", "فستان ساتان أسود محتشم", "clothes", 39.0,
          ["dress", "satin", "modest", "black", "فستان", "ساتان", "محتشم"],
          ["black", "أسود"], ["modest", "satin", "formal"], "عمّان",
          "فستان ساتان انسيابي بتفاصيل محتشمة وتصميم مناسب للعشاء والمناسبات.",
          "https://images.unsplash.com/photo-1591369822096-ffd140ec948f?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000006", "https://instagram.com/lunaamman"),
    Offer(7, "Silk Avenue", "طقم ساتان محتشم لسهرة هادئة", "clothes", 29.0,
          ["satin set", "modest", "black", "robe", "طقم", "ساتان", "محتشم"],
          ["black", "أسود"], ["modest", "lounge", "evening"], "إربد",
          "طقم ساتان مريح وأنيق بقصة هادئة وألوان حيادية.",
          "https://images.unsplash.com/photo-1595882100582-7dfd0a3a2f76?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000007", "https://instagram.com/silkavenuejo"),
    Offer(8, "Noir Closet", "فستان أسود بسيط بقصة مستقيمة", "clothes", 24.0,
          ["dress", "black", "basic", "minimal", "فستان", "أسود"],
          ["black", "أسود"], ["minimal", "day", "night"], "عمّان",
          "فستان أسود عملي يمكن تنسيقه للدوام أو المناسبات الخفيفة.",
          "https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000008", "https://instagram.com/noirclosetjo"),
    Offer(9, "Wardrobe JO", "عباية ساتان سوداء بلمعة ناعمة", "clothes", 46.0,
          ["abaya", "satin", "modest", "black", "عباية", "ساتان", "أسود"],
          ["black", "أسود"], ["modest", "luxury"], "السلط",
          "عباية ساتان سوداء بتفصيل انسيابي وخياطة نظيفة.",
          "https://images.unsplash.com/photo-1525507119028-ed4c629a60a3?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000009", "https://instagram.com/wardrobejo"),
    Offer(10, "Gift District", "بوكس هدية أسود فاخر مع ورد مجفف", "gifts", 22.0,
          ["gift box", "black", "flowers", "هدية", "بوكس", "ورد", "أسود"],
          ["black", "cream", "أسود", "كريمي"], ["luxury", "romantic"], "عمّان",
          "بوكس هدية جاهز مع تغليف فاخر وورد مجفف وبطاقة صغيرة.",
          "https://images.unsplash.com/photo-1513883049090-d0b7439799bf?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000010", "https://instagram.com/giftdistrictjo"),
    Offer(11, "Amman Gifting Co.", "طقم هدية فضي أنيق", "gifts", 26.0,
          ["gift", "silver", "set", "هدية", "فضي", "طقم"],
          ["silver", "فضي", "white", "أبيض"], ["elegant", "classic"], "عمّان",
          "مجموعة هدايا أنيقة بتغليف فضي تصلح للتخرج والمناسبات.",
          "https://images.unsplash.com/photo-1512909006721-3d6018887383?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000011", "https://instagram.com/ammangifting"),
    Offer(12, "Little Luxe", "سوار هدية مع علبة سوداء", "gifts", 14.0,
          ["gift", "bracelet", "black box", "هدية", "سوار", "علبة"],
          ["gold", "black", "ذهبي", "أسود"], ["classic", "gift"], "إربد",
          "سوار بسيط داخل علبة سوداء فاخرة وجاهز للإهداء.",
          "https://images.unsplash.com/photo-1548036328-c9fa89d128fa?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000012", "https://instagram.com/littleluxjo"),
    Offer(13, "Jabal Amman Watches", "ساعة كلاسيكية ذهبية بسوار معدني", "watches", 69.0,
          ["watch", "gold", "classic", "gold watch", "ساعة", "ذهبي"],
          ["gold", "black", "ذهبي", "أسود"], ["classic", "formal"], "عمّان",
          "ساعة كلاسيكية بلمسة ذهبية مناسبة للهدية والمظهر الرسمي.",
          "https://images.unsplash.com/photo-1523170335258-f5ed11844a49?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000013", "https://instagram.com/jabalammanwatches"),
    Offer(14, "Time House", "ساعة سوداء Minimal Dial", "watches", 55.0,
          ["watch", "black", "minimal", "ساعة", "أسود", "كلاسيك"],
          ["black", "silver", "أسود", "فضي"], ["minimal", "classic"], "عمّان",
          "قرص أسود بسيط بتفاصيل فضية يناسب اللبس اليومي والرسمي.",
          "https://images.unsplash.com/photo-1524805444758-089113d48a6d?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000014", "https://instagram.com/timehousejo"),
    Offer(15, "Irbid Time", "ساعة جلد بني كلاسيكية", "watches", 49.0,
          ["watch", "brown", "leather", "classic", "ساعة", "جلد", "بني"],
          ["brown", "silver", "بني", "فضي"], ["classic", "casual"], "إربد",
          "ساعة جلدية كلاسيكية مريحة للإطلالات اليومية.",
          "https://images.unsplash.com/photo-1434056886845-dac89ffe9b56?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000015", "https://instagram.com/irbidtime"),
    Offer(16, "Oud Amman", "عطر عود فاخر Royal Oud", "perfumes", 58.0,
          ["perfume", "oud", "luxury", "عطر", "عود", "فاخر"],
          ["amber", "black", "عنبر", "أسود"], ["luxury", "night"], "عمّان",
          "تركيبة عود شرقية دافئة بلمسات عنبر وورد مناسبة للمساء.",
          "https://images.unsplash.com/photo-1547887538-e3a2f32cb1cc?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000016", "https://instagram.com/oudamman"),
    Offer(17, "Scent 7", "دهن عود مركز 12 مل", "perfumes", 32.0,
          ["oud oil", "oud", "perfume", "دهن عود", "عطر", "عود"],
          ["amber", "brown", "عنبر", "بني"], ["arabic", "luxury"], "عمّان",
          "دهن عود مركز بحجم عملي وثبات واضح للتنسيق اليومي والمناسبات.",
          "https://images.unsplash.com/photo-1594035910387-fea47794261f?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000017", "https://instagram.com/scent7jo"),
    Offer(18, "Layali Perfumes", "عطر شرقي أسود Midnight", "perfumes", 44.0,
          ["perfume", "black", "oriental", "عطر", "شرقي", "أسود"],
          ["black", "amber", "أسود", "عنبر"], ["night", "oriental"], "الزرقاء",
          "عطر شرقي داكن بطابع مسائي وقارورة سوداء مطفّية.",
          "https://images.unsplash.com/photo-1592945403244-b3fbafd7f539?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000018", "https://instagram.com/layaliperfumesjo"),
    Offer(19, "Silver Line JO", "طقم مجوهرات فضي لامع", "gifts", 37.0,
          ["silver jewelry", "set", "gift", "مجوهرات", "فضي", "هدية", "طقم"],
          ["silver", "white", "فضي", "أبيض"], ["classic", "gift"], "عمّان",
          "طقم مجوهرات فضي بلمعة ناعمة مناسب كهدية أو مناسبة.",
          "https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000019", "https://instagram.com/silverlinejo"),
    Offer(20, "Ayla Accessories", "طقم عقد وأقراط فضي", "gifts", 31.0,
          ["silver jewelry", "necklace", "earrings", "مجوهرات", "عقد", "أقراط", "فضي"],
          ["silver", "فضي"], ["elegant", "classic"], "العقبة",
          "عقد وأقراط بتصميم هادئ يصلح للإهداء.",
          "https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000020", "https://instagram.com/aylaaccessories"),
    Offer(21, "Black Label Beauty", "أحمر شفاه أسود ساتان", "makeup", 10.0,
          ["lipstick", "satin", "black", "حومرة", "روج", "أسود"],
          ["black", "أسود"], ["satin", "luxury"], "عمّان",
          "تركيبة ساتان تعطي لونًا أسود واضحًا مع لمعة خفيفة.",
          "https://images.unsplash.com/photo-1571781926291-c477ebfd024b?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000021", "https://instagram.com/blacklabelbeautyjo"),
    Offer(22, "Misk Jordan", "عطر مسك وعود بتركيبة ناعمة", "perfumes", 36.0,
          ["perfume", "musk", "oud", "عطر", "مسك", "عود"],
          ["amber", "cream", "عنبر", "كريمي"], ["soft", "arabic"], "عمّان",
          "مزيج ناعم من المسك والعود للاستخدام اليومي والمناسبات الصغيرة.",
          "https://images.unsplash.com/photo-1615634260167-c8cdede054de?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000022", "https://instagram.com/miskjordan"),
    Offer(23, "Velvet Edit", "فستان مخمل أسود طويل", "clothes", 52.0,
          ["dress", "velvet", "long", "black", "فستان", "مخمل", "أسود"],
          ["black", "أسود"], ["formal", "party", "luxury"], "عمّان",
          "فستان مخمل طويل بتفاصيل أنيقة للمناسبات الرسمية.",
          "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000023", "https://instagram.com/velveteditjo"),
    Offer(24, "Satin Story", "طقم ساتان أسود مريح وأنيق", "clothes", 33.0,
          ["satin", "set", "black", "modest", "ساتان", "طقم", "أسود", "محتشم"],
          ["black", "أسود"], ["modest", "minimal", "evening"], "عمّان",
          "طقم ساتان بلمسة فاخرة وقصة محتشمة سهلة التنسيق.",
          "https://images.unsplash.com/photo-1618220179428-22790b461013?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000024", "https://instagram.com/satinstoryjo"),
    Offer(25, "Golden Hour", "ساعة ذهبية بتصميم كلاسيكي صغير", "watches", 62.0,
          ["watch", "gold", "classic", "small", "ساعة", "ذهبي", "كلاسيك"],
          ["gold", "black", "ذهبي", "أسود"], ["classic", "elegant"], "عمّان",
          "ساعة ذهبية صغيرة بتصميم كلاسيكي للاستخدام اليومي.",
          "https://images.unsplash.com/photo-1508057198894-247b23fe5ade?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000025", "https://instagram.com/goldenhourjo"),
    Offer(26, "Rose & Oud", "مجموعة عطر عود وحجم سفر", "perfumes", 28.0,
          ["oud", "travel", "perfume", "عود", "سفر", "عطر"],
          ["brown", "gold", "بني", "ذهبي"], ["travel", "gift"], "إربد",
          "مجموعة عطر عود وحجم سفر داخل علبة أنيقة مناسبة كهدية.",
          "https://images.unsplash.com/photo-1595425970377-c9703cf48b6d?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000026", "https://instagram.com/roseandoudjo"),
    Offer(27, "Maison Gifts", "بوكس تخرج فضي فاخر", "gifts", 23.0,
          ["graduation", "gift", "silver", "بوكس", "تخرج", "هدية", "فضي"],
          ["silver", "white", "فضي", "أبيض"], ["graduation", "elegant"], "عمّان",
          "بوكس تخرج أنيق بتغليف فضي وبطاقة تهنئة قابلة للتخصيص.",
          "https://images.unsplash.com/photo-1549465220-1a8b9238cd48?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000027", "https://instagram.com/maisongiftsjo"),
    Offer(28, "Nour Accessories", "طقم فضي مع حجر أبيض", "gifts", 42.0,
          ["silver jewelry", "stone", "gift", "مجوهرات", "فضي", "حجر", "هدية"],
          ["silver", "white", "فضي", "أبيض"], ["classic", "luxury"], "السلط",
          "طقم فضي مع حجر أبيض بتصميم نظيف وراقي.",
          "https://images.unsplash.com/photo-1605100804763-247f67b3557e?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000028", "https://instagram.com/nouraccessoriesjo"),
)

CATEGORY_LABELS = {
    "makeup": "مكياج",
    "clothes": "ملابس",
    "gifts": "هدايا",
    "watches": "ساعات",
    "perfumes": "عطور",
}

CATEGORY_SYNONYMS: Dict[str, set[str]] = {
    "makeup": {
        "مكياج", "ميكب", "مكياج", "makeup", "cosmetics", "روج", "حومرة", "حمرة",
        "حمرا", "احمر شفاه", "أحمر شفاه", "ليبستك", "lipstick", "lip", "عيون", "بلشر",
    },
    "clothes": {
        "ملابس", "لبس", "لبسة", "فستان", "فساتين", "فستين", "فستانه", "عباية", "عبايات",
        "ساتان", "مخمل", "ثوب", "ستايل", "clothes", "dress", "abaya", "satin", "velvet",
    },
    "gifts": {
        "هدية", "هديه", "هدايا", "بوكس", "هدايا", "تخرج", "gift", "gifts", "جوهرة", "مجوهرات",
        "فضي", "فضة", "سوار", "عقد", "اقراط", "أقراط", "jewelry", "silver",
    },
    "watches": {
        "ساعة", "ساعات", "ساعه", "watch", "watches", "تايم", "كلاسيك", "كلاسيكية",
    },
    "perfumes": {
        "عطر", "عطور", "عطورات", "برفان", "برفانات", "دهن عود", "عود", "مسك", "perfume", "perfumes", "oud", "musk",
    },
}

COLOR_SYNONYMS: Dict[str, set[str]] = {
    "black": {"أسود", "اسود", "سوداء", "سوده", "اسود", "black", "noir"},
    "white": {"أبيض", "ابيض", "بيضاء", "white"},
    "red": {"أحمر", "احمر", "حمرا", "red"},
    "silver": {"فضي", "فضية", "فضه", "فضة", "silver"},
    "gold": {"ذهبي", "ذهب", "ذهبية", "gold", "golden"},
    "brown": {"بني", "بنية", "brown"},
    "cream": {"كريمي", "سكري", "cream", "off-white"},
    "grey": {"رمادي", "رصاصي", "grey", "gray"},
    "pink": {"زهري", "وردي", "pink"},
}

STYLE_SYNONYMS: Dict[str, set[str]] = {
    "luxury": {"فاخر", "فخم", "فخمه", "luxury", "راقي", "رقي"},
    "party": {"حفلة", "حفله", "سهرة", "سهره", "party", "night", "سهرة"},
    "modest": {"محتشم", "محتشمة", "محترم", "modest", "ساتر"},
    "classic": {"كلاسيك", "كلاسيكي", "كلاسيكية", "classic", "رسمي", "formal"},
    "minimal": {"بسيط", "ناعم", "مينيمال", "minimal"},
    "gift": {"هدية", "هديه", "هدية", "gift"},
}

STOPWORDS = {
    "بدي", "بديش", "بدي", "بدّي", "بدي اشي", "شي", "اشي", "شيء", "الي", "إلي", "لي",
    "مع", "بدون", "لون", "سعر", "ميزانية", "ميزانيتي", "حد", "اقصى", "أقصى", "لحد", "حدود",
    "تحت", "اقل", "أقل", "من", "الى", "إلى", "دينار", "ديناراً", "جني", "جنيه", "jod", "jd",
    "بال", "لل", "ال", "و", "يا", "شو", "ايش", "اي", "أي", "بس", "فقط", "لـ", "عن",
}


# ---------------------------------------------------------------------------
# JSON serialization guard — explicitly handles list/dict extraction values.
# ---------------------------------------------------------------------------

def serialize_json(value: Any) -> str:
    """Serialize list/dict/other structured extraction values for TEXT columns."""
    if value is None:
        return "[]"
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps([value], ensure_ascii=False, separators=(",", ":"))


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("ـ", "")
    text = re.sub(r"[ًٌٍَُِّّْـ]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي")
    text = text.replace("ؤ", "و").replace("ئ", "ي")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_arabic_digits(value: str) -> str:
    return str(value).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def safe_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool) or value is None:
            return None
        value = normalize_arabic_digits(str(value)).replace(",", ".")
        return float(value)
    except (TypeError, ValueError):
        return None


def tokens(text: str) -> List[str]:
    clean = normalize_text(text)
    raw = re.findall(r"[a-z0-9_+#.-]+|[\u0600-\u06ff]+", clean, flags=re.IGNORECASE)
    return [t for t in raw if t not in STOPWORDS]


def find_number(text: str) -> Optional[float]:
    clean = normalize_arabic_digits(text)
    patterns = (
        r"(?:تحت|اقل من|أقل من|لحد|حدود|حد|ميزانية|ميزانيتي|اقصى|أقصى|ما بتتعدى|ما يتجاوز|بحدود)\s*(?:هو|هي|ال)?\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*(?:دينار|دينارات|jod|jd|د\.ا|دج)",
        r"(?:بـ|ب|حدها|حده)\s*(\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            number = safe_float(match.group(1))
            if number is not None:
                return number
    # Last-resort numeric extraction only if a currency/budget cue exists.
    if re.search(r"دينار|jod|jd|ميزاني|سعر|تحت|اقل|لحد|حدود|\$", clean, re.I):
        match = re.search(r"\b(\d+(?:\.\d+)?)\b", clean)
        if match:
            return safe_float(match.group(1))
    return None


def best_terms_for_category(category: str) -> set[str]:
    terms = set(CATEGORY_SYNONYMS.get(category, set()))
    normalized: set[str] = set()
    for term in terms:
        normalized.add(normalize_text(term))
    return normalized


def detect_categories(text: str) -> List[str]:
    clean = normalize_text(text)
    toks = set(tokens(clean))
    scores = []
    for category, synonyms in CATEGORY_SYNONYMS.items():
        hits = 0
        for synonym in synonyms:
            s = normalize_text(synonym)
            if " " in s:
                if s in clean:
                    hits += 2
            elif s in toks or s in clean:
                hits += 1
        if hits:
            scores.append((hits, category))
    scores.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scores]


def detect_colors(text: str) -> List[str]:
    clean = normalize_text(text)
    found: List[str] = []
    for canonical, synonyms in COLOR_SYNONYMS.items():
        for synonym in synonyms:
            if normalize_text(synonym) in clean:
                found.append(canonical)
                break
    return found


def detect_styles(text: str) -> List[str]:
    clean = normalize_text(text)
    found: List[str] = []
    for canonical, synonyms in STYLE_SYNONYMS.items():
        for synonym in synonyms:
            if normalize_text(synonym) in clean:
                found.append(canonical)
                break
    return found


def detect_product_terms(text: str) -> List[str]:
    clean = normalize_text(text)
    # Budget/currency numbers are already extracted separately and should not
    # dilute semantic product matching as ordinary keywords.
    return [t for t in tokens(clean) if len(t) > 1 and not re.fullmatch(r'\d+(?:\.\d+)?', t)]


# ---------------------------------------------------------------------------
# Optional Gemini extraction
# ---------------------------------------------------------------------------

_GEMINI_MODEL_CACHE: Any = None


def gemini_model() -> Any:
    global _GEMINI_MODEL_CACHE
    if _GEMINI_MODEL_CACHE is not None:
        return _GEMINI_MODEL_CACHE
    if not genai or not GOOGLE_API_KEY:
        return None
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        _GEMINI_MODEL_CACHE = genai.GenerativeModel(GEMINI_MODEL)
        return _GEMINI_MODEL_CACHE
    except Exception as exc:
        LOGGER.warning("Gemini initialization skipped: %s", exc)
        return None


def extract_with_gemini(user_text: str) -> Dict[str, Any]:
    model = gemini_model()
    if model is None:
        return {}

    prompt = f"""
أنت محلل نوايا شراء للسوق الأردني. حلّل النص العربي باللهجة الأردنية/الشامية.
أعد JSON فقط بدون markdown وبالمفاتيح:
category: واحدة أو أكثر من makeup, clothes, gifts, watches, perfumes
price_cap_jod: رقم أو null
colors: قائمة من canonical English colors
styles: قائمة من luxury, party, modest, classic, minimal, gift
product_terms: قائمة بكلمات المنتج المهمة كما يفهمها المتسوق

النص: {user_text!r}
"""
    try:
        response = model.generate_content(prompt)
        raw = getattr(response, "text", "") or ""
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except Exception as exc:
        LOGGER.warning("Gemini extraction failed; using local parser: %s", exc)
        return {}


def merge_extraction(local: Dict[str, Any], ai: Dict[str, Any]) -> Dict[str, Any]:
    categories: List[str] = []
    for source in (local.get("categories", []), ai.get("category", []), ai.get("categories", [])):
        if isinstance(source, str):
            source = [source]
        if isinstance(source, (list, tuple)):
            for item in source:
                if item in CATEGORY_LABELS and item not in categories:
                    categories.append(item)

    colors = list(dict.fromkeys(
        [*(local.get("colors", [])), *((ai.get("colors", []) if isinstance(ai.get("colors", []), list) else []))]
    ))
    styles = list(dict.fromkeys(
        [*(local.get("styles", [])), *((ai.get("styles", []) if isinstance(ai.get("styles", []), list) else []))]
    ))
    terms = list(dict.fromkeys(
        [*(local.get("product_terms", [])), *((ai.get("product_terms", []) if isinstance(ai.get("product_terms", []), list) else []))]
    ))

    price_cap = local.get("price_cap_jod")
    ai_cap = safe_float(ai.get("price_cap_jod"))
    if price_cap is None and ai_cap is not None:
        price_cap = ai_cap

    return {
        "categories": categories,
        "price_cap_jod": price_cap,
        "colors": colors,
        "styles": styles,
        "product_terms": terms,
        "raw_ai": ai if isinstance(ai, dict) else {},
    }


def extract_intent(user_text: str, use_ai: bool = True) -> Dict[str, Any]:
    local = {
        "categories": detect_categories(user_text),
        "price_cap_jod": find_number(user_text),
        "colors": detect_colors(user_text),
        "styles": detect_styles(user_text),
        "product_terms": detect_product_terms(user_text),
    }
    ai = extract_with_gemini(user_text) if use_ai else {}
    return merge_extraction(local, ai)


# ---------------------------------------------------------------------------
# SQLite schema and repository
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Drop the legacy/conflicting file and build a clean schema every launch."""
    with DB_LOCK:
        if DB_PATH.exists():
            LOGGER.warning("Removing legacy database: %s", DB_PATH)
            try:
                DB_PATH.unlink()
            except OSError as exc:
                LOGGER.error("Could not remove old DB: %s", exc)
                raise

        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(
                """
                CREATE TABLE buyer_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    raw_query TEXT NOT NULL,
                    categories TEXT NOT NULL,
                    colors TEXT NOT NULL,
                    styles TEXT NOT NULL,
                    product_terms TEXT NOT NULL,
                    price_cap_jod REAL,
                    ai_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE merchant_offers (
                    id INTEGER PRIMARY KEY,
                    merchant_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price_jod REAL NOT NULL CHECK(price_jod >= 0),
                    tags TEXT NOT NULL,
                    colors TEXT NOT NULL,
                    style TEXT NOT NULL,
                    city TEXT NOT NULL,
                    description TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    whatsapp_url TEXT NOT NULL,
                    instagram_url TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX idx_offers_category ON merchant_offers(category);
                CREATE INDEX idx_offers_price ON merchant_offers(price_jod);
                """
            )

            rows = []
            for offer in SEED_OFFERS:
                rows.append((
                    offer.id,
                    offer.merchant_name,
                    offer.title,
                    offer.category,
                    offer.price_jod,
                    serialize_json(offer.tags),
                    serialize_json(offer.colors),
                    serialize_json(offer.style),
                    offer.city,
                    offer.description,
                    offer.image_url,
                    offer.whatsapp_url,
                    offer.instagram_url,
                ))
            conn.executemany(
                """
                INSERT INTO merchant_offers
                (id, merchant_name, title, category, price_jod, tags, colors, style, city,
                 description, image_url, whatsapp_url, instagram_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_buyer_intent(request_id: str, raw_query: str, intent: Dict[str, Any]) -> None:
    """Persist all structured fields as TEXT-safe JSON to avoid list binding crashes."""
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO buyer_intents
                (request_id, raw_query, categories, colors, styles, product_terms, price_cap_jod, ai_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    raw_query,
                    serialize_json(intent.get("categories", [])),
                    serialize_json(intent.get("colors", [])),
                    serialize_json(intent.get("styles", [])),
                    serialize_json(intent.get("product_terms", [])),
                    safe_float(intent.get("price_cap_jod")),
                    serialize_json(intent.get("raw_ai", {})),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def read_json_list(raw: Any) -> List[str]:
    try:
        parsed = json.loads(raw or "[]")
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        if isinstance(parsed, dict):
            return [str(x) for x in parsed.keys()]
        return [str(parsed)]
    except Exception:
        return []


def get_offers() -> List[Offer]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM merchant_offers ORDER BY id ASC").fetchall()
        offers = []
        for row in rows:
            offers.append(Offer(
                id=row["id"],
                merchant_name=row["merchant_name"],
                title=row["title"],
                category=row["category"],
                price_jod=row["price_jod"],
                tags=read_json_list(row["tags"]),
                colors=read_json_list(row["colors"]),
                style=read_json_list(row["style"]),
                city=row["city"],
                description=row["description"],
                image_url=row["image_url"],
                whatsapp_url=row["whatsapp_url"],
                instagram_url=row["instagram_url"],
            ))
        return offers
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Semantic ranking
# ---------------------------------------------------------------------------

def normalized_set(values: Iterable[str]) -> set[str]:
    return {normalize_text(v) for v in values if v}


def offer_search_corpus(offer: Offer) -> set[str]:
    return normalized_set([
        offer.title,
        offer.category,
        offer.merchant_name,
        offer.description,
        *offer.tags,
        *offer.colors,
        *offer.style,
    ])


def overlap_token_score(query_tokens: set[str], offer: Offer) -> float:
    if not query_tokens:
        return 0.0
    corpus_tokens: set[str] = set()
    for value in [offer.title, offer.description, *offer.tags, *offer.colors, *offer.style]:
        corpus_tokens.update(tokens(value))
    overlap = query_tokens & corpus_tokens
    return min(1.0, len(overlap) / max(1, min(4, len(query_tokens))))


def score_offer(offer: Offer, intent: Dict[str, Any]) -> float:
    categories = set(intent.get("categories", []))
    colors = set(intent.get("colors", []))
    styles = set(intent.get("styles", []))
    product_terms = normalized_set(intent.get("product_terms", []))
    price_cap = safe_float(intent.get("price_cap_jod"))

    if price_cap is not None and offer.price_jod > price_cap:
        return -1.0

    score = 0.0
    # Category is the strongest signal.
    if categories:
        if offer.category in categories:
            score += 45.0
        else:
            # A specific category request should not leak unrelated inventory.
            return -1.0
    else:
        score += 20.0

    if colors:
        color_matches = len(set(offer.colors) & colors)
        if color_matches:
            score += min(20.0, color_matches * 20.0)
        else:
            score -= 8.0

    if styles:
        style_matches = len(set(offer.style) & styles)
        score += min(12.0, style_matches * 6.0)

    score += 18.0 * overlap_token_score(product_terms, offer)

    # Exact multiword / tag phrase matches provide additional relevance.
    clean_title = normalize_text(offer.title)
    clean_desc = normalize_text(offer.description)
    for term in product_terms:
        if len(term) >= 3 and term in clean_title:
            score += 4.0
        elif len(term) >= 3 and term in clean_desc:
            score += 1.5

    # Lightweight price fit within budget: cheaper items do not automatically win,
    # but near-budget products receive a small fit bonus.
    if price_cap and price_cap > 0:
        fit = max(0.0, min(1.0, offer.price_jod / price_cap))
        score += 3.0 * fit

    return max(0.0, min(100.0, round(score, 2)))


def recommend(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for offer in get_offers():
        score = score_offer(offer, intent)
        if score < 0:
            continue
        payload = asdict(offer)
        payload["category_label"] = CATEGORY_LABELS.get(offer.category, offer.category)
        payload["score"] = score
        results.append(payload)

    # No artificial [:3] / [:N] cap: ALL valid inventory matches are retained.
    results.sort(key=lambda item: (-item["score"], item["price_jod"], item["id"]))
    return results


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r'''<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#FAFAFA" />
  <meta name="description" content="التوصية — محرك توصية يفهم اللهجة الأردنية ويجمع العروض المطابقة." />
  <title>التوصية — محرك التوصية الأردني</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800;900&display=swap" rel="stylesheet" />
  <style>
    :root { color-scheme: light; }
    html, body { min-height: 100%; }
    body { font-family: 'Tajawal', system-ui, sans-serif; background: #FAFAFA; }
    .glass { background: rgba(255,255,255,.84); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
    .soft-shadow { box-shadow: 0 1px 0 rgba(17,24,39,.04), 0 16px 50px rgba(17,24,39,.055); }
    .hide-links a { filter: blur(6px); pointer-events: none; user-select: none; }
    .hide-links::after {
      content: '🔒 التواصل مع التاجر يظهر بعد تأكيد قراءة تعليمات دفع 1 دينار عبر CliQ';
      position: absolute; inset: 0; display:flex; align-items:center; justify-content:center;
      padding: 1rem; border-radius: 1.25rem; background: rgba(255,255,255,.86);
      color: #171717; font-size:.78rem; font-weight:800; text-align:center;
      border: 1px solid rgba(0,0,0,.05); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
    }
    .scrollbar-none::-webkit-scrollbar { display:none; }
    .scrollbar-none { scrollbar-width:none; }
    .safe-bottom { padding-bottom: max(1rem, env(safe-area-inset-bottom)); }
    @keyframes pulse-soft { 0%,100% { opacity:.55; } 50% { opacity:1; } }
    .pulse-soft { animation: pulse-soft 1.4s ease-in-out infinite; }
  </style>
</head>
<body class="text-neutral-900 tracking-tight">
  <div class="min-h-screen flex flex-col">
    <header class="sticky top-0 z-40 border-b border-neutral-100 glass">
      <div class="mx-auto max-w-3xl px-4 sm:px-6 py-3.5 sm:py-4 flex items-center justify-between gap-3">
        <div class="flex items-center gap-3 min-w-0">
          <div class="h-11 w-11 shrink-0 rounded-2xl bg-neutral-950 text-white grid place-items-center text-lg font-black">ت</div>
          <div class="min-w-0">
            <div class="text-[11px] text-neutral-400 font-bold">محرك التوصية الأردني</div>
            <h1 class="text-lg sm:text-xl font-black truncate">التوصية الذكية</h1>
          </div>
        </div>
        <span class="hidden xs:inline-flex sm:inline-flex rounded-full bg-neutral-950 text-white px-3 py-1.5 text-[10px] font-black tracking-widest">VIP LIVE FEED</span>
      </div>
    </header>

    <main class="flex-1 mx-auto max-w-3xl w-full px-4 sm:px-6 pt-6 sm:pt-10 pb-24">
      <section class="text-center mb-8 sm:mb-10">
        <div class="inline-flex items-center gap-2 rounded-full bg-white border border-neutral-100 px-3 py-1.5 text-[11px] font-bold text-neutral-500 shadow-sm mb-4">
          <span>✦</span><span>اكتبي طلبك بحرية</span><span>•</span><span>لهجة أردنية / شامية</span>
        </div>
        <h2 class="text-3xl sm:text-4xl font-black leading-[1.25] text-neutral-900">
          تعبتِ من اللف والدوران؟<br />
          <span class="text-neutral-400 font-medium text-xl sm:text-2xl">اكتبي شو بدك… والباقي علينا.</span>
        </h2>
        <p class="mt-4 text-sm text-neutral-500 max-w-xl mx-auto leading-7">
          فضفضي بأي صياغة طبيعية. النظام بفهم النية، النوع، اللون، والأسعار بالدينار الأردني وبعرض كل النتائج المطابقة بدون عدد محدود.
        </p>
      </section>

      <section class="bg-white p-4 sm:p-5 rounded-[2rem] sm:rounded-[2.5rem] border border-neutral-200/70 soft-shadow relative overflow-hidden">
        <form id="searchForm" class="space-y-3">
          <div class="rounded-3xl bg-neutral-50 border border-neutral-100 p-2">
            <textarea id="query" rows="3" required maxlength="2000"
              placeholder="مثلاً: بدي حومرة لون اسود تحت 12 دينار… أو فستان مخمل أسود لحفلة لحد 40 دينار"
              class="w-full bg-transparent px-4 py-3.5 outline-none text-sm sm:text-base font-medium text-neutral-800 resize-none placeholder:text-neutral-300 leading-7"></textarea>
          </div>
          <div class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
            <div class="text-[11px] text-neutral-400 font-medium bg-neutral-50 border border-neutral-100 px-3 py-2.5 rounded-2xl leading-5">
              💡 ما في كلمات مفتاحية إجبارية — اكتبي زي ما بتحكي مع صاحبتك.
            </div>
            <button id="searchBtn" type="submit" class="w-full sm:w-auto min-h-[54px] bg-neutral-950 hover:bg-neutral-800 text-white font-black text-sm px-8 rounded-2xl transition active:scale-[0.985]">
              أرسلي الطلب ✦
            </button>
          </div>
        </form>
        <div id="intentPills" class="mt-4 flex flex-wrap gap-1.5 pt-3 border-t border-neutral-50"></div>
      </section>

      <section class="mt-10 sm:mt-12 text-right">
        <div class="flex items-center justify-between gap-3 mb-5">
          <div>
            <div class="text-[10px] font-bold text-neutral-400 uppercase tracking-widest">LIVE MATCHES</div>
            <h3 id="resultsTitle" class="mt-1 text-lg font-black text-neutral-900">اكتبي طلبكِ لنبدأ الفرز</h3>
          </div>
          <span id="countBadge" class="hidden shrink-0 rounded-full bg-white border border-neutral-100 px-3 py-1.5 text-xs font-black text-neutral-500 shadow-sm"></span>
        </div>

        <div id="loading" class="hidden rounded-3xl bg-white border border-neutral-100 p-10 sm:p-12 text-center text-sm font-bold text-neutral-400 soft-shadow">
          <div class="text-2xl mb-3 pulse-soft">✦</div>
          جاري تحليل نيتكِ الشرائية وجمع كل العروض المطابقة…
        </div>

        <div id="empty" class="rounded-3xl bg-white border border-neutral-100 p-10 sm:p-12 text-center soft-shadow">
          <div class="text-3xl mb-3 text-neutral-300">✦</div>
          <div class="font-black text-neutral-700">لا توجد عروض معروضة حالياً</div>
          <div class="text-xs text-neutral-400 mt-1 leading-6">ابدئي بكتابة طلبك بالأعلى لرؤية العروض المطابقة.</div>
        </div>

        <div id="grid" class="grid grid-cols-1 sm:grid-cols-2 gap-5 mt-5"></div>
      </section>
    </main>

    <div id="dealModal" class="hidden fixed inset-0 z-50 bg-black/60 backdrop-blur-sm p-4 flex items-center justify-center">
      <div class="w-full max-w-md rounded-[2rem] bg-white p-5 sm:p-6 shadow-2xl border border-neutral-100">
        <div class="flex items-start justify-between gap-3 border-b border-neutral-100 pb-4">
          <div>
            <div class="text-[10px] text-neutral-400 font-black uppercase tracking-widest">DEAL ACCESS</div>
            <h4 class="text-lg font-black mt-1">افتحي الصفقة بقيمة 1 دينار</h4>
          </div>
          <button type="button" onclick="closeDealModal()" class="h-10 w-10 rounded-full bg-neutral-50 hover:bg-neutral-100 font-black text-neutral-500 flex items-center justify-center">×</button>
        </div>

        <div class="mt-5 rounded-3xl bg-neutral-50 border border-neutral-100 p-4 sm:p-5 text-right">
          <div class="font-black text-neutral-900 flex items-center gap-2">⚡ دفع CliQ محلي</div>
          <p class="mt-2 text-xs text-neutral-500 leading-6">
            لإظهار روابط التواصل المباشرة للمتجر، استخدمي الدفع المحلي بقيمة <span class="font-black text-neutral-900">1 دينار أردني فقط</span> عبر نظام CliQ.
          </p>
          <div class="mt-4 space-y-2">
            <div class="rounded-2xl bg-white border border-neutral-100 p-3 flex items-center justify-between gap-3">
              <span class="text-[11px] text-neutral-400 font-bold">البنك المستهدف</span>
              <span class="text-sm font-black text-neutral-900">البنك العربي (Arab Bank)</span>
            </div>
            <div class="rounded-2xl bg-white border border-neutral-100 p-3 flex items-center justify-between gap-3">
              <span class="text-[11px] text-neutral-400 font-bold">Alias ID / Username</span>
              <span class="font-mono font-black text-neutral-900 tracking-wider">MQRB</span>
            </div>
          </div>
        </div>

        <label class="mt-4 p-3.5 rounded-2xl bg-neutral-50/70 border border-neutral-100 flex items-start gap-3 cursor-pointer select-none">
          <input id="paidCheck" type="checkbox" class="mt-1 h-5 w-5 rounded border-neutral-300 accent-neutral-900" />
          <span class="text-xs text-neutral-600 font-bold leading-6">أؤكد أنني قرأت تعليمات الدفع اليدوي بقيمة 1 دينار عبر CliQ إلى البنك العربي باستخدام <span class="font-black">MQRB</span>.</span>
        </label>

        <button type="button" onclick="confirmDeal()" class="mt-4 w-full min-h-[56px] rounded-3xl bg-neutral-950 hover:bg-neutral-800 text-white font-black text-sm transition active:scale-[0.99]">
          أؤكد التعليمات وأظهر التواصل ✦
        </button>
        <div class="mt-3 text-center text-[10px] text-neutral-400 leading-5">هذه الشاشة لا تتحقق من التحويل البنكي تلقائياً؛ التأكيد هنا يحرر الروابط في الواجهة فقط.</div>
      </div>
    </div>
  </div>

<script>
  const form = document.getElementById('searchForm');
  const queryEl = document.getElementById('query');
  const searchBtn = document.getElementById('searchBtn');
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  const loading = document.getElementById('loading');
  const resultsTitle = document.getElementById('resultsTitle');
  const countBadge = document.getElementById('countBadge');
  const intentPills = document.getElementById('intentPills');
  const dealModal = document.getElementById('dealModal');
  let activeCard = null;

  const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const money = (v) => {
    const n = Number(v);
    return `${n.toFixed(Number.isInteger(n) ? 0 : 2)} د.أ`;
  };
  const categoryNames = {makeup:'مكياج', clothes:'ملابس', gifts:'هدايا', watches:'ساعات', perfumes:'عطور'};

  function renderIntent(intent) {
    const items = [];
    (intent.categories || []).forEach(c => items.push(`<span class="rounded-xl bg-neutral-950 text-white px-3 py-1.5 text-xs font-black">${esc(categoryNames[c] || c)}</span>`));
    (intent.colors || []).forEach(c => items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-bold text-neutral-600">🎨 ${esc(c)}</span>`));
    (intent.styles || []).forEach(c => items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-bold text-neutral-600">✦ ${esc(c)}</span>`));
    if (intent.price_cap_jod !== null && intent.price_cap_jod !== undefined) {
      items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">💰 حتى ${esc(money(intent.price_cap_jod))}</span>`);
    }
    intentPills.innerHTML = items.join('');
  }

  function productCard(item, index) {
    const score = Math.round(Number(item.score || 0));
    const tags = (item.tags || []).slice(0, 5).map(tag => `<span class="rounded-full bg-neutral-100 px-2 py-1 text-[10px] font-bold text-neutral-500">${esc(tag)}</span>`).join('');
    return `
      <article class="group bg-white rounded-[2rem] border border-neutral-200/70 overflow-hidden soft-shadow flex flex-col">
        <div class="relative aspect-[4/5] overflow-hidden bg-neutral-100">
          <img src="${esc(item.image_url)}" loading="lazy" referrerpolicy="no-referrer" class="h-full w-full object-cover transition duration-700 group-hover:scale-[1.03]" alt="${esc(item.title)}" onerror="this.style.opacity='.18'" />
          <div class="absolute inset-x-3 top-3 flex items-start justify-between gap-2">
            <span class="rounded-full bg-white/90 backdrop-blur px-3 py-1.5 text-[10px] font-black shadow-sm">${score}% توافق</span>
            <span class="rounded-full bg-black/65 text-white backdrop-blur px-3 py-1.5 text-[10px] font-bold">${esc(item.match_type || 'توصية')}</span>
          </div>
        </div>
        <div class="p-4 sm:p-5 flex-1 flex flex-col">
          <div class="flex items-center justify-between gap-2">
            <span class="text-[11px] text-neutral-400 font-black">${esc(categoryNames[item.category] || item.category)}</span>
            <span class="text-[11px] text-neutral-400 font-bold">${esc(item.city)}</span>
          </div>
          <h4 class="mt-2 text-sm sm:text-base font-black leading-6">${esc(item.title)}</h4>
          <div class="mt-1 text-xs text-neutral-400 font-bold">${esc(item.merchant_name)}</div>
          <p class="mt-2 text-xs sm:text-sm text-neutral-500 leading-6">${esc(item.description)}</p>
          <div class="mt-3 flex flex-wrap gap-1.5">${tags}</div>
          <div class="mt-auto pt-4 flex items-end justify-between gap-3">
            <div>
              <div class="text-[10px] text-neutral-400 font-bold">السعر</div>
              <div class="text-lg font-black">${esc(money(item.price_jod))}</div>
            </div>
            <button type="button" onclick='openDeal(${JSON.stringify(item)})' class="min-h-[48px] px-4 rounded-2xl bg-neutral-950 hover:bg-neutral-800 text-white font-black text-xs sm:text-sm transition active:scale-[0.985]">
              افتحي الصفقة <span class="opacity-60">(1 دينار)</span>
            </button>
          </div>
          <div class="contact-zone relative mt-4 p-2 rounded-2xl border border-neutral-100 hide-links" data-card="${index}">
            <div class="flex gap-2">
              <a href="${esc(item.whatsapp_url)}" target="_blank" rel="noopener noreferrer" class="flex-1 text-center rounded-xl bg-emerald-50 text-emerald-700 py-3 text-xs font-black">واتساب المتجر</a>
              <a href="${esc(item.instagram_url)}" target="_blank" rel="noopener noreferrer" class="flex-1 text-center rounded-xl bg-pink-50 text-pink-700 py-3 text-xs font-black">إنستغرام</a>
            </div>
          </div>
        </div>
      </article>`;
  }

  function openDeal(item) {
    activeCard = item;
    document.getElementById('paidCheck').checked = false;
    dealModal.classList.remove('hidden');
    document.body.classList.add('overflow-hidden');
  }

  function closeDealModal() {
    dealModal.classList.add('hidden');
    document.body.classList.remove('overflow-hidden');
  }

  dealModal.addEventListener('click', (event) => {
    if (event.target === dealModal) closeDealModal();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !dealModal.classList.contains('hidden')) closeDealModal();
  });

  function confirmDeal() {
    if (!document.getElementById('paidCheck').checked) {
      alert('الرجاء تأكيد قراءة تعليمات تحويل 1 دينار عبر CliQ إلى البنك العربي — MQRB أولاً.');
      return;
    }
    closeDealModal();
    document.querySelectorAll('.contact-zone').forEach(el => el.classList.remove('hide-links'));
    if (activeCard) {
      alert(`تم تحرير روابط التواصل لمنتج: ${activeCard.title}`);
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const query = queryEl.value.trim();
    if (!query) return;

    empty.classList.add('hidden');
    loading.classList.remove('hidden');
    grid.innerHTML = '';
    countBadge.classList.add('hidden');
    resultsTitle.textContent = 'جاري تحليل نيتكِ الشرائية…';
    searchBtn.disabled = true;
    searchBtn.classList.add('opacity-60', 'cursor-wait');

    try {
      const response = await fetch('/api/recommend', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({query})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'تعذر إتمام البحث');

      renderIntent(data.intent);
      const count = data.results.length;
      resultsTitle.textContent = count ? 'كل العروض التنافسية المطابقة' : 'لم نجد عروضًا مطابقة لهذه الشروط';
      countBadge.textContent = `${count} ${count === 1 ? 'عرض' : 'عروض'}`;
      countBadge.classList.remove('hidden');
      grid.innerHTML = data.results.map(productCard).join('');
      loading.classList.add('hidden');
      if (!count) empty.classList.remove('hidden');
      if (count) window.scrollTo({top: document.getElementById('grid').offsetTop - 90, behavior: 'smooth'});
    } catch (error) {
      loading.classList.add('hidden');
      empty.classList.remove('hidden');
      resultsTitle.textContent = 'صار خطأ في البحث';
      empty.innerHTML = `<div class="text-4xl mb-3">!</div><div class="font-black">${esc(error.message)}</div><div class="text-sm text-neutral-500 mt-2">جربي صياغة أخرى للطلب.</div>`;
    } finally {
      searchBtn.disabled = false;
      searchBtn.classList.remove('opacity-60', 'cursor-wait');
    }
  });

  queryEl.focus();
</script>
</body>
</html>'''

# ---------------------------------------------------------------------------
# HTTP application
# ---------------------------------------------------------------------------


def json_response(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, content: str, status: int = 200) -> None:
    body = content.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0") or 0)
    if content_length <= 0:
        return {}
    if content_length > 128 * 1024:
        raise ValueError("حجم الطلب كبير جدًا")
    raw = handler.rfile.read(content_length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("JSON غير صالح") from exc
    if not isinstance(data, dict):
        raise ValueError("تنسيق الطلب غير صالح")
    return data


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AlTawseya/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _set_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = HTML_TEMPLATE.replace("__PAYMENT_BANK_AR__", html.escape(PAYMENT_BANK_AR))
            html_response(self, body)
            return
        if path == "/api/health":
            json_response(self, {
                "ok": True,
                "service": "التوصية",
                "port": PORT,
                "inventory_count": len(SEED_OFFERS),
                "gemini_enabled": bool(gemini_model()),
                "timestamp": int(time.time()),
            })
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/recommend":
            self.send_error(404, "Not Found")
            return
        try:
            data = read_json_body(self)
            query = str(data.get("query", "")).strip()
            if not query:
                json_response(self, {"error": "اكتبي طلب الشراء أولًا."}, 400)
                return
            if len(query) > 2000:
                json_response(self, {"error": "النص طويل جدًا. اكتبي الطلب بجملة مختصرة وواضحة."}, 400)
                return

            request_id = uuid.uuid4().hex
            intent = extract_intent(query, use_ai=True)
            save_buyer_intent(request_id, query, intent)
            results = recommend(intent)
            json_response(self, {
                "ok": True,
                "request_id": request_id,
                "intent": {
                    "categories": intent["categories"],
                    "price_cap_jod": intent["price_cap_jod"],
                    "colors": intent["colors"],
                    "styles": intent["styles"],
                    "product_terms": intent["product_terms"],
                },
                "results": results,
                "payment": {
                    "deal_price_jod": DEAL_PRICE_JOD,
                    "system": "CliQ",
                    "target_bank": PAYMENT_BANK_AR,
                    "alias": PAYMENT_ALIAS,
                },
            })
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            LOGGER.error("Request failed: %s\n%s", exc, traceback.format_exc())
            json_response(self, {"error": "حدث خطأ داخلي غير متوقع."}, 500)


def main() -> None:
    init_db()
   if __name__ == "__main__":
    # جلب المنفذ الصحيح تلقائياً من منصة Render السحابية
    import os
    port = int(os.environ.get("PORT", 10000))
    
    # تشغيل السيرفر بالإعدادات المتوافقة مع المنصة
    server = HTTPServer(("0.0.0.0", port), AppHandler)
    print(f"🚀 Live Server Running Perfectly on Port {port}")
    
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
