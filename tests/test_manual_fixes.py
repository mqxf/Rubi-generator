import json
import tempfile
import unittest
from pathlib import Path

from rubi_gto.manual_fixes import (
    GENERATED_MANUAL_FIX_CANDIDATES_PATH,
    GENERATED_MANUAL_FIX_OVERRIDES_PATH,
    GENERATED_MANUAL_FIX_SUGGESTIONS_PATH,
    apply_manual_fix_overrides,
    export_manual_fix_candidates,
)
from rubi_gto.pipeline import annotate, ingest


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ManualFixTests(unittest.TestCase):
    def test_export_manual_fix_candidates_includes_unresolved_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 3,
                    "candidates": {
                        "minecraft:a": {
                            "id": "minecraft:a",
                            "key": "a",
                            "source_text": "村人",
                            "current_text": "村人",
                            "category": "compound_or_lexical_conflict",
                            "options": [
                                {"source": "fugashi+unidic", "annotated_text": "§^村(そん)§^人(じん)"},
                                {"source": "sudachi-full", "annotated_text": "§^村人(むらびと)"},
                            ],
                        },
                        "minecraft:b": {
                            "id": "minecraft:b",
                            "key": "b",
                            "source_text": "取引",
                            "current_text": "取引",
                            "category": "reading_only_conflict",
                            "options": [
                                {"source": "fugashi+unidic", "annotated_text": "§^取引(とりひき)"},
                                {"source": "sudachi-full", "annotated_text": "§^取引(とりひき)"},
                            ],
                        },
                        "minecraft:c": {
                            "id": "minecraft:c",
                            "key": "c",
                            "source_text": "高度",
                            "current_text": "高度",
                            "category": "other",
                            "options": [
                                {"source": "fugashi+unidic", "annotated_text": "§^高度(こうど)"},
                                {"source": "sudachi-full", "annotated_text": "§^高度(こうど)"},
                            ],
                        },
                    },
                },
            )
            _write_json(
                tmp_path / "review" / "generated" / "llm_review_results.json",
                {
                    "results": {
                        "minecraft:a": {"status": "error", "error": "invalid_annotation"},
                        "minecraft:b": {"status": "suggested"},
                    }
                },
            )

            summary = export_manual_fix_candidates(tmp_path)
            candidates = json.loads((tmp_path / GENERATED_MANUAL_FIX_CANDIDATES_PATH).read_text(encoding="utf-8"))
            overrides = json.loads((tmp_path / GENERATED_MANUAL_FIX_OVERRIDES_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["entry_count"], 2)
            self.assertEqual(sorted(candidates["entries"]), ["minecraft:a", "minecraft:c"])
            self.assertEqual(candidates["entries"]["minecraft:a"]["fugashi_version"], "§^村(そん)§^人(じん)")
            self.assertEqual(candidates["entries"]["minecraft:a"]["sudachi_version"], "§^村人(むらびと)")
            self.assertEqual(candidates["entries"]["minecraft:c"]["llm_status"], "unreviewed")
            self.assertEqual(overrides, {"minecraft:a": "", "minecraft:c": ""})

    def test_apply_manual_fix_overrides_rebuilds_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_root = tmp_path / "fixtures"
            _write_json(
                source_root / "assets" / "minecraft" / "lang" / "ja_jp.json",
                {"menu.singleplayer": "一人で遊ぶ"},
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "build": {"include_generated_by_default": True, "include_pending_by_default": True},
                    "sources": [
                        {
                            "id": "local-minecraft",
                            "type": "local_dir",
                            "path": str(source_root),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        }
                    ],
                },
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(tmp_path / "review" / "review_entries.json", {})

            ingest(tmp_path / "manifest.json", tmp_path)
            annotate(tmp_path)
            _write_json(
                tmp_path / GENERATED_MANUAL_FIX_OVERRIDES_PATH,
                {"minecraft:menu.singleplayer": "§^一人(ひとり)で§^遊(あそ)ぶ"},
            )

            summary = apply_manual_fix_overrides(
                tmp_path,
                manifest_path=tmp_path / "manifest.json",
                export_mode="full-pack",
                export_locale="ja_rubi",
            )

            suggestions = json.loads((tmp_path / GENERATED_MANUAL_FIX_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            built = json.loads(
                (tmp_path / "build" / "resourcepack" / "assets" / "minecraft" / "lang" / "ja_rubi.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(summary["applied_count"], 1)
            self.assertEqual(suggestions["minecraft:menu.singleplayer"]["source"], "manual-fix")
            self.assertEqual(built["menu.singleplayer"], "§^一人(ひとり)で§^遊(あそ)ぶ")

