import json
import unittest
from unittest.mock import patch

from rubi_gto.models import SourceSpec
from rubi_gto.sources import ingest_sources


class MinecraftAssetSourceTests(unittest.TestCase):
    def test_ingests_official_minecraft_lang_file(self) -> None:
        source = SourceSpec(
            id="minecraft-vanilla",
            type="minecraft_assets",
            target_namespace="minecraft",
            minecraft_version="1.20.1",
            locale="ja_jp",
        )

        def fake_http_json(url: str) -> dict:
            if url.endswith("version_manifest_v2.json"):
                return {
                    "versions": [
                        {
                            "id": "1.20.1",
                            "url": "https://example.test/1.20.1.json",
                        }
                    ]
                }
            if url == "https://example.test/1.20.1.json":
                return {
                    "assetIndex": {
                        "url": "https://example.test/assets.json",
                    }
                }
            if url == "https://example.test/assets.json":
                return {
                    "objects": {
                        "minecraft/lang/ja_jp.json": {
                            "hash": "abcdef1234567890",
                        }
                    }
                }
            raise AssertionError(url)

        def fake_http_bytes(url: str) -> bytes:
            self.assertEqual(url, "https://resources.download.minecraft.net/ab/abcdef1234567890")
            return json.dumps({"menu.singleplayer": "一人で遊ぶ"}).encode("utf-8")

        with patch("rubi_gto.sources._http_json", side_effect=fake_http_json), patch(
            "rubi_gto.sources._http_bytes", side_effect=fake_http_bytes
        ):
            records, errors = ingest_sources([source])

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].namespace, "minecraft")
        self.assertEqual(records[0].source_origin, "minecraft:1.20.1:ja_jp:minecraft/lang/ja_jp.json")
        self.assertEqual(records[0].key, "menu.singleplayer")
