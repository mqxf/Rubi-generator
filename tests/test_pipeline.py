import json
import tempfile
import unittest
from pathlib import Path

from rubi_gto.annotator import strip_rubi
from rubi_gto.pipeline import INGESTED_PATH, SOURCE_REPORT_PATH, build, ingest, report, run
from rubi_gto.sources import build_gto_workflow_manifest, build_instance_manifest


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class PipelineTests(unittest.TestCase):
    def test_ingest_can_filter_by_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_a = tmp_path / "fixtures_a"
            source_b = tmp_path / "fixtures_b"
            _write_json(source_a / "assets" / "minecraft" / "lang" / "ja_jp.json", {"menu.a": "設定"})
            _write_json(source_b / "assets" / "minecraft" / "lang" / "ja_jp.json", {"menu.b": "冒険"})
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "sources": [
                        {
                            "id": "source-a",
                            "type": "local_dir",
                            "path": str(source_a),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        },
                        {
                            "id": "source-b",
                            "type": "local_dir",
                            "path": str(source_b),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        },
                    ],
                },
            )

            summary = ingest(tmp_path / "manifest.json", tmp_path, source_ids=["source-b"])
            ingested = json.loads((tmp_path / INGESTED_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["selected_source_ids"], ["source-b"])
            self.assertEqual(summary["source_count"], 1)
            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(ingested[0]["key"], "menu.b")

    def test_ingest_can_rerun_failed_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_a = tmp_path / "fixtures_a"
            source_b = tmp_path / "fixtures_b"
            _write_json(source_a / "assets" / "minecraft" / "lang" / "ja_jp.json", {"menu.a": "設定"})
            _write_json(source_b / "assets" / "minecraft" / "lang" / "ja_jp.json", {"menu.b": "冒険"})
            _write_json(
                tmp_path / SOURCE_REPORT_PATH,
                {
                    "failed_source_ids": ["source-b"],
                },
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "sources": [
                        {
                            "id": "source-a",
                            "type": "local_dir",
                            "path": str(source_a),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        },
                        {
                            "id": "source-b",
                            "type": "local_dir",
                            "path": str(source_b),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        },
                    ],
                },
            )

            summary = ingest(tmp_path / "manifest.json", tmp_path, failed_only=True)
            ingested = json.loads((tmp_path / INGESTED_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["selected_source_ids"], ["source-b"])
            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(ingested[0]["key"], "menu.b")

    def test_ingest_preserves_previous_records_on_total_source_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / INGESTED_PATH,
                [
                    {
                        "namespace": "minecraft",
                        "key": "menu.singleplayer",
                        "source_text": "一人で遊ぶ",
                        "annotated_text": "一人で遊ぶ",
                        "source_origin": "cached",
                        "source_id": "cached",
                        "review_status": "pending",
                        "issues": [],
                        "notes": None,
                    }
                ],
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "sources": [
                        {
                            "id": "broken-source",
                            "type": "unsupported_source_type",
                        }
                    ],
                },
            )

            summary = ingest(tmp_path / "manifest.json", tmp_path)
            ingested = json.loads((tmp_path / INGESTED_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["record_count"], 0)
            self.assertTrue(summary["errors"])
            self.assertTrue(summary["preserved_previous_records"])
            self.assertEqual(len(ingested), 1)

    def test_pipeline_builds_resource_pack_from_local_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_root = tmp_path / "fixtures"
            _write_json(
                source_root / "assets" / "minecraft" / "lang" / "ja_jp.json",
                {
                    "menu.singleplayer": "一人で遊ぶ",
                    "menu.multiplayer": "マルチプレイ",
                },
            )

            manifest = {
                "pack": {"description": "test pack", "pack_format": 34},
                "sources": [
                    {
                        "id": "local-minecraft",
                        "type": "local_dir",
                        "path": str(source_root),
                        "include_globs": ["**/assets/*/lang/ja_jp.json"],
                    }
                ],
            }
            _write_json(tmp_path / "manifest.json", manifest)
            _write_json(
                tmp_path / "review" / "review_entries.json",
                {
                    "minecraft:menu.singleplayer": {
                        "approved": True,
                        "override_text": "§^一人(ひとり)で§^遊(あそ)ぶ",
                    }
                },
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})

            summary = run(tmp_path / "manifest.json", tmp_path, include_generated=False)

            output_path = tmp_path / "build" / "resourcepack" / "assets" / "minecraft" / "lang" / "ja_jp.json"
            built = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual(summary["ingest"]["record_count"], 2)
            self.assertEqual(summary["report"]["pending_count"], 1)
            self.assertEqual(built, {"menu.singleplayer": "§^一人(ひとり)で§^遊(あそ)ぶ"})

    def test_pipeline_can_include_generated_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_root = tmp_path / "fixtures"
            _write_json(
                source_root / "assets" / "gregtech" / "lang" / "ja_jp.json",
                {"machine.name": "高電圧機械"},
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "sources": [
                        {
                            "id": "local-gregtech",
                            "type": "local_dir",
                            "path": str(source_root),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        }
                    ],
                },
            )
            _write_json(
                tmp_path / "review" / "glossary.json",
                {
                    "terms": [
                        {"plain": "高電圧", "annotated": "§^高電圧(こうでんあつ)"},
                        {"plain": "機械", "annotated": "§^機械(きかい)"},
                    ]
                },
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})

            summary = run(tmp_path / "manifest.json", tmp_path, include_generated=True)

            output_path = tmp_path / "build" / "resourcepack" / "assets" / "gregtech" / "lang" / "ja_jp.json"
            built = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertTrue(summary["build"]["include_generated"])
            self.assertEqual(built["machine.name"], "§^高電圧(こうでんあつ)§^機械(きかい)")

    def test_pipeline_uses_suggestions_before_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_root = tmp_path / "fixtures"
            _write_json(
                source_root / "assets" / "gregtech" / "lang" / "ja_jp.json",
                {"machine.name": "遠心分離機"},
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "sources": [
                        {
                            "id": "local-gregtech",
                            "type": "local_dir",
                            "path": str(source_root),
                            "include_globs": ["**/assets/*/lang/ja_jp.json"],
                        }
                    ],
                },
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(
                tmp_path / "review" / "suggestions.json",
                {
                    "gregtech:machine.name": {
                        "annotated_text": "§^遠心分離機(えんしんぶんりき)",
                        "source": "llm",
                    }
                },
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})

            summary = run(tmp_path / "manifest.json", tmp_path, include_generated=True)
            annotated_records = json.loads((tmp_path / "build" / "annotated_records.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["annotate"]["status_counts"]["suggested"], 1)
            self.assertEqual(annotated_records[0]["annotated_text"], "§^遠心分離機(えんしんぶんりき)")
            self.assertEqual(annotated_records[0]["notes"], "suggestion:llm")

    def test_pipeline_builds_staged_instance_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "config" / "openloader" / "resources" / "quests" / "pack.mcmeta",
                {"id": "gto_quests", "pack": {"description": "Quest pack", "pack_format": 34}},
            )
            _write_json(
                tmp_path
                / "config"
                / "openloader"
                / "resources"
                / "quests"
                / "assets"
                / "gto"
                / "lang"
                / "ja_jp.json",
                {"gto.quest.title": "高電圧機械"},
            )
            guide_path = (
                tmp_path
                / "resourcepacks"
                / "guidepack"
                / "assets"
                / "ae2"
                / "ae2guide"
                / "_ja_jp"
                / "page.md"
            )
            guide_path.parent.mkdir(parents=True, exist_ok=True)
            guide_path.write_text("# 高電圧機械", encoding="utf-8")
            patchouli_root = tmp_path / "patchouli_books" / "testbook"
            patchouli_root.mkdir(parents=True, exist_ok=True)
            _write_json(
                patchouli_root / "book.json",
                {
                    "name": "高電圧機械",
                    "landing_text": "遠心分離機",
                },
            )
            _write_json(
                tmp_path / "review" / "glossary.json",
                {
                    "terms": [
                        {"plain": "高電圧", "annotated": "§^高電圧(こうでんあつ)"},
                        {"plain": "機械", "annotated": "§^機械(きかい)"},
                        {"plain": "遠心分離機", "annotated": "§^遠心分離機(えんしんぶんりき)"},
                    ]
                },
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})
            manifest = build_instance_manifest(
                tmp_path,
                pack_description="Instance pack",
                pack_format=34,
            )
            _write_json(tmp_path / "manifest.json", manifest)

            summary = run(tmp_path / "manifest.json", tmp_path, include_generated=True, include_pending=False)

            openloader_lang = json.loads(
                (
                    tmp_path
                    / "build"
                    / "staged"
                    / "config"
                    / "openloader"
                    / "resources"
                    / "gto_quests"
                    / "assets"
                    / "gto"
                    / "lang"
                    / "ja_jp.json"
                ).read_text(encoding="utf-8")
            )
            guide_text = (
                tmp_path
                / "build"
                / "staged"
                / "resourcepack"
                / "assets"
                / "ae2"
                / "ae2guide"
                / "_ja_jp"
                / "page.md"
            ).read_text(encoding="utf-8")
            patchouli_book = json.loads(
                (
                    tmp_path
                    / "build"
                    / "staged"
                    / "patchouli_books"
                    / "testbook"
                    / "book.json"
                ).read_text(encoding="utf-8")
            )

            self.assertEqual(summary["build"]["target_layout"], "instance")
            self.assertEqual(openloader_lang["gto.quest.title"], "§^高電圧(こうでんあつ)§^機械(きかい)")
            self.assertIn("§^高電圧(こうでんあつ)§^機械(きかい)", guide_text)
            self.assertEqual(patchouli_book["landing_text"], "§^遠心分離機(えんしんぶんりき)")

    def test_pipeline_skips_explicit_non_japanese_patchouli_and_guideme_locale_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            guide_path = (
                tmp_path
                / "resourcepacks"
                / "guidepack"
                / "assets"
                / "ae2"
                / "ae2guide"
                / "_zh_cn"
                / "page.md"
            )
            guide_path.parent.mkdir(parents=True, exist_ok=True)
            guide_path.write_text("# 高電圧機械", encoding="utf-8")
            patchouli_path = (
                tmp_path
                / "resourcepacks"
                / "patchouli_pack"
                / "assets"
                / "apotheosis"
                / "patchouli_books"
                / "apoth_chronicle"
                / "zh_cn"
                / "categories"
                / "enchanting"
                / "enchantments.json"
            )
            patchouli_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(
                patchouli_path,
                {
                    "name": "附魔",
                    "description": "高電圧機械",
                },
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(tmp_path / "review" / "review_entries.json", {})
            manifest = build_instance_manifest(
                tmp_path,
                pack_description="Instance pack",
                pack_format=34,
            )
            _write_json(tmp_path / "manifest.json", manifest)

            summary = run(tmp_path / "manifest.json", tmp_path, include_generated=True, include_pending=False)
            annotated = json.loads((tmp_path / "build" / "annotated_records.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["ingest"]["record_count"], 0)
            self.assertEqual(annotated, [])

    def test_full_pack_flattens_portable_openloader_content_into_pack_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "config" / "openloader" / "resources" / "quests" / "pack.mcmeta",
                {"id": "gto_quests", "pack": {"description": "Quest pack", "pack_format": 34}},
            )
            guide_path = (
                tmp_path
                / "config"
                / "openloader"
                / "resources"
                / "quests"
                / "assets"
                / "ae2"
                / "ae2guide"
                / "_ja_jp"
                / "page.md"
            )
            guide_path.parent.mkdir(parents=True, exist_ok=True)
            guide_path.write_text("# 高電圧機械", encoding="utf-8")
            _write_json(
                tmp_path / "review" / "glossary.json",
                {
                    "terms": [
                        {"plain": "高電圧", "annotated": "§^高電圧(こうでんあつ)"},
                        {"plain": "機械", "annotated": "§^機械(きかい)"},
                    ]
                },
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})
            manifest = build_instance_manifest(
                tmp_path,
                pack_description="Instance pack",
                pack_format=34,
            )
            _write_json(tmp_path / "manifest.json", manifest)

            summary = run(
                tmp_path / "manifest.json",
                tmp_path,
                include_generated=True,
                include_pending=False,
                export_mode="full-pack",
                export_locale="ja_rubi",
            )

            flattened_guide = (
                tmp_path / "build" / "resourcepack" / "assets" / "ae2" / "ae2guide" / "_ja_jp" / "page.md"
            )
            nested_guide = (
                tmp_path
                / "build"
                / "resourcepack"
                / "config"
                / "openloader"
                / "resources"
                / "gto_quests"
                / "assets"
                / "ae2"
                / "ae2guide"
                / "_ja_jp"
                / "page.md"
            )

            self.assertEqual(summary["build"]["export_mode"], "full-pack")
            self.assertTrue(flattened_guide.exists())
            self.assertFalse(nested_guide.exists())
            self.assertIn("§^高電圧(こうでんあつ)§^機械(きかい)", flattened_guide.read_text(encoding="utf-8"))

    def test_build_uses_manifest_default_for_generated_entries(self) -> None:
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
                    "build": {"include_generated_by_default": True},
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
            _write_json(
                tmp_path / "review" / "glossary.json",
                {"terms": [{"plain": "一人", "annotated": "§^一人(ひとり)"}, {"plain": "遊", "annotated": "§^遊(あそ)"}]},
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})

            run(tmp_path / "manifest.json", tmp_path)
            built = json.loads(
                (tmp_path / "build" / "resourcepack" / "assets" / "minecraft" / "lang" / "ja_jp.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(built["menu.singleplayer"], "§^一人(ひとり)で§^遊(あそ)ぶ")

    def test_build_can_override_manifest_default_to_approved_only(self) -> None:
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
                    "build": {"include_generated_by_default": True},
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
            _write_json(
                tmp_path / "review" / "glossary.json",
                {"terms": [{"plain": "一人", "annotated": "§^一人(ひとり)"}, {"plain": "遊", "annotated": "§^遊(あそ)"}]},
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})

            run(tmp_path / "manifest.json", tmp_path)
            summary = build(tmp_path / "manifest.json", tmp_path, include_generated=False)
            self.assertFalse(summary["include_generated"])
            self.assertFalse(
                (tmp_path / "build" / "resourcepack" / "assets" / "minecraft" / "lang" / "ja_jp.json").exists()
            )

    def test_build_can_include_pending_entries_from_manifest_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_root = tmp_path / "fixtures"
            _write_json(
                source_root / "assets" / "minecraft" / "lang" / "ja_jp.json",
                {"accessibility.button": "設定"},
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "build": {
                        "include_generated_by_default": True,
                        "include_pending_by_default": True,
                    },
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

            summary = run(tmp_path / "manifest.json", tmp_path)
            built = json.loads(
                (tmp_path / "build" / "resourcepack" / "assets" / "minecraft" / "lang" / "ja_jp.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertTrue(summary["build"]["include_pending"])
            self.assertEqual(built["accessibility.button"], "§^設定(せってい)")

    def test_pipeline_auto_annotates_with_installed_analyzers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_root = tmp_path / "fixtures"
            _write_json(
                source_root / "assets" / "minecraft" / "lang" / "ja_jp.json",
                {"adv.title": "冒険の時間"},
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "build": {
                        "include_generated_by_default": True,
                        "include_pending_by_default": True,
                    },
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

            summary = run(tmp_path / "manifest.json", tmp_path)
            built = json.loads(
                (tmp_path / "build" / "resourcepack" / "assets" / "minecraft" / "lang" / "ja_jp.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertIn(summary["annotate"]["status_counts"], ({"generated": 1}, {"pending": 1}))
            self.assertEqual(built["adv.title"], "§^冒険(ぼうけん)の§^時間(じかん)")

    def test_report_includes_review_category_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(tmp_path / "build" / "annotated_records.json", [])
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 2,
                    "candidates": {
                        "minecraft:a": {
                            "category": "compound_or_lexical_conflict",
                            "source_text": "村人",
                            "current_text": "村人",
                            "reason": "analyzer_conflict",
                            "options": [],
                            "source_origin": "test",
                        },
                        "minecraft:b": {
                            "category": "unresolved_counter_or_numeric_conflict",
                            "source_text": "2匹",
                            "current_text": "2匹",
                            "reason": "analyzer_conflict",
                            "options": [],
                            "source_origin": "test",
                        },
                    },
                },
            )
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates_by_category.json",
                {
                    "category_counts": {
                        "compound_or_lexical_conflict": 1,
                        "unresolved_counter_or_numeric_conflict": 1,
                    },
                    "categories": {
                        "compound_or_lexical_conflict": {"minecraft:a": {}},
                        "unresolved_counter_or_numeric_conflict": {"minecraft:b": {}},
                    },
                },
            )

            summary = report(tmp_path)

            self.assertEqual(summary["review_candidate_count"], 2)
            self.assertEqual(summary["review_category_counts"]["compound_or_lexical_conflict"], 1)
            self.assertEqual(summary["review_category_counts"]["unresolved_counter_or_numeric_conflict"], 1)

    def test_legacy_ftbquests_builds_rewritten_snbt_and_lang_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            quest_root = tmp_path / "config" / "ftbquests" / "quests"
            chapter_path = quest_root / "chapters" / "test.snbt"
            chapter_path.parent.mkdir(parents=True, exist_ok=True)
            chapter_path.write_text(
                "\n".join(
                    [
                        "{",
                        '\tquests: [',
                        "\t\t{",
                        '\t\t\tid: "quest_a"',
                        '\t\t\ttitle: "高電圧機械"',
                        '\t\t\tsubtitle: "遠心分離機"',
                        '\t\t\tdescription: [',
                        '\t\t\t\t"高電圧機械"',
                        '\t\t\t\t"[\'\', \'遠心分離機\']"',
                        "\t\t\t]",
                        "\t\t}",
                        "\t]",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(
                tmp_path / "review" / "suggestions.json",
                {
                    "gto:chapters/test.snbt::gto.test.quests.quest_a.title": {
                        "annotated_text": "§^高電圧(こうでんあつ)§^機械(きかい)"
                    },
                    "gto:chapters/test.snbt::gto.test.quests.quest_a.subtitle": {
                        "annotated_text": "§^遠心分離機(えんしんぶんりき)"
                    },
                    "gto:chapters/test.snbt::gto.test.quests.quest_a.description0": {
                        "annotated_text": "§^高電圧(こうでんあつ)§^機械(きかい)"
                    },
                    "gto:chapters/test.snbt::gto.test.quests.quest_a.description1.rich_text0": {
                        "annotated_text": "§^遠心分離機(えんしんぶんりき)"
                    },
                },
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "build": {
                        "include_generated_by_default": True,
                        "include_pending_by_default": False,
                        "target_layout": "instance",
                    },
                    "sources": [
                        {
                            "id": "ftbquests:quests",
                            "type": "ftbquests_legacy_inline",
                            "path": str(quest_root),
                            "target_namespace": "gto",
                            "locale": "ja_jp",
                            "output_kind": "instance",
                            "output_root": "config/ftbquests/quests",
                            "rewritten_output_root": "config/ftbquests/quests",
                            "lang_output_root": "config/openloader/resources/gto_quests",
                            "full_pack_rewrite_root": "assets/ftbquests",
                            "portability": "portable",
                        }
                    ],
                },
            )

            summary = run(tmp_path / "manifest.json", tmp_path, include_generated=True, include_pending=False)

            lang_payload = json.loads(
                (
                    tmp_path
                    / "build"
                    / "staged"
                    / "config"
                    / "openloader"
                    / "resources"
                    / "gto_quests"
                    / "assets"
                    / "gto"
                    / "lang"
                    / "ja_jp.json"
                ).read_text(encoding="utf-8")
            )
            rewritten_snbt = (
                tmp_path / "build" / "staged" / "config" / "ftbquests" / "quests" / "chapters" / "test.snbt"
            ).read_text(encoding="utf-8")

            self.assertEqual(summary["build"]["export_mode"], "overwrite")
            self.assertEqual(lang_payload["gto.test.quests.quest_a.title"], "§^高電圧(こうでんあつ)§^機械(きかい)")
            self.assertEqual(
                lang_payload["gto.test.quests.quest_a.description1.rich_text0"],
                "§^遠心分離機(えんしんぶんりき)",
            )
            self.assertIn('{gto.test.quests.quest_a.title}', rewritten_snbt)

            full_pack = build(
                tmp_path / "manifest.json",
                tmp_path,
                include_generated=True,
                include_pending=False,
                export_mode="full-pack",
                export_locale="ja_rubi",
            )
            full_pack_lang = json.loads(
                (tmp_path / "build" / "resourcepack" / "assets" / "gto" / "lang" / "ja_rubi.json").read_text(
                    encoding="utf-8"
                )
            )
            full_pack_snbt = (
                tmp_path / "build" / "resourcepack" / "assets" / "ftbquests" / "chapters" / "test.snbt"
            ).read_text(encoding="utf-8")
            pack_meta = json.loads((tmp_path / "build" / "resourcepack" / "pack.mcmeta").read_text(encoding="utf-8"))

            self.assertEqual(full_pack["export_locale"], "ja_rubi")
            self.assertIn("ja_rubi", pack_meta["language"])
            self.assertEqual(full_pack_lang["gto.test.quests.quest_a.subtitle"], "§^遠心分離機(えんしんぶんりき)")
            self.assertIn('{gto.test.quests.quest_a.description0}', full_pack_snbt)

    def test_legacy_ftbquests_preserves_unescaped_source_text_in_lang_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            quest_root = tmp_path / "config" / "ftbquests" / "quests"
            chapter_path = quest_root / "chapters" / "escaped.snbt"
            chapter_path.parent.mkdir(parents=True, exist_ok=True)
            chapter_path.write_text(
                "\n".join(
                    [
                        "{",
                        '\tquests: [',
                        "\t\t{",
                        '\t\t\tid: "quest_a"',
                        '\t\t\ttitle: \'Quote "A" %s\'',
                        "\t\t}",
                        "\t]",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(tmp_path / "review" / "review_entries.json", {})
            _write_json(
                tmp_path / "review" / "suggestions.json",
                {
                    'gto:chapters/escaped.snbt::gto.escaped.quests.quest_a.title': {
                        "annotated_text": 'Quote "A" %s'
                    }
                },
            )
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "build": {
                        "include_generated_by_default": True,
                        "include_pending_by_default": False,
                        "target_layout": "instance",
                    },
                    "sources": [
                        {
                            "id": "ftbquests:quests",
                            "type": "ftbquests_legacy_inline",
                            "path": str(quest_root),
                            "target_namespace": "gto",
                            "locale": "ja_jp",
                            "output_kind": "instance",
                            "output_root": "config/ftbquests/quests",
                            "rewritten_output_root": "config/ftbquests/quests",
                            "lang_output_root": "config/openloader/resources/gto_quests",
                            "full_pack_rewrite_root": "assets/ftbquests",
                            "portability": "portable",
                        }
                    ],
                },
            )

            run(tmp_path / "manifest.json", tmp_path, include_generated=True, include_pending=True)

            annotated = json.loads((tmp_path / "build" / "annotated_records.json").read_text(encoding="utf-8"))
            self.assertEqual(annotated[0]["source_text"], 'Quote "A" %s')
            lang_payload = json.loads(
                (
                    tmp_path
                    / "build"
                    / "staged"
                    / "config"
                    / "openloader"
                    / "resources"
                    / "gto_quests"
                    / "assets"
                    / "gto"
                    / "lang"
                    / "ja_jp.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(lang_payload["gto.escaped.quests.quest_a.title"], 'Quote "A" %s')

    def test_gto_workflow_merge_prefers_instance_on_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            instance_root = root / "GregTech-Odyssey"
            (instance_root / ".git").mkdir(parents=True, exist_ok=True)
            _write_json(
                instance_root / "resourcepacks" / "instancepack" / "pack.mcmeta",
                {"pack": {"description": "Instance pack", "pack_format": 34}},
            )
            _write_json(
                instance_root / "resourcepacks" / "instancepack" / "assets" / "gto" / "lang" / "ja_jp.json",
                {"dup.key": "インスタンス", "instance.only": "現地"},
            )

            translations_repo = root / "GTO-Translations"
            (translations_repo / ".git").mkdir(parents=True, exist_ok=True)
            _write_json(
                translations_repo / "ja_jp" / "resourcepacks" / "gto-lang-ja_jp" / "pack.mcmeta",
                {"pack": {"description": "Repo pack", "pack_format": 34}},
            )
            _write_json(
                translations_repo
                / "ja_jp"
                / "resourcepacks"
                / "gto-lang-ja_jp"
                / "assets"
                / "gto"
                / "lang"
                / "ja_jp.json",
                {"dup.key": "リポジトリ", "repo.only": "翻訳"},
            )

            _write_json(root / "review" / "glossary.json", {"terms": []})
            _write_json(root / "review" / "review_entries.json", {})
            manifest = build_gto_workflow_manifest(
                instance_root,
                repo_root=root,
                pack_description="Workflow pack",
                pack_format=34,
            )
            _write_json(root / "manifest.json", manifest)

            summary = run(root / "manifest.json", root)
            merged = json.loads(
                (root / "build" / "staged" / "resourcepack" / "assets" / "gto" / "lang" / "ja_jp.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(summary["build"]["target_layout"], "instance")
            self.assertEqual(strip_rubi(merged["dup.key"]), "インスタンス")
            self.assertEqual(strip_rubi(merged["repo.only"]), "翻訳")
            self.assertEqual(strip_rubi(merged["instance.only"]), "現地")

    def test_ftbquests_locale_snbt_overwrite_and_full_pack_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            quest_root = tmp_path / "config" / "ftbquests" / "quests"
            locale_path = quest_root / "lang" / "ja_jp.snbt"
            locale_path.parent.mkdir(parents=True, exist_ok=True)
            locale_path.write_text(
                "\n".join(
                    [
                        "{",
                        '\tchapter: {',
                        '\t\tquest: "高電圧機械"',
                        "\t}",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(
                tmp_path / "review" / "suggestions.json",
                {
                    "ftbquests:lang/ja_jp.snbt::chapter.quest": {
                        "annotated_text": "§^高電圧(こうでんあつ)§^機械(きかい)"
                    }
                },
            )
            _write_json(tmp_path / "review" / "review_entries.json", {})
            _write_json(
                tmp_path / "manifest.json",
                {
                    "pack": {"description": "test pack", "pack_format": 34},
                    "build": {
                        "include_generated_by_default": True,
                        "include_pending_by_default": False,
                        "target_layout": "instance",
                    },
                    "sources": [
                        {
                            "id": "ftbquests:lang",
                            "type": "ftbquests_locale_snbt",
                            "path": str(quest_root),
                            "locale": "ja_jp",
                            "target_locale": "ja_rubi",
                            "output_kind": "instance",
                            "output_root": "config/ftbquests/quests",
                            "portability": "overwrite_only",
                        }
                    ],
                },
            )

            run(tmp_path / "manifest.json", tmp_path, include_generated=True, include_pending=False)
            staged_snbt = (
                tmp_path / "build" / "staged" / "config" / "ftbquests" / "quests" / "lang" / "ja_rubi.snbt"
            ).read_text(encoding="utf-8")
            self.assertIn("§^高電圧(こうでんあつ)§^機械(きかい)", staged_snbt)

            with self.assertRaisesRegex(ValueError, "overwrite-only sources"):
                build(
                    tmp_path / "manifest.json",
                    tmp_path,
                    include_generated=True,
                    include_pending=False,
                    export_mode="full-pack",
                    export_locale="ja_rubi",
                )
