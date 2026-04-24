import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from rubi_gto.models import SourceSpec
from rubi_gto.sources import (
    build_gto_workflow_manifest,
    build_instance_content_report,
    build_instance_manifest,
    build_local_manifest,
    build_mod_archive_manifest,
    build_packwiz_translation_report,
    discover_local_sources,
    discover_mod_archives,
    ingest_sources,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_archive(
    path: Path,
    *,
    mods_toml: str | None = None,
    files: dict[str, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        if mods_toml is not None:
            archive.writestr("META-INF/mods.toml", mods_toml)
        for name, content in sorted((files or {}).items()):
            archive.writestr(name, content)


class DiscoveryTests(unittest.TestCase):
    def test_discovers_local_repos_with_japanese_lang_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root / "GregTech-Modern" / "src" / "main" / "resources" / "assets" / "gregtech" / "lang" / "ja_jp.json",
                {"machine.name": "高電圧機械"},
            )
            _write_json(
                root / "UnrelatedRepo" / "README.json",
                {"ignored": True},
            )

            discovered = discover_local_sources(root)

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["id"], "GregTech-Modern")
            self.assertIn("src/main/resources/assets/gregtech/lang/ja_jp.json", discovered[0]["detected_files"])
            self.assertEqual(discovered[0]["detected_namespaces"], ["gregtech"])

    def test_builds_packwiz_translation_report_with_local_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_root = root / "GregTech-Odyssey"
            mods_dir = pack_root / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)
            (mods_dir / "applied-energistics-2.pw.toml").write_text(
                "\n".join(
                    [
                        'name = "Applied Energistics 2"',
                        'filename = "ae2-forge.jar"',
                        'side = "both"',
                        "",
                        "[update.curseforge]",
                        "project-id = 223794",
                        "file-id = 123456",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _write_json(
                root / "Applied-Energistics-2-gto" / "src" / "main" / "resources" / "assets" / "ae2" / "lang" / "ja_jp.json",
                {"gui.title": "ネットワーク"},
            )

            report = build_packwiz_translation_report(pack_root, root)

            self.assertEqual(report["mod_count"], 1)
            self.assertEqual(report["mods_with_likely_local_translation_repo"], 1)
            self.assertEqual(report["mods_without_likely_local_translation_repo"], 0)
            self.assertEqual(report["mods"][0]["update"]["source"], "curseforge")
            self.assertEqual(report["mods"][0]["update"]["project_id"], 223794)
            self.assertEqual(report["mods"][0]["likely_local_translation_repo_matches"][0]["id"], "Applied-Energistics-2-gto")

    def test_discovers_nested_git_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_root = root / "nested" / "upstream" / "ExampleMod"
            (repo_root / ".git").mkdir(parents=True, exist_ok=True)
            _write_json(
                repo_root / "src" / "main" / "resources" / "assets" / "examplemod" / "lang" / "ja_jp.json",
                {"gui.title": "例"},
            )

            discovered = discover_local_sources(root)

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["id"], "ExampleMod")
            self.assertEqual(discovered[0]["detected_namespaces"], ["examplemod"])
            self.assertEqual(discovered[0]["detected_files"], ["src/main/resources/assets/examplemod/lang/ja_jp.json"])

    def test_builds_local_manifest_with_vanilla_and_build_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root / "GregTech-Modern" / "src" / "main" / "resources" / "assets" / "gtceu" / "lang" / "ja_jp.json",
                {"machine.name": "高電圧機械"},
            )

            manifest = build_local_manifest(
                root,
                pack_description="Test pack",
                pack_format=34,
                include_vanilla=True,
            )

            self.assertEqual(manifest["pack"]["description"], "Test pack")
            self.assertEqual(manifest["build"]["include_generated_by_default"], True)
            self.assertEqual(manifest["build"]["include_pending_by_default"], True)
            self.assertEqual(manifest["sources"][0]["type"], "minecraft_assets")
            self.assertEqual(manifest["sources"][1]["id"], "GregTech-Modern")

    def test_packwiz_report_includes_search_terms_and_stubs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_root = root / "GregTech-Odyssey"
            mods_dir = pack_root / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)
            (mods_dir / "ars-nouveau.pw.toml").write_text(
                "\n".join(
                    [
                        'name = "Ars Nouveau"',
                        'filename = "ars_nouveau-1.20.1-4.12.7-all.jar"',
                        'side = "both"',
                        "",
                        "[update.curseforge]",
                        "project-id = 401955",
                        "file-id = 6688854",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            report = build_packwiz_translation_report(pack_root, root)

            candidate = report["upstream_lookup_candidates"][0]
            self.assertEqual(candidate["search_terms"], ["Ars Nouveau", "ars-nouveau", "ars_nouveau-1.20.1-4.12.7-all"])
            self.assertEqual(candidate["source_stub_examples"]["local_dir"]["type"], "local_dir")
            self.assertEqual(candidate["source_stub_examples"]["github_repo_archive"]["type"], "github_repo_archive")

    def test_discovers_mod_archives_with_mod_id_and_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "mods"
            _write_archive(
                mods_dir / "ExampleMod-1.0.0.jar",
                mods_toml="\n".join(
                    [
                        '[[mods]]',
                        'modId = "examplemod"',
                        'displayName = "Example Mod"',
                        "",
                    ]
                ),
                files={
                    "assets/examplemod/lang/ja_jp.json": json.dumps({"gui.title": "例"}),
                },
            )

            discovered = discover_mod_archives(mods_dir)

            self.assertEqual(discovered["archive_count"], 1)
            self.assertEqual(discovered["detected_archive_count"], 1)
            self.assertEqual(discovered["ja_source_count"], 1)
            self.assertEqual(discovered["detected_namespaces"], ["examplemod"])
            self.assertEqual(discovered["entries"][0]["id"], "examplemod")
            self.assertEqual(discovered["sources"][0]["display_names"], ["Example Mod"])

    def test_builds_mod_archive_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "mods"
            _write_archive(
                mods_dir / "ExampleMod-1.0.0.jar",
                mods_toml='[[mods]]\nmodId = "examplemod"\n',
                files={"assets/examplemod/lang/ja_jp.json": json.dumps({"gui.title": "例"})},
            )

            manifest = build_mod_archive_manifest(
                mods_dir,
                pack_description="Archive pack",
                pack_format=34,
                include_vanilla=True,
            )

            self.assertEqual(manifest["sources"][0]["type"], "minecraft_assets")
            self.assertEqual(manifest["sources"][1]["type"], "local_archive")
            self.assertEqual(manifest["discovery"]["ja_source_count"], 1)

    def test_ingests_local_mod_archives_with_archive_mod_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "mods"
            _write_archive(
                mods_dir / "ExampleMod-1.0.0.jar",
                mods_toml='[[mods]]\nmodId = "examplemod"\n',
                files={"assets/examplemod/lang/ja_jp.json": json.dumps({"gui.title": "例"})},
            )

            source = SourceSpec(
                id="mods-folder",
                type="local_mod_archives",
                path=str(mods_dir),
                include_globs=["assets/*/lang/ja_jp.json"],
            )
            records, errors = ingest_sources([source])

            self.assertEqual(errors, [])
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].namespace, "examplemod")
            self.assertEqual(records[0].source_id, "examplemod")

    def test_builds_instance_content_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            instance_root = Path(temp_dir)
            mods_dir = instance_root / "mods"
            _write_archive(
                mods_dir / "ExampleMod-1.0.0.jar",
                mods_toml='[[mods]]\nmodId = "examplemod"\n',
                files={"assets/examplemod/lang/ja_jp.json": json.dumps({"gui.title": "例"})},
            )
            _write_json(
                instance_root / "config" / "openloader" / "resources" / "quests" / "pack.mcmeta",
                {"id": "gto_quests", "pack": {"description": "Quest pack", "pack_format": 34}},
            )
            _write_json(
                instance_root
                / "config"
                / "openloader"
                / "resources"
                / "quests"
                / "assets"
                / "gto"
                / "lang"
                / "ja_jp.json",
                {"gto.quest.title": "冒険"},
            )
            (instance_root / "config" / "ftbquests" / "quests").mkdir(parents=True, exist_ok=True)
            (instance_root / "config" / "ftbquests" / "quests" / "data.snbt").write_text("{}", encoding="utf-8")
            _write_json(
                instance_root / "resourcepacks" / "guidepack" / "pack.mcmeta",
                {"pack": {"description": "Guide pack", "pack_format": 34}},
            )
            guide_path = (
                instance_root
                / "resourcepacks"
                / "guidepack"
                / "assets"
                / "ae2"
                / "ae2guide"
                / "_en_us"
                / "page.md"
            )
            guide_path.parent.mkdir(parents=True, exist_ok=True)
            guide_path.write_text("# Guide", encoding="utf-8")
            patchouli_path = instance_root / "patchouli_books" / "testbook" / "book.json"
            patchouli_path.parent.mkdir(parents=True, exist_ok=True)
            patchouli_path.write_text("{}", encoding="utf-8")

            report = build_instance_content_report(
                instance_root,
                pack_description="Instance pack",
                pack_format=34,
                include_vanilla=True,
            )

            self.assertEqual(report["mods"]["ja_source_count"], 1)
            self.assertEqual(report["openloader_resources"]["ja_source_count"], 1)
            self.assertEqual(report["ftbquests"]["quest_root_count"], 1)
            self.assertEqual(report["patchouli_external_books"]["book_count"], 1)
            self.assertEqual(len(report["guide_sources"]), 1)
            self.assertEqual(report["manifest"]["sources"][0]["type"], "minecraft_assets")
            self.assertEqual(len(report["manifest"]["sources"]), 6)
            self.assertEqual(report["source_count"], 6)
            self.assertTrue(any(source["type"] == "ftbquests_legacy_inline" for source in report["manifest"]["sources"]))
            quest_source = next(source for source in report["manifest"]["sources"] if source["type"] == "ftbquests_legacy_inline")
            self.assertEqual(quest_source["portability"], "portable")
            self.assertEqual(quest_source["full_pack_rewrite_root"], "assets/ftbquests")

    def test_builds_instance_manifest_with_staged_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            instance_root = Path(temp_dir)
            _write_archive(
                instance_root / "mods" / "ExampleMod-1.0.0.jar",
                mods_toml='[[mods]]\nmodId = "examplemod"\n',
                files={"assets/examplemod/lang/ja_jp.json": json.dumps({"gui.title": "例"})},
            )
            _write_json(
                instance_root / "config" / "openloader" / "resources" / "quests" / "pack.mcmeta",
                {"id": "gto_quests", "pack": {"description": "Quest pack", "pack_format": 34}},
            )
            _write_json(
                instance_root
                / "config"
                / "openloader"
                / "resources"
                / "quests"
                / "assets"
                / "gto"
                / "lang"
                / "ja_jp.json",
                {"gto.quest.title": "冒険"},
            )
            manifest = build_instance_manifest(
                instance_root,
                pack_description="Instance pack",
                pack_format=34,
                include_vanilla=True,
            )

            self.assertEqual(manifest["build"]["target_layout"], "instance")
            self.assertEqual(manifest["sources"][0]["type"], "minecraft_assets")
            self.assertEqual(manifest["sources"][1]["output_root"], "resourcepack")
            self.assertTrue(
                any(source["output_kind"] == "openloader" for source in manifest["sources"][1:])
            )

    def test_builds_gto_workflow_manifest_with_repo_priority_gto_lang_and_instance_mods_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            instance_root = root / "GregTech-Odyssey"
            (instance_root / ".git").mkdir(parents=True, exist_ok=True)
            _write_archive(
                instance_root / "mods" / "othermod.jar",
                mods_toml='[[mods]]\nmodId = "othermod"\n',
                files={"assets/othermod/lang/ja_jp.json": json.dumps({"other.key": "別翻訳"}, ensure_ascii=False)},
            )
            _write_json(
                instance_root / "resourcepacks" / "instancepack" / "pack.mcmeta",
                {"pack": {"description": "Instance pack", "pack_format": 34}},
            )
            _write_json(
                instance_root / "resourcepacks" / "instancepack" / "assets" / "gto" / "lang" / "ja_jp.json",
                {"dup": "instance"},
            )

            mod_repo = root / "GregTech-Modern"
            (mod_repo / ".git").mkdir(parents=True, exist_ok=True)
            _write_json(
                mod_repo / "src" / "main" / "resources" / "assets" / "gtceu" / "lang" / "ja_jp.json",
                {"machine.name": "高電圧機械"},
            )

            translations_repo = root / "GTO-Translations"
            (translations_repo / ".git").mkdir(parents=True, exist_ok=True)
            _write_json(
                translations_repo / "ja_jp" / "resourcepacks" / "gto-lang-ja_jp" / "pack.mcmeta",
                {"pack": {"description": "GTO translations", "pack_format": 34}},
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
                {"dup": "repo"},
            )
            _write_json(
                translations_repo
                / "ja_jp"
                / "resourcepacks"
                / "gto-lang-ja_jp"
                / "assets"
                / "gtocore"
                / "lang"
                / "ja_jp.json",
                {"core": "repo"},
            )
            (instance_root / "config" / "ftbquests" / "quests").mkdir(parents=True, exist_ok=True)
            (instance_root / "config" / "ftbquests" / "quests" / "data.snbt").write_text("{}", encoding="utf-8")

            manifest = build_gto_workflow_manifest(
                instance_root,
                repo_root=root,
                pack_description="Workflow pack",
                pack_format=34,
            )

            self.assertEqual(manifest["build"]["target_layout"], "resourcepack")
            self.assertEqual(
                manifest["workflow"]["merge_rule"],
                "gto_translations_repo_supplies_gto_namespaces_instance_mod_archives_supply_everything_else",
            )
            self.assertTrue(any(source["merge_priority"] == 20 for source in manifest["sources"]))
            self.assertTrue(any(source["merge_priority"] == 30 for source in manifest["sources"]))
            self.assertTrue(any(source["id"] == "othermod" for source in manifest["sources"]))
            repo_source = next(source for source in manifest["sources"] if source["id"].startswith("repo-pack:GTO-Translations"))
            self.assertEqual(sorted(repo_source["include_namespaces"]), ["gto", "gtocore"])
            othermod_source = next(source for source in manifest["sources"] if source["id"] == "othermod")
            self.assertEqual(sorted(othermod_source["exclude_namespaces"]), ["gto", "gto_core", "gtocore"])
            self.assertFalse(any(source["id"].startswith("ftbquests:") for source in manifest["sources"]))
