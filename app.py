#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
التوصية — Jordanian Recommendation Engine (v2 — production-ready)

Run (dev):
    python app.py

Run (prod):
    gunicorn app:application --bind 0.0.0.0:8000

Optional Gemini extraction:
    pip install -U google-generativeai
    export GOOGLE_API_KEY="..."

v2 changes over v1:
  - No DB deletion on boot (schema is CREATE IF NOT EXISTS + seed-if-empty).
  - /healthz endpoint for Render health checks.
  - WSGI application callable (`application`) so gunicorn works correctly.
  - Hard vs Soft budget constraints (hard_max / soft_target / flexible /
    cheapest / quality_first).
  - Size extraction per product type (bra band/cup, shoe EU/US, clothing
    letters, scarf dimensions).
  - New categories: shoes, lingerie, scarves (+ matching seed inventory).
  - Color exclusions ("ما بدي أسود").
  - Sessions + /api/refine follow-up ("بدي أرخص"، "مش شرط الأسود").
  - Human-readable reasons per recommendation.
  - PaymentProvider abstraction (MockCliqProvider).
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
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

try:
    import google.generativeai as genai  # type: ignore
except Exception:  # Optional dependency.
    genai = None


HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
DB_PATH = Path(os.getenv("DATABASE_FILE", "decision_engine.db"))
if not DB_PATH.is_absolute():
    DB_PATH = Path(__file__).with_name(DB_PATH.name)
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
    sizes: List[str] = field(default_factory=list)
    size_system: str = ""  # bra | shoe_eu | shoe_us | clothing_letter | scarf_dimensions | ""


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
          "https://wa.me/962790000005", "https://instagram.com/maison7jo",
          ["S", "M", "L"], "clothing_letter"),
    Offer(6, "Luna Amman", "فستان ساتان أسود محتشم", "clothes", 39.0,
          ["dress", "satin", "modest", "black", "فستان", "ساتان", "محتشم"],
          ["black", "أسود"], ["modest", "satin", "formal"], "عمّان",
          "فستان ساتان انسيابي بتفاصيل محتشمة وتصميم مناسب للعشاء والمناسبات.",
          "https://images.unsplash.com/photo-1591369822096-ffd140ec948f?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000006", "https://instagram.com/lunaamman",
          ["M", "L", "XL"], "clothing_letter"),
    Offer(7, "Silk Avenue", "طقم ساتان محتشم لسهرة هادئة", "clothes", 29.0,
          ["satin set", "modest", "black", "robe", "طقم", "ساتان", "محتشم"],
          ["black", "أسود"], ["modest", "lounge", "evening"], "إربد",
          "طقم ساتان مريح وأنيق بقصة هادئة وألوان حيادية.",
          "https://images.unsplash.com/photo-1595882100582-7dfd0a3a2f76?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000007", "https://instagram.com/silkavenuejo",
          ["S", "M", "L", "XL"], "clothing_letter"),
    Offer(8, "Noir Closet", "فستان أسود بسيط بقصة مستقيمة", "clothes", 24.0,
          ["dress", "black", "basic", "minimal", "فستان", "أسود"],
          ["black", "أسود"], ["minimal", "day", "night"], "عمّان",
          "فستان أسود عملي يمكن تنسيقه للدوام أو المناسبات الخفيفة.",
          "https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000008", "https://instagram.com/noirclosetjo",
          ["S", "M", "L", "XL"], "clothing_letter"),
    Offer(9, "Wardrobe JO", "عباية ساتان سوداء بلمعة ناعمة", "clothes", 46.0,
          ["abaya", "satin", "modest", "black", "عباية", "ساتان", "أسود"],
          ["black", "أسود"], ["modest", "luxury"], "السلط",
          "عباية ساتان سوداء بتفصيل انسيابي وخياطة نظيفة.",
          "https://images.unsplash.com/photo-1525507119028-ed4c629a60a3?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000009", "https://instagram.com/wardrobejo",
          ["M", "L", "XL"], "clothing_letter"),
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
          "https://wa.me/962790000023", "https://instagram.com/velveteditjo",
          ["M", "L"], "clothing_letter"),
    Offer(24, "Satin Story", "طقم ساتان أسود مريح وأنيق", "clothes", 33.0,
          ["satin", "set", "black", "modest", "ساتان", "طقم", "أسود", "محتشم"],
          ["black", "أسود"], ["modest", "minimal", "evening"], "عمّان",
          "طقم ساتان بلمسة فاخرة وقصة محتشمة سهلة التنسيق.",
          "https://images.unsplash.com/photo-1618220179428-22790b461013?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000024", "https://instagram.com/satinstoryjo",
          ["S", "M", "L"], "clothing_letter"),
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
    # --- v2: shoes ---
    Offer(29, "Step & Style", "كعب واطي أسود مريح للدوام", "shoes", 27.0,
          ["heels", "low heel", "black", "shoes", "شوز", "كعب", "كعب واطي", "أسود"],
          ["black", "أسود"], ["minimal", "day", "classic"], "عمّان",
          "شوز كعب واطي مريح مناسب للدوام والمشاوير اليومية.",
          "https://images.unsplash.com/photo-1543163521-1bf539c55dd2?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000029", "https://instagram.com/stepstylejo",
          ["37", "38", "39", "40"], "shoe_eu"),
    Offer(30, "Heels House JO", "شوز كعب عالي أسود للسهرات", "shoes", 34.0,
          ["heels", "high heel", "party", "black", "شوز", "كعب", "سهرة", "أسود"],
          ["black", "أسود"], ["party", "night", "elegant"], "عمّان",
          "كعب عالي أنيق بلون أسود مناسب للحفلات والمناسبات المسائية.",
          "https://images.unsplash.com/photo-1596703263926-eb0762ee17e4?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000030", "https://instagram.com/heelshousejo",
          ["36", "37", "38", "39", "40", "41"], "shoe_eu"),
    Offer(31, "Comfort Walk", "سنيكرز أبيض يومي خفيف", "shoes", 22.0,
          ["sneakers", "white", "casual", "shoes", "سنيكرز", "شوز", "أبيض"],
          ["white", "أبيض"], ["minimal", "casual", "day"], "إربد",
          "سنيكرز خفيف ومريح للاستخدام اليومي.",
          "https://images.unsplash.com/photo-1549298916-b41d501d3772?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000031", "https://instagram.com/comfortwalkjo",
          ["37", "38", "39", "40", "41", "42"], "shoe_eu"),
    Offer(32, "Velvet Step", "بوت شتوي بني جلد", "shoes", 39.0,
          ["boots", "brown", "leather", "winter", "بوت", "شوز", "بني"],
          ["brown", "بني"], ["classic", "casual"], "عمّان",
          "بوت جلد بني دافئ للإطلالات الشتوية.",
          "https://images.unsplash.com/photo-1520639888713-7851133b1ed0?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000032", "https://instagram.com/velvetstepjo",
          ["38", "39", "40", "41", "42"], "shoe_eu"),
    Offer(42, "Ballerina JO", "باليرينا سوداء ناعمة", "shoes", 19.0,
          ["ballerina", "flat", "black", "shoes", "باليرينا", "شوز", "أسود", "فلات"],
          ["black", "أسود"], ["minimal", "day", "soft"], "عمّان",
          "باليرينا سوداء مريحة وخفيفة للاستخدام اليومي.",
          "https://images.unsplash.com/photo-1560343090-f0409e92791a?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000042", "https://instagram.com/ballerinajo",
          ["36", "37", "38", "39", "40"], "shoe_eu"),
    # --- v2: lingerie ---
    Offer(33, "Intima JO", "برا قطن مريح بدون سلك", "lingerie", 9.0,
          ["bra", "cotton", "wireless", "برا", "سوتيان", "قطن", "مريح"],
          ["black", "white", "أسود", "أبيض"], ["minimal", "day"], "عمّان",
          "برا قطن ناعم بدون سلك معدني، مريح للاستخدام اليومي.",
          "https://images.unsplash.com/photo-1594744803329-e58b31de8bf5?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000033", "https://instagram.com/intimajo",
          ["34B", "34C", "36B", "36C", "38C"], "bra"),
    Offer(34, "Lace & Co", "سوتيان دانتيل أسود فاخر", "lingerie", 12.5,
          ["bra", "lace", "black", "برا", "سوتيان", "دانتيل", "أسود"],
          ["black", "أسود"], ["luxury", "elegant"], "عمّان",
          "سوتيان دانتيل أسود بخامة ناعمة وتفاصيل راقية.",
          "https://images.unsplash.com/photo-1582639510494-c80b5de9f148?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000034", "https://instagram.com/laceandcojo",
          ["32B", "34B", "34C", "36C"], "bra"),
    Offer(35, "Soft Touch", "طقم داخلي قطن ناعم", "lingerie", 11.0,
          ["underwear set", "cotton", "طقم", "داخلي", "لانجيري", "قطن"],
          ["cream", "pink", "كريمي", "زهري"], ["minimal", "soft"], "الزرقاء",
          "طقم داخلي قطني ناعم بألوان هادئة.",
          "https://images.unsplash.com/photo-1616627561950-9f746e330187?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000035", "https://instagram.com/softtouchjo",
          ["S", "M", "L", "XL"], "clothing_letter"),
    # --- v2: scarves / hijab ---
    Offer(36, "Hijab House", "طرحة شيفون سوداء خفيفة", "scarves", 6.0,
          ["scarf", "chiffon", "black", "طرحة", "شيفون", "حجاب", "أسود"],
          ["black", "أسود"], ["minimal", "day"], "عمّان",
          "طرحة شيفون خفيفة بثبات جيد للاستخدام اليومي.",
          "https://images.unsplash.com/photo-1601924994987-69e26d50dc26?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000036", "https://instagram.com/hijabhousejo",
          ["180x70"], "scarf_dimensions"),
    Offer(37, "Cotton Wrap", "شال قطن طويل وعريض", "scarves", 8.5,
          ["scarf", "cotton", "long", "wide", "شال", "قطن", "طويل", "عريض"],
          ["cream", "grey", "كريمي", "رمادي"], ["modest", "minimal"], "إربد",
          "شال قطن طويل وعريض بخامة غير شفافة ومريحة.",
          "https://images.unsplash.com/photo-1583391733956-6c78276477e2?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000037", "https://instagram.com/cottonwrapjo",
          ["200x80"], "scarf_dimensions"),
    Offer(38, "Medina Scarves", "شال ساتان فاخر للمناسبات", "scarves", 11.0,
          ["scarf", "satin", "luxury", "شال", "ساتان", "فاخر", "مناسبات"],
          ["gold", "cream", "ذهبي", "كريمي"], ["luxury", "elegant"], "عمّان",
          "شال ساتان بلمعة ناعمة مناسب للمناسبات والعزائم.",
          "https://images.unsplash.com/photo-1601370690183-1c7796ecec61?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000038", "https://instagram.com/medinaScarvesjo",
          ["190x75"], "scarf_dimensions"),
    Offer(39, "Warm Line", "شال صوف شتوي عريض", "scarves", 13.0,
          ["scarf", "wool", "winter", "wide", "شال", "صوف", "شتاء", "عريض"],
          ["brown", "grey", "بني", "رمادي"], ["classic", "warm"], "السلط",
          "شال صوف شتوي عريض وثقيل للتدفئة.",
          "https://images.unsplash.com/photo-1520903920243-00d872a2d1c9?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000039", "https://instagram.com/warmlinejo",
          ["200x90"], "scarf_dimensions"),
    # --- v2: extra clothes ---
    Offer(40, "Closet 36", "فستان أسود قصير كاجوال", "clothes", 26.0,
          ["dress", "short", "casual", "black", "فستان", "أسود", "كاجوال"],
          ["black", "أسود"], ["minimal", "casual", "day"], "عمّان",
          "فستان أسود قصير بقصة كاجوال سهلة التنسيق.",
          "https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000040", "https://instagram.com/closet36jo",
          ["S", "M", "L"], "clothing_letter"),
    Offer(41, "Urban Thread", "هودي أسود أوفرسايز", "clothes", 18.0,
          ["hoodie", "oversize", "black", "هودي", "أسود", "اوفرسايز"],
          ["black", "أسود"], ["casual", "street", "minimal"], "الزرقاء",
          "هودي أسود أوفرسايز بخامة قطنية ثقيلة.",
          "https://images.unsplash.com/photo-1556821840-3a63f95609a7?auto=format&fit=crop&w=1000&q=88",
          "https://wa.me/962790000041", "https://instagram.com/urbanthreadjo",
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
        "ساتان", "مخمل", "ثوب", "هودي", "بلوزة", "بلوزه", "تيشيرت", "بنطلون", "جينز",
        "clothes", "dress", "abaya", "hoodie", "shirt",
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
        "برا", "سوتيان", "لانجيري", "حمالة", "حماله", "داخلي", "كولوت",
        "bra", "lingerie", "underwear",
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
    "cream": {"كريمي", "سكري", "cream", "off-white"},
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

STOPWORDS = {
    "بدي", "بديش", "بدّي", "بدها", "بده", "شي", "اشي", "شيء", "الي", "إلي", "لي",
    "مع", "بدون", "لون", "سعر", "ميزانية", "ميزانيتي", "حد", "اقصى", "أقصى", "لحد", "حدود",
    "تحت", "اقل", "أقل", "من", "الى", "إلى", "دينار", "ديناراً", "جني", "جنيه", "jod", "jd",
    "بال", "لل", "ال", "و", "يا", "شو", "ايش", "اي", "أي", "بس", "فقط", "لـ", "عن",
    "انا", "أنا", "يكون", "تكون", "هو", "هي", "ممكن", "لو", "اذا", "إذا",
}


# ---------------------------------------------------------------------------
# JSON serialization guard
# ---------------------------------------------------------------------------

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
    r"(\d+(?:\.\d+)?)\s*(?:دينار|دينارات|jod|jd|د\.ا|دج)",
    r"(?:بـ|ب|حدها|حده)\s*(\d+(?:\.\d+)?)",
)


def extract_budget(text: str) -> Dict[str, Any]:
    """Return {"amount": float|None, "kind": hard_max|soft_target|flexible|cheapest|quality_first|none}."""
    t = normalize_arabic_digits(normalize_text(text))
    amount: Optional[float] = None
    for pattern in _BUDGET_AMOUNT_PATTERNS:
        m = re.search(pattern, t, flags=re.IGNORECASE)
        if m:
            amount = safe_float(m.group(1))
            if amount is not None:
                break
    if amount is None and re.search(r"دينار|jod|jd|ميزاني|سعر|تحت|اقل|لحد|حدود|اكثر|أكثر|يتجاوز|\$", t, re.I):
        m = re.search(r"\b(\d+(?:\.\d+)?)\b", t)
        if m:
            amount = safe_float(m.group(1))

    kind = "none"
    for k, pat in BUDGET_KIND_PATTERNS:
        if re.search(pat, t, re.I):
            kind = k
            break
    if kind == "none" and amount is not None:
        kind = "hard_max"  # رقم مع سياق ميزانية بدون تليين = قيد صارم افتراضيًا
    return {"amount": amount, "kind": kind}


# ---------------------------------------------------------------------------
# Size extraction per product type (v2)
# ---------------------------------------------------------------------------

BRA_RE = re.compile(r"\b(28|30|32|34|36|38|40|42)\s*([A-Fa-f]{1,2})\b")
SHOE_EU_RE = re.compile(r"\b(3[5-9]|4[0-6])\s*(eu|أوروبي|اوروبي|أوروبى)?\b", re.I)
SHOE_US_RE = re.compile(r"\b([5-9]|1[0-2])\s*(us|أمريكي|امريكي|امريكى)\b", re.I)
CLOTH_LETTER_RE = re.compile(r"\b(xxs|xs|s|m|l|xl|xxl|xxxl)\b", re.I)
CLOTH_NUM_RE = re.compile(r"\b(3[4-9]|4[0-8])\b")
DIM_RE = re.compile(r"(\d{2,3})\s*[×xX*]\s*(\d{2,3})")

_SHOE_CONTEXT = r"شوز|حذاء|كندرة|كعب|مقاس|بوت|سنيكرز|باليرينا|فلات|shoe|heel"
_CLOTH_CONTEXT = r"فستان|فساتين|ملابس|لبس|عباية|هودي|بلوزة|ثوب|dress|clothes"


def extract_sizes(text: str, categories: Optional[List[str]] = None) -> Dict[str, Any]:
    t = normalize_arabic_digits(normalize_text(text))
    out: Dict[str, Any] = {"system": None, "value": None, "band": None, "cup": None}
    m = BRA_RE.search(t)
    if m and not re.search(r"(?:أوروبي|اوروبي|eu)\b", t):
        out.update(system="bra", band=int(m.group(1)), cup=m.group(2).upper(),
                   value=f"{m.group(1)}{m.group(2).upper()}")
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
    return [t for t in tokens(clean) if len(t) > 1 and not re.fullmatch(r"\d+(?:\.\d+)?", t)]


# ---------------------------------------------------------------------------
# Optional Gemini extraction (extended schema)
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
category: قائمة من makeup, clothes, gifts, watches, perfumes, shoes, lingerie, scarves
budget: {{"amount": رقم أو null, "kind": واحدة من hard_max, soft_target, flexible, cheapest, quality_first, none}}
colors: قائمة canonical English colors المطلوبة
excluded_colors: ألوان رفضتها المستخدمة صراحة
excluded_terms: خامات/أنواع رفضتها (مثل شيفون)
styles: قائمة من luxury, party, modest, classic, minimal, gift
sizes: {{"system": bra|shoe_eu|shoe_us|clothing_letter|scarf_dimensions|null, "value": قيمة المقاس أو null, "band": رقم أو null, "cup": حرف أو null}}
brand_soft: اسم براند عالمي ذُكر كمرجع (مثل Zara) أو null
product_terms: كلمات المنتج المهمة

القواعد: "تحت/ما يتجاوز/ما بدفع أكثر" = hard_max. "بحدود/حوالي/تقريبًا" = soft_target. "ممكن أزيد لو الجودة" = flexible.

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
                "hard_max", "soft_target", "flexible", "cheapest", "quality_first"} else "hard_max"

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
# Follow-up refinement (v2) — "بدي أرخص" / "أفخم" / "مش شرط الأسود"
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
    elif new.get("budget", {}).get("amount") is not None or new.get("budget", {}).get("kind") in ("cheapest", "quality_first"):
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


# ---------------------------------------------------------------------------
# SQLite schema and repository (v2 — NO deletion on boot, seed-if-missing)
# ---------------------------------------------------------------------------

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
            # ترقية قواعد بيانات v1 القديمة بدون فقدان بياناتها
            _ensure_columns(conn, "merchant_offers", {
                "sizes": "TEXT NOT NULL DEFAULT '[]'",
                "size_system": "TEXT NOT NULL DEFAULT ''",
            })
            _ensure_columns(conn, "buyer_intents", {
                "budget_json": "TEXT NOT NULL DEFAULT '{}'",
                "sizes_json": "TEXT NOT NULL DEFAULT '{}'",
            })
            rows = [(
                o.id, o.merchant_name, o.title, o.category, o.price_jod,
                serialize_json(o.tags), serialize_json(o.colors), serialize_json(o.style),
                o.city, o.description, o.image_url, o.whatsapp_url, o.instagram_url,
                serialize_json(o.sizes), o.size_system,
            ) for o in SEED_OFFERS]
            conn.executemany(
                """
                INSERT OR IGNORE INTO merchant_offers
                (id, merchant_name, title, category, price_jod, tags, colors, style, city,
                 description, image_url, whatsapp_url, instagram_url, sizes, size_system)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
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


def create_session(session_id: str, intent: Dict[str, Any], user_ip: str = "") -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, user_ip, intent_json, updated_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (session_id, user_ip, serialize_json(intent)),
            )
            conn.commit()
        finally:
            conn.close()


def get_session_intent(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT intent_json FROM sessions WHERE session_id = ?",
                           (session_id,)).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["intent_json"])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
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


# ---------------------------------------------------------------------------
# Ranking with Hard/Soft budgets + sizes + explanations (v2)
# ---------------------------------------------------------------------------

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
        return None  # المنتج بدون بيانات مقاس — لا نعاقبه
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
            return None  # لا نرفض لاختلاف النظام، فقط لا نكافئ
        return str(sizes.get("value")) in offer.sizes
    if system in ("clothing_letter",):
        if offer.size_system != "clothing_letter":
            return False
        return str(sizes.get("value", "")).upper() in {s.upper() for s in offer.sizes}
    if system == "clothing_numeric":
        return None  # soft — نكافئ فقط عند التطابق النصي
    if system == "scarf_dimensions":
        return None  # soft
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

    # ---- Hard filters ----
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

    # ---- Budget fit ----
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
        elif kind == "flexible":
            if offer.price_jod <= amount:
                score += 4.0
            reasons.append(f"سعره {offer.price_jod:.0f} د.أ وميزانيتك مرنة")

    # ---- Sizes ----
    if sm is True:
        score += 25.0
        system = sizes.get("system")
        if system == "bra":
            reasons.append(f"متوفر بمقاسك {sizes.get('band')}{sizes.get('cup')}")
        elif system == "shoe_eu":
            reasons.append(f"متوفر بمقاسك {sizes.get('value')} أوروبي")
        elif system == "clothing_letter":
            reasons.append(f"متوفر بمقاس {sizes.get('value')}")

    # ---- Priorities ----
    if intent.get("priority") == "cheapest":
        score += max(0.0, 15.0 - offer.price_jod * 0.3)
    if intent.get("priority") == "quality" and "luxury" in offer.style:
        score += 8.0

    return max(0.0, min(100.0, round(score, 2))), (reasons or ["أقرب متاح لطلبك"])


def recommend(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for offer in get_offers():
        score, reasons = score_offer(offer, intent)
        if score < 0:
            continue
        payload = asdict(offer)
        payload["category_label"] = CATEGORY_LABELS.get(offer.category, offer.category)
        payload["score"] = score
        payload["reasons"] = reasons
        payload["match_type"] = "توصية دقيقة" if score >= 60 else "توصية قريبة"
        results.append(payload)
    if intent.get("priority") == "cheapest":
        results.sort(key=lambda item: (item["price_jod"], -item["score"], item["id"]))
    else:
        results.sort(key=lambda item: (-item["score"], item["price_jod"], item["id"]))
    return results


# ---------------------------------------------------------------------------
# Payment abstraction (v2)
# ---------------------------------------------------------------------------

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
            "note": "التحويل يدوي عبر CliQ؛ التأكيد في الواجهة يحرر الروابط فقط.",
        }

    def verify(self, session_id: str) -> bool:
        return False  # لا تحقق تلقائي في النسخة اليدوية


PAYMENT_PROVIDER: PaymentProvider = MockCliqProvider()


# ---------------------------------------------------------------------------
# HTML UI (v2 — reasons, refine follow-up, dynamic placeholder)
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
        <span class="hidden sm:inline-flex rounded-full bg-neutral-950 text-white px-3 py-1.5 text-[10px] font-black tracking-widest">VIP LIVE FEED</span>
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
          فضفضي بأي صياغة طبيعية — بنفهم المقاسات، الميزانية الصارمة والمرنة، والألوان المرفوضة. وبعدين تقدري تعدّلي طلبك: «بدي أرخص»، «بدي أفخم»، «مش شرط الأسود».
        </p>
      </section>

      <section class="bg-white p-4 sm:p-5 rounded-[2rem] sm:rounded-[2.5rem] border border-neutral-200/70 soft-shadow relative overflow-hidden">
        <form id="searchForm" class="space-y-3">
          <div class="rounded-3xl bg-neutral-50 border border-neutral-100 p-2">
            <textarea id="query" rows="3" required maxlength="2000"
              placeholder="احكيلنا شو ببالك… حتى بالعامية"
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
        <form id="refineForm" class="hidden mt-3 flex gap-2">
          <input id="refineInput" maxlength="500" placeholder="عدّلي طلبك: بدي أرخص / بدي أفخم / مش شرط الأسود…"
            class="flex-1 rounded-2xl bg-neutral-50 border border-neutral-100 px-4 py-3 text-sm font-medium outline-none placeholder:text-neutral-300" />
          <button type="submit" class="min-h-[48px] px-5 rounded-2xl bg-white border border-neutral-200 hover:bg-neutral-50 font-black text-sm transition">حدّثي ↻</button>
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
  const refineForm = document.getElementById('refineForm');
  const refineInput = document.getElementById('refineInput');
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

  const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
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
    if (b.amount !== null && b.amount !== undefined) {
      items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">💰 ${esc(budgetLabels[b.kind] || 'حتى')} ${esc(money(b.amount))}</span>`);
    } else if (b.kind === 'cheapest') {
      items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">💰 بدي الأرخص</span>`);
    } else if (b.kind === 'quality_first') {
      items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">✨ الجودة أهم من السعر</span>`);
    }
    const s = intent.sizes || {};
    if (s.system === 'bra') items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">📏 مقاس ${esc(s.band)}${esc(s.cup)}</span>`);
    else if (s.system === 'shoe_eu') items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">📏 مقاس ${esc(s.value)} أوروبي</span>`);
    else if (s.system && s.value) items.push(`<span class="rounded-xl bg-white border border-neutral-200 px-3 py-1.5 text-xs font-black text-neutral-600">📏 مقاس ${esc(s.value)}</span>`);
    intentPills.innerHTML = items.join('');
  }

  function productCard(item, index) {
    const score = Math.round(Number(item.score || 0));
    const tags = (item.tags || []).slice(0, 5).map(tag => `<span class="rounded-full bg-neutral-100 px-2 py-1 text-[10px] font-bold text-neutral-500">${esc(tag)}</span>`).join('');
    const reasons = (item.reasons || []).slice(0, 3).map(r =>
      `<li class="flex items-start gap-1.5 text-[11px] text-emerald-700 font-bold leading-5"><span class="mt-0.5">✓</span><span>${esc(r)}</span></li>`).join('');
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
          <ul class="mt-3 space-y-1">${reasons}</ul>
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
  dealModal.addEventListener('click', (event) => { if (event.target === dealModal) closeDealModal(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !dealModal.classList.contains('hidden')) closeDealModal(); });
  function confirmDeal() {
    if (!document.getElementById('paidCheck').checked) {
      alert('الرجاء تأكيد قراءة تعليمات تحويل 1 دينار عبر CliQ إلى البنك العربي — MQRB أولاً.');
      return;
    }
    closeDealModal();
    document.querySelectorAll('.contact-zone').forEach(el => el.classList.remove('hide-links'));
    if (activeCard) alert(`تم تحرير روابط التواصل لمنتج: ${activeCard.title}`);
  }

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

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const query = queryEl.value.trim();
    if (!query) return;
    empty.classList.add('hidden');
    grid.innerHTML = '';
    countBadge.classList.add('hidden');
    resultsTitle.textContent = 'جاري تحليل نيتكِ الشرائية…';
    setBusy(true);
    try {
      const response = await fetch('/api/recommend', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({query})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'تعذر إتمام البحث');
      sessionId = data.session_id;
      refineForm.classList.remove('hidden');
      renderResults(data);
    } catch (error) {
      renderError(error.message);
    } finally { setBusy(false); }
  });

  refineForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = refineInput.value.trim();
    if (!message || !sessionId) return;
    setBusy(true);
    try {
      const response = await fetch('/api/refine', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({session_id: sessionId, message})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'تعذر تحديث الطلب');
      refineInput.value = '';
      renderResults(data);
    } catch (error) {
      renderError(error.message);
    } finally { setBusy(false); }
  });

  queryEl.focus();
</script>
</body>
</html>'''


# ---------------------------------------------------------------------------
# HTTP application — shared router for the dev server AND the WSGI callable
# ---------------------------------------------------------------------------

STATUS_TEXT = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}


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


def _recommend_payload(intent: Dict[str, Any], request_id: str, session_id: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "session_id": session_id,
        "intent": _intent_public(intent),
        "results": recommend(intent),
        "payment": PAYMENT_PROVIDER.instructions(),
    }


def route_request(method: str, path: str, body: bytes, client_ip: str = "") -> Tuple[int, List[Tuple[str, str]], bytes]:
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
            if path in ("/", "/index.html"):
                page = HTML_TEMPLATE.replace("__PAYMENT_BANK_AR__", html.escape(PAYMENT_BANK_AR))
                return 200, headers + [("Content-Type", "text/html; charset=utf-8")], page.encode("utf-8")
            if path == "/healthz":
                return respond({"status": "ok"})
            if path == "/api/health":
                return respond({
                    "ok": True, "service": "التوصية", "version": "2.0", "port": PORT,
                    "inventory_count": len(SEED_OFFERS),
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
                create_session(session_id, intent, client_ip)
                return respond(_recommend_payload(intent, request_id, session_id))

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
                request_id = uuid.uuid4().hex
                save_buyer_intent(request_id, f"[refine] {message}", intent)
                create_session(session_id, intent, client_ip)
                return respond(_recommend_payload(intent, request_id, session_id))

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
        status, headers, payload = route_request(method, path, body, self.client_address[0] if self.client_address else "")
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
# WSGI entrypoint for gunicorn:  gunicorn app:application
# ---------------------------------------------------------------------------

def application(environ: Dict[str, Any], start_response: Any) -> List[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    body = environ["wsgi.input"].read(min(length, 128 * 1024)) if length > 0 else b""
    status, headers, payload = route_request(method, path, body, environ.get("REMOTE_ADDR", ""))
    start_response(f"{status} {STATUS_TEXT.get(status, 'OK')}", headers + [("Content-Length", str(len(payload)))])
    return [payload]


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    LOGGER.info("التوصية v2 تعمل على http://%s:%d", HOST, PORT)
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

init_db() # آمن ومتكرر CREATE IF NOT EXISTS + INSERT OR IGNORE - لا حذف إطلاقاً

if __name__ == "__main__":
    import os
    # سحب المنفذ ديناميكياً من السيرفر وإجبار النظام على وضعه في البيئة التشغيلية
    port = os.environ.get("PORT", "8000")
    os.environ["PORT"] = port
    
    main()