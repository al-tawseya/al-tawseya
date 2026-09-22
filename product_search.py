from __future__ import annotations

import difflib
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
import urllib.request
import urllib.error

try:
    from google.genai import types as genai_types  # type: ignore
except Exception:
    genai_types = None

LOGGER = logging.getLogger("al-tawseya.product-search")


ARABIC_NORMALIZATION = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ؤ": "و", "ئ": "ي",
    "ة": "ه",
})


SEARCH_SYNONYMS = {
    "بنطال": ["بنطال", "بنطلون", "سروال", "سراويل", "بنطل", "pants", "trousers", "jeans"],
    "بنطلون": ["بنطال", "بنطلون", "سروال", "سراويل", "بنطل", "pants", "trousers", "jeans"],
    "سراويل": ["بنطال", "بنطلون", "سروال", "سراويل", "pants", "trousers"],
    "ستيانه": ["ستيانه", "ستيّانة", "ستيانة", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "wireless bra"],
    "ستيّانة": ["ستيانه", "ستيّانة", "ستيانة", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "wireless bra"],
    "ستيانة": ["ستيانه", "ستيّانة", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "wireless bra"],
    "سوتيان": ["ستيانه", "ستيّانة", "ستيانة", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "wireless bra"],
    "برا": ["ستيانه", "ستيّانة", "سوتيان", "حمالة صدر", "صدرية", "bra", "bras", "brassiere"],
    "شوز": ["شوز", "حذاء", "كندرة", "shoe", "shoes", "sneakers", "heels", "boots", "flats"],
    "حذاء": ["شوز", "حذاء", "كندرة", "shoe", "shoes", "sneakers", "heels", "boots", "flats"],
    "فستان": ["فستان", "فساتين", "dress", "dresses", "evening dress", "maxi dress"],
    "عطر": ["عطر", "عطور", "برفان", "perfume", "fragrance", "parfum", "eau de parfum"],
    "روج": ["روج", "حومرة", "حمرة", "أحمر شفاه", "lipstick", "liquid lipstick", "lip gloss"],
    "مكياج": ["مكياج", "ميكب", "makeup", "cosmetics", "beauty"],
    "عباية": ["عباية", "عبايات", "abaya", "modest abaya"],
    "طرحة": ["طرحة", "حجاب", "شال", "scarf", "hijab", "shawl"],
}


COLOR_SYNONYMS = {
    "black": ["اسود", "أسود", "black", "noir"],
    "white": ["ابيض", "أبيض", "white"],
    "red": ["احمر", "أحمر", "red"],
    "blue": ["ازرق", "أزرق", "blue", "navy"],
    "green": ["اخضر", "أخضر", "green"],
    "pink": ["زهري", "وردي", "pink", "rose"],
    "beige": ["بيج", "beige", "camel"],
    "brown": ["بني", "brown", "tan"],
    "grey": ["رمادي", "رصاصي", "grey", "gray"],
    "cream": ["كريمي", "سكري", "cream"],
    "gold": ["ذهبي", "gold"],
    "silver": ["فضي", "silver"],
}


CATEGORY_KEYWORDS = {
    "clothes": ["بنطال", "بنطلون", "سروال", "pants", "trousers", "jeans", "فستان", "dress", "ملابس"],
    "lingerie": ["ستيانه", "ستيّانة", "ستيانة", "سوتيان", "برا", "حمالة صدر", "صدرية", "bra", "bras", "lingerie", "underwear"],
    "shoes": ["شوز", "حذاء", "كندرة", "shoe", "shoes", "sneakers", "heels", "boots", "flats"],
    "perfumes": ["عطر", "عطور", "برفان", "perfume", "fragrance", "parfum"],
    "makeup": ["روج", "مكياج", "ميكب", "lipstick", "makeup", "cosmetics"],
    "scarves": ["طرحة", "حجاب", "شال", "scarf", "hijab", "shawl"],
    "watches": ["ساعة", "ساعات", "watch", "watches"],
    "gifts": ["هدية", "هدايا", "gift", "gifts", "بوكس"],
}


STOPWORDS = {
    "بدي", "بدّي", "بدها", "بده", "شي", "اشي", "شيء", "الي", "إلي", "لي",
    "مع", "بدون", "لون", "سعر", "ميزانية", "ميزانيتي", "حد", "اقصى", "أقصى",
    "تحت", "اقل", "أقل", "من", "الى", "إلى", "دينار", "دينارات", "jod", "jd",
    "بال", "لل", "ال", "و", "يا", "شو", "ايش", "اي", "أي", "بس", "فقط",
    "عن", "انا", "أنا", "يكون", "تكون", "هو", "هي", "ممكن", "لو", "اذا", "إذا",
    "للدوام", "دوام", "للبيت", "للمنزل", "اجل", "شان", "عشان",
}


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.translate(ARABIC_NORMALIZATION)
    text = re.sub(r"[ًٌٍَُِّّْـ]", "", text)
    text = re.sub(r"[^a-z0-9+#.\-\u0600-\u06ff\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_digits(value: Any) -> str:
    return str(value or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = normalize_digits(value).replace(",", ".")
        return float(text)
    except (TypeError, ValueError):
        return None


def tokens(value: Any) -> List[str]:
    text = normalize_text(value)
    raw = re.findall(r"[a-z0-9_+#.-]+|[؀-ۿ]+", text, re.I)
    return [x for x in raw if x not in STOPWORDS and len(x) > 1]


def normalized_terms(value: Iterable[str]) -> List[str]:
    out: List[str] = []
    for item in value:
        t = normalize_text(item)
        if t and t not in out:
            out.append(t)
    return out


def canonicalize_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith(("utm_", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "affiliate"))]
    clean = parsed._replace(fragment="", query=urlencode(query))
    return urlunparse(clean)


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_domain(a: str, b: str) -> bool:
    return domain_of(a) == domain_of(b) and bool(domain_of(a))


@dataclass
class SearchDependencies:
    get_gemini_client: Callable[[], Any]
    model_name: str
    live_enabled: bool = True
    max_results: int = 8
    query_count: int = 8
    cache_ttl_seconds: int = 180
    grounding_source_extractor: Optional[Callable[[Any], List[Dict[str, str]]]] = None


class ProductIntentEngine:
    def enrich(self, user_text: str, base_intent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base = dict(base_intent or {})
        terms = list(base.get("product_terms", []))
        synonyms = list(base.get("synonyms", []))

        clean = normalize_text(user_text)
        for key, variants in SEARCH_SYNONYMS.items():
            if normalize_text(key) in clean:
                terms.extend(variants[:8])
                synonyms.extend(variants)

        for term in terms:
            synonyms.extend(SEARCH_SYNONYMS.get(normalize_text(term), []))

        if not base.get("categories"):
            cats = self._detect_categories(clean)
            if cats:
                base["categories"] = cats

        if not base.get("colors"):
            base["colors"] = self._detect_colors(clean)

        if not base.get("sizes") or not base.get("sizes", {}).get("system"):
            size = self._detect_size(clean, base.get("categories") or [])
            if size["system"]:
                base["sizes"] = size

        base["product_terms"] = list(dict.fromkeys(terms))[:50]
        base["synonyms"] = list(dict.fromkeys(synonyms))[:40]

        required = self._required_traits(clean)
        base["required_traits"] = list(dict.fromkeys(list(base.get("required_traits", [])) + required))
        return base

    def _detect_categories(self, text: str) -> List[str]:
        scores: List[Tuple[int, str]] = []
        for category, words in CATEGORY_KEYWORDS.items():
            score = 0
            for word in words:
                n = normalize_text(word)
                if " " in n and n in text:
                    score += 2
                elif n in tokens(text) or n in text:
                    score += 1
            if score:
                scores.append((score, category))
        scores.sort(key=lambda x: (-x[0], x[1]))
        return [c for _, c in scores]

    def _detect_colors(self, text: str) -> List[str]:
        out: List[str] = []
        for color, words in COLOR_SYNONYMS.items():
            if any(normalize_text(w) in text for w in words):
                out.append(color)
        return out

    def _detect_size(self, text: str, categories: Sequence[str]) -> Dict[str, Any]:
        out = {"system": None, "value": None, "band": None, "cup": None}
        m = re.search(r"\b(28|30|32|34|36|38|40|42)\s*([A-Fa-f]{1,2})\b", normalize_digits(text))
        if m and ("lingerie" in categories or re.search(r"bra|ستيانه|سوتيان|برا|حمالة", text, re.I)):
            out.update({"system": "bra", "band": int(m.group(1)), "cup": m.group(2).upper(), "value": f"{m.group(1)}{m.group(2).upper()}"})
            return out
        m = re.search(r"\b(35|36|37|38|39|40|41|42|43|44|45|46)\b", normalize_digits(text))
        if m and ("shoes" in categories or re.search(r"شوز|حذاء|shoe|كندرة", text, re.I)):
            out.update({"system": "shoe_eu", "value": int(m.group(1))})
        else:
            m = re.search(r"\b(XXS|XS|S|M|L|XL|XXL|XXXL)\b", text, re.I)
            if m and ("clothes" in categories or re.search(r"بنطال|بنطلون|فستان|dress|clothes", text, re.I)):
                out.update({"system": "clothing_letter", "value": m.group(1).upper()})
        return out

    def _required_traits(self, text: str) -> List[str]:
        required: List[str] = []
        if re.search(r"بدون\s+سلك|مش\s+بسلك|مو\s+بسلك|wireless|non[- ]?wire", text, re.I):
            required.append("wireless")
        if re.search(r"واسع|wide\s*leg|wide-leg|baggy|loose", text, re.I):
            required.append("wide")
        if re.search(r"مريح|راحة|comfortable|comfort", text, re.I):
            required.append("comfortable")
        if re.search(r"للدوام|دوام|office|workwear", text, re.I):
            required.append("workwear")
        if re.search(r"جينز|jeans|denim", text, re.I):
            required.append("denim")
        return required


class QueryGenerator:
    def __init__(self, intent_engine: ProductIntentEngine):
        self.intent_engine = intent_engine

    def generate(self, user_text: str, intent: Dict[str, Any], refresh_nonce: str = "") -> List[str]:
        intent = self.intent_engine.enrich(user_text, intent)
        terms = " ".join(intent.get("synonyms", [])[:10])
        category = " ".join(intent.get("categories", [])[:2])
        colors = " ".join(intent.get("colors", [])[:3])
        size = intent.get("sizes", {}) or {}
        size_text = str(size.get("value") or "")
        budget = intent.get("budget", {}) or {}
        budget_text = ""
        if budget.get("amount") is not None:
            budget_text = f"{budget.get('amount')} JOD"
        raw = user_text.strip()

        q = [
            f"{raw} الأردن شراء منتج متجر",
            f"{terms} Jordan JOD price online",
            f"{category} {terms} الأردن",
            f"site:.jo {terms}",
            f"site:.jo {terms} {colors} {size_text}".strip(),
            f"{terms} {colors} {budget_text} Jordan".strip(),
            f"{terms} Jordan local store buy",
            f"{terms} {size_text} alternative stores Jordan {refresh_nonce[-8:] if refresh_nonce else ''}".strip(),
        ]
        # Search the global web only when local/Jordan wording is insufficient or on refresh.
        if refresh_nonce:
            q.append(f"{terms} international stores Jordan shipping {refresh_nonce[-6:]}")
        return list(dict.fromkeys(x.strip() for x in q if x.strip()))[: max(6, min(12, int(os.getenv("SEARCH_QUERY_COUNT", "8"))))]


class WebSearchEngine:
    def __init__(self, deps: SearchDependencies):
        self.deps = deps

    def discover(self, user_text: str, intent: Dict[str, Any], queries: Sequence[str], avoid_urls: Sequence[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.deps.live_enabled:
            return [], {"status": "disabled"}

        client = self.deps.get_gemini_client()
        if client is None or genai_types is None:
            return [], {"status": "gemini_unavailable"}

        avoid = {canonicalize_url(u) for u in avoid_urls if canonicalize_url(u)}
        candidates: List[Dict[str, Any]] = []
        seen: set = set()
        errors: List[str] = []

        for rank, query in enumerate(queries, start=1):
            prompt = f"""
أنت وكيل اكتشاف منتجات حقيقي داخل محرك بحث تسوق.
نفّذ Google Search الآن ولا تعتمد على الذاكرة.

طلب المستخدم: {user_text!r}
النية المفهومية: {json.dumps(intent, ensure_ascii=False)}
استعلام البحث: {query!r}

ابحث عن صفحات منتجات قابلة للشراء فقط.
الأولوية للمتاجر الأردنية، ثم المتاجر التي تشحن إلى الأردن، ثم الإقليمية، ثم العالمية.
استخدم مرادفات اللهجة الأردنية/الشامية والعربية والإنجليزية.
مثال:
بنطال = بنطال/بنطلون/سروال/pants/trousers/jeans
ستيّانة = ستيانة/سوتيان/برا/حمالة صدر/صدرية/bra/bras

لا تخترع روابط.
لا تعطي الصفحة الرئيسية أو صفحة بحث عامة إذا كانت صفحة المنتج متاحة.
أعد الروابط التي عثرت عليها فعليًا، ويمكنك ذكر اسم المنتج بجانب الرابط.
"""
            try:
                response = client.models.generate_content(
                    model=self.deps.model_name,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                        temperature=0.1,
                    ),
                )

                sources = self.deps.grounding_source_extractor(response) if self.deps.grounding_source_extractor else self._grounding_sources(response)
                text_response = getattr(response, "text", "") or ""
                urls = [x.get("url") for x in sources if x.get("url")]
                urls.extend(re.findall(r"https?://\S+", text_response))

                for source_rank, url in enumerate(urls, start=1):
                    url = canonicalize_url(url)
                    if not url or url in avoid or url in seen:
                        continue
                    seen.add(url)
                    candidates.append({
                        "source_url": url,
                        "search_query": query,
                        "search_rank": rank * 1000 + source_rank,
                        "source_domain": domain_of(url),
                        "discovered_at": time.time(),
                        "grounding_title": (next((x.get("title", "") for x in sources if x.get("url") == url), "")[:180]),
                    })
            except Exception as exc:
                errors.append(f"query_{rank}:{type(exc).__name__}")
                LOGGER.warning("Product search query %s failed: %s", rank, exc)

            if len(candidates) >= 32:
                break

        return candidates, {"status": "ok", "queries": list(queries), "candidate_count": len(candidates), "errors": errors}

    @staticmethod
    def _grounding_sources(response: Any) -> List[Dict[str, str]]:
        sources: List[Dict[str, str]] = []
        seen: set = set()
        try:
            for candidate in getattr(response, "candidates", None) or []:
                metadata = getattr(candidate, "grounding_metadata", None)
                for chunk in getattr(metadata, "grounding_chunks", None) or []:
                    web = getattr(chunk, "web", None)
                    uri = getattr(web, "uri", None) if web else None
                    title = getattr(web, "title", None) if web else None
                    uri = canonicalize_url(uri)
                    if uri and uri not in seen:
                        seen.add(uri)
                        sources.append({"url": uri, "title": str(title or "")})
        except Exception:
            pass
        return sources


class ProductPageFetcher:
    def fetch(self, url: str, timeout: int = 8) -> Dict[str, Any]:
        url = canonicalize_url(url)
        if not url:
            return {"verification_status": "rejected", "rejection_reason": "broken_url"}

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 AlTawseyaProductSearch/10.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                final_url = canonicalize_url(resp.geturl())
                code = int(getattr(resp, "status", 200) or 200)
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if code >= 400:
                    return {"verification_status": "rejected", "rejection_reason": f"http_{code}"}
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    return {"verification_status": "rejected", "rejection_reason": "not_product_page"}

                raw = resp.read(1_200_000)
                text = raw.decode("utf-8", errors="ignore")
                if len(text.strip()) < 200:
                    return {"verification_status": "rejected", "rejection_reason": "empty_page"}

                return {"verification_status": "verified_page", "final_url": final_url, "html": text}
        except urllib.error.HTTPError as exc:
            return {"verification_status": "rejected", "rejection_reason": f"http_{exc.code}"}
        except Exception as exc:
            return {"verification_status": "rejected", "rejection_reason": "broken_url", "error": type(exc).__name__}


class ProductExtractor:
    PRICE_META_KEYS = (
        ("price", r'<meta[^>]+(?:property|name)=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)'),
        ("currency", r'<meta[^>]+(?:property|name)=["\']product:price:currency["\'][^>]+content=["\']([^"\']+)'),
    )

    def extract(self, page: Dict[str, Any], candidate: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
        if page.get("verification_status") != "verified_page":
            return {"verification_status": "rejected", "rejection_reason": page.get("rejection_reason", "broken_url")}

        raw = page.get("html", "")
        final_url = page.get("final_url") or candidate.get("source_url")
        product = self._from_json_ld(raw)

        title = product.get("title") or self._meta(raw, "og:title") or self._html_title(raw) or candidate.get("grounding_title", "")
        description = product.get("description") or self._meta(raw, "og:description")
        image_url = product.get("image_url") or self._meta(raw, "og:image")
        canonical = product.get("canonical_url") or self._canonical(raw) or final_url
        brand = product.get("brand", "")
        sku = product.get("sku", "")
        availability = product.get("availability", "")
        sizes = product.get("sizes", [])
        colors = product.get("colors", [])
        category = product.get("category", "")
        material = product.get("material", "")
        price = product.get("price")
        currency = product.get("currency", "")

        if price is None:
            meta = self._meta_price(raw)
            price = meta.get("price")
            currency = currency or meta.get("currency", "")

        if price is None:
            visible = self._visible_price(raw)
            price = visible.get("price")
            currency = currency or visible.get("currency", "")

        title = re.sub(r"\s+", " ", html.unescape(str(title or ""))).strip()[:220]
        description = re.sub(r"\s+", " ", html.unescape(str(description or ""))).strip()[:500]
        if len(title) < 3:
            return {"verification_status": "rejected", "rejection_reason": "not_product_page"}

        if not price or safe_float(price) is None or safe_float(price) <= 0:
            return {"verification_status": "partially_verified", "rejection_reason": "no_price",
                    "title": title, "canonical_url": canonicalize_url(canonical)}

        return {
            "verification_status": "verified",
            "title": title,
            "description": description,
            "image_url": self._absolute_image(final_url, image_url),
            "canonical_url": canonicalize_url(canonical) or final_url,
            "merchant_name": self._merchant_name(raw, final_url),
            "brand": brand,
            "sku": sku,
            "availability": availability,
            "sizes": sizes,
            "colors": colors,
            "category": category,
            "material": material,
            "price_original": round(float(price), 4),
            "currency_original": str(currency or "").upper(),
        }

    def _from_json_ld(self, raw: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw, re.I | re.S):
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
                graph = node.get("@graph")
                if isinstance(graph, list):
                    queue.extend(graph)
                typ = str(node.get("@type") or "").lower()
                if "product" not in typ and not node.get("offers"):
                    continue

                out["title"] = out.get("title") or str(node.get("name") or "")
                out["description"] = out.get("description") or str(node.get("description") or "")
                out["brand"] = out.get("brand") or self._value(node.get("brand"))
                out["sku"] = out.get("sku") or str(node.get("sku") or "")
                out["category"] = out.get("category") or str(node.get("category") or "")
                out["material"] = out.get("material") or str(node.get("material") or "")
                image = node.get("image")
                if isinstance(image, list):
                    image = image[0] if image else ""
                if isinstance(image, dict):
                    image = image.get("url", "")
                out["image_url"] = out.get("image_url") or str(image or "")

                color = node.get("color")
                if color:
                    out["colors"] = out.get("colors", []) + ([color] if isinstance(color, str) else list(color))
                size = node.get("size")
                if size:
                    out["sizes"] = out.get("sizes", []) + ([size] if isinstance(size, str) else list(size))

                offers = node.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    out["price"] = out.get("price") or offers.get("price") or offers.get("lowPrice")
                    out["currency"] = out.get("currency") or str(offers.get("priceCurrency") or "")
                    out["availability"] = out.get("availability") or str(offers.get("availability") or "")
                if out.get("title") and out.get("price"):
                    break
        return out

    @staticmethod
    def _value(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("name") or value.get("value") or "")
        return str(value or "")

    @staticmethod
    def _meta(raw: str, property_name: str) -> str:
        patterns = [
            rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
            rf'<meta[^>]+name=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)',
        ]
        for pattern in patterns:
            m = re.search(pattern, raw, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
        return ""

    @staticmethod
    def _canonical(raw: str) -> str:
        m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', raw, re.I)
        if not m:
            return ""
        return html.unescape(m.group(1)).strip()

    @staticmethod
    def _html_title(raw: str) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        if not m:
            return ""
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()

    def _meta_price(self, raw: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {"price": None, "currency": ""}
        for key, pattern in self.PRICE_META_KEYS:
            m = re.search(pattern, raw, re.I)
            if not m:
                continue
            value = html.unescape(m.group(1)).strip()
            if key == "price":
                result["price"] = safe_float(value)
            else:
                result["currency"] = value.upper()
        return result

    def _visible_price(self, raw: str) -> Dict[str, Any]:
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", html.unescape(text))
        patterns = [
            (r"(?:JOD|JD|د\.?ا|دينار)\s*([0-9]+(?:[.,][0-9]{1,2})?)", "JOD"),
            (r"([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:JOD|JD|د\.?ا|دينار)", "JOD"),
            (r"\$\s*([0-9]+(?:[.,][0-9]{1,2})?)", "USD"),
            (r"€\s*([0-9]+(?:[.,][0-9]{1,2})?)", "EUR"),
            (r"£\s*([0-9]+(?:[.,][0-9]{1,2})?)", "GBP"),
        ]
        for pattern, currency in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                return {"price": safe_float(m.group(1)), "currency": currency}
        return {"price": None, "currency": ""}

    @staticmethod
    def _merchant_name(raw: str, final_url: str) -> str:
        value = ProductExtractor._meta(raw, "og:site_name")
        if value:
            return value[:100]
        title = ProductExtractor._html_title(raw)
        if " - " in title:
            return title.rsplit(" - ", 1)[-1][:100]
        return domain_of(final_url)[:100]

    @staticmethod
    def _absolute_image(page_url: str, value: str) -> str:
        if not value:
            return ""
        return urljoin(page_url, value)


class CurrencyConverter:
    def __init__(self):
        self.template = os.getenv("FX_API_URL_TEMPLATE", "https://open.er-api.com/v6/latest/{currency}")
        self.timeout = 5
        self.cache: Dict[str, Tuple[float, float]] = {}
        self.lock = threading.RLock()

    def to_jod(self, amount: float, currency: str) -> Dict[str, Any]:
        currency = str(currency or "").upper()
        if currency in {"JOD", "JD"}:
            return {"price_jod": amount, "rate": 1.0, "source": "identity", "timestamp": time.time()}
        if not currency:
            return {"price_jod": None, "rate": None, "source": "", "timestamp": time.time()}

        now = time.time()
        with self.lock:
            cached = self.cache.get(currency)
            if cached and now - cached[1] < 3600:
                return {"price_jod": round(amount * cached[0], 4), "rate": cached[0], "source": "fx_cache", "timestamp": cached[1]}

        try:
            endpoint = self.template.format(currency=currency)
            req = urllib.request.Request(endpoint, headers={"User-Agent": "AlTawseyaFX/10.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read(200_000).decode("utf-8", errors="ignore"))
            rate = safe_float((data.get("rates") or {}).get("JOD"))
            if rate is None or rate <= 0:
                raise ValueError("missing_jod_rate")
            with self.lock:
                self.cache[currency] = (rate, now)
            return {"price_jod": round(amount * rate, 4), "rate": rate, "source": self.template.split("/v", 1)[0], "timestamp": now}
        except Exception as exc:
            LOGGER.info("FX conversion skipped for %s: %s", currency, exc)
            return {"price_jod": None, "rate": None, "source": "", "timestamp": now}


class ProductNormalizer:
    def normalize(self, product: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
        title = re.sub(r"\s+", " ", str(product.get("title") or "")).strip()
        description = re.sub(r"\s+", " ", str(product.get("description") or "")).strip()
        url = canonicalize_url(product.get("canonical_url") or product.get("store_url"))
        currency = str(product.get("currency_original") or "").upper()
        original_price = safe_float(product.get("price_original"))
        if original_price is None or original_price <= 0:
            raise ValueError("no_price")

        merchant = str(product.get("merchant_name") or domain_of(url) or "متجر").strip()[:120]
        normalized = {
            **product,
            "title": title[:220],
            "description": description[:500],
            "canonical_url": url,
            "store_url": url,
            "merchant_name": merchant,
            "normalized_domain": domain_of(url),
            "price_original": original_price,
            "currency_original": currency or "JOD",
        }
        return normalized


class ProductVerifier:
    def __init__(self):
        self.cache: Dict[str, Tuple[bool, float]] = {}
        self.lock = threading.RLock()

    def verify_match(self, product: Dict[str, Any], user_text: str, intent: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        title = normalize_text(product.get("title"))
        description = normalize_text(product.get("description"))
        body = f"{title} {description} {normalize_text(product.get('category'))}"
        categories = list(intent.get("categories", []))
        terms = normalized_terms(list(intent.get("product_terms", [])) + list(intent.get("synonyms", [])))

        if categories:
            category_text = " ".join(CATEGORY_KEYWORDS.get(categories[0], []))
            if category_text and not any(normalize_text(x) in body for x in CATEGORY_KEYWORDS.get(categories[0], [])):
                # URLs/titles can be English while extracted category is vague. Require
                # at least one semantic product term before treating as a category mismatch.
                if not any(x in body for x in terms[:20]):
                    return False, "wrong_category"

        if terms:
            overlap = 0
            for term in terms[:30]:
                nt = normalize_text(term)
                if nt and nt in body:
                    overlap += 1
            if overlap == 0:
                return False, "low_relevance"

        return True, None


class DuplicateDetector:
    def __init__(self):
        self.similarity_threshold = 0.93

    def fingerprint(self, product: Dict[str, Any]) -> str:
        sku = normalize_text(product.get("sku"))
        brand = normalize_text(product.get("brand"))
        title = " ".join(tokens(product.get("title")))
        image = canonicalize_url(product.get("image_url"))
        raw = "|".join([sku, brand, title, image])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def are_same(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        if canonicalize_url(a.get("canonical_url")) == canonicalize_url(b.get("canonical_url")):
            return True

        sku_a, sku_b = normalize_text(a.get("sku")), normalize_text(b.get("sku"))
        if sku_a and sku_b and sku_a == sku_b:
            return True

        brand_a, brand_b = normalize_text(a.get("brand")), normalize_text(b.get("brand"))
        title_a = " ".join(tokens(a.get("title")))
        title_b = " ".join(tokens(b.get("title")))
        sim = difflib.SequenceMatcher(None, title_a, title_b).ratio()
        if brand_a and brand_b and brand_a == brand_b and sim >= self.similarity_threshold:
            return True

        img_a = canonicalize_url(a.get("image_url"))
        img_b = canonicalize_url(b.get("image_url"))
        if img_a and img_b and img_a == img_b and sim >= 0.86:
            return True

        return False


class ProductMatcher:
    def hard_filter(self, product: Dict[str, Any], intent: Dict[str, Any]) -> Tuple[bool, str]:
        price = safe_float(product.get("price_jod"))
        budget = intent.get("budget", {}) or {}

        if price is None or price <= 0:
            return False, "no_price"

        amount = safe_float(budget.get("amount"))
        kind = budget.get("kind", "none")
        if amount is not None and kind == "hard_max" and price > amount:
            return False, "budget_exceeded"

        ex_colors = set(intent.get("excluded_colors", []))
        text = normalize_text(f"{product.get('title','')} {product.get('description','')} {' '.join(product.get('colors', []))}")
        if ex_colors:
            for color, synonyms in COLOR_SYNONYMS.items():
                if color in ex_colors and any(normalize_text(x) in text for x in synonyms):
                    return False, "excluded_color"

        for term in normalized_terms(intent.get("excluded_terms", [])):
            if term == "سلك":
                # "بدون سلك" and wireless are compatible with the requirement.
                if "wireless" in text or "بدون سلك" in text or "بدون سلك" in normalize_text(product.get("description")):
                    continue
            if term and term in text:
                return False, "excluded_term"

        sizes = intent.get("sizes", {}) or {}
        if sizes.get("system"):
            requested = normalize_text(sizes.get("value"))
            product_sizes = {normalize_text(x) for x in product.get("sizes", [])}
            if requested:
                if not product_sizes:
                    return False, "size_unknown"
                if requested not in product_sizes:
                    # Some stores write 36 C instead of 36C.
                    compact = {x.replace(" ", "") for x in product_sizes}
                    if requested.replace(" ", "") not in compact:
                        return False, "wrong_size"

        required_traits = set(intent.get("required_traits", []))
        body = normalize_text(f"{product.get('title','')} {product.get('description','')} {' '.join(product.get('tags', []))}")
        if "wireless" in required_traits and not (
            "wireless" in body or "بدون سلك" in body or "no wire" in body or "non wire" in body or "non-wire" in body
        ):
            return False, "wireless_unverified"
        if "wide" in required_traits and not any(x in body for x in ("wide leg", "wide-leg", "واسع", "baggy", "loose fit", "relaxed fit")):
            return False, "style_unverified"
        if "denim" in required_traits and not any(x in body for x in ("denim", "jeans", "جينز")):
            return False, "material_unverified"

        return True, ""


class PriceComparator:
    def compare(self, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        groups: List[List[Dict[str, Any]]] = []
        detector = DuplicateDetector()

        for product in products:
            placed = False
            for group in groups:
                if detector.are_same(product, group[0]):
                    group.append(product)
                    placed = True
                    break
            if not placed:
                groups.append([product])

        merged: List[Dict[str, Any]] = []
        for group in groups:
            group = sorted(group, key=lambda x: (safe_float(x.get("price_jod")) or 1e12, x.get("search_rank", 999999)))
            primary = dict(group[0])
            comparisons = []
            for item in group:
                comparisons.append({
                    "merchant_name": item.get("merchant_name"),
                    "domain": item.get("normalized_domain"),
                    "price_jod": item.get("price_jod"),
                    "price_original": item.get("price_original"),
                    "currency_original": item.get("currency_original"),
                    "url": item.get("store_url"),
                })
            primary["price_comparison"] = comparisons
            primary["price_options"] = len(comparisons)
            primary["lowest_price_jod"] = min(x["price_jod"] for x in comparisons if x.get("price_jod") is not None)
            primary["highest_price_jod"] = max(x["price_jod"] for x in comparisons if x.get("price_jod") is not None)
            merged.append(primary)
        return merged


class RankingEngine:
    def score(self, product: Dict[str, Any], user_text: str, intent: Dict[str, Any]) -> Tuple[float, List[str]]:
        body = normalize_text(f"{product.get('title','')} {product.get('description','')} {product.get('category','')} {' '.join(product.get('tags', []))}")
        title = normalize_text(product.get("title"))
        score = 0.0
        reasons: List[str] = []

        categories = intent.get("categories", [])
        terms = normalized_terms(list(intent.get("product_terms", [])) + list(intent.get("synonyms", [])))
        colors = intent.get("colors", [])
        required = set(intent.get("required_traits", []))
        sizes = intent.get("sizes", {}) or {}
        budget = intent.get("budget", {}) or {}

        if categories:
            cat_words = CATEGORY_KEYWORDS.get(categories[0], [])
            cat_match = any(normalize_text(x) in body for x in cat_words)
            if cat_match:
                score += 18
                reasons.append("الفئة مطابقة لطلبك")
        else:
            score += 10

        exact_hits = sum(1 for term in terms[:24] if normalize_text(term) in title)
        semantic_hits = sum(1 for term in terms[:32] if normalize_text(term) in body)
        score += min(18, exact_hits * 3)
        score += min(22, semantic_hits * 1.5)
        if exact_hits:
            reasons.append("اسم المنتج قريب مباشرة من الكلمات التي طلبتيها")

        product_colors = {normalize_text(x) for x in product.get("colors", [])}
        for color in colors:
            if normalize_text(color) in product_colors or normalize_text(color) in body:
                score += 8
                reasons.append(f"اللون {color} موجود أو مذكور في المنتج")
                break

        if sizes.get("system"):
            requested = normalize_text(sizes.get("value"))
            if requested and requested in {normalize_text(x) for x in product.get("sizes", [])}:
                score += 14
                reasons.append(f"المقاس {sizes.get('value')} مؤكد في الصفحة")

        for trait, pts, label in (
            ("wireless", 10, "بدون سلك"),
            ("wide", 8, "قصة واسعة"),
            ("comfortable", 5, "مريح"),
            ("workwear", 5, "مناسب للدوام"),
            ("denim", 5, "جينز/Denim"),
        ):
            if trait in required and any(x in body for x in {
                "wireless": ["wireless", "بدون سلك", "no wire", "non wire"],
                "wide": ["wide leg", "wide-leg", "واسع", "baggy", "loose fit", "relaxed fit"],
                "comfortable": ["comfortable", "comfort", "مريح", "ناعم"],
                "workwear": ["work", "office", "formal", "دوام", "مكتب"],
                "denim": ["denim", "jeans", "جينز"],
            }[trait]):
                score += pts
                reasons.append(label)

        amount = safe_float(budget.get("amount"))
        if amount:
            price = safe_float(product.get("price_jod")) or 0
            kind = budget.get("kind")
            if kind in {"soft_target", "quality_first", "flexible"}:
                distance = abs(price - amount) / max(amount, 1)
                score += max(0, 8 * (1 - min(distance, 1)))
                reasons.append("السعر قريب من الميزانية")
            elif kind == "cheapest":
                score += max(0, 8 - price * 0.15)
                reasons.append("السعر منخفض مقارنة بالخيارات")
            elif kind == "hard_max":
                score += max(0, 6 - max(0, amount - price) / max(amount, 1) * 3)
                reasons.append("السعر ضمن الحد المطلوب")

        if domain_of(product.get("store_url", "" )).endswith(".jo"):
            score += 7
            reasons.append("متجر بنطاق أردني")

        if product.get("verification_status") == "verified":
            score += 8
        if product.get("price_options", 1) > 1:
            score += 5
            reasons.append(f"تم العثور على {product.get('price_options')} خيارات سعر لنفس المنتج")

        return round(min(100, score), 2), list(dict.fromkeys(reasons))[:4]

    def rank(self, products: List[Dict[str, Any]], user_text: str, intent: Dict[str, Any], max_results: int) -> List[Dict[str, Any]]:
        scored = []
        for product in products:
            score, reasons = self.score(product, user_text, intent)
            product = dict(product)
            product["score"] = score
            product["reasons"] = reasons or ["مطابقة من البحث المباشر"]
            product["match_type"] = "بحث مباشر"
            scored.append(product)

        scored.sort(key=lambda x: (-x["score"], x.get("lowest_price_jod", x.get("price_jod", 1e12)), x.get("search_rank", 999999)))
        return self._diversify(scored, max_results)

    @staticmethod
    def _diversify(products: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
        first_pass: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}
        rest: List[Dict[str, Any]] = []
        for product in products:
            domain = product.get("normalized_domain", "")
            if counts.get(domain, 0) < 3:
                first_pass.append(product)
                counts[domain] = counts.get(domain, 0) + 1
                if len(first_pass) >= max_results:
                    break
            else:
                rest.append(product)
        if len(first_pass) < max_results:
            for product in products:
                if product in first_pass:
                    continue
                first_pass.append(product)
                if len(first_pass) >= max_results:
                    break
        return first_pass


class SearchCache:
    def __init__(self, ttl_seconds: int = 180, max_items: int = 64):
        self.ttl = max(30, ttl_seconds)
        self.max_items = max_items
        self.data: Dict[str, Tuple[float, List[Dict[str, Any]], Dict[str, Any]]] = {}
        self.lock = threading.RLock()

    def _key(self, user_text: str, intent: Dict[str, Any], refresh_nonce: str) -> str:
        material = json.dumps({
            "q": normalize_text(user_text),
            "intent": intent,
            "refresh": refresh_nonce[-24:],
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, user_text: str, intent: Dict[str, Any], refresh_nonce: str) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
        key = self._key(user_text, intent, refresh_nonce)
        with self.lock:
            item = self.data.get(key)
            if not item:
                return None
            timestamp, products, debug = item
            if time.time() - timestamp > self.ttl:
                self.data.pop(key, None)
                return None
            return [dict(x) for x in products], dict(debug)

    def set(self, user_text: str, intent: Dict[str, Any], refresh_nonce: str, products: List[Dict[str, Any]], debug: Dict[str, Any]) -> None:
        key = self._key(user_text, intent, refresh_nonce)
        with self.lock:
            if len(self.data) >= self.max_items:
                oldest = min(self.data.items(), key=lambda kv: kv[1][0])[0]
                self.data.pop(oldest, None)
            self.data[key] = (time.time(), [dict(x) for x in products], dict(debug))


class ProductSearchEngine:
    def __init__(self, deps: SearchDependencies):
        self.deps = deps
        self.intent_engine = ProductIntentEngine()
        self.query_generator = QueryGenerator(self.intent_engine)
        self.web_search = WebSearchEngine(deps)
        self.fetcher = ProductPageFetcher()
        self.extractor = ProductExtractor()
        self.converter = CurrencyConverter()
        self.verifier = ProductVerifier()
        self.matcher = ProductMatcher()
        self.comparator = PriceComparator()
        self.ranker = RankingEngine()
        self.cache = SearchCache(deps.cache_ttl_seconds)
        self.debug_history: deque = deque(maxlen=50)
        self.lock = threading.RLock()

    def search(self, user_text: str, base_intent: Dict[str, Any], refresh_nonce: str = "", avoid_urls: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        started = time.time()
        enriched = self.intent_engine.enrich(user_text, base_intent)
        cached = self.cache.get(user_text, enriched, refresh_nonce)
        if cached:
            products, debug = cached
            self.debug_history.append(debug)
            return products

        queries = self.query_generator.generate(user_text, enriched, refresh_nonce)
        candidates, discovery_debug = self.web_search.discover(user_text, enriched, queries, avoid_urls or [])

        products: List[Dict[str, Any]] = []
        rejection_reasons: Dict[str, int] = {}
        partial_count = 0

        for candidate in candidates:
            page = self.fetcher.fetch(candidate["source_url"])
            if page.get("verification_status") != "verified_page":
                reason = page.get("rejection_reason", "broken_url")
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue

            product = self.extractor.extract(page, candidate, enriched)
            if product.get("verification_status") != "verified":
                reason = product.get("rejection_reason", "not_product_page")
                if reason == "no_price":
                    partial_count += 1
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue

            fx = self.converter.to_jod(float(product["price_original"]), product.get("currency_original", ""))
            if fx.get("price_jod") is None:
                rejection_reasons["no_reliable_jod_conversion"] = rejection_reasons.get("no_reliable_jod_conversion", 0) + 1
                continue

            product["price_jod"] = round(float(fx["price_jod"]), 2)
            product["exchange_rate"] = fx.get("rate")
            product["conversion_source"] = fx.get("source")
            product["conversion_timestamp"] = fx.get("timestamp")
            product["verified_at"] = time.time()
            product["search_rank"] = candidate.get("search_rank", 999999)
            product["source_url"] = candidate.get("source_url")
            product["verification_status"] = "verified"

            ok, reason = self.verifier.verify_match(product, user_text, enriched)
            if not ok:
                rejection_reasons[reason or "low_relevance"] = rejection_reasons.get(reason or "low_relevance", 0) + 1
                continue

            try:
                product = ProductNormalizer().normalize(product, enriched)
            except ValueError as exc:
                reason = str(exc)
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue
            product["tags"] = list(dict.fromkeys(enriched.get("product_terms", [])[:12]))
            product["source"] = "google_search"
            products.append(product)

        # Exact URL and cross-store duplicate products are merged before ranking.
        products = self.comparator.compare(products)
        products = self.matcher_filter(products, enriched, rejection_reasons)

        ranked = self.ranker.rank(products, user_text, enriched, self.deps.max_results)
        for item in ranked:
            item["id"] = self._stable_id(item.get("store_url"))
            item["image_url"] = item.get("image_url") or ""
            item["city"] = "الأردن"
            item["category"] = item.get("category") or (enriched.get("categories") or ["gifts"])[0]
            item["colors"] = item.get("colors") or []
            item["style"] = []
            item["sizes"] = item.get("sizes") or []
            item["size_system"] = (enriched.get("sizes") or {}).get("system") or ""
            item["store_url"] = item.get("store_url") or item.get("canonical_url")
            item.pop("canonical_url", None)
            # Payload must not expose internal debug-only fields.
            item.pop("search_rank", None)

        debug = {
            "timestamp": time.time(),
            "duration_seconds": round(time.time() - started, 3),
            "original_query": user_text,
            "parsed_intent": enriched,
            "generated_queries": queries,
            "number_of_candidates": len(candidates),
            "number_of_verified_pages": len(products),
            "partial_no_price": partial_count,
            "rejection_reasons": rejection_reasons,
            "duplicate_count": max(0, len(products) - len(ranked)),
            "final_results": [{"id": x.get("id"), "title": x.get("title"), "store": x.get("merchant_name"), "price_jod": x.get("price_jod")} for x in ranked],
            "search_duration": round(time.time() - started, 3),
            "discovery": discovery_debug,
        }
        self.debug_history.append(debug)
        self.cache.set(user_text, enriched, refresh_nonce, ranked, debug)
        return ranked

    def matcher_filter(self, products: List[Dict[str, Any]], intent: Dict[str, Any], rejection_reasons: Dict[str, int]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for product in products:
            ok, reason = self.matcher.hard_filter(product, intent)
            if not ok:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue
            out.append(product)
        return out

    def debug_snapshot(self) -> List[Dict[str, Any]]:
        return list(self.debug_history)

    @staticmethod
    def _stable_id(url: str) -> int:
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:12]
        return 100000000 + (int(digest, 16) % 89999999)
