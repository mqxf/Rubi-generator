import unittest

from rubi_gto.annotator import apply_glossary, strip_rubi, validate_annotation


class AnnotatorTests(unittest.TestCase):
    def test_strip_rubi_restores_plain_text(self) -> None:
        self.assertEqual(strip_rubi("§^食べ(たべ)る"), "食べる")
    
    def test_multi_reading_trailing_kanji(self) -> None:
        self.assertEqual(strip_rubi("§^食(く)う"), "食う")

    def test_validate_accepts_split_annotations(self) -> None:
        issues = validate_annotation("日本語の本", "§^日本語(にほんご)の§^本(ほん)")
        self.assertEqual(issues, [])

    def test_validate_rejects_plain_text_mismatch(self) -> None:
        issues = validate_annotation("一人で遊ぶ", "§^一人(ひとり)で§^遊(あそ)")
        self.assertIn("plain_text_mismatch", issues)

    def test_glossary_prefers_longer_terms_first(self) -> None:
        text, changed = apply_glossary(
            "高電圧機械",
            [
                {"plain": "電圧", "annotated": "§^電圧(でんあつ)"},
                {"plain": "高電圧", "annotated": "§^高電圧(こうでんあつ)"},
            ],
        )
        self.assertTrue(changed)
        self.assertEqual(text, "§^高電圧(こうでんあつ)機械")
