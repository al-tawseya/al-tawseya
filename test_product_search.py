import unittest

from product_search import (
    ProductIntentEngine,
    QueryGenerator,
    DuplicateDetector,
    ProductMatcher,
    RankingEngine,
    normalize_text,
)


class ProductSearchUnitTests(unittest.TestCase):
    def setUp(self):
        self.intent_engine = ProductIntentEngine()
        self.matcher = ProductMatcher()
        self.ranker = RankingEngine()

    def test_arabic_normalization(self):
        self.assertEqual(normalize_text("سِتْيّانَة"), "ستيا نه".replace(" ", "") if False else "ستيانه")
        self.assertEqual(normalize_text("بنطــال"), "بنطال")

    def test_lingerie_synonyms(self):
        intent = self.intent_engine.enrich("بدي ستيّانة مريحة بدون سلك مقاس 36C", {})
        self.assertIn("lingerie", intent["categories"])
        self.assertIn("wireless", intent["required_traits"])
        self.assertEqual(intent["sizes"]["system"], "bra")
        self.assertEqual(intent["sizes"]["value"], "36C")

    def test_pants_synonyms(self):
        intent = self.intent_engine.enrich("بدي بنطلون اسود واسع للدوام", {})
        self.assertIn("clothes", intent["categories"])
        self.assertIn("wide", intent["required_traits"])
        self.assertIn("workwear", intent["required_traits"])
        self.assertIn("بنطال", intent["synonyms"])
        self.assertIn("pants", intent["synonyms"])

    def test_query_generator(self):
        intent = self.intent_engine.enrich("black wide leg pants under 20 JOD Jordan", {})
        queries = QueryGenerator(self.intent_engine).generate("black wide leg pants under 20 JOD Jordan", intent)
        self.assertGreaterEqual(len(queries), 6)
        self.assertTrue(any("site:.jo" in q for q in queries))
        self.assertTrue(any("JOD" in q or "Jordan" in q for q in queries))

    def test_hard_budget_filter(self):
        intent = {"budget": {"amount": 15, "kind": "hard_max"}, "sizes": {}, "required_traits": []}
        product = {"price_jod": 20, "title": "Black Pants", "description": "", "colors": [], "sizes": []}
        ok, reason = self.matcher.hard_filter(product, intent)
        self.assertFalse(ok)
        self.assertEqual(reason, "budget_exceeded")

    def test_hard_size_filter(self):
        intent = {"budget": {"amount": None, "kind": "none"}, "sizes": {"system": "bra", "value": "36C"}, "required_traits": []}
        product = {"price_jod": 12, "title": "Wireless Bra", "description": "wireless", "colors": [], "sizes": ["34B"]}
        ok, reason = self.matcher.hard_filter(product, intent)
        self.assertFalse(ok)
        self.assertEqual(reason, "wrong_size")

    def test_duplicate_detector_same_product(self):
        detector = DuplicateDetector()
        a = {"canonical_url": "https://a.example/p/1", "brand": "ACME", "sku": "X1", "title": "Black Wide Leg Pants", "image_url": ""}
        b = {"canonical_url": "https://b.example/product/x", "brand": "ACME", "sku": "X1", "title": "Black Wide-Leg Pants", "image_url": ""}
        self.assertTrue(detector.are_same(a, b))

    def test_ranking_prefers_semantic_match(self):
        intent = {
            "categories": ["clothes"],
            "product_terms": ["بنطال", "pants"],
            "synonyms": ["بنطلون", "trousers", "pants"],
            "colors": ["black"],
            "sizes": {},
            "budget": {"amount": None, "kind": "none"},
            "required_traits": ["wide"],
        }
        products = [
            {
                "id": 1, "title": "Black Wide Leg Pants", "description": "wide leg office trousers",
                "category": "clothes", "tags": ["pants"], "colors": ["black"], "sizes": [],
                "price_jod": 20, "store_url": "https://store.jo/p1", "normalized_domain": "store.jo",
                "verification_status": "verified", "price_options": 1,
            },
            {
                "id": 2, "title": "Red Gift Box", "description": "gift box",
                "category": "gifts", "tags": ["gift"], "colors": ["red"], "sizes": [],
                "price_jod": 5, "store_url": "https://gift.jo/p2", "normalized_domain": "gift.jo",
                "verification_status": "verified", "price_options": 1,
            },
        ]
        ranked = self.ranker.rank(products, "بدي بنطال اسود واسع", intent, 8)
        self.assertEqual(ranked[0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
