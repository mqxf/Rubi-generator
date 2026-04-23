import json
import unittest
from pathlib import Path

from rubi_gto.annotator import RUBI_PATTERN, apply_glossary, strip_rubi, validate_annotation


class TechnicalTermTests(unittest.TestCase):
    def test_curated_technical_terms_have_valid_annotations(self) -> None:
        path = Path(__file__).resolve().parent.parent / "review" / "glossaries" / "technical_core.json"
        if not path.exists():
            self.skipTest("technical_core.json not present in this workspace")
        payload = json.loads(path.read_text(encoding="utf-8"))

        for term in payload["terms"]:
            with self.subTest(term=term["plain"]):
                self.assertEqual(strip_rubi(term["annotated"]), term["plain"])
                self.assertEqual(validate_annotation(term["plain"], term["annotated"]), [])

    def test_adjacent_complex_terms_without_kana_are_split(self) -> None:
        source = "高電圧機械"
        annotated, changed = apply_glossary(
            source,
            [
                {"plain": "高電圧", "annotated": "§^高電圧(こうでんあつ)"},
                {"plain": "機械", "annotated": "§^機械(きかい)"},
            ],
        )

        self.assertTrue(changed)
        self.assertEqual(annotated, "§^高電圧(こうでんあつ)§^機械(きかい)")
        self.assertEqual(len(RUBI_PATTERN.findall(annotated)), 2)
        self.assertEqual(validate_annotation(source, annotated), [])

    def test_phrase_with_multiple_complex_terms_validates(self) -> None:
        source = "高電圧機械と遠心分離機を電解槽へ送る"
        annotated = (
            "§^高電圧(こうでんあつ)§^機械(きかい)と"
            "§^遠心分離機(えんしんぶんりき)を"
            "§^電解槽(でんかいそう)へ§^送(おく)る"
        )

        self.assertEqual(strip_rubi(annotated), source)
        self.assertEqual(len(RUBI_PATTERN.findall(annotated)), 5)
        self.assertEqual(validate_annotation(source, annotated), [])
