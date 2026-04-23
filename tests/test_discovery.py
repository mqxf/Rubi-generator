import json
import tempfile
import unittest
from pathlib import Path

from rubi_gto.sources import discover_local_sources


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
