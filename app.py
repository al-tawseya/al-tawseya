from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import csv
import threading
import time
import traceback
import urllib.request
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse, urljoin

try:
    from google import genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore
except Exception:  # Optional dependency.
    genai = None
    genai_types = None


HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DB_PATH = Path(os.getenv("DATABASE_FILE", "decision_engine.db"))
if not DB_PATH.is_absolute():
    DB_PATH = Path(__file__).with_name(DB_PATH.name)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("al-tawseya")
DB_LOCK = threading.RLock()

PAYMENT_BANK = "Arab Bank"
PAYMENT_BANK_AR = "البنك العربي"
PAYMENT_ALIAS = os.getenv("PAYMENT_ALIAS", "MQRB").strip()
DEAL_PRICE_JOD = float(os.getenv("DEAL_PRICE_JOD", "1"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "").strip()
AUTH_DAYS = int(os.getenv("AUTH_DAYS", "30"))
LIVE_SEARCH_ENABLED = os.getenv("LIVE_SEARCH_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
LIVE_SEARCH_MAX = max(4, min(10, int(os.getenv("LIVE_SEARCH_MAX", "8"))))


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
    sizes: List[str] = field(default_factory=list)
    size_system: str = ""  # bra | shoe_eu | shoe_us | clothing_letter | scarf_dimensions | ""
    store_url: str = ""


SEED_OFFERS: Sequence[Offer] = (
    Offer(1, "Luxe Amman", "أحمر شفاه مات أسود Velvet Noir", "makeup", 9.0,
    ["lipstick", "matte", "black", "velvet", "حومرة", "روج"],
    ["black", "أسود"], ["luxury", "evening"], "عمّان",
    "أحمر شفاه مات بدرجة سوداء عميقة وثبات طويل للمناسبات والتنسيقات الجريئة.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(2, "Nude House JO", "روج سائل أسود Black Ink", "makeup", 7.5,
    ["liquid lipstick", "lip", "black", "حومرة", "روج", "أسود"],
    ["black", "أسود"], ["minimal", "bold"], "عمّان",
    "روج سائل بتركيبة مرنة ولمعة مطفّية ناعمة.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(3, "Amman Beauty Lab", "روج مخملي أسود مع محدد شفاه", "makeup", 12.0,
    ["lipstick", "liner", "black", "makeup", "حومرة", "مكياج"],
    ["black", "أسود"], ["professional", "night"], "عمّان",
    "ثنائية روج ومحدد بدرجة أسود كلاسيكية لإطلالة مسائية متكاملة.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(4, "The Makeup Room", "باقة مكياج العيون Black Smoke", "makeup", 18.0,
    ["eyeshadow", "smokey", "black", "makeup", "مكياج"],
    ["black", "grey", "أسود", "رمادي"], ["party", "night"], "الزرقاء",
    "لوحة ظلال دخانية بدرجات سوداء ورمادية مناسبة للسهرات.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(5, "Maison 7", "فستان مخمل أسود للحفلات", "clothes", 34.0,
    ["dress", "party", "black", "velvet", "فستان", "حفلة", "أسود"],
    ["black", "أسود"], ["party", "velvet", "elegant"], "عمّان",
    "فستان سهرة مخمل أسود بقصة أنيقة ومناسبة للحفلات والمناسبات.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com",
    ["S", "M", "L"], "clothing_letter"),
    Offer(6, "Luna Amman", "فستان ساتان أسود محتشم", "clothes", 39.0,
    ["dress", "satin", "modest", "black", "فستان", "ساتان", "محتشم"],
    ["black", "أسود"], ["modest", "satin", "formal"], "عمّان",
    "فستان ساتان انسيابي بتفاصيل محتشمة وتصميم مناسب للعشاء والمناسبات.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com",
    ["M", "L", "XL"], "clothing_letter"),
    Offer(7, "Silk Avenue", "طقم ساتان محتشم لسهرة هادئة", "clothes", 29.0,
    ["satin set", "modest", "black", "robe", "طقم", "ساتان", "محتشم"],
    ["black", "أسود"], ["modest", "lounge", "evening"], "إربد",
    "طقم ساتان مريح وأنيق بقصة هادئة وألوان حيادية.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com",
    ["S", "M", "L", "XL"], "clothing_letter"),
    Offer(8, "Noir Closet", "فستان أسود بسيط بقصة مستقيمة", "clothes", 24.0,
    ["dress", "black", "basic", "minimal", "فستان", "أسود"],
    ["black", "أسود"], ["minimal", "day", "night"], "عمّان",
    "فستان أسود عملي يمكن تنسيقه للدوام أو المناسبات الخفيفة.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com",
    ["S", "M", "L", "XL"], "clothing_letter"),
    Offer(9, "Wardrobe JO", "عباية ساتان سوداء بلمعة ناعمة", "clothes", 46.0,
    ["abaya", "satin", "modest", "black", "عباية", "ساتان", "أسود"],
    ["black", "أسود"], ["modest", "luxury"], "السلط",
    "عباية ساتان سوداء بتفصيل انسيابي وخياطة نظيفة.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com",
    ["M", "L", "XL"], "clothing_letter"),
    Offer(10, "Gift District", "بوكس هدية أسود فاخر مع ورد مجفف", "gifts", 22.0,
    ["gift box", "black", "flowers", "هدية", "بوكس", "ورد", "أسود"],
    ["black", "cream", "أسود", "كريمي"], ["luxury", "romantic"], "عمّان",
    "بوكس هدية جاهز مع تغليف فاخر وورد مجفف وبطاقة صغيرة.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(11, "Amman Gifting Co.", "طقم هدية فضي أنيق", "gifts", 26.0,
    ["gift", "silver", "set", "هدية", "فضي", "طقم"],
    ["silver", "فضي", "white", "أبيض"], ["elegant", "classic"], "عمّان",
    "مجموعة هدايا أنيقة بتغليف فضي تصلح للتخرج والمناسبات.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(12, "Little Luxe", "سوار هدية مع علبة سوداء", "gifts", 14.0,
    ["gift", "bracelet", "black box", "هدية", "سوار", "علبة"],
    ["gold", "black", "ذهبي", "أسود"], ["classic", "gift"], "إربد",
    "سوار بسيط داخل علبة سوداء فاخرة وجاهز للإهداء.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(13, "Jabal Amman Watches", "ساعة كلاسيكية ذهبية بسوار معدني", "watches", 69.0,
    ["watch", "gold", "classic", "gold watch", "ساعة", "ذهبي"],
    ["gold", "black", "ذهبي", "أسود"], ["classic", "formal"], "عمّان",
    "ساعة كلاسيكية بلمسة ذهبية مناسبة للهدية والمظهر الرسمي.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(14, "Time House", "ساعة سوداء Minimal Dial", "watches", 55.0,
    ["watch", "black", "minimal", "ساعة", "أسود", "كلاسيك"],
    ["black", "silver", "أسود", "فضي"], ["minimal", "classic"], "عمّان",
    "قرص أسود بسيط بتفاصيل فضية يناسب اللبس اليومي والرسمي.",
    "https://unsplash.com",
    "https://wa.me", "https://instagram.com"),
    Offer(15, "Irbid Time", "ساعة جلد بني كلاسيكية", "watches", 49.0,
    ["watch", "brown", "leather", "classic", "ساعة", "جلد", "بني"],
    ["brown", "silver", "بني", "فضي"], ["classic", "casual"], "إربد",
    "ساعة جلدية كلاسيكية مريحة للإطلالات اليومية.",


    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(16, "Oud Amman", "عطر عود فاخر Royal Oud", "perfumes", 58.0,
    ["perfume", "oud", "luxury", "عطر", "عود", "فاخر"],
    ["amber", "black", "عنبر", "أسود"], ["luxury", "night"], "عمّان",
    "تركيبة عود شرقية دافئة بلمسات عنبر وورد مناسبة للمساء.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(17, "Scent 7", "دهن عود مركز 12 مل", "perfumes", 32.0,
    ["oud oil", "oud", "perfume", "دهن عود", "عطر", "عود"],
    ["amber", "brown", "عنبر", "بني"], ["arabic", "luxury"], "عمّان",
    "دهن عود مركز بحجم عملي وثبات واضح للتنسيق اليومي والمناسبات.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(18, "Layali Perfumes", "عطر شرقي أسود Midnight", "perfumes", 44.0,
    ["perfume", "black", "oriental", "عطر", "شرقي", "أسود"],
    ["black", "amber", "أسود", "عنبر"], ["night", "oriental"], "الزرقاء",
    "عطر شرقي داكن بطابع مسائي وقارورة سوداء مطفّية.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(19, "Silver Line JO", "طقم مجوهرات فضي لامع", "gifts", 37.0,
    ["silver jewelry", "set", "gift", "مجوهرات", "فضي", "هدية", "طقم"],
    ["silver", "white", "فضي", "أبيض"], ["classic", "gift"], "عمّان",
    "طقم مجوهرات فضي بلمعة ناعمة مناسب كهدية أو مناسبة.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(20, "Ayla Accessories", "طقم عقد وأقراط فضي", "gifts", 31.0,
    ["silver jewelry", "necklace", "earrings", "مجوهرات", "عقد", "أقراط", "فضي"],
    ["silver", "فضي"], ["elegant", "classic"], "العقبة",
    "عقد وأقراط بتصميم هادئ يصلح للإهداء.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(21, "Black Label Beauty", "أحمر شفاه أسود ساتان", "makeup", 10.0,
    ["lipstick", "satin", "black", "حومرة", "روج", "أسود"],
    ["black", "أسود"], ["satin", "luxury"], "عمّان",
    "تركيبة ساتان تعطي لونًا أسود واضحًا مع لمعة خفيفة.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(22, "Misk Jordan", "عطر مسك وعود بتركيبة ناعمة", "perfumes", 36.0,
    ["perfume", "musk", "oud", "عطر", "مسك", "عود"],
    ["amber", "cream", "عنبر", "كريمي"], ["soft", "arabic"], "عمّان",
    "مزيج ناعم من المسك والعود للاستخدام اليومي والمناسبات الصغيرة.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(23, "Velvet Edit", "فستان مخمل أسود طويل", "clothes", 52.0,
    ["dress", "velvet", "long", "black", "فستان", "مخمل", "أسود"],
    ["black", "أسود"], ["formal", "party", "luxury"], "عمّان",
    "فستان مخمل طويل بتفاصيل أنيقة للمناسبات الرسمية.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["M", "L"], "clothing_letter"),
    Offer(24, "Satin Story", "طقم ساتان أسود مريح وأنيق", "clothes", 33.0,
    ["satin", "set", "black", "modest", "ساتان", "طقم", "أسود", "محتشم"],
    ["black", "أسود"], ["modest", "minimal", "evening"], "عمّان",
    "طقم ساتان بلمسة فاخرة وقصة محتشمة سهلة التنسيق.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["S", "M", "L"], "clothing_letter"),
    Offer(25, "Golden Hour", "ساعة ذهبية بتصميم كلاسيكي صغير", "watches", 62.0,
    ["watch", "gold", "classic", "small", "ساعة", "ذهبي", "كلاسيك"],
    ["gold", "black", "ذهبي", "أسود"], ["classic", "elegant"], "عمّان",
    "ساعة ذهبية صغيرة بتصميم كلاسيكي للاستخدام اليومي.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(26, "Rose & Oud", "مجموعة عطر عود وحجم سفر", "perfumes", 28.0,
    ["oud", "travel", "perfume", "عود", "سفر", "عطر"],
    ["brown", "gold", "بني", "ذهبي"], ["travel", "gift"], "إربد",
    "مجموعة عطر عود وحجم سفر داخل علبة أنيقة مناسبة كهدية.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(27, "Maison Gifts", "بوكس تخرج فضي فاخر", "gifts", 23.0,
    ["graduation", "gift", "silver", "بوكس", "تخرج", "هدية", "فضي"],
    ["silver", "white", "فضي", "أبيض"], ["graduation", "elegant"], "عمّان",
    "بوكس تخرج أنيق بتغليف فضي وبطاقة تهنئة قابلة للتخصيص.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    Offer(28, "Nour Accessories", "طقم فضي مع حجر أبيض", "gifts", 42.0,
    ["silver jewelry", "stone", "gift", "مجوهرات", "فضي", "حجر", "هدية"],
    ["silver", "white", "فضي", "أبيض"], ["classic", "luxury"], "السلط",
    "طقم فضي مع حجر أبيض بتصميم نظيف وراقي.",
    "unsplash.com",
    "wa.me", "instagram.com"),
    # --- v2: shoes ---
    Offer(29, "Step & Style", "كعب واطي أسود مريح للدوام", "shoes", 27.0,
    ["heels", "low heel", "black", "shoes", "شوز", "كعب", "كعب واطي", "أسود"],
    ["black", "أسود"], ["minimal", "day", "classic"], "عمّان",
    "شوز كعب واطي مريح مناسب للدوام والمشاوير اليومية.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["37", "38", "39", "40"], "shoe_eu"),
    Offer(30, "Heels House JO", "شوز كعب عالي أسود للسهرات", "shoes", 34.0,
    ["heels", "high heel", "party", "black", "شوز", "كعب", "سهرة", "أسود"],
    ["black", "أسود"], ["party", "night", "elegant"], "عمّان",
    "كعب عالي أنيق بلون أسود مناسب للحفلات والمناسبات المسائية.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["36", "37", "38", "39", "40", "41"], "shoe_eu"),
    Offer(31, "Comfort Walk", "سنيكرز أبيض يومي خفيف", "shoes", 22.0,
    ["sneakers", "white", "casual", "shoes", "سنيكرز", "شوز", "أبيض"],
    ["white", "أبيض"], ["minimal", "casual", "day"], "إربد",
    "سنيكرز خفيف ومريح للاستخدام اليومي.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["37", "38", "39", "40", "41", "42"], "shoe_eu"),
    Offer(32, "Velvet Step", "بوت شتوي بني جلد", "shoes", 39.0,
    ["boots", "brown", "leather", "winter", "بوت", "شوز", "بني"],
    ["brown", "بني"], ["classic", "casual"], "عمّان",
    "بوت جلد بني دافئ للإطلالات الشتوية.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["38", "39", "40", "41", "42"], "shoe_eu"),
    Offer(42, "Ballerina JO", "باليرينا سوداء ناعمة", "shoes", 19.0,
    ["ballerina", "flat", "black", "shoes", "باليرينا", "شوز", "أسود", "فلات"],
    ["black", "أسود"], ["minimal", "day", "soft"], "عمّان",
    "باليرينا سوداء مريحة وخفيفة للاستخدام اليومي.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["36", "37", "38", "39", "40"], "shoe_eu"),
    # --- v2: lingerie ---
    Offer(33, "Intima JO", "برا قطن مريح بدون سلك", "lingerie", 9.0,
    ["bra", "cotton", "wireless", "برا", "سوتيان", "قطن", "مريح"],
    ["black", "white", "أسود", "أبيض"], ["minimal", "day"], "عمّان",
    "برا قطن ناعم بدون سلك معدني، مريح للاستخدام اليومي.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["34B", "34C", "36B", "36C", "38C"], "bra"),
    Offer(34, "Lace & Co", "سوتيان دانتيل أسود فاخر", "lingerie", 12.5,
    ["bra", "lace", "black", "برا", "سوتيان", "دانتيل", "أسود"],
    ["black", "أسود"], ["luxury", "elegant"], "عمّان",
    "سوتيان دانتيل أسود بخامة ناعمة وتفاصيل راقية.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["32B", "34B", "34C", "36C"], "bra"),

    Offer(35, "Soft Touch", "طقم داخلي قطن ناعم", "lingerie", 11.0,
    ["underwear set", "cotton", "طقم", "داخلي", "لانجيري", "قطن"],
    ["cream", "pink", "كريمي", "زهري"], ["minimal", "soft"], "الزرقاء",
    "طقم داخلي قطني ناعم بألوان هادئة.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["S", "M", "L", "XL"], "clothing_letter"),
    # --- v2: scarves / hijab ---
    Offer(36, "Hijab House", "طرحة شيفون سوداء خفيفة", "scarves", 6.0,
    ["scarf", "chiffon", "black", "طرحة", "شيفون", "حجاب", "أسود"],
    ["black", "أسود"], ["minimal", "day"], "عمّان",
    "طرحة شيفون خفيفة بثبات جيد للاستخدام اليومي.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["180x70"], "scarf_dimensions"),
    Offer(37, "Cotton Wrap", "شال قطن طويل وعريض", "scarves", 8.5,
    ["scarf", "cotton", "long", "wide", "شال", "قطن", "طويل", "عريض"],
    ["cream", "grey", "كريمي", "رمادي"], ["modest", "minimal"], "إربد",
    "شال قطن طويل وعريض بخامة غير شفافة ومريحة.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["200x80"], "scarf_dimensions"),
    Offer(38, "Medina Scarves", "شال ساتان فاخر للمناسبات", "scarves", 11.0,
    ["scarf", "satin", "luxury", "شال", "ساتان", "فاخر", "مناسبات"],
    ["gold", "cream", "ذهبي", "كريمي"], ["luxury", "elegant"], "عمّان",
    "شال ساتان بلمعة ناعمة مناسب للمناسبات والعزائم.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["190x75"], "scarf_dimensions"),
    Offer(39, "Warm Line", "شال صوف شتوي عريض", "scarves", 13.0,
    ["scarf", "wool", "winter", "wide", "شال", "صوف", "شتاء", "عريض"],
    ["brown", "grey", "بني", "رمادي"], ["classic", "warm"], "السلط",
    "شال صوف شتوي عريض وثقيل للتدفئة.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["200x90"], "scarf_dimensions"),
    # --- v2: extra clothes ---
    Offer(40, "Closet 36", "فستان أسود قصير كاجوال", "clothes", 26.0,
    ["dress", "short", "casual", "black", "فستان", "أسود", "كاجوال"],
    ["black", "أسود"], ["minimal", "casual", "day"], "عمّان",
    "فستان أسود قصير بقصة كاجوال سهلة التنسيق.",
    "unsplash.com",
    "wa.me", "instagram.com",
    ["S", "M", "L"], "clothing_letter"),
    Offer(41, "Urban Thread", "هودي أسود أوفرسايز", "clothes", 18.0,
    ["hoodie", "oversize", "black", "هودي", "أسود", "اوفرسايز"],
    ["black", "أسود"], ["casual", "street", "minimal"], "الزرقاء",
    "هودي أسود أوفرسايز بخامة قطنية ثقيلة.",
    "unsplash.com",
    "wa.me", "instagram.com",
["M", "L", "XL"], "clothing_letter"),
    )
CATEGORY_LABELS = {
    "makeup": "مكياج",
    "clothes": "ملابس",
    "gifts": "هدايا",
    "watches": "ساعات",
    "perfumes": "عطور",
    "shoes": "أحذية",
    "lingerie": "ملابس داخلية",
    "scarves": "طرحات وشالات",
    }

CATEGORY_SYNONYMS: Dict[str, set] = {
    "makeup": {
    "مكياج", "ميكب", "makeup", "cosmetics", "روج", "حومرة", "حمرة", "حمرا",
    "احمر شفاه", "أحمر شفاه", "ليبستك", "lipstick", "lip", "عيون", "بلشر",
    },
    "clothes": {
    "ملابس", "لبس", "لبسة", "فستان", "فساتين", "فستين", "فستانه", "عباية", "عبايات",
    "ساتان", "مخمل", "ثوب", "هودي", "بلوزة", "بلوزه", "تيشيرت", "بنطلون", "بنطال", "سروال",
    "سراويل", "جينز", "jeans", "pants", "trousers", "bottoms", "cargo", "leggings", "تنورة",
    "تنوره", "skirt", "شورت", "shorts", "clothes", "dress", "abaya", "hoodie", "shirt",
    },
    "gifts": {
    "هدية", "هديه", "هدايا", "بوكس", "تخرج", "gift", "gifts", "جوهرة", "مجوهرات",
    "فضي", "فضة", "سوار", "عقد", "اقراط", "أقراط", "jewelry", "silver",
    },
    "watches": {
    "ساعة", "ساعات", "ساعه", "watch", "watches", "تايم",
    },
    "perfumes": {
    "عطر", "عطور", "عطورات", "برفان", "برفانات", "دهن عود", "عود", "مسك",
    "perfume", "perfumes", "oud", "musk",
    },
    "shoes": {
    "شوز", "حذاء", "كندرة", "كندره", "كعب", "بوت", "سنيكرز", "باليرينا", "فلات",
    "shoes", "heels", "sneakers", "boots", "ballerina", "flats",
    },
    "lingerie": {
    "برا", "براة", "سوتيان", "ستيانه", "ستيّانة", "ستيانة", "حمالة صدر", "حماله صدر", "حمالة",
    "حماله", "صدرية", "صدرية نسائية", "لانجيري", "داخلي", "ملابس داخلية", "كولوت",
    "bra", "bras", "brassiere", "lingerie", "underwear",
    },
    "scarves": {
    "شال", "شالات", "طرحة", "طرحه", "طرحات", "حجاب", "اسكارف", "سكارف",
    "scarf", "scarves", "hijab", "shawl",
    },
    }

COLOR_SYNONYMS: Dict[str, set] = {
    "black": {"أسود", "اسود", "سوداء", "سوده", "black", "noir"},
    "white": {"أبيض", "ابيض", "بيضاء", "white"},
    "red": {"أحمر", "احمر", "حمرا", "red"},
    "silver": {"فضي", "فضية", "فضه", "فضة", "silver"},
    "gold": {"ذهبي", "ذهب", "ذهبية", "gold", "golden"},
    "brown": {"بني", "بنية", "brown"},
    "cream": {"kريمي", "سكري", "cream", "off-white"},
    "grey": {"رمادي", "رصاصي", "grey", "gray"},
    "pink": {"زهري", "وردي", "pink"},
    }

COLOR_AR = {
    "black": "أسود", "white": "أبيض", "red": "أحمر", "silver": "فضي",
    "gold": "ذهبي", "brown": "بني", "cream": "كريمي", "grey": "رمادي", "pink": "زهري",
    }

STYLE_SYNONYMS: Dict[str, set] = {
    "luxury": {"فاخر", "فخم", "فخمه", "luxury", "راقي", "رقي", "classy", "افخم", "أفخم"},
    "party": {"حفلة", "حفله", "سهرة", "سهره", "party", "night"},
    "modest": {"محتشم", "محتشمة", "محترم", "modest", "ساتر"},
    "classic": {"كلاسيك", "كلاسيكي", "كلاسيكية", "classic", "رسمي", "formal"},
    "minimal": {"بسيط", "ناعم", "مينيمال", "minimal"},
    "gift": {"هدية", "هديه", "gift"},
    }

# Semantic shopping expansions. These are deliberately broad so a one-word query such as
# "بنطال" or the Jordanian "ستيّانة" never depends on an exact catalog keyword.
SEARCH_EXPANSIONS: Dict[str, List[str]] = {
    "بنطال": ["بنطال", "بنطلون", "سروال", "سراويل", "pants", "trousers", "jeans", "cargo pants", "wide leg pants", "straight leg pants"],
    "بنطلون": ["بنطلون", "بنطال", "سروال", "pants", "trousers", "jeans", "cargo pants", "wide leg pants"],
    "سروال": ["سروال", "بنطال", "بنطلون", "pants", "trousers", "jeans", "cargo pants"],
    "ستيانه": ["ستيّانة", "ستيانه", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "brassiere", "wireless bra", "sports bra"],
    "ستيّانة": ["ستيّانة", "ستيانه", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "brassiere", "wireless bra", "sports bra"],
    "سوتيان": ["سوتيان", "ستيّانة", "ستيانه", "برا", "حمالة صدر", "صدرية", "bra", "bras", "brassiere"],
    "برا": ["برا", "سوتيان", "ستيّانة", "حمالة صدر", "صدرية", "bra", "bras", "brassiere", "wireless bra"],
    "حمالة صدر": ["حمالة صدر", "سوتيان", "ستيّانة", "برا", "صدرية", "bra", "bras", "brassiere"],
    "شوز": ["شوز", "حذاء", "كندرة", "sneakers", "shoes", "heels", "boots", "flats"],
    "حذاء": ["حذاء", "شوز", "كندرة", "shoes", "sneakers", "heels", "boots", "flats"],
    "فستان": ["فستان", "فساتين", "dress", "dresses", "evening dress", "maxi dress", "modest dress"],
    "عطر": ["عطر", "عطور", "برفان", "perfume", "fragrance", "parfum", "eau de parfum"],
    "روج": ["روج", "أحمر شفاه", "حومرة", "حمرة", "lipstick", "liquid lipstick", "lip gloss"],
    "مكياج": ["مكياج", "ميكب", "makeup", "cosmetics", "beauty"],
    "عباية": ["عباية", "عبايات", "abaya", "modest abaya", "black abaya"],
    "طرحة": ["طرحة", "حجاب", "شال", "scarf", "hijab", "shawl"],
}


def semantic_search_terms(user_text: str, intent: Optional[Dict[str, Any]] = None) -> List[str]:
    clean = normalize_text(user_text)
    terms: List[str] = []
    def add(value: Any) -> None:
        value = normalize_text(str(value or "")).strip()
        if value and len(value) > 1 and value not in terms:
            terms.append(value)
    for key in (intent or {}).get("product_terms", []):
        add(key)
        for variant in SEARCH_EXPANSIONS.get(normalize_text(key), []):
            add(variant)
    for key, variants in SEARCH_EXPANSIONS.items():
        if normalize_text(key) in clean:
            for variant in variants:
                add(variant)
    for synonym in (intent or {}).get("synonyms", []):
        add(synonym)
    for category in (intent or {}).get("categories", []):
        for synonym in CATEGORY_SYNONYMS.get(category, set()):
            add(synonym)
    return terms[:40]


def build_live_search_queries(user_text: str, intent: Dict[str, Any], refresh_nonce: str = "") -> List[str]:
    terms = semantic_search_terms(user_text, intent)
    category = ", ".join(intent.get("categories", []))
    budget = intent.get("budget", {}) or {}
    constraints = []
    if budget.get("amount") is not None:
        constraints.append(f"under {budget['amount']} JOD" if budget.get("kind") == "hard_max" else f"around {budget['amount']} JOD")
    constraints.extend([str(x) for x in intent.get("colors", [])[:3]])
    core = " ".join(terms[:8])
    queries = [
        f"{user_text} الأردن شراء سعر متجر",
        f"{core} Jordan JOD price online shopping",
        f"site:.jo {core} price",
        f"{core} Amman Jordan store price",
    ]
    if category:
        queries.append(f"{category} {core} الأردن سعر")
    if constraints:
        queries.append(f"{core} {' '.join(constraints)} Jordan")
    if refresh_nonce:
        queries.append(f"{core} Jordan alternatives different stores {refresh_nonce[-8:]}")
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:7]


STOPWORDS = {
    "بدي", "بديش", "بدّي", "بدها", "بده", "شي", "اشي", "شيء", "الي", "إلي", "لي",
    "مع", "بدون", "لون", "سعر", "ميزانية", "ميزانيتي", "حد", "اقصى", "أقصى", "لحد", "حدود",
    "تحت", "اقل", "أقل", "من", "الى", "إلى", "دينار", "ديناراً", "جني", "جنيه", "jod", "jd",
    "بال", "لل", "ال", "و", "يا", "شو", "ايش", "اي", "أي", "بس", "فقط", "لـ", "عن",
    "انا", "أنا", "يكون", "تكون", "هو", "هي", "ممكن", "لو", "اذا", "إذا",
    }
def serialize_json(value: Any) -> str:
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
    text = text.replace("ة", "ه")
    text = re.sub(r"[^a-z0-9+#.\-\u0600-\u06ff\s]", " ", text)
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
    raw = re.findall(r"[a-z0-9_+#.-]+|[؀-ۿ]+", clean, flags=re.IGNORECASE)
    return [t for t in raw if t not in STOPWORDS]

# ---------------------------------------------------------------------------
# Hard vs Soft budget extraction (v2)
# ---------------------------------------------------------------------------

BUDGET_KIND_PATTERNS: Sequence[Tuple[str, str]] = (
    ("cheapest", r"الارخص|أرخص شي|ارخص شي|بدي الارخص|cheapest"),
    ("quality_first", r"السعر مش مهم|السعر مو مهم|السعر ما بيهم|جودة اهم|الجودة اهم|quality first"),
    ("hard_max", r"تحت|اقل من|أقل من|ما يتجاوز|ما بتتعدى|ما بدفع اكثر|ما بتدفع اكثر|اقصى|أقصى|ما بزيد عن|max"),
    ("soft_target", r"بحدود|حوالي|حوالى|تقريبا|around"),
    ("flexible", r"ممكن ازيد|ممكن أزيد|لو الجودة|اذا الجودة|إذا الجودة|flex"),
)

_BUDGET_AMOUNT_PATTERNS: Sequence[str] = (
    r"(?:تحت|اقل من|أقل من|لحد|حدود|حد|ميزانية|ميزانيتي|اقصى|أقصى|ما بتتعدى|ما يتجاوز|ما بدفع اكثر من|ما بتدفع اكثر من|ما بزيد عن|بحدود|حوالي|حوالى|تقريبا)\s*(?:هو|هي|ال)?\s*(\d+(?:\.\d+)?)",
    r"(\d+(?:\.\d+)?)\s*(?:دينار|دينارات|jod|jd|د.ا|دج)",
    r"(?:بـ|ب|حدها|حده)\s*(\d+(?:\.\d+)?)",
)


def extract_budget(text: str) -> Dict[str, Any]:
    """Return budget amount and budget behavior."""
    t = normalize_arabic_digits(normalize_text(text))
    amount: Optional[float] = None
    for pattern in _BUDGET_AMOUNT_PATTERNS:
        m = re.search(pattern, t, flags=re.IGNORECASE)
        if m:
            amount = safe_float(m.group(1))
            if amount is not None:
                break
    if amount is None and re.search(r"دينار|jod|jd|ميزاني|سعر|تحت|اقل|لحد|حدود|اكثر|أكثر|يتجاوز|$", t, re.I):
        m = re.search(r"\b(\d+(?:\.\d+)?)\b", t)
        if m:
            amount = safe_float(m.group(1))

    kind = "none"
    for k, pat in BUDGET_KIND_PATTERNS:
        if re.search(pat, t, re.I):
            kind = k
            break
    if kind == "none" and amount is not None:
        kind = "hard_max"
    return {"amount": amount, "kind": kind}

# ---------------------------------------------------------------------------
# Size extraction per product type (v2)
# ---------------------------------------------------------------------------

BRA_RE = re.compile(r"\b(28|30|32|34|36|38|40|42)\s*([A-Fa-f]{1,2})\b")
SHOE_EU_RE = re.compile(r"\b(3[5-9]|4[0-6])\s*(eu|أوروبي|اوروبي|أوروبى)?\b", re.I)
SHOE_US_RE = re.compile(r"\b([5-9]|1[0-2])\s*(us|أمريكي|امريكي|امريكى)\b", re.I)
CLOTH_LETTER_RE = re.compile(r"\b(xxs|xs|s|m|l|xl|xxl|xxxl)\b", re.I)
CLOTH_NUM_RE = re.compile(r"\b(3[4-9]|4[0-8])\b")
DIM_RE = re.compile(r"(\d{2,3})\s*[×xX]\s*(\d{2,3})")

_SHOE_CONTEXT = r"شوز|حذاء|كندرة|كعب|مقاس|بوت|سنيكرز|باليرينا|فلات|shoe|heel"
_CLOTH_CONTEXT = r"فستان|فساتين|ملابس|لبس|عباية|هودي|بلوزة|ثوب|dress|clothes"


def extract_sizes(text: str, categories: Optional[List[str]] = None) -> Dict[str, Any]:
    t = normalize_arabic_digits(normalize_text(text))
    out: Dict[str, Any] = {"system": None, "value": None, "band": None, "cup": None}
    m = BRA_RE.search(t)
    if m and not re.search(r"(?:أوروبي|اوروبي|eu)\b", t):
        out.update(
            system="bra",
            band=int(m.group(1)),
            cup=m.group(2).upper(),
            value=f"{m.group(1)}{m.group(2).upper()}",
        )
        return out
    m = SHOE_US_RE.search(t)
    if m:
        out.update(system="shoe_us", value=int(m.group(1)))
        return out
    m = SHOE_EU_RE.search(t)
    if m and (re.search(r"أوروبي|اوروبي|أوروبى|\beu\b", t) or re.search(_SHOE_CONTEXT, t)):
        out.update(system="shoe_eu", value=int(m.group(1)))
        return out
    m = DIM_RE.search(t)
    if m:
        out.update(system="scarf_dimensions", value=f"{m.group(1)}x{m.group(2)}")
        return out
    m = CLOTH_LETTER_RE.search(t)
    if m and (re.search(_CLOTH_CONTEXT, t) or (categories and "clothes" in categories)):
        out.update(system="clothing_letter", value=m.group(1).upper())
        return out
    m = CLOTH_NUM_RE.search(t)
    if m and (categories and "clothes" in categories) and not extract_budget(text)["amount"]:
        out.update(system="clothing_numeric", value=int(m.group(1)))
        return out
    return out

# ---------------------------------------------------------------------------
# Category / color / style / terms detection
# ---------------------------------------------------------------------------

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

_EXCLUDE_RE = re.compile(r"(?:ما بدي|ما بده|ما بدها|مش|مو|بدون|من غير)\s+([؀-ۿA-Za-z]+)")
_EXCLUDE_IGNORE = {"شرط", "مهم", "ضروري", "مشكلة", "ايش", "شي"}


def detect_exclusions(text: str) -> Tuple[List[str], List[str]]:
    """Return (excluded_colors, excluded_terms)."""
    clean = normalize_text(text)
    ex_colors: List[str] = []
    ex_terms: List[str] = []
    for m in _EXCLUDE_RE.finditer(clean):
        word = m.group(1)
        if word in _EXCLUDE_IGNORE:
            continue
        mapped = None
        for canonical, synonyms in COLOR_SYNONYMS.items():
            if word in {normalize_text(s) for s in synonyms}:
                mapped = canonical
                break
        if mapped and mapped not in ex_colors:
            ex_colors.append(mapped)
        elif not mapped and len(word) > 1 and word not in ex_terms:
            ex_terms.append(word)
    return ex_colors, ex_terms


def detect_brand(text: str) -> Optional[str]:
    m = re.search(r"(?:مثل|زي|شبيه\s+ب?)\s*([A-Za-z][A-Za-z0-9]{1,20})", text or "")
    return m.group(1) if m else None


def detect_product_terms(text: str) -> List[str]:
    clean = normalize_text(text)
    found: List[str] = []
    for term in tokens(clean):
        if len(term) > 1 and not re.fullmatch(r"\d+(?:\.\d+)?", term):
            if term not in found:
                found.append(term)
            for variant in SEARCH_EXPANSIONS.get(term, []):
                v = normalize_text(variant)
                if v and v not in found:
                    found.append(v)
    return found[:50]

# ---------------------------------------------------------------------------
# Optional Gemini extraction (extended schema)
# ---------------------------------------------------------------------------

_GEMINI_CLIENT: Any = None


def fallback_image_url(offer_id: int) -> str:
    return f"/media/offers/{int(offer_id)}.svg"


def safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.strip().lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"}:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False
            return True
        except ValueError:
            pass
        return not host.endswith((".local", ".internal", ".lan"))
    except Exception:
        return False


def normalize_external_url(url: Any) -> str:
    value = str(url or "").strip()
    return value if safe_public_url(value) else ""


def verify_external_url(url: Any, timeout: int = 8) -> Tuple[bool, str, str]:
    """Verify a public HTTP(S) URL and return (ok, final_url, status)."""
    candidate = normalize_external_url(url)
    if not candidate:
        return False, "", "invalid_url"
    try:
        req = urllib.request.Request(
            candidate,
            headers={"User-Agent": "Mozilla/5.0 AlTawseyaBot/7.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = normalize_external_url(resp.geturl())
            code = int(getattr(resp, "status", 200) or 200)
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if not final_url:
                return False, "", "unsafe_redirect"
            if code >= 400:
                return False, final_url, f"http_{code}"
            if content_type and not any(x in content_type for x in ("text/html", "application/xhtml+xml")):
                return False, final_url, "not_html"
            return True, final_url, "verified"
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 429}:
            final_url = normalize_external_url(getattr(exc, "url", "") or candidate)
            return bool(final_url), final_url, f"http_{exc.code}"
        return False, normalize_external_url(getattr(exc, "url", "") or candidate), f"http_{exc.code}"
    except Exception as exc:
        LOGGER.debug("URL verification failed for %s: %s", candidate, exc)
        return False, "", "unreachable"


def fetch_open_graph_image(page_url: str) -> str:
    page_url = normalize_external_url(page_url)
    if not page_url:
        return ""
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0 AlTawseya/6.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            final_url = resp.geturl()
            if not safe_public_url(final_url):
                return ""
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                return ""
            raw = resp.read(700_000)
        text = raw.decode("utf-8", errors="ignore")
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                image = urljoin(final_url, html.unescape(m.group(1).strip()))
                if safe_public_url(image):
                    return image
    except Exception as exc:
        LOGGER.debug("OpenGraph image fetch skipped for %s: %s", page_url, exc)
    return ""


def offer_svg(offer: Optional[Offer]) -> bytes:
    title = html.escape((offer.title if offer else "منتج")[:70])
    merchant = html.escape((offer.merchant_name if offer else "التوصية")[:36])
    category = html.escape(CATEGORY_LABELS.get(offer.category, offer.category) if offer else "عرض")
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1000">
<defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop offset="0%" stop-color="#f5f5f5"/><stop offset="100%" stop-color="#e5e5e5"/></linearGradient></defs>
<rect width="800" height="1000" fill="url(#g)"/><circle cx="400" cy="350" r="170" fill="#111" opacity=".08"/>
<text x="400" y="300" text-anchor="middle" font-family="Tahoma,Arial" font-size="90" fill="#111">ت</text>
<text x="400" y="690" text-anchor="middle" font-family="Tahoma,Arial" font-size="34" font-weight="700" fill="#111">{title}</text>
<text x="400" y="748" text-anchor="middle" font-family="Tahoma,Arial" font-size="25" fill="#555">{merchant}</text>
<text x="400" y="800" text-anchor="middle" font-family="Tahoma,Arial" font-size="22" fill="#777">{category}</text>
<text x="400" y="920" text-anchor="middle" font-family="Tahoma,Arial" font-size="24" fill="#222">صورة مؤقتة — تُستبدل بصورة المتجر عند العثور عليها</text>
</svg>"""
    return svg.encode('utf-8')


def _live_offer_id(url: str) -> int:
    return 100000000 + (int(hashlib.sha256(url.encode('utf-8')).hexdigest()[:12], 16) % 89999999)


def _extract_grounding_urls(response: Any) -> List[str]:
    urls: List[str] = []
    try:
        for candidate in getattr(response, "candidates", None) or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None) if web else None
                if uri and safe_public_url(uri) and uri not in urls:
                    urls.append(uri)
    except Exception:
        pass
    return urls


def _extract_grounding_sources(response: Any) -> List[Dict[str, str]]:
    sources: List[Dict[str, str]] = []
    seen: set = set()
    try:
        for candidate in getattr(response, "candidates", None) or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None) if web else None
                title = getattr(web, "title", None) if web else None
                uri = normalize_external_url(uri)
                if uri and uri not in seen:
                    sources.append({"url": uri, "title": str(title or "").strip()[:180]})
                    seen.add(uri)
    except Exception as exc:
        LOGGER.debug("Grounding source extraction failed: %s", exc)
    return sources


def fetch_product_metadata(page_url: str) -> Dict[str, Any]:
    """Read public product metadata from a verified page without inventing values."""
    page_url = normalize_external_url(page_url)
    if not page_url:
        return {}
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0 AlTawseyaSearch/8.0"})
        with urllib.request.urlopen(req, timeout=7) as resp:
            final_url = normalize_external_url(resp.geturl())
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if not final_url or "text/html" not in content_type:
                return {}
            raw = resp.read(900_000)
        text = raw.decode("utf-8", errors="ignore")
        out: Dict[str, Any] = {"url": final_url, "title": "", "description": "", "image_url": "", "price_jod": None, "currency": ""}
        # Prefer structured product data (JSON-LD), then OpenGraph/meta tags.
        for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
            try:
                data = json.loads(html.unescape(block.strip()))
            except Exception:
                continue
            nodes = data if isinstance(data, list) else [data]
            queue = list(nodes)
            while queue:
                node = queue.pop(0)
                if not isinstance(node, dict):
                    continue
                if isinstance(node.get("@graph"), list):
                    queue.extend(node["@graph"])
                typ = str(node.get("@type") or "").lower()
                if "product" not in typ and not node.get("offers"):
                    continue
                out["title"] = out["title"] or str(node.get("name") or "").strip()
                out["description"] = out["description"] or str(node.get("description") or "").strip()
                image = node.get("image")
                if isinstance(image, list):
                    image = image[0] if image else ""
                if isinstance(image, dict):
                    image = image.get("url", "")
                out["image_url"] = out["image_url"] or normalize_external_url(image)
                offers = node.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price = safe_float(offers.get("price") or offers.get("lowPrice"))
                    currency = str(offers.get("priceCurrency") or "").upper()
                    if price is not None and currency:
                        out["price_jod"] = price if currency in {"JOD", "JD"} else None
                        out["currency"] = currency
                if out["title"] and out["price_jod"] is not None:
                    break
        meta_patterns = [
            ("title", r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)'),
            ("description", r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)'),
            ("image_url", r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)'),
            ("price_jod", r'<meta[^>]+(?:property|name)=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)'),
            ("currency", r'<meta[^>]+(?:property|name)=["\']product:price:currency["\'][^>]+content=["\']([^"\']+)'),
        ]
        for key, pattern in meta_patterns:
            m = re.search(pattern, text, re.I)
            if m and not out.get(key):
                value = html.unescape(m.group(1).strip())
                if key == "image_url":
                    value = normalize_external_url(urljoin(final_url, value))
                elif key == "price_jod":
                    value = safe_float(value)
                elif key == "currency":
                    value = value.upper()
                out[key] = value
        if out.get("currency") not in {"", "JOD", "JD"}:
            out["price_jod"] = None
        if not out["title"]:
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            if m:
                out["title"] = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
        if not out["image_url"]:
            out["image_url"] = fetch_open_graph_image(final_url)
        return out
    except Exception as exc:
        LOGGER.debug("Product metadata fetch skipped for %s: %s", page_url, exc)
        return {}


def live_search_offers(user_text: str, intent: Dict[str, Any], refresh_nonce: str = "", avoid_urls: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    client = gemini_model()
    if client is None or not LIVE_SEARCH_ENABLED:
        return []
    avoid_urls = [u for u in (avoid_urls or []) if safe_public_url(u)]
    search_terms = semantic_search_terms(user_text, intent)
    search_queries = build_live_search_queries(user_text, intent, refresh_nonce)
    ai_phrases = [str(x) for x in intent.get("search_phrases", []) if str(x).strip()]
    query_block = "\n".join(f"- {q}" for q in list(dict.fromkeys(ai_phrases + search_queries))[:10])
    avoid_block = "\n".join(f"- {u}" for u in avoid_urls[:40]) or "- لا يوجد"
    prompt = f"""
أنت محرك بحث تسوق حقيقي للسوق الأردني. نفّذ بحث Google حي الآن باستخدام أداة Google Search.
لا تجب من الذاكرة. تعامل مع الطلب كبحث شراء متعدد الاستعلامات، وليس سؤالًا عامًا.

طلب المستخدم الأصلي: {user_text!r}
المفاهيم الدلالية والمرادفات: {json.dumps(search_terms[:30], ensure_ascii=False)}
عبارات البحث المقترحة:
{query_block}
النوايا المستخرجة: {json.dumps(_intent_public(intent), ensure_ascii=False)}
رقم تنويع البحث: {refresh_nonce!r}

ابحث عبر عدة استعلامات مختلفة. إذا كان الطلب كلمة واحدة، وسّعه دلاليًا قبل البحث.
مثال: "بنطال" = بنطال/بنطلون/سروال/pants/trousers/jeans.
مثال: "ستيّانة" = ستيانة/سوتيان/برا/حمالة صدر/صدرية/bra/bras.
لا تتعامل مع الكلمة المحلية كأنها خطأ أو فئة مجهولة.

أعد JSON فقط:
{{"offers":[{{"title":"اسم المنتج الحقيقي","merchant_name":"اسم المتجر الحقيقي","category":"makeup|clothes|gifts|watches|perfumes|shoes|lingerie|scarves","price_jod":رقم أو null,"description":"وصف قصير من الصفحة","city":"الأردن أو المدينة إن ظهرت","source_url":"الرابط الكامل لصفحة المنتج","image_url":"رابط الصورة إن ظهر","tags":[],"colors":[],"style":[],"sizes":[],"size_system":""}}]}}

قواعد:
1) يجب أن يكون source_url رابط صفحة منتج/عرض حقيقي، وليس صفحة بحث عامة أو الصفحة الرئيسية كلما أمكن.
2) لا تخترع أي منتج أو متجر أو سعر.
3) لا تستخدم نفس الرابط الموجود في قائمة الروابط السابقة.
4) أعطِ الأولوية للمتاجر الأردنية والمتاجر التي تعرض السعر بالدينار الأردني أو تشحن للأردن.
5) ابحث في متاجر متعددة، وليس متجرًا واحدًا.
6) إذا لم تجد السعر في نتيجة البحث، لا تخمّنه؛ اتركه null ودع النظام يقرأ بيانات الصفحة.
7) أعطِ نتائج متنوعة تغطي مرادفات الطلب.
8) عند refresh ابحث عن منتجات/متاجر مختلفة، وليس مجرد إعادة ترتيب النتائج القديمة.
"""
    try:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            temperature=0.2,
        ) if genai_types else None
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=config)
        data = json.loads(getattr(response, "text", "{}") or "{}")
        candidates = data.get("offers", []) if isinstance(data, dict) else []
        grounding_sources = _extract_grounding_sources(response)
        # If structured JSON is weak, use the actual grounded URLs as a second discovery layer.
        if not isinstance(candidates, list):
            candidates = []
        existing_urls = {normalize_external_url(x.get("source_url")) for x in candidates if isinstance(x, dict)}
        for source in grounding_sources:
            if source["url"] not in existing_urls:
                candidates.append({"source_url": source["url"], "title": source.get("title", "")})
        out: List[Dict[str, Any]] = []
        seen = set(avoid_urls)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            url = normalize_external_url(item.get("source_url"))
            if not url or url in seen:
                continue
            verified, final_url, verify_status = verify_external_url(url)
            if not verified:
                LOGGER.info("Skipping unverified live offer URL %s (%s)", url, verify_status)
                continue
            url = final_url or url
            if url in seen:
                continue
            metadata = fetch_product_metadata(url)
            price = safe_float(item.get("price_jod"))
            if price is None:
                price = safe_float(metadata.get("price_jod"))
            if price is None or price <= 0:
                continue
            title = str(item.get("title") or "").strip()[:180]
            if len(title) < 3:
                title = str(metadata.get("title") or "").strip()[:180]
            if len(title) < 3:
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                description = str(metadata.get("description") or "").strip()
            merchant = str(item.get("merchant_name") or "").strip()[:100]
            if not merchant:
                merchant = (urlparse(url).hostname or "متجر").replace("www.", "")[:100]
            image = normalize_external_url(item.get("image_url")) or normalize_external_url(metadata.get("image_url")) or fetch_open_graph_image(url)
            oid = _live_offer_id(url)
            text_for_category = " ".join([title, description, " ".join(_as_list(item.get("tags"))), url])
            category = str(item.get("category") or "").strip()
            if category not in CATEGORY_LABELS:
                detected = detect_categories(text_for_category)
                category = detected[0] if detected else (intent.get("categories") or ["gifts"])[0]
            tags = list(dict.fromkeys(_as_list(item.get("tags")) + search_terms[:12]))
            colors = _as_list(item.get("colors"))
            style = _as_list(item.get("style"))
            out.append({
                "id": oid, "merchant_name": merchant, "title": title, "category": category,
                "price_jod": round(price, 2), "tags": tags, "colors": colors, "style": style,
                "city": str(item.get("city") or "الأردن")[:60],
                "description": (description or "عرض حقيقي تم العثور عليه عبر البحث المباشر")[:360],
                "image_url": image or fallback_image_url(oid), "whatsapp_url": "", "instagram_url": "",
                "sizes": _as_list(item.get("sizes")), "size_system": str(item.get("size_system") or ""),
                "store_url": url, "source": "google_search",
            })
            seen.add(url)
            if len(out) >= LIVE_SEARCH_MAX:
                break
        return out
    except Exception as exc:
        LOGGER.warning("Live Google Search failed; using internal inventory: %s", exc)
        return []


def gemini_model() -> Any:
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is not None:
        return _GEMINI_CLIENT
    if not genai or not GOOGLE_API_KEY:
        return None
    try:
        _GEMINI_CLIENT = genai.Client(api_key=GOOGLE_API_KEY)
        return _GEMINI_CLIENT
    except Exception as exc:
        LOGGER.warning("Gemini initialization skipped: %s", exc)
        return None


def extract_with_gemini(user_text: str) -> Dict[str, Any]:
    client = gemini_model()
    if client is None:
        return {}
    prompt = f"""
أنت محرك فهم طلبات شراء للسوق الأردني. افهم العربية الفصحى والعامية الأردنية والشامية، والأخطاء الإملائية، والكلمات الناقصة، والعربي-الإنجليزي المختلط، والاختصارات. لا تعتمد على تطابق الكلمات حرفيًا.
أعد JSON فقط بهذه المفاتيح:
category: قائمة من makeup, clothes, gifts, watches, perfumes, shoes, lingerie, scarves
budget: {{"amount": رقم أو null, "kind": واحدة من hard_max, soft_target, flexible, cheapest, quality_first, none}}
colors: قائمة canonical English colors المطلوبة
excluded_colors: ألوان رُفضت صراحة
excluded_terms: خامات/أنواع رُفضت صراحة
styles: قائمة من luxury, party, modest, classic, minimal, gift
sizes: {{"system": bra|shoe_eu|shoe_us|clothing_letter|scarf_dimensions|null, "value": قيمة أو null, "band": رقم أو null, "cup": حرف أو null}}
brand_soft: اسم براند ذُكر فقط كمرجع أو null
product_terms: كلمات/مفاهيم أساسية للمنتج حتى لو كانت باللهجة المحلية أو بالإنجليزية
synonyms: 10-20 مفاهيم بديلة تساعد البحث الدلالي، ويجب أن تشمل المرادفات الأردنية والشامية والعربية الفصحى والإنجليزية، مثل بنطال/بنطلون/سروال/pants، وستيّانة/سوتيان/برا/حمالة صدر/bra عند اللزوم
search_phrases: 4-8 عبارات بحث قصيرة وطبيعية تصلح لـ Google Search

قواعد الميزانية: تحت/ما يتجاوز/ما بدفع أكثر = hard_max. بحدود/حوالي/تقريبًا = soft_target. ممكن أزيد لو الجودة = flexible.

النص الأصلي: {user_text!r}
"""
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(response_mime_type="application/json") if genai_types else None,
        )
        raw = getattr(response, "text", "") or ""
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        LOGGER.warning("Gemini extraction failed; using local parser: %s", exc)
        return {}


def ai_rerank(user_text: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    client = gemini_model()
    if client is None or not results:
        return results
    candidates = []
    for item in results[:18]:
        candidates.append({
            "id": item["id"], "title": item["title"], "category": item["category"],
            "price_jod": item["price_jod"], "tags": item.get("tags", []),
            "colors": item.get("colors", []), "style": item.get("style", []),
            "description": item.get("description", ""), "sizes": item.get("sizes", []),
        })
    prompt = f"""
رتّب المرشحين لطلب شراء عربي أردني طبيعي. احسب الملاءمة الدلالية لاختلاف اللهجة والمرادفات والأخطاء الإملائية والعربي-الإنجليزي. لا تستبعد المرشح فقط لأن الكلمات مختلفة. لا تغيّر قيود الميزانية الصريحة.
أعد JSON على شكل قائمة فقط: [{{"id": رقم, "score": رقم من 0 إلى 100, "reasons": [عبارتان عربيتان مختصرتان]}}].
طلب المستخدم: {user_text!r}
المرشحون: {json.dumps(candidates, ensure_ascii=False)}
"""
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(response_mime_type="application/json") if genai_types else None,
        )
        parsed = json.loads(getattr(response, "text", "[]") or "[]")
        if not isinstance(parsed, list):
            return results
        ranking = {int(x["id"]):(float(x.get("score",0)), x.get("reasons") or []) for x in parsed if isinstance(x,dict) and str(x.get("id","")).isdigit()}
        for item in results:
            if item["id"] in ranking:
                score, rs = ranking[item["id"]]
                item["score"] = round(max(item["score"], min(100.0, score)), 2)
                item["reasons"] = list(dict.fromkeys(rs + item.get("reasons", [])))[:4]
                item["match_type"] = "توصية ذكية" if item["score"] >= 60 else "توصية قريبة"
        results.sort(key=lambda x: (-x["score"], x["price_jod"], x["id"]))
        return results
    except Exception as exc:
        LOGGER.warning("Gemini rerank failed; keeping local order: %s", exc)
        return results


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def merge_extraction(local: Dict[str, Any], ai: Dict[str, Any]) -> Dict[str, Any]:
    categories: List[str] = []
    for source in (local.get("categories", []), ai.get("category", []), ai.get("categories", [])):
        for item in _as_list(source):
            if item in CATEGORY_LABELS and item not in categories:
                categories.append(item)

    colors = list(dict.fromkeys(local.get("colors", []) + _as_list(ai.get("colors"))))
    styles = list(dict.fromkeys(local.get("styles", []) + _as_list(ai.get("styles"))))
    terms = list(dict.fromkeys(local.get("product_terms", []) + _as_list(ai.get("product_terms"))))
    synonyms = list(dict.fromkeys(_as_list(ai.get("synonyms"))))
    excluded_colors = list(dict.fromkeys(local.get("excluded_colors", []) + _as_list(ai.get("excluded_colors"))))
    excluded_terms = list(dict.fromkeys(local.get("excluded_terms", []) + _as_list(ai.get("excluded_terms"))))

    # الاستبعاد يغلب الطلب: لو اللون مرفوض صراحة يُحذف من قائمة المطلوب
    colors = [c for c in colors if c not in excluded_colors]

    budget = dict(local.get("budget", {"amount": None, "kind": "none"}))
    ai_budget = ai.get("budget") if isinstance(ai.get("budget"), dict) else {}
    if budget.get("amount") is None:
        ai_amount = safe_float(ai_budget.get("amount") or ai.get("price_cap_jod"))
        if ai_amount is not None:
            budget["amount"] = ai_amount
            budget["kind"] = ai_budget.get("kind") if ai_budget.get("kind") in {
                "hard_max", "soft_target", "flexible", "cheapest", "quality_first"
            } else "hard_max"

    sizes = local.get("sizes", {})
    if not sizes.get("system") and isinstance(ai.get("sizes"), dict):
        ai_sizes = ai["sizes"]
        if ai_sizes.get("system"):
            sizes = {
                "system": ai_sizes.get("system"),
                "value": ai_sizes.get("value"),
                "band": ai_sizes.get("band"),
                "cup": (str(ai_sizes["cup"]).upper() if ai_sizes.get("cup") else None),
            }

    return {
        "categories": categories,
        "budget": budget,
        "colors": colors,
        "excluded_colors": excluded_colors,
        "excluded_terms": excluded_terms,
        "styles": styles,
        "sizes": sizes,
        "brand_soft": local.get("brand_soft") or ai.get("brand_soft"),
        "priority": local.get("priority"),
        "product_terms": terms,
        "synonyms": synonyms[:30],
        "search_phrases": list(dict.fromkeys(_as_list(ai.get("search_phrases"))))[:10],
        "raw_ai": ai if isinstance(ai, dict) else {},
    }


def extract_intent(user_text: str, use_ai: bool = True) -> Dict[str, Any]:
    budget = extract_budget(user_text)
    ex_colors, ex_terms = detect_exclusions(user_text)
    categories = detect_categories(user_text)
    colors = [c for c in detect_colors(user_text) if c not in ex_colors]
    priority = None
    if budget["kind"] == "cheapest":
        priority = "cheapest"
    elif budget["kind"] == "quality_first":
        priority = "quality"
    local = {
        "categories": categories,
        "budget": budget,
        "colors": colors,
        "excluded_colors": ex_colors,
        "excluded_terms": ex_terms,
        "styles": detect_styles(user_text),
        "sizes": extract_sizes(user_text, categories),
        "brand_soft": detect_brand(user_text),
        "priority": priority,
        "product_terms": [t for t in detect_product_terms(user_text) if t not in ex_terms],
    }
    ai = extract_with_gemini(user_text) if use_ai else {}
    return merge_extraction(local, ai)

# ---------------------------------------------------------------------------
# Follow-up refinement (v2)
# ---------------------------------------------------------------------------

def refine_intent(prev: Dict[str, Any], new_text: str) -> Dict[str, Any]:
    new = extract_intent(new_text, use_ai=True)
    merged: Dict[str, Any] = {k: (list(v) if isinstance(v, list) else v) for k, v in prev.items()}
    t = normalize_text(new_text)

    if re.search(r"ارخص|أرخص|orخص", t):
        merged["priority"] = "cheapest"
    if re.search(r"افخم|أفخم|ارقي|أرقى|افخم من", t):
        merged["styles"] = list(dict.fromkeys(list(merged.get("styles", [])) + ["luxury"]))
        merged["priority"] = "quality"
    if re.search(r"مش شرط|مو شرط|مش مهم|مش ضروري", t):
        if new.get("colors"):
            drop = set(new["colors"])
            merged["colors"] = [c for c in merged.get("colors", []) if c not in drop]
        elif re.search(r"لون", t):
            merged["colors"] = []
    if new.get("sizes", {}).get("system") is None and re.search(r"مقاس", t):
        merged["sizes"] = {"system": None, "value": None, "band": None, "cup": None}
    if re.search(r"مش مهم السعر|السعر مش مهم|ما عندي مشكلة بالسعر", t):
        merged["budget"] = {"amount": None, "kind": "quality_first"}
        merged["priority"] = "quality"
    elif new.get("budget", {}).get("amount") is not None or new.get("budget", {}).get("kind") in (
        "cheapest", "quality_first"
    ):
        merged["budget"] = new["budget"]
        if new["budget"]["kind"] == "cheapest":
            merged["priority"] = "cheapest"

    if new.get("colors") and not re.search(r"مش شرط|مو شرط", t):
        merged["colors"] = list(dict.fromkeys(list(merged.get("colors", [])) + new["colors"]))
    if new.get("excluded_colors"):
        merged["excluded_colors"] = list(dict.fromkeys(list(merged.get("excluded_colors", [])) + new["excluded_colors"]))
        merged["colors"] = [c for c in merged.get("colors", []) if c not in set(new["excluded_colors"])]
    if new.get("excluded_terms"):
        merged["excluded_terms"] = list(dict.fromkeys(list(merged.get("excluded_terms", [])) + new["excluded_terms"]))
    if new.get("sizes", {}).get("system"):
        merged["sizes"] = new["sizes"]
    for key in ("categories", "styles"):
        if new.get(key):
            merged[key] = list(dict.fromkeys(list(merged.get(key, [])) + new[key]))
    if new.get("product_terms"):
        merged["product_terms"] = list(dict.fromkeys(list(merged.get("product_terms", [])) + new["product_terms"]))
    if new.get("brand_soft"):
        merged["brand_soft"] = new["brand_soft"]
    return merged

_SCHEMA = """
CREATE TABLE IF NOT EXISTS buyer_intents (
id INTEGER PRIMARY KEY AUTOINCREMENT,
request_id TEXT NOT NULL UNIQUE,
raw_query TEXT NOT NULL,
categories TEXT NOT NULL,
colors TEXT NOT NULL,
styles TEXT NOT NULL,
product_terms TEXT NOT NULL,
price_cap_jod REAL,
budget_json TEXT NOT NULL DEFAULT '{}',
sizes_json TEXT NOT NULL DEFAULT '{}',
ai_payload TEXT NOT NULL,
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS merchant_offers (
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
sizes TEXT NOT NULL DEFAULT '[]',
size_system TEXT NOT NULL DEFAULT '',
source_url TEXT NOT NULL DEFAULT '',
source TEXT NOT NULL DEFAULT 'seed',
last_checked TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
verification_status TEXT NOT NULL DEFAULT 'unverified',
verified_url TEXT NOT NULL DEFAULT '',
verified_at TEXT,
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
product_id TEXT PRIMARY KEY,
name TEXT NOT NULL,
category TEXT,
subcategory TEXT,
description TEXT,
price REAL,
currency TEXT DEFAULT 'JOD',
old_price REAL,
discount REAL,
brand TEXT,
color TEXT,
size TEXT,
size_system TEXT,
material TEXT,
features TEXT,
tags TEXT,
rating REAL,
review_count INTEGER,
image_url TEXT,
store_name TEXT,
product_url TEXT,
shipping_cost REAL,
availability TEXT,
location TEXT,
last_checked TEXT,
source TEXT,
raw_data TEXT,
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
session_id TEXT PRIMARY KEY,
user_ip TEXT,
intent_json TEXT NOT NULL DEFAULT '{}',
paid INTEGER DEFAULT 0,
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deal_access (
deal_id TEXT PRIMARY KEY,
session_id TEXT NOT NULL,
offer_id INTEGER NOT NULL,
status TEXT NOT NULL DEFAULT 'pending_payment',
transaction_ref TEXT,
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
submitted_at TEXT,
approved_at TEXT,
approved_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_deal_access_session ON deal_access(session_id);
CREATE INDEX IF NOT EXISTS idx_deal_access_offer ON deal_access(offer_id);
CREATE INDEX IF NOT EXISTS idx_deal_access_status ON deal_access(status);

CREATE TABLE IF NOT EXISTS session_offers (
session_id TEXT NOT NULL,
offer_id INTEGER NOT NULL,
rank_order INTEGER NOT NULL DEFAULT 0,
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
PRIMARY KEY (session_id, offer_id)
);
CREATE INDEX IF NOT EXISTS idx_session_offers_session ON session_offers(session_id);

CREATE TABLE IF NOT EXISTS users (
id INTEGER PRIMARY KEY AUTOINCREMENT,
full_name TEXT NOT NULL,
phone TEXT NOT NULL UNIQUE,
password_salt TEXT NOT NULL,
password_hash TEXT NOT NULL,
created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_sessions (
token_hash TEXT PRIMARY KEY,
user_id INTEGER NOT NULL,
expires_at TEXT NOT NULL,
created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS carts (
user_id INTEGER PRIMARY KEY,
updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cart_items (
user_id INTEGER NOT NULL,
offer_id INTEGER NOT NULL,
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
PRIMARY KEY (user_id, offer_id)
);

CREATE TABLE IF NOT EXISTS payment_batches (
batch_id TEXT PRIMARY KEY,
user_id INTEGER NOT NULL,
item_count INTEGER NOT NULL,
total_jod REAL NOT NULL,
payer_name TEXT,
status TEXT NOT NULL DEFAULT 'draft',
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
submitted_at TEXT,
approved_at TEXT,
rejected_at TEXT,
approved_by TEXT
);

CREATE TABLE IF NOT EXISTS payment_batch_items (
batch_id TEXT NOT NULL,
offer_id INTEGER NOT NULL,
deal_id TEXT NOT NULL UNIQUE,
PRIMARY KEY (batch_id, offer_id)
);

CREATE TABLE IF NOT EXISTS suggestions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
message TEXT NOT NULL,
created_at TEXT DEFAULT CURRENT_TIMESTAMP,
status TEXT NOT NULL DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS payment_events (
id INTEGER PRIMARY KEY AUTOINCREMENT,
external_id TEXT NOT NULL UNIQUE,
payer_name TEXT NOT NULL,
amount_jod REAL NOT NULL,
raw_message TEXT,
received_at TEXT DEFAULT CURRENT_TIMESTAMP,
matched_batch_id TEXT,
match_status TEXT NOT NULL DEFAULT 'unmatched'
);
CREATE INDEX IF NOT EXISTS idx_payment_events_status ON payment_events(match_status);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_cart_items_user ON cart_items(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_batches_user ON payment_batches(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_batches_status ON payment_batches(status);

CREATE INDEX IF NOT EXISTS idx_offers_category ON merchant_offers(category);
CREATE INDEX IF NOT EXISTS idx_offers_price ON merchant_offers(price_jod);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
"""

def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    """Ensure schema; NEVER drop existing data. Seed only missing rows."""
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            _ensure_columns(conn, "merchant_offers", {
                "sizes": "TEXT NOT NULL DEFAULT '[]'",
                "size_system": "TEXT NOT NULL DEFAULT ''",
                "store_url": "TEXT NOT NULL DEFAULT ''",
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT 'seed'",
                "last_checked": "TEXT NOT NULL DEFAULT ''",
                "verification_status": "TEXT NOT NULL DEFAULT 'unverified'",
                "verified_url": "TEXT NOT NULL DEFAULT ''",
                "verified_at": "TEXT",
            })
            _ensure_columns(conn, "sessions", {
                "user_id": "INTEGER",
                "raw_query": "TEXT NOT NULL DEFAULT ''",
            })
            _ensure_columns(conn, "deal_access", {
                "user_id": "INTEGER",
                "batch_id": "TEXT",
                "payer_name": "TEXT",
            })
            _ensure_columns(conn, "buyer_intents", {
                "budget_json": "TEXT NOT NULL DEFAULT '{}'",
                "sizes_json": "TEXT NOT NULL DEFAULT '{}'",
            })
            rows = [(
                o.id, o.merchant_name, o.title, o.category, o.price_jod,
                serialize_json(o.tags), serialize_json(o.colors), serialize_json(o.style),
                o.city, o.description, (o.image_url if safe_public_url(o.image_url) and o.image_url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) else fallback_image_url(o.id)), o.whatsapp_url, o.instagram_url, o.store_url,
                serialize_json(o.sizes), o.size_system, "", "seed",
            ) for o in SEED_OFFERS]
            conn.executemany(
                """
                INSERT OR IGNORE INTO merchant_offers
                (id, merchant_name, title, category, price_jod, tags, colors, style, city,
                 description, image_url, whatsapp_url, instagram_url, store_url, sizes, size_system, source_url, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.execute("UPDATE merchant_offers SET whatsapp_url = '' WHERE whatsapp_url IN ('https://wa.me','http://wa.me','wa.me')")
            conn.execute("UPDATE merchant_offers SET instagram_url = '' WHERE instagram_url IN ('https://instagram.com','http://instagram.com','instagram.com')")
            conn.execute("UPDATE merchant_offers SET store_url = '' WHERE store_url IS NULL OR store_url IN ('https://wa.me','http://wa.me','https://instagram.com','http://instagram.com','wa.me','instagram.com')")
            conn.execute("UPDATE merchant_offers SET image_url = ('/media/offers/' || id || '.svg') WHERE image_url IN ('https://unsplash.com','http://unsplash.com','unsplash.com','https://www.unsplash.com','http://www.unsplash.com','www.unsplash.com','unsplash.com/') OR image_url IS NULL OR image_url = ''")
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM merchant_offers").fetchone()[0]
            LOGGER.info("DB ready at %s — %d offers", DB_PATH, count)
        finally:
            conn.close()


_DB_READY = False


def ensure_db() -> None:
    global _DB_READY
    if not _DB_READY:
        init_db()
        _DB_READY = True


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_buyer_intent(request_id: str, raw_query: str, intent: Dict[str, Any]) -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            cap = intent.get("budget", {}).get("amount")
            conn.execute(
                """
                INSERT INTO buyer_intents
                (request_id, raw_query, categories, colors, styles, product_terms,
                 price_cap_jod, budget_json, sizes_json, ai_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, raw_query,
                    serialize_json(intent.get("categories", [])),
                    serialize_json(intent.get("colors", [])),
                    serialize_json(intent.get("styles", [])),
                    serialize_json(intent.get("product_terms", [])),
                    safe_float(cap),
                    serialize_json(intent.get("budget", {})),
                    serialize_json(intent.get("sizes", {})),
                    serialize_json(intent.get("raw_ai", {})),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def create_session(session_id: str, intent: Dict[str, Any], user_ip: str = "", user_id: Optional[int] = None, raw_query: str = "") -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, user_ip, intent_json, user_id, raw_query, updated_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (session_id, user_ip, serialize_json(intent), user_id, raw_query[:2000]),
            )
            conn.commit()
        finally:
            conn.close()


def get_session_meta(session_id: str) -> Optional[sqlite3.Row]:
    ensure_db()
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()


def get_session_intent(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT intent_json FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["intent_json"])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    finally:
        conn.close()


def get_offer_by_id(offer_id: int) -> Optional[Offer]:
    ensure_db()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM merchant_offers WHERE id = ?", (offer_id,)).fetchone()
        if not row:
            return None
        return Offer(
            id=row["id"], merchant_name=row["merchant_name"], title=row["title"],
            category=row["category"], price_jod=row["price_jod"],
            tags=read_json_list(row["tags"]), colors=read_json_list(row["colors"]),
            style=read_json_list(row["style"]), city=row["city"],
            description=row["description"], image_url=(row["image_url"] or fallback_image_url(int(row["id"]))),
            whatsapp_url=row["whatsapp_url"], instagram_url=row["instagram_url"],
            sizes=read_json_list(row["sizes"]) if "sizes" in row.keys() else [],
            size_system=row["size_system"] if "size_system" in row.keys() else "",
            store_url=row["store_url"] if "store_url" in row.keys() else "",
        )
    finally:
        conn.close()


def get_deal(deal_id: str) -> Optional[sqlite3.Row]:
    ensure_db()
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM deal_access WHERE deal_id = ?",
            (deal_id,),
        ).fetchone()
    finally:
        conn.close()


def save_session_offers(session_id: str, result_ids: Sequence[int]) -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM session_offers WHERE session_id = ?", (session_id,))
            for idx, offer_id in enumerate(result_ids):
                conn.execute("INSERT OR REPLACE INTO session_offers (session_id, offer_id, rank_order) VALUES (?, ?, ?)", (session_id, int(offer_id), idx))
            conn.commit()
        finally:
            conn.close()


def offer_urls_for_ids(ids: Sequence[int]) -> List[str]:
    ids = [int(x) for x in ids if str(x).isdigit()]
    if not ids:
        return []
    conn = get_connection()
    try:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT store_url FROM merchant_offers WHERE id IN ({marks})", ids).fetchall()
        return [str(r["store_url"]) for r in rows if safe_public_url(str(r["store_url"] or ""))]
    finally:
        conn.close()


def upsert_live_offers(items: Sequence[Dict[str, Any]]) -> List[int]:
    ids: List[int] = []
    with DB_LOCK:
        conn = get_connection()
        try:
            for item in items:
                oid = int(item["id"]); ids.append(oid)
                existing = conn.execute("SELECT id FROM merchant_offers WHERE id=?", (oid,)).fetchone()
                values = (
                    item["merchant_name"], item["title"], item["category"], float(item["price_jod"]),
                    serialize_json(item.get("tags", [])), serialize_json(item.get("colors", [])), serialize_json(item.get("style", [])),
                    item.get("city", "الأردن"), item.get("description", ""), item.get("image_url") or fallback_image_url(oid),
                    "", "", item.get("store_url", ""), serialize_json(item.get("sizes", [])), item.get("size_system", ""),
                    item.get("store_url", ""), "google_search",
                )
                if existing:
                    conn.execute('''UPDATE merchant_offers SET merchant_name=?,title=?,category=?,price_jod=?,tags=?,colors=?,style=?,city=?,description=?,image_url=?,whatsapp_url=?,instagram_url=?,store_url=?,sizes=?,size_system=?,source_url=?,source=?,last_checked=CURRENT_TIMESTAMP,verification_status='verified',verified_url=?,verified_at=CURRENT_TIMESTAMP WHERE id=?''', values + (item.get("store_url", ""), oid))
                else:
                    conn.execute('''INSERT INTO merchant_offers (id,merchant_name,title,category,price_jod,tags,colors,style,city,description,image_url,whatsapp_url,instagram_url,store_url,sizes,size_system,source_url,source,verification_status,verified_url,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (oid,) + values + ("verified", item.get("store_url", ""), time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        finally:
            conn.close()
    return ids


def create_or_get_deal(session_id: str, offer_id: int) -> Optional[Dict[str, Any]]:
    intent = get_session_intent(session_id)
    if intent is None:
        return None
    conn_check = get_connection()
    try:
        allowed = conn_check.execute("SELECT 1 FROM session_offers WHERE session_id=? AND offer_id=?", (session_id, offer_id)).fetchone()
    finally:
        conn_check.close()
    if not allowed:
        return None

    with DB_LOCK:
        conn = get_connection()
        try:
            existing = conn.execute(
                """SELECT * FROM deal_access
                   WHERE session_id = ? AND offer_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id, offer_id),
            ).fetchone()
            if existing and existing["status"] in {"pending_payment", "payment_submitted", "paid"}:
                return dict(existing)

            deal_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO deal_access
                   (deal_id, session_id, offer_id, status)
                   VALUES (?, ?, ?, 'pending_payment')""",
                (deal_id, session_id, offer_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM deal_access WHERE deal_id = ?", (deal_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def submit_deal_payment(deal_id: str, session_id: str, transaction_ref: str) -> Optional[Dict[str, Any]]:
    transaction_ref = transaction_ref.strip()[:160]
    if not transaction_ref:
        return None
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM deal_access WHERE deal_id = ? AND session_id = ?",
                (deal_id, session_id),
            ).fetchone()
            if not row:
                return None
            if row["status"] == "paid":
                return dict(row)
            conn.execute(
                """UPDATE deal_access
                   SET status = 'payment_submitted', transaction_ref = ?, submitted_at = CURRENT_TIMESTAMP
                   WHERE deal_id = ? AND session_id = ?""",
                (transaction_ref, deal_id, session_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM deal_access WHERE deal_id = ?", (deal_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def deal_status_for_session(deal_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    row = get_deal(deal_id)
    if not row or row["session_id"] != session_id:
        return None
    return dict(row)


def set_deal_status(deal_id: str, status: str) -> bool:
    if status not in {"paid", "rejected"}:
        return False
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT deal_id FROM deal_access WHERE deal_id = ?", (deal_id,)).fetchone()
            if not row:
                return False
            if status == "paid":
                conn.execute(
                    """UPDATE deal_access
                       SET status = 'paid', approved_at = CURRENT_TIMESTAMP, approved_by = 'admin'
                       WHERE deal_id = ?""",
                    (deal_id,),
                )
            else:
                conn.execute(
                    "UPDATE deal_access SET status = 'rejected' WHERE deal_id = ?",
                    (deal_id,),
                )
            conn.commit()
            return True
        finally:
            conn.close()


def list_pending_deals() -> List[Dict[str, Any]]:
    ensure_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT d.*, m.merchant_name, m.title, m.price_jod
               FROM deal_access d
               LEFT JOIN merchant_offers m ON m.id = d.offer_id
               WHERE d.status = 'payment_submitted'
               ORDER BY d.submitted_at DESC, d.created_at DESC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_offer_links(offer_id: int, store_url: str, whatsapp_url: str, instagram_url: str, image_url: str) -> bool:
    def clean_url(value: str, label: str, allow_empty: bool = True) -> str:
        value = value.strip()[:2000]
        if not value and allow_empty:
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{label} يجب أن يكون رابطًا يبدأ بـ https://")
        host = (parsed.hostname or "").lower()
        if host in {"wa.me", "instagram.com", "www.instagram.com", "unsplash.com", "www.unsplash.com"} and not parsed.path.strip("/"):
            raise ValueError(f"{label}: استخدمي رابط الصفحة/المتجر الحقيقي، وليس الرابط العام للموقع.")
        return value

    store_url = clean_url(store_url, "رابط المتجر")
    if store_url:
        verified, final_url, verify_status = verify_external_url(store_url)
        if not verified:
            raise ValueError(f"رابط المتجر لا يمكن التحقق منه الآن ({verify_status}). استخدمي رابط صفحة المنتج/المتجر الحقيقي.")
        store_url = final_url or store_url
    whatsapp_url = clean_url(whatsapp_url, "رابط واتساب")
    instagram_url = clean_url(instagram_url, "رابط إنستغرام")
    image_url = clean_url(image_url, "رابط الصورة")

    with DB_LOCK:
        conn = get_connection()
        try:
            cur = conn.execute(
                """UPDATE merchant_offers
                   SET store_url = ?, whatsapp_url = ?, instagram_url = ?, image_url = ?
                   WHERE id = ?""",
                (store_url, whatsapp_url, instagram_url, image_url, offer_id),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()



def bulk_update_offer_links(raw_text: str) -> Dict[str, Any]:
    """Bulk-update offer URLs.

    Accepted formats per line:
      30|https://example.com
      30,https://example.com
      30|https://store|https://wa.me/...|https://instagram.com/...|https://image...
    A CSV header row is allowed. Blank/comment lines are ignored.
    """
    import io
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("الصق قائمة الروابط أولاً.")

    rows = []
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'):
            continue
        if '|' in line:
            parts=[x.strip() for x in line.split('|')]
        else:
            try:
                parts=next(csv.reader([line]))
                parts=[x.strip() for x in parts]
            except Exception:
                parts=[x.strip() for x in line.split(',')]
        if not parts:
            continue
        first=parts[0].lower()
        if first in {'id','offer_id','deal_id','رقم','رقم الإعلان'}:
            continue
        try:
            offer_id=int(parts[0])
        except Exception:
            raise ValueError(f"رقم الإعلان غير صالح في السطر: {line}")
        store_url=parts[1] if len(parts)>1 else ''
        whatsapp_url=parts[2] if len(parts)>2 else None
        instagram_url=parts[3] if len(parts)>3 else None
        image_url=parts[4] if len(parts)>4 else None
        rows.append((offer_id, store_url, whatsapp_url, instagram_url, image_url))

    if not rows:
        raise ValueError("لم أجد أي أسطر صالحة.")

    updated=[]; skipped=[]
    with DB_LOCK:
        conn=get_connection()
        try:
            for offer_id, store_url, wa, ig, img in rows:
                exists=conn.execute("SELECT whatsapp_url, instagram_url, image_url FROM merchant_offers WHERE id=?", (offer_id,)).fetchone()
                if not exists:
                    skipped.append(offer_id); continue
                if wa is None: wa=exists[0] or ''
                if ig is None: ig=exists[1] or ''
                if img is None: img=exists[2] or ''
                # Reuse single-offer validation.
                ok=update_offer_links(offer_id, store_url, wa, ig, img)
                if ok: updated.append(offer_id)
            conn.commit()
        finally:
            conn.close()
    return {"updated": updated, "skipped": skipped, "count": len(updated)}

def list_admin_offers() -> List[Dict[str, Any]]:
    ensure_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, merchant_name, title, price_jod, store_url, whatsapp_url, instagram_url, image_url, verification_status, verified_url, verified_at
               FROM merchant_offers ORDER BY id ASC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()




def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
    if len(password) < 6:
        raise ValueError("كلمة المرور يجب أن تكون 6 أحرف/أرقام على الأقل.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
        return hmac.compare_digest(digest.hex(), expected_hex)
    except Exception:
        return False


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00962"):
        digits = digits[5:]
    if digits.startswith("962") and len(digits) > 9:
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) < 8:
        raise ValueError("أدخلي رقم هاتف صحيح.")
    return "+962" + digits


def register_user(full_name: str, phone: str, password: str) -> str:
    full_name = " ".join((full_name or "").split())[:100]
    if len(full_name) < 2:
        raise ValueError("أدخلي اسمك.")
    phone = _normalize_phone(phone)
    salt, digest = _hash_password(password)
    with DB_LOCK:
        conn = get_connection()
        try:
            try:
                cur = conn.execute("INSERT INTO users (full_name, phone, password_salt, password_hash) VALUES (?, ?, ?, ?)", (full_name, phone, salt, digest))
            except sqlite3.IntegrityError:
                raise ValueError("هذا الرقم مسجّل من قبل. استخدمي تسجيل الدخول.")
            conn.execute("INSERT OR IGNORE INTO carts (user_id) VALUES (?)", (cur.lastrowid,))
            conn.commit()
            user_id = int(cur.lastrowid)
        finally:
            conn.close()
    return create_auth_token(user_id)


def login_user(phone: str, password: str) -> str:
    phone = _normalize_phone(phone)
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        if not row or not _verify_password(password, row["password_salt"], row["password_hash"]):
            raise ValueError("رقم الهاتف أو كلمة المرور غير صحيحة.")
        return create_auth_token(int(row["id"]))
    finally:
        conn.close()


def create_auth_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + AUTH_DAYS * 86400))
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at < CURRENT_TIMESTAMP")
            conn.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user_id, expires_at))
            conn.commit()
        finally:
            conn.close()
    return token


def get_user_from_token(token: str) -> Optional[sqlite3.Row]:
    token = (token or "").strip()
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        return conn.execute(
            """SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at >= CURRENT_TIMESTAMP""",
            (token_hash,),
        ).fetchone()
    finally:
        conn.close()


def get_user_cart(user_id: int) -> Dict[str, Any]:
    ensure_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT m.id, m.merchant_name, m.title, m.price_jod, m.image_url
               FROM cart_items c JOIN merchant_offers m ON m.id = c.offer_id
               WHERE c.user_id = ? ORDER BY c.created_at DESC""",
            (user_id,),
        ).fetchall()
        items = [dict(r) for r in rows]
        total = round(len(items) * DEAL_PRICE_JOD, 2)
        return {"items": items, "count": len(items), "total_jod": total}
    finally:
        conn.close()


def user_has_paid_offer(user_id: int, offer_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT 1 FROM deal_access WHERE user_id = ? AND offer_id = ? AND status = 'paid' LIMIT 1""",
            (user_id, offer_id),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def add_to_cart(user_id: int, offer_id: int) -> Dict[str, Any]:
    ensure_db()
    if get_offer_by_id(offer_id) is None:
        raise ValueError("الإعلان غير موجود.")
    if user_has_paid_offer(user_id, offer_id):
        raise ValueError("هذا الإعلان مفتوح لديك مسبقًا.")
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO carts (user_id, updated_at) VALUES (?, CURRENT_TIMESTAMP)", (user_id,))
            conn.execute("INSERT OR IGNORE INTO cart_items (user_id, offer_id) VALUES (?, ?)", (user_id, offer_id))
            conn.execute("UPDATE carts SET updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
    return get_user_cart(user_id)


def remove_from_cart(user_id: int, offer_id: int) -> Dict[str, Any]:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM cart_items WHERE user_id = ? AND offer_id = ?", (user_id, offer_id))
            conn.execute("UPDATE carts SET updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
    return get_user_cart(user_id)


def start_checkout(user_id: int) -> Dict[str, Any]:
    cart = get_user_cart(user_id)
    if not cart["items"]:
        raise ValueError("سلتك فارغة.")
    item_ids = [int(x["id"]) for x in cart["items"]]
    if any(user_has_paid_offer(user_id, oid) for oid in item_ids):
        raise ValueError("يوجد إعلان مفتوح لديك بالفعل في السلة. حدّثي السلة ثم حاولي مرة أخرى.")
    batch_id = uuid.uuid4().hex
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("INSERT INTO payment_batches (batch_id, user_id, item_count, total_jod, status) VALUES (?, ?, ?, ?, 'draft')", (batch_id, user_id, cart["count"], cart["total_jod"]))
            for offer_id in item_ids:
                deal_id = uuid.uuid4().hex
                conn.execute("INSERT INTO deal_access (deal_id, session_id, offer_id, status, user_id, batch_id) VALUES (?, ?, ?, 'pending_payment', ?, ?)", (deal_id, "", offer_id, user_id, batch_id))
                conn.execute("INSERT INTO payment_batch_items (batch_id, offer_id, deal_id) VALUES (?, ?, ?)", (batch_id, offer_id, deal_id))
            conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
    return get_payment_batch(user_id, batch_id)


def get_payment_batch(user_id: int, batch_id: str) -> Dict[str, Any]:
    conn = get_connection()
    try:
        b = conn.execute("SELECT * FROM payment_batches WHERE batch_id = ? AND user_id = ?", (batch_id, user_id)).fetchone()
        if not b:
            raise ValueError("الطلب غير موجود.")
        items = conn.execute(
            """SELECT m.id, m.title, m.merchant_name, m.price_jod, p.deal_id, d.status, m.store_url, m.whatsapp_url, m.instagram_url
               FROM payment_batch_items p JOIN merchant_offers m ON m.id=p.offer_id
               JOIN deal_access d ON d.deal_id=p.deal_id WHERE p.batch_id=? ORDER BY m.id""",
            (batch_id,),
        ).fetchall()
        out = dict(b)
        out["items"] = [dict(r) for r in items]
        return out
    finally:
        conn.close()


def submit_checkout(user_id: int, batch_id: str, payer_name: str) -> Dict[str, Any]:
    payer_name = " ".join((payer_name or "").split())[:120]
    if len(payer_name) < 2:
        raise ValueError("اكتبي الاسم الذي تم التحويل منه كما يظهر في إشعار البنك.")
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM payment_batches WHERE batch_id=? AND user_id=?", (batch_id, user_id)).fetchone()
            if not row:
                raise ValueError("الطلب غير موجود.")
            if row["status"] not in {"draft", "rejected"}:
                return get_payment_batch(user_id, batch_id)
            conn.execute("UPDATE payment_batches SET payer_name=?, status='payment_submitted', submitted_at=CURRENT_TIMESTAMP WHERE batch_id=?", (payer_name, batch_id))
            conn.execute("UPDATE deal_access SET status='payment_submitted', payer_name=? WHERE batch_id=?", (payer_name, batch_id))
            conn.commit()
        finally:
            conn.close()
    return get_payment_batch(user_id, batch_id)


def list_user_batches(user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        batches = conn.execute("SELECT * FROM payment_batches WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        out=[]
        for b in batches:
            item_rows = conn.execute("""SELECT m.id,m.title,m.merchant_name,m.price_jod,p.deal_id,d.status,m.store_url,m.whatsapp_url,m.instagram_url
                                      FROM payment_batch_items p JOIN merchant_offers m ON m.id=p.offer_id
                                      JOIN deal_access d ON d.deal_id=p.deal_id WHERE p.batch_id=? ORDER BY m.id""", (b["batch_id"],)).fetchall()
            x=dict(b); x["items"]= [dict(r) for r in item_rows]; out.append(x)
        return out
    finally:
        conn.close()


def list_pending_batches() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows=conn.execute("""SELECT p.*, u.full_name, u.phone FROM payment_batches p JOIN users u ON u.id=p.user_id
                             WHERE p.status='payment_submitted' ORDER BY p.submitted_at DESC""").fetchall()
        out=[]
        for b in rows:
            x=dict(b)
            x["items"]=[dict(r) for r in conn.execute("""SELECT m.title,m.merchant_name,m.price_jod,p.offer_id FROM payment_batch_items p JOIN merchant_offers m ON m.id=p.offer_id WHERE p.batch_id=? ORDER BY m.id""", (b["batch_id"],)).fetchall()]
            out.append(x)
        return out
    finally:
        conn.close()


def set_batch_status(batch_id: str, status: str) -> bool:
    if status not in {"paid", "rejected"}:
        return False
    with DB_LOCK:
        conn=get_connection()
        try:
            b=conn.execute("SELECT * FROM payment_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not b: return False
            if status=='paid':
                conn.execute("UPDATE payment_batches SET status='paid', approved_at=CURRENT_TIMESTAMP, approved_by='admin' WHERE batch_id=?", (batch_id,))
                conn.execute("UPDATE deal_access SET status='paid', approved_at=CURRENT_TIMESTAMP, approved_by='admin' WHERE batch_id=?", (batch_id,))
            else:
                conn.execute("UPDATE payment_batches SET status='rejected', rejected_at=CURRENT_TIMESTAMP WHERE batch_id=?", (batch_id,))
                conn.execute("UPDATE deal_access SET status='rejected' WHERE batch_id=?", (batch_id,))
            conn.commit(); return True
        finally: conn.close()



def _normalize_person_name(value: str) -> str:
    text = normalize_text(value or "")
    text = re.sub(r"[إأآٱ]", "ا", text)
    text = text.replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def process_incoming_payment(external_id: str, payer_name: str, amount_jod: float, raw_message: str = "") -> Dict[str, Any]:
    if not PAYMENT_WEBHOOK_SECRET:
        raise ValueError("لم يتم إعداد PAYMENT_WEBHOOK_SECRET بعد.")
    external_id = (external_id or "").strip()[:200]
    payer_name = " ".join((payer_name or "").split())[:120]
    amount_jod = round(float(amount_jod), 2)
    if not external_id or not payer_name or amount_jod <= 0:
        raise ValueError("بيانات الحوالة غير مكتملة.")
    with DB_LOCK:
        conn = get_connection()
        try:
            try:
                conn.execute("INSERT INTO payment_events (external_id,payer_name,amount_jod,raw_message) VALUES (?,?,?,?)", (external_id,payer_name,amount_jod,raw_message[:4000]))
            except sqlite3.IntegrityError:
                row=conn.execute("SELECT * FROM payment_events WHERE external_id=?", (external_id,)).fetchone()
                return dict(row) if row else {"match_status":"duplicate"}
            candidates=conn.execute("SELECT * FROM payment_batches WHERE status='payment_submitted' AND ABS(total_jod-?) < 0.01 ORDER BY submitted_at ASC", (amount_jod,)).fetchall()
            name_key=_normalize_person_name(payer_name)
            matches=[b for b in candidates if _normalize_person_name(b["payer_name"] or "") == name_key]
            if len(matches)==1:
                batch=matches[0]
                conn.execute("UPDATE payment_batches SET status='paid', approved_at=CURRENT_TIMESTAMP, approved_by='auto' WHERE batch_id=?", (batch["batch_id"],))
                conn.execute("UPDATE deal_access SET status='paid', approved_at=CURRENT_TIMESTAMP, approved_by='auto' WHERE batch_id=?", (batch["batch_id"],))
                conn.execute("UPDATE payment_events SET matched_batch_id=?, match_status='matched' WHERE external_id=?", (batch["batch_id"], external_id))
            elif len(matches)>1:
                conn.execute("UPDATE payment_events SET match_status='ambiguous' WHERE external_id=?", (external_id,))
            else:
                conn.execute("UPDATE payment_events SET match_status='unmatched' WHERE external_id=?", (external_id,))
            conn.commit()
            row=conn.execute("SELECT * FROM payment_events WHERE external_id=?", (external_id,)).fetchone()
            return dict(row) if row else {"match_status":"unmatched"}
        finally: conn.close()

def list_suggestions() -> List[Dict[str, Any]]:
    conn=get_connection()
    try:
        rows=conn.execute("""SELECT s.*,u.full_name,u.phone FROM suggestions s LEFT JOIN users u ON u.id=s.user_id ORDER BY s.created_at DESC LIMIT 200""").fetchall()
        return [dict(r) for r in rows]
    finally: conn.close()


def save_suggestion(user_id: Optional[int], message: str) -> None:
    message = " ".join((message or "").split())[:1000]
    if len(message) < 3: raise ValueError("اكتبي الاقتراح أولًا.")
    with DB_LOCK:
        conn=get_connection()
        try:
            conn.execute("INSERT INTO suggestions (user_id,message) VALUES (?,?)", (user_id,message)); conn.commit()
        finally: conn.close()

def _admin_ok(password: str) -> bool:
    return bool(ADMIN_PASSWORD) and hmac.compare_digest(password, ADMIN_PASSWORD)


def _admin_page() -> str:
    return r"""
<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>إدارة التوصية</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-neutral-100 text-neutral-900"><main class="max-w-6xl mx-auto p-4 sm:p-8">
<div class="flex items-center justify-between gap-4 mb-6"><div><h1 class="text-3xl font-black">لوحة إدارة التوصية</h1><p class="text-sm text-neutral-500 mt-1">المدفوعات، روابط المتاجر، واقتراحات المستخدمين.</p></div><a href="/" class="rounded-xl bg-white px-4 py-2 font-black border">الموقع</a></div>
<div id="loginBox" class="bg-white rounded-3xl p-5 border shadow-sm"><div class="font-black text-lg">تسجيل دخول الإدارة</div><div class="mt-4 flex gap-2"><input id="adminPassword" type="password" class="flex-1 rounded-xl bg-neutral-100 px-4 py-3 outline-none" placeholder="كلمة مرور الإدارة"><button onclick="login()" class="rounded-xl bg-neutral-950 text-white px-5 font-black">دخول</button></div><div id="loginMsg" class="text-sm mt-3"></div></div>
<div id="panel" class="hidden">
<section class="mt-6 bg-white rounded-3xl p-5 border shadow-sm"><div class="flex items-center justify-between gap-3"><div><h2 class="text-xl font-black">سلات/دفعات قيد التحقق</h2><p class="text-sm text-neutral-500">قارني الاسم والمبلغ مع حركة CliQ التي وصلت لحسابك ثم اعتمدي الدفعة كاملة.</p></div><button onclick="loadAll()" class="rounded-xl bg-neutral-100 px-4 py-2 font-black">تحديث</button></div><div id="batches" class="mt-4 space-y-3"></div></section>
<section class="mt-6 bg-white rounded-3xl p-5 border shadow-sm"><div><h2 class="text-xl font-black">روابط الإعلانات — تحديث جماعي</h2><p class="text-sm text-neutral-500">لا حاجة لتعديل 42 إعلانًا واحدًا واحدًا. الصق الروابط دفعة واحدة بصيغة <b>رقم الإعلان|الرابط</b>، سطر لكل إعلان. مثال: <code>30|https://example.com</code></p><div class="mt-3 flex flex-wrap gap-2"><button onclick="downloadTemplate()" class="rounded-xl bg-neutral-100 px-4 py-2 font-black">تحميل قالب 42 إعلانًا</button><button onclick="bulkSave()" class="rounded-xl bg-neutral-950 text-white px-4 py-2 font-black">حفظ الروابط دفعة واحدة</button></div><textarea id="bulkLinks" class="mt-3 w-full min-h-56 rounded-2xl bg-neutral-100 p-4 font-mono text-sm" dir="ltr" placeholder="1|https://example.com
2|https://example.com
30|https://example.com"></textarea><div id="bulkMsg" class="mt-2 text-sm"></div></div><details class="mt-5"><summary class="cursor-pointer font-black">تعديل إعلان واحد يدويًا (اختياري)</summary><div id="offers" class="mt-4 space-y-4"></div></details></section>
<section class="mt-6 bg-white rounded-3xl p-5 border shadow-sm"><div><h2 class="text-xl font-black">صندوق الاقتراحات</h2><p class="text-sm text-neutral-500">آخر اقتراحات المستخدمين.</p></div><div id="suggestions" class="mt-4 space-y-3"></div></section>
</div></main>
<script>
let password='';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.error||'حدث خطأ');return d;}
async function login(){const p=document.getElementById('adminPassword').value;if(!p)return;try{await api('/api/admin/login',{password:p});password=p;document.getElementById('loginBox').classList.add('hidden');document.getElementById('panel').classList.remove('hidden');loadAll();}catch(e){document.getElementById('loginMsg').textContent=e.message;document.getElementById('loginMsg').className='text-sm mt-3 text-red-600 font-bold';}}
async function loadBatches(){const d=await api('/api/admin/pending-batches',{password});const box=document.getElementById('batches');if(!d.batches.length){box.innerHTML='<div class="rounded-2xl bg-neutral-50 p-5 text-sm text-neutral-500">لا توجد دفعات معلقة.</div>';return;}box.innerHTML=d.batches.map(x=>`<article class="rounded-2xl border p-4"><div class="font-black">${esc(x.full_name)} — ${esc(x.phone)}</div><div class="text-sm text-neutral-500 mt-1">Batch: ${esc(x.batch_id)} · المبلغ المطلوب: <span class="font-black text-neutral-900">${esc(x.total_jod)} د.أ</span> · عدد الإعلانات: ${esc(x.item_count)}</div><div class="mt-2 text-sm">اسم المحوّل الذي أدخله المستخدم: <span class="font-black">${esc(x.payer_name||'—')}</span></div><div class="mt-3 rounded-2xl bg-neutral-50 p-3 text-sm">${x.items.map(i=>`<div>• ${esc(i.title)} — ${esc(i.merchant_name)} — ${esc(i.price_jod)} د.أ</div>`).join('')}</div><div class="mt-3 flex gap-2"><button onclick="decideBatch('${esc(x.batch_id)}','paid')" class="rounded-xl bg-emerald-600 text-white px-4 py-2 font-black">تم التحقق — فتح الطلبات</button><button onclick="decideBatch('${esc(x.batch_id)}','rejected')" class="rounded-xl bg-red-50 text-red-700 px-4 py-2 font-black">رفض</button></div></article>`).join('');}
async function decideBatch(id,action){if(action==='paid'&&!confirm('هل تأكدتِ من وصول المبلغ المطلوب؟'))return;try{await api('/api/admin/batch-decision',{password,batch_id:id,action});loadAll();}catch(e){alert(e.message);}}
async function loadOffers(){const d=await api('/api/admin/offers',{password});document.getElementById('offers').innerHTML=d.offers.map(x=>`<article class="rounded-2xl border p-4"><div class="font-black">#${x.id} — ${esc(x.merchant_name)} — ${esc(x.title)}</div><div class="text-xs mt-1 ${x.store_url?'text-emerald-600':'text-red-600'}">${x.store_url?'رابط المتجر مضبوط':'رابط المتجر غير مضاف بعد'}</div><div class="grid md:grid-cols-4 gap-2 mt-3"><input data-id="${x.id}" data-field="store_url" value="${esc(x.store_url)}" class="rounded-xl bg-neutral-100 px-3 py-3 text-sm" placeholder="رابط المتجر الحقيقي"><input data-id="${x.id}" data-field="whatsapp_url" value="${esc(x.whatsapp_url)}" class="rounded-xl bg-neutral-100 px-3 py-3 text-sm" placeholder="واتساب"><input data-id="${x.id}" data-field="instagram_url" value="${esc(x.instagram_url)}" class="rounded-xl bg-neutral-100 px-3 py-3 text-sm" placeholder="إنستغرام"><input data-id="${x.id}" data-field="image_url" value="${esc(x.image_url)}" class="rounded-xl bg-neutral-100 px-3 py-3 text-sm" placeholder="رابط الصورة"><button onclick="saveOffer(${x.id})" class="rounded-xl bg-neutral-950 text-white px-4 py-3 font-black md:col-span-4">حفظ</button></div></article>`).join('');}
async function saveOffer(id){const get=f=>document.querySelector(`[data-id="${id}"][data-field="${f}"]`).value;try{await api('/api/admin/offer-update',{password,offer_id:id,store_url:get('store_url'),whatsapp_url:get('whatsapp_url'),instagram_url:get('instagram_url'),image_url:get('image_url')});alert('تم الحفظ');loadOffers();}catch(e){alert(e.message);}}
function downloadTemplate(){fetch('/api/admin/offers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})}).then(r=>r.json()).then(d=>{let csv='id,merchant_name,title,store_url\n'+d.offers.map(x=>`${x.id},"${String(x.merchant_name).replaceAll('\"','\"\"')}","${String(x.title).replaceAll('\"','\"\"')}",${x.store_url||''}`).join('\n');const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='al-tawseya-offer-links.csv';a.click();URL.revokeObjectURL(a.href);}).catch(e=>alert(e.message));}
async function bulkSave(){const text=document.getElementById('bulkLinks').value;const msg=document.getElementById('bulkMsg');msg.textContent='جارٍ الحفظ...';msg.className='mt-2 text-sm text-neutral-500';try{const d=await api('/api/admin/bulk-offer-links',{password,raw_text:text});msg.textContent=`تم تحديث ${d.count} إعلانًا.`+(d.skipped?.length?` الإعلانات غير الموجودة: ${d.skipped.join(', ')}`:'');msg.className='mt-2 text-sm text-emerald-700 font-bold';loadOffers();}catch(e){msg.textContent=e.message;msg.className='mt-2 text-sm text-red-600 font-bold';}}
async function loadSuggestions(){const d=await api('/api/admin/suggestions',{password});document.getElementById('suggestions').innerHTML=d.suggestions.length?d.suggestions.map(x=>`<div class="rounded-2xl border p-4"><div class="text-sm font-black">${esc(x.full_name||'زائر')}</div><div class="text-xs text-neutral-400 mt-1">${esc(x.created_at||'')}</div><div class="mt-2">${esc(x.message)}</div></div>`).join(''):'<div class="rounded-2xl bg-neutral-50 p-5 text-sm text-neutral-500">لا يوجد اقتراحات بعد.</div>';}
async function loadAll(){try{await Promise.all([loadBatches(),loadOffers(),loadSuggestions()]);}catch(e){alert(e.message);}}
</script></body></html>
"""


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
    ensure_db()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM merchant_offers ORDER BY id ASC").fetchall()
        offers = []
        for row in rows:
            offers.append(Offer(
                id=row["id"], merchant_name=row["merchant_name"], title=row["title"],
                category=row["category"], price_jod=row["price_jod"],
                tags=read_json_list(row["tags"]), colors=read_json_list(row["colors"]),
                style=read_json_list(row["style"]), city=row["city"],
                description=row["description"], image_url=row["image_url"],
                whatsapp_url=row["whatsapp_url"], instagram_url=row["instagram_url"],
                sizes=read_json_list(row["sizes"]) if "sizes" in row.keys() else [],
                size_system=row["size_system"] if "size_system" in row.keys() else "",
            ))
        return offers
    finally:
        conn.close()

def normalized_set(values: Iterable[str]) -> set:
    return {normalize_text(v) for v in values if v}


def offer_corpus(offer: Offer) -> set:
    corpus: set = set()
    for value in [offer.title, offer.description, *offer.tags, *offer.colors, *offer.style]:
        corpus.update(tokens(value))
    return corpus


def overlap_token_score(query_tokens: set, offer: Offer) -> float:
    if not query_tokens:
        return 0.0
    overlap = query_tokens & offer_corpus(offer)
    return min(1.0, len(overlap) / max(1, min(4, len(query_tokens))))


def budget_allows(budget: Dict[str, Any], price: float) -> bool:
    amount = budget.get("amount")
    kind = budget.get("kind", "none")
    if amount is None:
        return True
    if kind == "hard_max":
        return price <= amount
    if kind == "soft_target":
        return price <= amount * 1.2
    if kind == "flexible":
        return price <= amount * 1.3
    if kind == "cheapest":
        return price <= amount * 1.5
    return True


def size_match(offer: Offer, sizes: Dict[str, Any]) -> Optional[bool]:
    """True = matches, False = explicit mismatch, None = no constraint/unknown."""
    system = sizes.get("system")
    if not system:
        return None
    if not offer.sizes:
        return None
    if system == "bra":
        if offer.size_system != "bra":
            return False
        target = f"{sizes.get('band')}{sizes.get('cup')}".upper()
        return target in {s.upper() for s in offer.sizes}
    if system == "shoe_eu":
        if offer.size_system not in ("shoe_eu", ""):
            return False
        return str(sizes.get("value")) in offer.sizes
    if system == "shoe_us":
        if offer.size_system != "shoe_us":
            return None
        return str(sizes.get("value")) in offer.sizes
    if system == "clothing_letter":
        if offer.size_system != "clothing_letter":
            return False
        return str(sizes.get("value", "")).upper() in {s.upper() for s in offer.sizes}
    if system == "clothing_numeric":
        return None
    if system == "scarf_dimensions":
        return None
    return None


def score_offer(offer: Offer, intent: Dict[str, Any]) -> Tuple[float, List[str]]:
    categories = set(intent.get("categories", []))
    colors = set(intent.get("colors", []))
    excluded_colors = set(intent.get("excluded_colors", []))
    excluded_terms = normalized_set(intent.get("excluded_terms", []))
    styles = set(intent.get("styles", []))
    product_terms = normalized_set(intent.get("product_terms", []))
    budget = intent.get("budget", {}) or {}
    sizes = intent.get("sizes", {}) or {}
    reasons: List[str] = []

    if not budget_allows(budget, offer.price_jod):
        return -1.0, []
    offer_color_canon = {c for c in offer.colors if c in COLOR_SYNONYMS}
    if excluded_colors and offer_color_canon & excluded_colors:
        return -1.0, []
    if excluded_terms and excluded_terms & offer_corpus(offer):
        return -1.0, []
    sm = size_match(offer, sizes)
    if sm is False:
        return -1.0, []

    score = 0.0
    if categories:
        if offer.category in categories:
            score += 45.0
            reasons.append(f"من فئة {CATEGORY_LABELS.get(offer.category, offer.category)} اللي طلبتيها")
        else:
            return -1.0, []
    else:
        score += 20.0

    if colors:
        color_matches = offer_color_canon & colors
        if color_matches:
            score += min(20.0, len(color_matches) * 20.0)
            reasons.append(f"متوفر باللون {COLOR_AR.get(sorted(color_matches)[0], '')} كما طلبتِ")
        else:
            score -= 8.0

    if styles:
        style_matches = set(offer.style) & styles
        score += min(12.0, len(style_matches) * 6.0)
        if style_matches:
            reasons.append("ستايله يوافق طلبك")

    score += 18.0 * overlap_token_score(product_terms, offer)

    clean_title = normalize_text(offer.title)
    clean_desc = normalize_text(offer.description)
    for term in product_terms:
        if len(term) >= 3 and term in clean_title:
            score += 4.0
        elif len(term) >= 3 and term in clean_desc:
            score += 1.5

    amount = budget.get("amount")
    kind = budget.get("kind", "none")
    if amount:
        if kind == "hard_max":
            fit = max(0.0, min(1.0, offer.price_jod / amount))
            score += 3.0 * fit
            reasons.append(f"ضمن ميزانيتك ({offer.price_jod:.0f} من {amount:.0f} د.أ)")
        elif kind == "soft_target":
            distance = abs(offer.price_jod - amount) / max(amount, 1)
            score += max(0.0, 6.0 * (1 - distance))
            reasons.append(f"قريب من ميزانيتك ({offer.price_jod:.0f} د.أ)")
        elif kind == "flexible" and offer.price_jod <= amount:
            score += 4.0
            reasons.append(f"سعره {offer.price_jod:.0f} د.أ وميزانيتك مرنة")

    if sm is True:
        score += 25.0
        system = sizes.get("system")
        if system == "bra":
            reasons.append(f"متوفر بمقاسك {sizes.get('band')}{sizes.get('cup')}")
        elif system == "shoe_eu":
            reasons.append(f"متوفر بمقاسك {sizes.get('value')} أوروبي")
        elif system == "clothing_letter":
            reasons.append(f"متوفر بمقاس {sizes.get('value')}")

    if intent.get("priority") == "cheapest":
        score += max(0.0, 15.0 - offer.price_jod * 0.3)
    if intent.get("priority") == "quality" and "luxury" in offer.style:
        score += 8.0

    return max(0.0, min(100.0, round(score, 2))), (reasons or ["أقرب متاح لطلبك"])


def recommend(intent: Dict[str, Any], raw_query: str = "", refresh_nonce: str = "", avoid_ids: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    avoid_set = {int(x) for x in (avoid_ids or []) if str(x).isdigit()}
    for offer in get_offers():
        if offer.id in avoid_set:
            continue
        score, reasons = score_offer(offer, intent)
        if score < 0:
            continue
        payload = asdict(offer)
        payload.pop("whatsapp_url", None); payload.pop("instagram_url", None); payload.pop("store_url", None)
        payload["image_url"] = payload.get("image_url") or fallback_image_url(offer.id)
        payload["category_label"] = CATEGORY_LABELS.get(offer.category, offer.category)
        payload["score"] = score; payload["reasons"] = reasons
        payload["match_type"] = "توصية دقيقة" if score >= 60 else "توصية قريبة"
        results.append(payload)

    live = live_search_offers(raw_query, intent, refresh_nonce, avoid_urls=offer_urls_for_ids(list(avoid_set))) if raw_query else []
    if live:
        live_ids = upsert_live_offers(live)
        for item, oid in zip(live, live_ids):
            live_offer = Offer(id=oid, merchant_name=item["merchant_name"], title=item["title"], category=item["category"], price_jod=item["price_jod"], tags=item.get("tags", []), colors=item.get("colors", []), style=item.get("style", []), city=item.get("city", "الأردن"), description=item.get("description", ""), image_url=item.get("image_url", fallback_image_url(oid)), whatsapp_url="", instagram_url="", sizes=item.get("sizes", []), size_system=item.get("size_system", ""), store_url=item.get("store_url", ""))
            score, reasons = score_offer(live_offer, intent)
            if score < 0: score, reasons = 55.0, ["نتيجة من بحث مباشر على الويب"]
            payload = dict(item)
            payload.pop("store_url", None); payload.pop("whatsapp_url", None); payload.pop("instagram_url", None)
            payload["category_label"] = CATEGORY_LABELS.get(item["category"], item["category"])
            payload["score"] = max(score, 55.0); payload["reasons"] = list(dict.fromkeys(["نتيجة حديثة من بحث مباشر على الويب"] + reasons))[:4]
            payload["match_type"] = "بحث مباشر"; payload["image_url"] = item.get("image_url") or fallback_image_url(oid)
            results.append(payload)
        live_ids_set = set(live_ids)
        live_results = [x for x in results if x["id"] in live_ids_set]
        static_results = [x for x in results if x["id"] not in live_ids_set]
        static_results.sort(key=lambda x: (-x["score"], x["price_jod"], x["id"]))
        results = live_results + static_results[:max(4, LIVE_SEARCH_MAX // 2)]
    else:
        if intent.get("priority") == "cheapest":
            results.sort(key=lambda item: (item["price_jod"], -item["score"], item["id"]))
        else:
            results.sort(key=lambda item: (-item["score"], item["price_jod"], item["id"]))
        if raw_query:
            results = ai_rerank(raw_query, results)
    return results[:18]

class PaymentProvider:
    """Interface — بدّلي الـ implementation لما يتوفر API رسمي."""
    name = "abstract"

    def instructions(self) -> Dict[str, Any]:
        raise NotImplementedError

    def verify(self, session_id: str) -> bool:
        raise NotImplementedError


class MockCliqProvider(PaymentProvider):
    """CliQ يدوي: لا يوجد API عام لـ CliQ B2C — التحقق تأكيد ذاتي من المستخدمة."""
    name = "cliq_manual"

    def instructions(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "system": "CliQ",
            "deal_price_jod": DEAL_PRICE_JOD,
            "target_bank": PAYMENT_BANK_AR,
            "alias": PAYMENT_ALIAS,
            "auto_verify": False,
            "note": "التحويل يدوي عبر CliQ؛ لا يتم فتح روابط المتجر إلا بعد مراجعة التحويل من لوحة الإدارة.",
        }

    def verify(self, session_id: str) -> bool:
        return False


PAYMENT_PROVIDER: PaymentProvider = MockCliqProvider()
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#111111" />
  <title>التوصية — محرك التوصية الأردني</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root { color-scheme: light; }
    html, body { min-height: 100%; }
    body { font-family: Tahoma, Arial, sans-serif; background: #fafafa; }
    .glass { background: rgba(255,255,255,.86); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
    .soft-shadow { box-shadow: 0 1px 0 rgba(17,24,39,.04), 0 16px 50px rgba(17,24,39,.055); }
    .hide-links a { filter: blur(6px); pointer-events: none; user-select: none; }
    .hide-links::after {
      content: '🔒 روابط المتجر تظهر بعد التحقق من تحويل 1 دينار عبر CliQ';
      position: absolute; inset: 0; display:flex; align-items:center; justify-content:center;
      padding: 1rem; border-radius: 1.25rem; background: rgba(255,255,255,.90);
      color: #171717; font-size:.78rem; font-weight:800; text-align:center;
      border: 1px solid rgba(0,0,0,.05); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
    }
    .safe-bottom { padding-bottom: max(1rem, env(safe-area-inset-bottom)); }
    @keyframes pulse-soft { 0%,100% { opacity:.55; } 50% { opacity:1; } }
    .pulse-soft { animation: pulse-soft 1.4s ease-in-out infinite; }
    .hero-grid { background-image: radial-gradient(rgba(0,0,0,.055) 1px, transparent 1px); background-size: 20px 20px; }
    .no-scrollbar::-webkit-scrollbar { display:none; }
    .no-scrollbar { -ms-overflow-style:none; scrollbar-width:none; }
  </style>
</head>
<body class="text-neutral-900">
  <header class="sticky top-0 z-30 glass border-b border-neutral-200/70">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
      <div class="flex items-center gap-3 min-w-0">
        <div class="w-10 h-10 rounded-2xl bg-neutral-950 text-white flex items-center justify-center font-black">ت</div>
        <div class="min-w-0">
          <div class="font-black leading-5 truncate">محرك التوصية الأردني</div>
          <div class="text-[11px] text-neutral-400 font-bold">التوصية الذكية</div>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" onclick="openCart()" class="relative rounded-2xl bg-neutral-100 px-3 py-2 text-xs font-black">🛒 السلة <span id="cartBadge" class="hidden absolute -top-2 -left-2 min-w-5 h-5 rounded-full bg-emerald-600 text-white text-[10px] flex items-center justify-center"></span></button>
        <button type="button" onclick="openAccount()" id="accountBtn" class="rounded-2xl bg-neutral-950 text-white px-3 py-2 text-xs font-black">تسجيل الدخول</button>
      </div>
    </div>
  </header>

  <main class="hero-grid min-h-[calc(100vh-4rem)]">
    <section class="max-w-6xl mx-auto px-4 sm:px-6 pt-8 sm:pt-12 pb-10">
      <div class="max-w-4xl mx-auto text-center">
        <div class="inline-flex items-center gap-2 rounded-full border border-neutral-200 bg-white/85 px-4 py-2 text-xs font-black text-neutral-700 soft-shadow">
          ✦ اكتبي طلبك بحرية <span class="text-neutral-400">•</span> لهجة أردنية / شامية
        </div>
        <h1 class="mt-5 text-3xl sm:text-5xl font-black tracking-tight">تعبتِ من اللف والدوران؟</h1>
        <p class="mt-3 text-xl sm:text-2xl font-black text-neutral-600">اكتبي شو بدك… والباقي علينا.</p>
        <p class="mt-4 text-sm sm:text-base text-neutral-500 leading-7 max-w-3xl mx-auto">
          فضفضي بأي صياغة طبيعية — بنفهم المقاسات، الميزانية الصارمة والمرنة، والألوان المرفوضة.
          وبعدين تقدري تعدّلي طلبك: «بدي أرخص»، «بدي أفخم»، «مش شرط الأسود».
        </p>
        <div class="mt-4 text-xs sm:text-sm font-bold text-neutral-500">💡 ما في كلمات مفتاحية إجبارية — اكتبي زي ما بتحكي مع صاحبتك.</div>
      </div>

      <div class="max-w-4xl mx-auto mt-7">
        <form id="searchForm" class="bg-white rounded-[2rem] border border-neutral-200 p-2 sm:p-3 soft-shadow">
          <div class="flex flex-col sm:flex-row gap-2">
            <textarea id="query" rows="2" maxlength="2000" autocomplete="off"
              class="flex-1 resize-none rounded-2xl border-0 bg-neutral-50 px-4 py-3 outline-none focus:ring-2 focus:ring-neutral-900 text-sm sm:text-base font-bold placeholder:text-neutral-400"
              placeholder="مثال: بدي شوز كعبه واطي مقاسي 38 أوروبي"></textarea>
            <button id="searchBtn" type="submit"
              class="sm:w-48 min-h-[54px] rounded-2xl bg-neutral-950 hover:bg-neutral-800 text-white font-black transition disabled:opacity-60 disabled:cursor-wait">
              أرسلي الطلب ✦
            </button>
          </div>
          <div class="mt-2 px-2 text-[11px] text-neutral-400 font-bold">نحلل طلبك ثم نعرض العروض التي تطابق الفئة والميزانية والمقاس واللون قدر الإمكان.</div>
        </form>

        <form id="refineForm" class="hidden mt-3 bg-white rounded-2xl border border-neutral-200 p-2 soft-shadow">
          <div class="flex gap-2">
            <input id="refineInput" maxlength="500" autocomplete="off" class="flex-1 min-w-0 rounded-xl bg-neutral-50 px-4 py-3 outline-none focus:ring-2 focus:ring-neutral-900 text-sm font-bold" placeholder="عدّلي الطلب: بدي أرخص / أفخم / مش شرط الأسود" />
            <button type="submit" class="px-4 rounded-xl bg-neutral-100 hover:bg-neutral-200 font-black text-sm">حدّثي ↻</button>
          </div>
        </form><button id="refreshResultsBtn" type="button" class="hidden mt-3 w-full min-h-[46px] rounded-2xl border border-neutral-200 bg-white hover:bg-neutral-50 font-black">🔄 خيارات جديدة من البحث المباشر</button>
        <button type="button" onclick="openSuggestion()" class="mt-3 text-xs font-black text-neutral-500 hover:text-neutral-900">💡 صندوق اقتراحات</button>
      </div>

      <section class="max-w-6xl mx-auto mt-10">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
          <div>
            <div class="flex items-center gap-2 text-[11px] font-black text-neutral-400 tracking-widest">LIVE MATCHES</div>
            <h2 id="resultsTitle" class="mt-1 text-xl sm:text-2xl font-black">اكتبي طلبكِ لنبدأ الفرز</h2>
          </div>
          <div id="countBadge" class="hidden rounded-full bg-neutral-950 text-white px-3 py-2 text-xs font-black"></div>
        </div>

        <div id="intentPills" class="flex flex-wrap gap-2 min-h-2 mb-5"></div>

        <div id="loading" class="hidden rounded-[2rem] border border-neutral-200 bg-white p-8 text-center soft-shadow">
          <div class="inline-flex items-center gap-3 font-black"><span class="pulse-soft">✦</span> جاري تحليل نيتكِ الشرائية وجمع كل العروض المطابقة…</div>
        </div>

        <div id="grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5"></div>

        <div id="empty" class="mt-4 rounded-[2rem] border border-dashed border-neutral-300 bg-white p-10 text-center soft-shadow">
          <div class="text-4xl mb-3">✦</div>
          <div class="font-black">لا توجد عروض معروضة حالياً</div>
          <div class="text-sm text-neutral-500 mt-2">ابدئي بكتابة طلبك بالأعلى لرؤية العروض المطابقة.</div>
        </div>
      </section>
    </section>
  </main>

  <div id="dealModal" class="hidden fixed inset-0 z-50 bg-black/50 p-4 flex items-center justify-center">
    <div class="w-full max-w-md rounded-[2rem] bg-white p-6 sm:p-7 soft-shadow" role="dialog" aria-modal="true">
      <div class="flex items-start justify-between gap-4">
        <div>
          <div class="text-[11px] font-black tracking-widest text-neutral-400">DEAL ACCESS</div>
          <h3 class="mt-1 text-2xl font-black">فتح هذا الإعلان مقابل 1 دينار</h3>
        </div>
        <button type="button" onclick="closeDealModal()" class="w-10 h-10 rounded-xl bg-neutral-100 font-black text-lg">×</button>
      </div>
      <div class="mt-5 rounded-2xl bg-neutral-50 border border-neutral-200 p-4 text-sm leading-7">
        <div class="font-black">⚡ الدفع عبر CliQ</div>
        <p class="mt-1 text-neutral-600">حوّلي 1 دينار إلى الحساب التالي، ثم أدخلي رقم/مرجع الحركة. لن تظهر روابط المتجر إلا بعد أن يتم التحقق من التحويل من لوحة الإدارة.</p>
      </div>
      <div class="grid grid-cols-2 gap-3 mt-4">
        <div class="rounded-2xl bg-white border border-neutral-200 p-4">
          <div class="text-[10px] text-neutral-400 font-black">البنك المستهدف</div>
          <div class="mt-1 font-black text-sm">البنك العربي (Arab Bank)</div>
        </div>
        <div class="rounded-2xl bg-white border border-neutral-200 p-4">
          <div class="text-[10px] text-neutral-400 font-black">CliQ Alias</div>
          <div class="mt-1 font-black text-sm tracking-widest">MQRB</div>
        </div>
      </div>
      <div id="dealTitle" class="mt-4 rounded-2xl border border-neutral-200 p-4 text-sm font-black"></div>
      <label class="block mt-4">
        <span class="text-xs font-black text-neutral-500">رقم/مرجع التحويل</span>
        <input id="transactionRef" maxlength="160" class="mt-2 w-full rounded-2xl bg-neutral-100 px-4 py-3 outline-none focus:ring-2 focus:ring-neutral-900 text-sm font-bold" placeholder="مثال: 123456789">
      </label>
      <button type="button" onclick="submitPayment()" id="submitPaymentBtn" class="mt-4 w-full min-h-[52px] rounded-2xl bg-neutral-950 hover:bg-neutral-800 text-white font-black">أرسلت التحويل — أرسل طلب التحقق</button>
      <button type="button" onclick="refreshDeal()" id="refreshDealBtn" class="mt-2 w-full min-h-[46px] rounded-2xl bg-neutral-100 hover:bg-neutral-200 font-black">تحديث حالة الطلب</button>
      <div id="dealStatus" class="mt-3 rounded-2xl bg-neutral-50 p-4 text-sm leading-6 text-neutral-600">لم يتم إرسال طلب التحقق بعد.</div>
      <div id="dealLinks" class="hidden mt-3 grid grid-cols-2 gap-2"></div>
      <div class="mt-3 text-[11px] text-neutral-400 leading-5">لا يوجد اعتماد على مربع «أؤكد أنني دفعت». التفعيل يتم من الخادم فقط بعد مراجعة التحويل.</div>
    </div>
  </div>
  <div id="authModal" class="hidden fixed inset-0 z-50 bg-black/50 p-4 flex items-center justify-center"><div class="w-full max-w-md rounded-[2rem] bg-white p-6"><div class="flex items-center justify-between"><h3 class="text-2xl font-black">حسابك</h3><button onclick="closeAuth()" class="w-10 h-10 rounded-xl bg-neutral-100 font-black">×</button></div><div class="mt-4 flex gap-2"><button id="loginTab" onclick="switchAuth('login')" class="flex-1 rounded-xl bg-neutral-950 text-white py-3 font-black">دخول</button><button id="registerTab" onclick="switchAuth('register')" class="flex-1 rounded-xl bg-neutral-100 py-3 font-black">حساب جديد</button></div><div id="authForm" class="mt-4 space-y-3"></div><div id="authMsg" class="text-sm mt-3"></div></div></div>
  <div id="cartModal" class="hidden fixed inset-0 z-50 bg-black/50 p-4 flex items-center justify-center"><div class="w-full max-w-lg rounded-[2rem] bg-white p-6"><div class="flex items-center justify-between"><h3 class="text-2xl font-black">سلة الطلبات</h3><button onclick="closeCart()" class="w-10 h-10 rounded-xl bg-neutral-100 font-black">×</button></div><div id="cartItems" class="mt-4 space-y-2"></div><div class="mt-4 rounded-2xl bg-neutral-50 p-4 flex items-center justify-between"><span class="font-black">الإجمالي</span><span id="cartTotal" class="font-black text-lg"></span></div><button onclick="startCheckoutFlow()" class="mt-4 w-full min-h-[52px] rounded-2xl bg-neutral-950 text-white font-black">الدفع للطلبات في السلة</button></div></div>
  <div id="checkoutModal" class="hidden fixed inset-0 z-50 bg-black/50 p-4 flex items-center justify-center"><div class="w-full max-w-lg rounded-[2rem] bg-white p-6"><div class="flex items-center justify-between"><h3 class="text-2xl font-black">إتمام الدفع</h3><button onclick="closeCheckout()" class="w-10 h-10 rounded-xl bg-neutral-100 font-black">×</button></div><div id="checkoutSummary" class="mt-4 rounded-2xl bg-neutral-50 p-4 text-sm leading-6"></div><label class="block mt-4"><span class="text-xs font-black text-neutral-500">اسم المحوّل</span><input id="payerName" maxlength="120" class="mt-2 w-full rounded-2xl bg-neutral-100 px-4 py-3 outline-none focus:ring-2 focus:ring-neutral-900 font-bold" placeholder="مثال: هلا نايف المشاقبة"></label><button id="submitCheckoutBtn" onclick="submitCheckout()" class="mt-4 w-full min-h-[52px] rounded-2xl bg-neutral-950 text-white font-black">أرسلت التحويل — أرسل الطلب</button><div id="checkoutStatus" class="mt-3 rounded-2xl bg-neutral-50 p-4 text-sm"></div></div></div>
  <div id="accountModal" class="hidden fixed inset-0 z-50 bg-black/50 p-4 flex items-center justify-center"><div class="w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-[2rem] bg-white p-6"><div class="flex items-center justify-between"><div><div class="text-xs text-neutral-400 font-black">حسابي</div><h3 id="accountName" class="text-2xl font-black"></h3></div><button onclick="closeAccount()" class="w-10 h-10 rounded-xl bg-neutral-100 font-black">×</button></div><button onclick="loadOrders()" class="mt-4 rounded-xl bg-neutral-100 px-4 py-2 font-black">تحديث الطلبات</button><div id="ordersBox" class="mt-4 space-y-3"></div><button onclick="logoutUser()" class="mt-4 rounded-xl bg-red-50 text-red-700 px-4 py-2 font-black">تسجيل الخروج</button></div></div>
  <div id="suggestModal" class="hidden fixed inset-0 z-50 bg-black/50 p-4 flex items-center justify-center"><div class="w-full max-w-md rounded-[2rem] bg-white p-6"><div class="flex items-center justify-between"><h3 class="text-2xl font-black">صندوق الاقتراحات</h3><button onclick="closeSuggestion()" class="w-10 h-10 rounded-xl bg-neutral-100 font-black">×</button></div><textarea id="suggestionText" rows="5" maxlength="1000" class="mt-4 w-full rounded-2xl bg-neutral-100 px-4 py-3 outline-none focus:ring-2 focus:ring-neutral-900 font-bold" placeholder="ما الذي تريدين تحسينه أو إضافته؟"></textarea><button onclick="sendSuggestion()" class="mt-4 w-full min-h-[50px] rounded-2xl bg-neutral-950 text-white font-black">إرسال الاقتراح</button><div id="suggestMsg" class="mt-3 text-sm"></div></div></div>
  <script>
const form = document.getElementById('searchForm');
const refineForm = document.getElementById('refineForm');
const refineInput = document.getElementById('refineInput');
const queryEl = document.getElementById('query');
const searchBtn = document.getElementById('searchBtn');
const refreshResultsBtn = document.getElementById('refreshResultsBtn');
const LAST_QUERY_KEY='tawseya_last_query';
const LAST_IDS_KEY='tawseya_last_result_ids';
const grid = document.getElementById('grid');
const empty = document.getElementById('empty');
const loading = document.getElementById('loading');
const resultsTitle = document.getElementById('resultsTitle');
const countBadge = document.getElementById('countBadge');
const intentPills = document.getElementById('intentPills');
const dealModal = document.getElementById('dealModal');
let activeCard = null;
let activeDealId = null;
let dealPoll = null;
let paidDeals = {};
let authToken = localStorage.getItem('tawseya_auth') || '';
let currentUser = null;
let authMode = 'login';
let activeBatchId = null;
let pendingCartOfferId = null;
let orderPoll = null;
let knownOrderStatuses = {};
let sessionId = null;

const EX = [
"بدي فستان أسود لحفلة تحت 30",
"بدي هدية ناعمة لصاحبتي عمرها 23 بحدود 15",
"بدي شوز كعبه واطي مقاسي 38 أوروبي",
"بدي شال طويل وعريض ومش شيفون",
"بدي برا 34C مريح",
"بدي شي مثل Zara بس أرخص"
];
let exIdx = 0;
setInterval(() => { if (document.activeElement !== queryEl && !queryEl.value) queryEl.placeholder = EX[exIdx++ % EX.length]; }, 2800);

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const money = (v) => { const n = Number(v); return `${n.toFixed(Number.isInteger(n) ? 0 : 2)} د.أ`; };
const categoryNames = {makeup:'مكياج', clothes:'ملابس', gifts:'هدايا', watches:'ساعات', perfumes:'عطور', shoes:'أحذية', lingerie:'ملابس داخلية', scarves:'طرحات وشالات'};
const budgetLabels = {hard_max:'حد أقصى', soft_target:'بحدود', flexible:'مرنة حوالي', cheapest:'الأرخص', quality_first:'الجودة أولاً'};

function renderIntent(intent) {
const items = [];
(intent.categories || []).forEach(c => items.push(`<span class="rounded-xl bg-neutral-950 text-white px-3 py-1.5 text-xs font-black">${esc(categoryNames[c] || c)}</span>`));
(intent.colors || []).forEach(c => items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-bold text-neutral-600">🎨 ${esc(c)}</span>`));
(intent.excluded_colors || []).forEach(c => items.push(`<span class="rounded-xl bg-red-50 border border-red-100 px-3 py-1.5 text-xs font-bold text-red-500">🚫 بدون ${esc(c)}</span>`));
(intent.styles || []).forEach(c => items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-bold text-neutral-600">✦ ${esc(c)}</span>`));
const b = intent.budget || {};
if (b.amount !== null && b.amount !== undefined) items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">💰 ${esc(budgetLabels[b.kind] || 'حتى')} ${esc(money(b.amount))}</span>`);
else if (b.kind === 'cheapest') items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">💰 بدي الأرخص</span>`);
else if (b.kind === 'quality_first') items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">✨ الجودة أهم من السعر</span>`);
const s = intent.sizes || {};
if (s.system === 'bra') items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">📏 مقاس ${esc(s.band)}${esc(s.cup)}</span>`);
else if (s.system === 'shoe_eu') items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">📏 مقاس ${esc(s.value)} أوروبي</span>`);
else if (s.system && s.value) items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">📏 مقاس ${esc(s.value)}</span>`);
intentPills.innerHTML = items.join('');
}

function protectedButtons(dealId) {
return `<button type="button" onclick="openProtectedLink('${esc(dealId)}','whatsapp')" class="flex-1 text-center rounded-xl bg-emerald-50 text-emerald-700 py-3 text-xs font-black">واتساب المتجر</button><button type="button" onclick="openProtectedLink('${esc(dealId)}','instagram')" class="flex-1 text-center rounded-xl bg-pink-50 text-pink-700 py-3 text-xs font-black">إنستغرام</button>`;
}
function lockedZone() {
return `<div class="flex gap-2"><div class="flex-1 text-center rounded-xl bg-neutral-100 text-neutral-500 py-3 text-[11px] font-black">🔒 الدفع والتحقق مطلوبان</div></div>`;
}
function unlockCard(offerId, dealId) {
paidDeals[String(offerId)] = dealId;
const card = grid.querySelector(`[data-offer-id="${offerId}"]`);
if (!card) return;
const zone = card.querySelector('.contact-zone');
if (!zone) return;
zone.classList.remove('hide-links');
zone.innerHTML = `<div class="flex gap-2">${protectedButtons(dealId)}</div>`;
}
function productCard(item, index) {
const score = Math.round(Number(item.score || 0));
const tags = (item.tags || []).slice(0, 5).map(tag => `<span class="rounded-full bg-neutral-100 px-2 py-1 text-[10px] font-bold text-neutral-500">${esc(tag)}</span>`).join('');
const reasons = (item.reasons || []).slice(0, 3).map(r => `<li class="flex items-start gap-1.5 text-[11px] text-emerald-700 font-bold leading-5"><span class="mt-0.5">✓</span><span>${esc(r)}</span></li>`).join('');
const knownDeal = paidDeals[String(item.id)];
const zone = knownDeal ? `<div class="flex gap-2">${protectedButtons(knownDeal)}</div>` : lockedZone();
return ` <article data-offer-id="${item.id}" class="group bg-white rounded-[2rem] border border-neutral-200/70 overflow-hidden soft-shadow flex flex-col"><div class="relative aspect-[4/5] overflow-hidden bg-neutral-100"><img src="${esc(item.image_url)}" loading="lazy" referrerpolicy="no-referrer" class="h-full w-full object-cover transition duration-700 group-hover:scale-[1.03]" alt="${esc(item.title)}" onerror="if(!this.dataset.fallback){this.dataset.fallback='1';this.src='/media/offers/'+this.closest('[data-offer-id]').dataset.offerId+'.svg';}else{this.style.opacity='.18';}" /><div class="absolute inset-x-3 top-3 flex items-start justify-between gap-2"><span class="rounded-full bg-white/90 backdrop-blur px-3 py-1.5 text-[10px] font-black shadow-sm">${score}% توافق</span><span class="rounded-full bg-black/65 text-white backdrop-blur px-3 py-1.5 text-[10px] font-bold">${esc(item.match_type || 'توصية')}</span></div></div><div class="p-4 sm:p-5 flex-1 flex flex-col"><div class="flex items-center justify-between gap-2"><span class="text-[11px] text-neutral-400 font-black">${esc(categoryNames[item.category] || item.category)}</span><span class="text-[11px] text-neutral-400 font-bold">${esc(item.city)}</span></div><h4 class="mt-2 text-sm sm:text-base font-black leading-6">${esc(item.title)}</h4><div class="mt-1 text-xs text-neutral-400 font-bold">${esc(item.merchant_name)}</div><p class="mt-2 text-xs sm:text-sm text-neutral-500 leading-6">${esc(item.description)}</p><ul class="mt-3 space-y-1">${reasons}</ul><div class="mt-3 flex flex-wrap gap-1.5">${tags}</div><div class="mt-auto pt-4 flex items-end justify-between gap-3"><div><div class="text-[10px] text-neutral-400 font-bold">السعر</div><div class="text-lg font-black">${esc(money(item.price_jod))}</div></div><button type="button" onclick='openDeal(${JSON.stringify({id:item.id,title:item.title,price_jod:item.price_jod})})' class="min-h-[48px] px-4 rounded-2xl bg-neutral-950 hover:bg-neutral-800 text-white font-black text-xs sm:text-sm transition active:scale-[0.985]">إضافة للسلة <span class="opacity-60">(1 د)</span></button></div><div class="contact-zone relative mt-4 p-2 rounded-2xl border border-neutral-100 ${knownDeal ? '' : 'hide-links'}" data-card="${index}">${zone}</div></div></article>`;
}

async function addToCart(item){
if(!authToken){pendingCartOfferId=Number(item.id);openAuth('register');return;}
try{const r=await fetch('/api/cart/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:authToken,offer_id:Number(item.id)})});const d=await r.json();if(!r.ok)throw new Error(d.error||'تعذر إضافة الإعلان');updateCartUI(d);showToast('تمت إضافة الإعلان إلى السلة.');}catch(e){alert(e.message);}}
async function updateCartUI(data){const badge=document.getElementById('cartBadge');if(data.count){badge.textContent=data.count;badge.classList.remove('hidden');}else badge.classList.add('hidden');}
async function openCart(){if(!authToken){openAuth('login');return;}try{const d=await apiPublic('/api/cart/get',{token:authToken});renderCart(d);document.getElementById('cartModal').classList.remove('hidden');}catch(e){alert(e.message);}}
function closeCart(){document.getElementById('cartModal').classList.add('hidden');}
async function apiPublic(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.error||'حدث خطأ');return d;}
function renderCart(d){updateCartUI(d);document.getElementById('cartTotal').textContent=`${d.total_jod} د.أ`;document.getElementById('cartItems').innerHTML=d.items.length?d.items.map(x=>`<div class="flex items-center justify-between gap-3 rounded-2xl border p-3"><div><div class="font-black">${esc(x.title)}</div><div class="text-xs text-neutral-400">${esc(x.merchant_name)}</div></div><button onclick="removeCart(${x.id})" class="rounded-xl bg-red-50 text-red-700 px-3 py-2 font-black text-xs">حذف</button></div>`).join(''):'<div class="rounded-2xl bg-neutral-50 p-5 text-sm text-neutral-500">السلة فارغة.</div>';}
async function removeCart(id){try{const d=await apiPublic('/api/cart/remove',{token:authToken,offer_id:id});renderCart(d);}catch(e){alert(e.message);}}
function openAuth(mode='login'){authMode=mode;renderAuth();document.getElementById('authModal').classList.remove('hidden');}
function closeAuth(){document.getElementById('authModal').classList.add('hidden');}
function switchAuth(mode){authMode=mode;renderAuth();}
function renderAuth(){document.getElementById('authForm').innerHTML=authMode==='login'?`<input id="authPhone" class="w-full rounded-xl bg-neutral-100 px-4 py-3 font-bold" placeholder="رقم الهاتف"><input id="authPassword" type="password" class="w-full rounded-xl bg-neutral-100 px-4 py-3 font-bold" placeholder="كلمة المرور"><button onclick="submitAuth()" class="w-full rounded-xl bg-neutral-950 text-white py-3 font-black">دخول</button>`:`<input id="authName" class="w-full rounded-xl bg-neutral-100 px-4 py-3 font-bold" placeholder="الاسم"><input id="authPhone" class="w-full rounded-xl bg-neutral-100 px-4 py-3 font-bold" placeholder="رقم الهاتف"><input id="authPassword" type="password" class="w-full rounded-xl bg-neutral-100 px-4 py-3 font-bold" placeholder="كلمة المرور (6 أحرف على الأقل)"><button onclick="submitAuth()" class="w-full rounded-xl bg-neutral-950 text-white py-3 font-black">إنشاء الحساب</button>`;}
async function submitAuth(){try{const body=authMode==='login'?{phone:document.getElementById('authPhone').value,password:document.getElementById('authPassword').value}:{full_name:document.getElementById('authName').value,phone:document.getElementById('authPhone').value,password:document.getElementById('authPassword').value};const d=await apiPublic(authMode==='login'?'/api/auth/login':'/api/auth/register',body);authToken=d.token;currentUser=d.user;localStorage.setItem('tawseya_auth',authToken);document.getElementById('authMsg').textContent='تم الدخول بنجاح.';document.getElementById('authMsg').className='text-sm mt-3 text-emerald-700 font-bold';updateAccountBtn();const pending=pendingCartOfferId;pendingCartOfferId=null;setTimeout(async()=>{closeAuth();if(pending){try{const c=await apiPublic('/api/cart/add',{token:authToken,offer_id:pending});updateCartUI(c);openCart();}catch(e){alert(e.message);}}},400);loadCartSafe();}catch(e){document.getElementById('authMsg').textContent=e.message;document.getElementById('authMsg').className='text-sm mt-3 text-red-600 font-bold';}}
async function initAuth(){if(!authToken){updateAccountBtn();return;}try{const d=await apiPublic('/api/auth/me',{token:authToken});currentUser=d.user;updateAccountBtn();await loadCartSafe();}catch(_){authToken='';localStorage.removeItem('tawseya_auth');updateAccountBtn();}}
function updateAccountBtn(){const b=document.getElementById('accountBtn');b.textContent=currentUser?`حسابي: ${currentUser.full_name.split(' ')[0]}`:'تسجيل الدخول';}
async function loadCartSafe(){try{const d=await apiPublic('/api/cart/get',{token:authToken});updateCartUI(d);}catch(_){}}
async function openAccount(){if(!authToken){openAuth('login');return;}try{const d=await apiPublic('/api/auth/me',{token:authToken});currentUser=d.user;updateAccountBtn();renderOrders(d.orders);document.getElementById('accountModal').classList.remove('hidden');startOrderPolling();}catch(e){alert(e.message);}}
function closeAccount(){stopOrderPolling();document.getElementById('accountModal').classList.add('hidden');}
function statusLabel(s){return {draft:'مسودة',payment_submitted:'قيد التحقق',paid:'تم التحقق',rejected:'مرفوض'}[s]||s;}
function renderOrders(orders){const box=document.getElementById('ordersBox');document.getElementById('accountName').textContent=currentUser?currentUser.full_name:'';box.innerHTML=orders.length?orders.map(o=>`<article class="rounded-2xl border p-4"><div class="flex items-center justify-between gap-3"><div class="font-black">طلب ${esc(o.batch_id.slice(0,8))}</div><span class="rounded-full ${o.status==='paid'?'bg-emerald-100 text-emerald-700':o.status==='rejected'?'bg-red-100 text-red-700':'bg-amber-100 text-amber-700'} px-3 py-1 text-xs font-black">${statusLabel(o.status)}</span></div><div class="mt-2 text-sm">المبلغ: <b>${esc(o.total_jod)} د.أ</b> · عدد الإعلانات: <b>${esc(o.item_count)}</b>${o.payer_name?` · اسم المحوّل: <b>${esc(o.payer_name)}</b>`:''}</div>${o.status==='paid'?`<div class="mt-3 grid gap-2">${o.items.map(i=>`<div class="rounded-xl bg-emerald-50 p-3"><div class="font-black">${esc(i.title)}</div><div class="mt-2 flex flex-wrap gap-2"><button onclick="openPaid('${esc(i.deal_id)}','store')" class="rounded-xl bg-neutral-950 text-white px-3 py-2 font-black text-xs">فتح المتجر</button><button onclick="openPaid('${esc(i.deal_id)}','whatsapp')" class="rounded-xl bg-white px-3 py-2 font-black text-xs">واتساب</button><button onclick="openPaid('${esc(i.deal_id)}','instagram')" class="rounded-xl bg-white px-3 py-2 font-black text-xs">إنستغرام</button></div></div>`).join('')}</div>`:'<div class="mt-3 text-sm text-neutral-500">بعد اعتماد التحويل ستظهر روابط هذه الطلبات هنا تلقائيًا.</div>'}</article>`).join(''):'<div class="rounded-2xl bg-neutral-50 p-5 text-sm text-neutral-500">لا توجد طلبات بعد.</div>';}
async function loadOrders(){try{const d=await apiPublic('/api/orders',{token:authToken});let becamePaid=false;for(const o of d.orders){if(knownOrderStatuses[o.batch_id]&&knownOrderStatuses[o.batch_id]!=="paid"&&o.status==="paid")becamePaid=true;knownOrderStatuses[o.batch_id]=o.status;}renderOrders(d.orders);if(becamePaid){document.getElementById('accountModal').classList.remove('hidden');showToast('✅ تم التحقق من الدفع. طلباتك أصبحت جاهزة للفتح.');}}catch(e){if(e.message)console.warn(e.message);}}
function startOrderPolling(){stopOrderPolling();let attempts=0;orderPoll=setInterval(async()=>{attempts++;await loadOrders();if(attempts>60)stopOrderPolling();},5000);}
function stopOrderPolling(){if(orderPoll){clearInterval(orderPoll);orderPoll=null;}}
async function openPaid(dealId,channel){
const popup=window.open('about:blank','_blank','noopener,noreferrer');
try{
 const d=await apiPublic('/api/deal/open',{token:authToken,deal_id:dealId,session_id:'',channel});
 if(popup){popup.location.href=d.url;}else{window.location.href=d.url;}
}catch(e){if(popup)popup.close();alert(e.message);}
}
function logoutUser(){authToken='';currentUser=null;localStorage.removeItem('tawseya_auth');updateAccountBtn();closeAccount();loadCartSafe();}
async function startCheckoutFlow(){if(!authToken){closeCart();openAuth('login');return;}try{const d=await apiPublic('/api/checkout/start',{token:authToken});activeBatchId=d.batch.batch_id;document.getElementById('checkoutSummary').innerHTML=`سيتم إرسال <b>${d.batch.item_count}</b> طلبات بقيمة <b>${d.batch.total_jod} د.أ</b> إجمالًا. بعد التحويل اكتبي الاسم الذي ظهر في إشعار البنك.`;document.getElementById('checkoutStatus').textContent='';document.getElementById('checkoutModal').classList.remove('hidden');closeCart();}catch(e){alert(e.message);}}
function closeCheckout(){document.getElementById('checkoutModal').classList.add('hidden');}
async function submitCheckout(){if(!activeBatchId)return;const name=document.getElementById('payerName').value.trim();if(!name){alert('اكتبي اسم المحوّل.');return;}const btn=document.getElementById('submitCheckoutBtn');btn.disabled=true;try{const d=await apiPublic('/api/checkout/submit',{token:authToken,batch_id:activeBatchId,payer_name:name});document.getElementById('checkoutStatus').innerHTML='<span class="text-amber-700 font-black">تم إرسال الطلب. سننتظر التحقق من التحويل.</span><br>يمكنك إغلاق هذه النافذة والعودة لاحقًا من «حسابي > طلباتي».';startOrderPolling();setTimeout(()=>closeCheckout(),800);loadOrders();}catch(e){document.getElementById('checkoutStatus').textContent=e.message;}finally{btn.disabled=false;}}
function openSuggestion(){document.getElementById('suggestModal').classList.remove('hidden');}
function closeSuggestion(){document.getElementById('suggestModal').classList.add('hidden');}
async function sendSuggestion(){try{await apiPublic('/api/suggestions',{token:authToken,message:document.getElementById('suggestionText').value});document.getElementById('suggestMsg').textContent='تم إرسال اقتراحك.';document.getElementById('suggestionText').value='';}catch(e){document.getElementById('suggestMsg').textContent=e.message;}}
function showToast(message){const t=document.createElement('div');t.textContent=message;t.className='fixed bottom-5 left-1/2 -translate-x-1/2 z-[80] rounded-full bg-neutral-950 text-white px-4 py-3 text-sm font-black shadow-xl';document.body.appendChild(t);setTimeout(()=>t.remove(),1800);}

async function openDeal(item) { return addToCart(item); }
async function legacyOpenDeal(item) {
if (!sessionId) { alert('أرسلي طلب البحث أولاً.'); return; }
activeCard = item;
activeDealId = null;
document.getElementById('transactionRef').value = '';
document.getElementById('dealTitle').textContent = item.title || '';
document.getElementById('dealStatus').textContent = 'جاري تجهيز طلب الدفع…';
document.getElementById('dealLinks').classList.add('hidden');
document.getElementById('submitPaymentBtn').disabled = true;
document.getElementById('transactionRef').disabled = true;
dealModal.classList.remove('hidden');
document.body.classList.add('overflow-hidden');
try {
const response = await fetch('/api/deal/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,offer_id:Number(item.id)})});
const data = await response.json();
if(!response.ok) throw new Error(data.error || 'تعذر إنشاء طلب الدفع');
activeDealId = data.deal_id;
showDealStatus(data);
} catch(e) {
document.getElementById('dealStatus').textContent = e.message;
}
}
function showDealStatus(data){
const status = data.status;
if(status === 'paid') { unlockCard(activeCard.id, activeDealId); document.getElementById('dealStatus').innerHTML='<span class="text-emerald-700 font-black">تم التحقق من الدفع. روابط هذا الإعلان أصبحت متاحة.</span>'; document.getElementById('submitPaymentBtn').classList.add('hidden'); document.getElementById('refreshDealBtn').classList.add('hidden'); return; }
if(status === 'payment_submitted') { document.getElementById('dealStatus').innerHTML='<span class="font-black">تم إرسال طلب التحقق.</span><br>بعد مراجعة التحويل من لوحة الإدارة سيظهر رابطا التواصل لهذا الإعلان فقط.'; startDealPolling(); return; }
document.getElementById('dealStatus').textContent='حوّلي 1 دينار أولاً ثم اكتبي رقم/مرجع الحركة وأرسلي طلب التحقق.';
document.getElementById('submitPaymentBtn').disabled=false;
document.getElementById('transactionRef').disabled=false;
}
async function submitPayment(){
if(!activeDealId || !sessionId) return;
const ref=document.getElementById('transactionRef').value.trim();
if(!ref){alert('اكتبي رقم/مرجع التحويل.');return;}
document.getElementById('submitPaymentBtn').disabled=true;
try{const r=await fetch('/api/deal/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,deal_id:activeDealId,transaction_ref:ref})});const d=await r.json();if(!r.ok)throw new Error(d.error||'تعذر إرسال الطلب');showDealStatus(d);}catch(e){document.getElementById('dealStatus').textContent=e.message;document.getElementById('submitPaymentBtn').disabled=false;}}
async function refreshDeal(){if(!activeDealId||!sessionId)return;try{const r=await fetch(`/api/deal/status?session_id=${encodeURIComponent(sessionId)}&deal_id=${encodeURIComponent(activeDealId)}`);const d=await r.json();if(!r.ok)throw new Error(d.error||'تعذر قراءة الحالة');showDealStatus(d);if(d.status==='paid') stopDealPolling();}catch(e){document.getElementById('dealStatus').textContent=e.message;}}
function startDealPolling(){stopDealPolling();let attempts=0;dealPoll=setInterval(async()=>{attempts+=1;await refreshDeal();if(attempts>=60)stopDealPolling();},5000);}
function stopDealPolling(){if(dealPoll){clearInterval(dealPoll);dealPoll=null;}}
async function openProtectedLink(dealId, channel){
if(!sessionId) return;
try{const r=await fetch('/api/deal/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,deal_id:dealId,channel,token:authToken})});const d=await r.json();if(!r.ok)throw new Error(d.error||'هذا الرابط غير متاح');window.open(d.url,'_blank','noopener');}catch(e){alert(e.message);}}
function closeDealModal(){stopDealPolling();dealModal.classList.add('hidden');document.body.classList.remove('overflow-hidden');}
dealModal.addEventListener('click',(event)=>{if(event.target===dealModal)closeDealModal();});
document.addEventListener('keydown',(event)=>{if(event.key==='Escape'&&!dealModal.classList.contains('hidden'))closeDealModal();});


function setBusy(busy) {
searchBtn.disabled = busy;
searchBtn.classList.toggle('opacity-60', busy);
searchBtn.classList.toggle('cursor-wait', busy);
loading.classList.toggle('hidden', !busy);
}

function renderResults(data) {
renderIntent(data.intent);
const count = data.results.length;
resultsTitle.textContent = count ? 'كل العروض التنافسية المطابقة' : 'لم نجد عروضًا مطابقة لهذه الشروط — جرّبي «مش شرط اللون» أو وسّعي الميزانية';
countBadge.textContent = `${count} ${count === 1 ? 'عرض' : 'عروض'}`;
countBadge.classList.remove('hidden');
grid.innerHTML = data.results.map(productCard).join('');
if (!count) empty.classList.remove('hidden');
if (count) window.scrollTo({top: grid.offsetTop - 90, behavior: 'smooth'});
}

function renderError(message) {
empty.classList.remove('hidden');
resultsTitle.textContent = 'صار خطأ في البحث';
empty.innerHTML = `<div class="text-4xl mb-3">!</div><div class="font-black">${esc(message)}</div><div class="text-sm text-neutral-500 mt-2">جربي صياغة أخرى للطلب.</div>`;
}

async function runSearch(query, isRefresh=false){
query=(query||'').trim(); if(!query) return;
empty.classList.add('hidden'); grid.innerHTML=''; countBadge.classList.add('hidden'); resultsTitle.textContent=isRefresh?'نبحث عن خيارات جديدة فعلًا…':'جاري تحليل طلبك والبحث المباشر…'; setBusy(true);
try{
 const oldIds=(()=>{try{return JSON.parse(localStorage.getItem(LAST_IDS_KEY)||'[]')}catch(_){return[]}})();
 const response=await fetch('/api/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,token:authToken,refresh_nonce:isRefresh?String(Date.now()):'',avoid_ids:isRefresh?oldIds:[]})});
 const data=await response.json(); if(!response.ok) throw new Error(data.error||'تعذر إتمام البحث');
 paidDeals={}; sessionId=data.session_id; localStorage.setItem(LAST_QUERY_KEY,query); localStorage.setItem(LAST_IDS_KEY,JSON.stringify((data.results||[]).map(x=>x.id)));
 refineForm.classList.remove('hidden'); if(refreshResultsBtn) refreshResultsBtn.classList.remove('hidden'); renderResults(data);
 if(data.live_search===false && isRefresh) showToast('البحث المباشر غير متاح حاليًا، فتم استخدام المخزون الداخلي.');
}catch(error){renderError(error.message);}finally{setBusy(false);}
}

form.addEventListener('submit', async (event) => { event.preventDefault(); await runSearch(queryEl.value.trim(), false); });
if(refreshResultsBtn) refreshResultsBtn.addEventListener('click', ()=>runSearch(localStorage.getItem(LAST_QUERY_KEY)||queryEl.value,true));

refineForm.addEventListener('submit', async (event) => {
event.preventDefault();
const message = refineInput.value.trim();
if (!message || !sessionId) return;
setBusy(true);
try {
const response = await fetch('/api/refine', {
method: 'POST', headers: {'Content-Type':'application/json'},
body: JSON.stringify({session_id: sessionId, message, token: authToken})
});
const data = await response.json();
if (!response.ok) throw new Error(data.error || 'تعذر تحديث الطلب');
refineInput.value = '';
renderResults(data);
} catch (error) {
renderError(error.message);
} finally { setBusy(false); }
});

initAuth();
const savedQuery=localStorage.getItem(LAST_QUERY_KEY);
if(savedQuery){queryEl.value=savedQuery; setTimeout(()=>runSearch(savedQuery,true),350);}
queryEl.focus();
  </script>
</body>
</html>
"""

STATUS_TEXT = {
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


def _intent_public(intent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "categories": intent.get("categories", []),
        "budget": intent.get("budget", {"amount": None, "kind": "none"}),
        "colors": intent.get("colors", []),
        "excluded_colors": intent.get("excluded_colors", []),
        "styles": intent.get("styles", []),
        "sizes": intent.get("sizes", {}),
        "product_terms": intent.get("product_terms", []),
    }


def _recommend_payload(intent: Dict[str, Any], request_id: str, session_id: str, raw_query: str = "", refresh_nonce: str = "", avoid_ids: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    results = recommend(intent, raw_query, refresh_nonce=refresh_nonce, avoid_ids=avoid_ids)
    save_session_offers(session_id, [int(x["id"]) for x in results])
    return {
        "ok": True,
        "request_id": request_id,
        "session_id": session_id,
        "intent": _intent_public(intent),
        "results": results,
        "payment": PAYMENT_PROVIDER.instructions(),
        "live_search": bool(LIVE_SEARCH_ENABLED and gemini_model()),
    }


def _deal_response(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "deal_id": row["deal_id"],
        "offer_id": int(row["offer_id"]),
        "status": row["status"],
        "submitted_at": row.get("submitted_at"),
        "payment": PAYMENT_PROVIDER.instructions(),
    }


ADMIN_HTML_CONTENT_TYPE = "text/html; charset=utf-8"

def route_request(method: str, path: str, body: bytes, client_ip: str = "") -> Tuple[int, List[Tuple[str, str]], bytes]:
    # Gunicorn imports the WSGI application directly, so make schema initialization
    # explicit for every request path. This also upgrades an existing SQLite DB
    # from an older release without requiring a manual migration step.
    ensure_db()
    headers: List[Tuple[str, str]] = [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Cache-Control", "no-store"),
    ]

    def respond(payload: Dict[str, Any], status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return status, headers + [("Content-Type", "application/json; charset=utf-8")], data

    try:
        if method == "GET":
            if path == "/admin":
                return 200, headers + [("Content-Type", ADMIN_HTML_CONTENT_TYPE)], _admin_page().encode("utf-8")
            if path.startswith("/media/offers/") and path.endswith(".svg"):
                m = re.fullmatch(r"/media/offers/(\d+)\.svg", path)
                if not m:
                    return respond({"error":"Not Found"},404)
                offer = get_offer_by_id(int(m.group(1)))
                if offer is None:
                    return respond({"error":"Not Found"},404)
                return 200, headers + [("Content-Type", "image/svg+xml; charset=utf-8"), ("Cache-Control", "public, max-age=86400")], offer_svg(offer)
            if path.startswith("/api/deal/status"):
                parsed = urlparse(path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                session_id = query.get("session_id", [""])[0]
                deal_id = query.get("deal_id", [""])[0]
                row = deal_status_for_session(deal_id, session_id)
                if not row:
                    return respond({"error": "طلب الدفع غير موجود."}, 404)
                return respond(_deal_response(row))
            if path in ("/", "/index.html"):
                page = HTML_TEMPLATE.replace("البنك العربي", html.escape(PAYMENT_BANK_AR))
                return 200, headers + [("Content-Type", "text/html; charset=utf-8")], page.encode("utf-8")
            if path == "/healthz":
                return respond({"status": "ok"})
            if path == "/api/health":
                return respond({
                    "ok": True,
                    "service": "التوصية",
                    "version": "7.0",
                    "port": PORT,
                    "inventory_count": len(SEED_OFFERS),
                    "live_search_enabled": LIVE_SEARCH_ENABLED,
                    "gemini_enabled": bool(gemini_model()),
                    "timestamp": int(time.time()),
                })
            return respond({"error": "Not Found"}, 404)

        if method == "POST":
            if len(body) > 128 * 1024:
                return respond({"error": "حجم الطلب كبير جدًا"}, 400)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                return respond({"error": "JSON غير صالح"}, 400)
            if not isinstance(data, dict):
                return respond({"error": "تنسيق الطلب غير صالح"}, 400)

            if path == "/api/auth/register":
                try:
                    token = register_user(str(data.get("full_name", "")), str(data.get("phone", "")), str(data.get("password", "")))
                except ValueError as exc:
                    return respond({"error": str(exc)}, 400)
                user = get_user_from_token(token)
                return respond({"ok": True, "token": token, "user": {"id": user["id"], "full_name": user["full_name"], "phone": user["phone"]}})

            if path == "/api/auth/login":
                try:
                    token = login_user(str(data.get("phone", "")), str(data.get("password", "")))
                except ValueError as exc:
                    return respond({"error": str(exc)}, 401)
                user = get_user_from_token(token)
                return respond({"ok": True, "token": token, "user": {"id": user["id"], "full_name": user["full_name"], "phone": user["phone"]}})

            if path == "/api/auth/me":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error": "الجلسة منتهية. سجلي الدخول من جديد."}, 401)
                return respond({"ok": True, "user": {"id": user["id"], "full_name": user["full_name"], "phone": user["phone"]}, "orders": list_user_batches(int(user["id"]))})

            if path == "/api/cart/get":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error":"يلزم تسجيل الدخول."},401)
                return respond({"ok":True, **get_user_cart(int(user["id"]))})

            if path == "/api/cart/add":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error":"سجلي الدخول أولًا حتى نحفظ السلة والطلبات."},401)
                try: offer_id=int(data.get("offer_id",0)); cart=add_to_cart(int(user["id"]),offer_id)
                except ValueError as exc: return respond({"error":str(exc)},400)
                return respond({"ok":True, **cart})

            if path == "/api/cart/remove":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error":"يلزم تسجيل الدخول."},401)
                try: offer_id=int(data.get("offer_id",0)); cart=remove_from_cart(int(user["id"]),offer_id)
                except ValueError as exc: return respond({"error":str(exc)},400)
                return respond({"ok":True, **cart})

            if path == "/api/checkout/start":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error":"سجلي الدخول أولًا."},401)
                try: batch=start_checkout(int(user["id"]))
                except ValueError as exc: return respond({"error":str(exc)},400)
                return respond({"ok":True,"batch":batch,"payment":PAYMENT_PROVIDER.instructions()})

            if path == "/api/checkout/submit":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error":"يلزم تسجيل الدخول."},401)
                try: batch=submit_checkout(int(user["id"]), str(data.get("batch_id","")).strip(), str(data.get("payer_name","")).strip())
                except ValueError as exc: return respond({"error":str(exc)},400)
                return respond({"ok":True,"batch":batch})

            if path == "/api/orders":
                user = get_user_from_token(str(data.get("token", "")))
                if not user: return respond({"error":"يلزم تسجيل الدخول."},401)
                return respond({"ok":True,"orders":list_user_batches(int(user["id"]))})

            if path == "/api/payments/incoming":
                secret = str(data.get("secret", ""))
                if not PAYMENT_WEBHOOK_SECRET or not hmac.compare_digest(secret, PAYMENT_WEBHOOK_SECRET):
                    return respond({"error":"غير مصرح."}, 401)
                try:
                    event=process_incoming_payment(str(data.get("external_id","")), str(data.get("payer_name","")), float(data.get("amount_jod",0)), str(data.get("raw_message","")))
                except ValueError as exc:
                    return respond({"error":str(exc)},400)
                return respond({"ok":True,"event":event})

            if path == "/api/suggestions":
                token=str(data.get("token", "")); user=get_user_from_token(token)
                try: save_suggestion(int(user["id"]) if user else None, str(data.get("message", "")))
                except ValueError as exc: return respond({"error":str(exc)},400)
                return respond({"ok":True})

            if path == "/api/admin/login":
                password = str(data.get("password", ""))
                if not _admin_ok(password):
                    return respond({"error": "كلمة مرور الإدارة غير صحيحة."}, 401)
                return respond({"ok": True})

            if path == "/api/admin/pending-batches":
                password = str(data.get("password", ""))
                if not _admin_ok(password): return respond({"error":"غير مصرح."},401)
                return respond({"ok":True,"batches":list_pending_batches()})

            if path == "/api/admin/batch-decision":
                password = str(data.get("password", ""))
                if not _admin_ok(password): return respond({"error":"غير مصرح."},401)
                batch_id=str(data.get("batch_id","")).strip(); action=str(data.get("action","")).strip()
                if action not in {"paid","rejected"} or not batch_id: return respond({"error":"بيانات القرار غير صالحة."},400)
                if not set_batch_status(batch_id, action): return respond({"error":"الدفعة غير موجودة."},404)
                return respond({"ok":True,"status":action})

            if path == "/api/admin/suggestions":
                password = str(data.get("password", ""))
                if not _admin_ok(password): return respond({"error":"غير مصرح."},401)
                return respond({"ok":True,"suggestions":list_suggestions()})

            if path == "/api/admin/pending":
                password = str(data.get("password", ""))
                if not _admin_ok(password):
                    return respond({"error": "غير مصرح."}, 401)
                return respond({"ok": True, "deals": list_pending_deals()})

            if path == "/api/admin/offers":
                password = str(data.get("password", ""))
                if not _admin_ok(password):
                    return respond({"error": "غير مصرح."}, 401)
                return respond({"ok": True, "offers": list_admin_offers()})

            if path == "/api/admin/decision":
                password = str(data.get("password", ""))
                if not _admin_ok(password):
                    return respond({"error": "غير مصرح."}, 401)
                deal_id = str(data.get("deal_id", "")).strip()
                action = str(data.get("action", "")).strip()
                if action not in {"paid", "rejected"} or not deal_id:
                    return respond({"error": "بيانات القرار غير صالحة."}, 400)
                if not set_deal_status(deal_id, action):
                    return respond({"error": "طلب الدفع غير موجود."}, 404)
                return respond({"ok": True, "status": action})

            if path == "/api/admin/bulk-offer-links":
                password = str(data.get("password", ""))
                if not _admin_ok(password):
                    return respond({"error": "غير مصرح."}, 401)
                try:
                    result = bulk_update_offer_links(str(data.get("raw_text", "")))
                except ValueError as exc:
                    return respond({"error": str(exc)}, 400)
                return respond({"ok": True, **result})

            if path == "/api/admin/offer-update":
                password = str(data.get("password", ""))
                if not _admin_ok(password):
                    return respond({"error": "غير مصرح."}, 401)
                try:
                    offer_id = int(data.get("offer_id", 0))
                    ok = update_offer_links(offer_id, str(data.get("store_url", "")), str(data.get("whatsapp_url", "")), str(data.get("instagram_url", "")), str(data.get("image_url", "")))
                except (ValueError, TypeError) as exc:
                    return respond({"error": str(exc)}, 400)
                if not ok:
                    return respond({"error": "الإعلان غير موجود."}, 404)
                return respond({"ok": True})

            if path == "/api/deal/start":
                session_id = str(data.get("session_id", "")).strip()
                try:
                    offer_id = int(data.get("offer_id", 0))
                except (TypeError, ValueError):
                    offer_id = 0
                if not session_id or not offer_id:
                    return respond({"error": "بيانات الصفقة غير مكتملة."}, 400)
                offer = get_offer_by_id(offer_id)
                if offer is None:
                    return respond({"error": "الإعلان غير موجود."}, 404)
                row = create_or_get_deal(session_id, offer_id)
                if not row:
                    return respond({"error": "هذا الإعلان غير متاح ضمن نتائج البحث الحالية."}, 403)
                return respond(_deal_response(row))

            if path == "/api/deal/submit":
                session_id = str(data.get("session_id", "")).strip()
                deal_id = str(data.get("deal_id", "")).strip()
                transaction_ref = str(data.get("transaction_ref", "")).strip()
                if not session_id or not deal_id or not transaction_ref:
                    return respond({"error": "يلزم رقم/مرجع التحويل."}, 400)
                row = submit_deal_payment(deal_id, session_id, transaction_ref)
                if not row:
                    return respond({"error": "طلب الدفع غير موجود أو لا يخص هذه الجلسة."}, 403)
                return respond(_deal_response(row))

            if path == "/api/deal/open":
                session_id = str(data.get("session_id", "")).strip()
                deal_id = str(data.get("deal_id", "")).strip()
                token = str(data.get("token", "")).strip()
                channel = str(data.get("channel", "")).strip().lower()
                if channel not in {"store", "whatsapp", "instagram"}:
                    return respond({"error": "نوع الرابط غير صالح."}, 400)
                user = get_user_from_token(token)
                row = get_deal(deal_id)
                if user and row and row["user_id"] == int(user["id"]):
                    pass
                else:
                    row = deal_status_for_session(deal_id, session_id)
                if not row or row["status"] != "paid":
                    return respond({"error": "يجب التحقق من الدفع لهذا الإعلان أولاً."}, 402)
                offer = get_offer_by_id(int(row["offer_id"]))
                if offer is None:
                    return respond({"error": "الإعلان غير موجود."}, 404)
                url = offer.store_url if channel == "store" else (offer.whatsapp_url if channel == "whatsapp" else offer.instagram_url)
                verified, final_url, verify_status = verify_external_url(url)
                if not verified:
                    return respond({"error": "تعذر فتح رابط المنتج حاليًا. هذا العرض أصبح غير متاح، وسنبحث لك عن بديل."}, 409)
                with DB_LOCK:
                    conn = get_connection()
                    try:
                        conn.execute(
                            "UPDATE merchant_offers SET verification_status='verified', verified_url=?, verified_at=CURRENT_TIMESTAMP, last_checked=CURRENT_TIMESTAMP WHERE id=?",
                            (final_url or url, int(offer.id)),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                return respond({"ok": True, "url": final_url or url})

            if path == "/api/recommend":
                query = str(data.get("query", "")).strip()
                if not query:
                    return respond({"error": "اكتبي طلب الشراء أولًا."}, 400)
                if len(query) > 2000:
                    return respond({"error": "النص طويل جدًا. اكتبي الطلب بجملة مختصرة وواضحة."}, 400)
                ensure_db()
                request_id = uuid.uuid4().hex
                session_id = uuid.uuid4().hex
                intent = extract_intent(query, use_ai=True)
                save_buyer_intent(request_id, query, intent)
                user = get_user_from_token(str(data.get("token", "")))
                create_session(session_id, intent, client_ip, int(user["id"]) if user else None, query)
                avoid_ids = data.get("avoid_ids") if isinstance(data.get("avoid_ids"), list) else []
                refresh_nonce = str(data.get("refresh_nonce", ""))[:80]
                return respond(_recommend_payload(intent, request_id, session_id, query, refresh_nonce, avoid_ids))

            if path == "/api/refine":
                session_id = str(data.get("session_id", "")).strip()
                message = str(data.get("message", "")).strip()
                if not session_id or not message:
                    return respond({"error": "يلزم session_id ورسالة التعديل."}, 400)
                if len(message) > 500:
                    return respond({"error": "رسالة التعديل طويلة جدًا."}, 400)
                prev = get_session_intent(session_id)
                if prev is None:
                    return respond({"error": "الجلسة غير موجودة أو انتهت. أعيدي إرسال الطلب من جديد."}, 404)
                intent = refine_intent(prev, message)
                meta = get_session_meta(session_id)
                raw_query = ((meta["raw_query"] if meta and "raw_query" in meta.keys() else "") + " " + message).strip()
                request_id = uuid.uuid4().hex
                save_buyer_intent(request_id, f"[refine] {message}", intent)
                user = get_user_from_token(str(data.get("token", "")))
                create_session(session_id, intent, client_ip, int(user["id"]) if user else (int(meta["user_id"]) if meta and meta["user_id"] else None), raw_query)
                return respond(_recommend_payload(intent, request_id, session_id, raw_query))

            return respond({"error": "Not Found"}, 404)

        return respond({"error": "Method Not Allowed"}, 405)
    except Exception as exc:  # noqa: BLE001 — never leak internals to clients
        LOGGER.error("Request failed: %s\n%s", exc, traceback.format_exc())
        return respond({"error": "حدث خطأ داخلي غير متوقع."}, 500)


# ---------------------------------------------------------------------------
# Dev server (python app.py) — stdlib handler on top of the shared router
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AlTawseya/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        body = b""
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                length = 0
            if length > 0:
                body = self.rfile.read(min(length, 128 * 1024))
        status, headers, payload = route_request(
            method,
            path,
            body,
            self.client_address[0] if self.client_address else "",
        )
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")


# ---------------------------------------------------------------------------
# WSGI entrypoint for gunicorn: gunicorn app:application
# ---------------------------------------------------------------------------


def application(environ: Dict[str, Any], start_response: Any) -> List[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    query_string = environ.get("QUERY_STRING", "") or ""
    if query_string:
        path = f"{path}?{query_string}"
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    body = environ["wsgi.input"].read(min(length, 128 * 1024)) if length > 0 else b""
    status, headers, payload = route_request(method, path, body, environ.get("REMOTE_ADDR", ""))
    start_response(
        f"{status} {STATUS_TEXT.get(status, 'OK')}",
        headers + [("Content-Length", str(len(payload)))],
    )
    return [payload]


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    LOGGER.info("التوصية v3 تعمل على http://%s:%d", HOST, PORT)
    LOGGER.info("Inventory seeded: %d offers", len(SEED_OFFERS))
    if GOOGLE_API_KEY and genai:
        LOGGER.info("Gemini extraction enabled with model=%s", GEMINI_MODEL)
    else:
        LOGGER.info("Gemini extraction disabled; deterministic local Arabic parser is active")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        LOGGER.info("Stopping server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
