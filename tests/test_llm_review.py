import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rubi_gto.llm_review import (
    GENERATED_LLM_REVIEW_REPORT_PATH,
    GENERATED_LLM_REVIEW_RESULTS_PATH,
    GENERATED_LLM_SUGGESTIONS_PATH,
    _load_env_file,
    llm_review,
)
from rubi_gto.pipeline import annotate


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FakeClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create_structured_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, object],
        reasoning_effort: str | None,
        max_output_tokens: int,
    ) -> dict[str, object]:
        self.requests.append(
            {
                "model": model,
                "instructions": instructions,
                "input_text": input_text,
                "schema_name": schema_name,
                "schema": schema,
                "reasoning_effort": reasoning_effort,
                "max_output_tokens": max_output_tokens,
            }
        )
        return self._responses.pop(0)


class RetryThenSuccessClient:
    def __init__(self) -> None:
        self.calls = 0

    def create_structured_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, object],
        reasoning_effort: str | None,
        max_output_tokens: int,
    ) -> dict[str, object]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("OpenAI API request failed with HTTP 429: rate limit exceeded retry_after=0")
        return {
            "resolution_type": "pick_option",
            "option_choice": "a",
            "conflict_choices": [],
            "final_annotation": "",
        }


class LLMReviewTests(unittest.TestCase):
    def test_load_env_file_sets_missing_values_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env_path = tmp_path / ".env"
            env_path.write_text(
                'OPENAI_API_KEY="test-key"\nOPENAI_BASE_URL=https://example.invalid/v1\n',
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "https://override.invalid/v1"}, clear=True):
                loaded = _load_env_file(env_path)
                self.assertTrue(loaded)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "test-key")
                self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://override.invalid/v1")

    def test_llm_review_can_mix_reading_only_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:status.body": {
                            "id": "minecraft:status.body",
                            "namespace": "minecraft",
                            "key": "status.body",
                            "category": "reading_only_conflict",
                            "source_text": "本当に身体が不足している",
                            "current_text": "本当に身体が不足している",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^本当(ほんと)に§^身体(からだ)が§^不足(ぶそく)している",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^本当(ほんとう)に§^身体(しんたい)が§^不足(ふそく)している",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )
            client = FakeClient(
                [
                    {
                        "resolution_type": "per_conflict",
                        "option_choice": "none",
                        "conflict_choices": [
                            {"index": 0, "choice": "b"},
                            {"index": 1, "choice": "a"},
                            {"index": 2, "choice": "b"},
                        ],
                        "final_annotation": "",
                    }
                ]
            )

            summary = llm_review(tmp_path, model="gpt-5", client=client)

            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            results = json.loads((tmp_path / GENERATED_LLM_REVIEW_RESULTS_PATH).read_text(encoding="utf-8"))
            report = json.loads((tmp_path / GENERATED_LLM_REVIEW_REPORT_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"suggested": 1})
            self.assertFalse(summary["env_loaded"])
            self.assertEqual(
                suggestions["minecraft:status.body"]["annotated_text"],
                "§^本当(ほんとう)に§^身体(からだ)が§^不足(ふそく)している",
            )
            self.assertEqual(results["results"]["minecraft:status.body"]["status"], "suggested")
            self.assertEqual(results["results"]["minecraft:status.body"]["representative_record_id"], "minecraft:status.body")
            self.assertEqual(results["results"]["minecraft:status.body"]["grouped_record_ids"], ["minecraft:status.body"])
            self.assertEqual(report["aggregate_option_choice_counts"].get("none", 0), 1)
            self.assertEqual(report["aggregate_conflict_choice_counts"]["a"], 1)
            self.assertEqual(report["aggregate_conflict_choice_counts"]["b"], 2)
            self.assertEqual(report["selected_group_count"], 1)
            self.assertEqual(report["last_run_record_ids"], ["minecraft:status.body"])
            self.assertEqual(report["last_run_records"][0]["annotated_text"], "§^本当(ほんとう)に§^身体(からだ)が§^不足(ふそく)している")
            self.assertIn('"key":"status.body"', str(client.requests[0]["input_text"]))

    def test_llm_review_groups_identical_reading_only_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 2,
                    "candidates": {
                        "minecraft:first": {
                            "id": "minecraft:first",
                            "namespace": "minecraft",
                            "key": "first",
                            "category": "reading_only_conflict",
                            "source_text": "本当に身体が不足している",
                            "current_text": "本当に身体が不足している",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^本当(ほんと)に§^身体(からだ)が§^不足(ぶそく)している",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^本当(ほんとう)に§^身体(しんたい)が§^不足(ふそく)している",
                                },
                            ],
                            "source_origin": "test",
                        },
                        "minecraft:second": {
                            "id": "minecraft:second",
                            "namespace": "minecraft",
                            "key": "second",
                            "category": "reading_only_conflict",
                            "source_text": "本当に身体が不足していた",
                            "current_text": "本当に身体が不足していた",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^本当(ほんと)に§^身体(からだ)が§^不足(ぶそく)していた",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^本当(ほんとう)に§^身体(しんたい)が§^不足(ふそく)していた",
                                },
                            ],
                            "source_origin": "test",
                        },
                    },
                },
            )
            client = FakeClient(
                [
                    {
                        "resolution_type": "per_conflict",
                        "option_choice": "none",
                        "conflict_choices": [
                            {"index": 0, "choice": "b"},
                            {"index": 1, "choice": "a"},
                            {"index": 2, "choice": "b"},
                        ],
                        "final_annotation": "",
                    }
                ]
            )

            summary = llm_review(tmp_path, model="gpt-5", client=client)

            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            results = json.loads((tmp_path / GENERATED_LLM_REVIEW_RESULTS_PATH).read_text(encoding="utf-8"))
            report = json.loads((tmp_path / GENERATED_LLM_REVIEW_REPORT_PATH).read_text(encoding="utf-8"))

            self.assertEqual(len(client.requests), 1)
            self.assertEqual(summary["selected_candidate_count"], 2)
            self.assertEqual(summary["selected_group_count"], 1)
            self.assertEqual(summary["status_counts"], {"suggested": 2})
            self.assertEqual(
                suggestions["minecraft:first"]["annotated_text"],
                "§^本当(ほんとう)に§^身体(からだ)が§^不足(ふそく)している",
            )
            self.assertEqual(
                suggestions["minecraft:second"]["annotated_text"],
                "§^本当(ほんとう)に§^身体(からだ)が§^不足(ふそく)していた",
            )
            self.assertEqual(results["results"]["minecraft:first"]["representative_record_id"], "minecraft:first")
            self.assertEqual(
                results["results"]["minecraft:second"]["grouped_record_ids"],
                ["minecraft:first", "minecraft:second"],
            )
            self.assertEqual(report["selected_group_count"], 1)
            self.assertEqual(report["last_run_group_ids"], ["minecraft:first"])

    def test_llm_review_accepts_merged_annotation_for_compound_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:adv.trade": {
                            "id": "minecraft:adv.trade",
                            "namespace": "minecraft",
                            "key": "adv.trade",
                            "category": "compound_or_lexical_conflict",
                            "source_text": "建築限界高度で村人と取引をする",
                            "current_text": "建築限界高度で村人と取引をする",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^建築(けんちく)§^限界(げんかい)§^高度(こうど)で§^村(そん)§^人(じん)と§^取引(とりひき)をする",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^建築限界(けんちくげんかい)§^高度(こうど)で§^村人(むらびと)と§^取引(とりひき)をする",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )
            client = FakeClient(
                [
                    {
                        "resolution_type": "merged_annotation",
                        "option_choice": "none",
                        "conflict_choices": [],
                        "final_annotation": "§^建築限界(けんちくげんかい)§^高度(こうど)で§^村人(むらびと)と§^取引(とりひき)をする",
                    }
                ]
            )

            summary = llm_review(tmp_path, model="gpt-5", client=client)
            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"suggested": 1})
            self.assertEqual(
                suggestions["minecraft:adv.trade"]["annotated_text"],
                "§^建築限界(けんちくげんかい)§^高度(こうど)で§^村人(むらびと)と§^取引(とりひき)をする",
            )

    def test_llm_review_can_merge_partial_option_into_current_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:adv.exile": {
                            "id": "minecraft:adv.exile",
                            "namespace": "minecraft",
                            "key": "adv.exile",
                            "category": "multiline_conflict",
                            "source_text": "襲撃隊の大将を倒す。\n当分の間村から離れて過ごされてみてはいかがでしょうか…",
                            "current_text": "§^襲撃隊(しゅうげきたい)の§^大将(たいしょう)を§^倒(たお)す。\n当分の間村から離れて過ごされてみてはいかがでしょうか…",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^当分(とうぶん)の§^間(あいだ)§^村(むら)から§^離れ(はなれ)て§^過(す)ごされてみてはいかがでしょうか…",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^当分(とうぶん)の§^間村(まむら)から§^離れ(はなれ)て§^過(す)ごされてみてはいかがでしょうか…",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )
            client = FakeClient(
                [
                    {
                        "resolution_type": "pick_option",
                        "option_choice": "a",
                        "conflict_choices": [],
                        "final_annotation": "",
                    }
                ]
            )

            summary = llm_review(tmp_path, model="gpt-4.1-mini", client=client)
            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"suggested": 1})
            self.assertEqual(
                suggestions["minecraft:adv.exile"]["annotated_text"],
                "§^襲撃隊(しゅうげきたい)の§^大将(たいしょう)を§^倒(たお)す。\n§^当分(とうぶん)の§^間(あいだ)§^村(むら)から§^離れ(はなれ)て§^過(す)ごされてみてはいかがでしょうか…",
            )

    def test_llm_review_falls_back_to_non_sudachi_option_on_abstain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:adv.trade": {
                            "id": "minecraft:adv.trade",
                            "namespace": "minecraft",
                            "key": "adv.trade",
                            "category": "compound_or_lexical_conflict",
                            "source_text": "村人と取引をする",
                            "current_text": "村人と取引をする",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^村(そん)§^人(じん)と§^取引(とりひき)をする",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^村人(むらびと)と§^取引(とりひき)をする",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )
            client = FakeClient(
                [
                    {
                        "resolution_type": "abstain",
                        "option_choice": "none",
                        "conflict_choices": [],
                        "final_annotation": "",
                    }
                ]
            )

            summary = llm_review(tmp_path, model="gpt-4.1-mini", client=client)
            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            results = json.loads((tmp_path / GENERATED_LLM_REVIEW_RESULTS_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"fallback_suggested": 1})
            self.assertEqual(
                suggestions["minecraft:adv.trade"]["annotated_text"],
                "§^村(そん)§^人(じん)と§^取引(とりひき)をする",
            )
            self.assertEqual(results["results"]["minecraft:adv.trade"]["option_choice"], "a")
            self.assertEqual(results["results"]["minecraft:adv.trade"]["status"], "fallback_suggested")

    def test_llm_review_falls_back_to_sudachi_when_other_option_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:adv.trade": {
                            "id": "minecraft:adv.trade",
                            "namespace": "minecraft",
                            "key": "adv.trade",
                            "category": "compound_or_lexical_conflict",
                            "source_text": "村人と取引をする",
                            "current_text": "村人と取引をする",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^村(そん)§^人(じん)で§^取引(とりひき)をする",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^村人(むらびと)と§^取引(とりひき)をする",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )
            client = FakeClient(
                [
                    {
                        "resolution_type": "abstain",
                        "option_choice": "none",
                        "conflict_choices": [],
                        "final_annotation": "",
                    }
                ]
            )

            summary = llm_review(tmp_path, model="gpt-4.1-mini", client=client)
            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            results = json.loads((tmp_path / GENERATED_LLM_REVIEW_RESULTS_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"fallback_suggested": 1})
            self.assertEqual(
                suggestions["minecraft:adv.trade"]["annotated_text"],
                "§^村人(むらびと)と§^取引(とりひき)をする",
            )
            self.assertEqual(results["results"]["minecraft:adv.trade"]["option_choice"], "b")
            self.assertEqual(results["results"]["minecraft:adv.trade"]["status"], "fallback_suggested")

    def test_manual_suggestions_override_generated_llm_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "build" / "ingested_records.json",
                [
                    {
                        "namespace": "minecraft",
                        "key": "menu.singleplayer",
                        "source_text": "一人で遊ぶ",
                        "annotated_text": "一人で遊ぶ",
                        "source_origin": "test",
                        "source_id": "test",
                        "review_status": "pending",
                        "issues": [],
                        "notes": None,
                    }
                ],
            )
            _write_json(tmp_path / "review" / "glossary.json", {"terms": []})
            _write_json(tmp_path / "review" / "review_entries.json", {})
            _write_json(
                tmp_path / "review" / "generated" / "llm_suggestions.json",
                {
                    "minecraft:menu.singleplayer": {
                        "annotated_text": "§^一人(いちにん)で§^遊(ゆう)ぶ",
                        "source": "llm:gpt-5",
                    }
                },
            )
            _write_json(
                tmp_path / "review" / "suggestions.json",
                {
                    "minecraft:menu.singleplayer": {
                        "annotated_text": "§^一人(ひとり)で§^遊(あそ)ぶ",
                        "source": "manual-llm",
                    }
                },
            )

            summary = annotate(tmp_path)
            annotated = json.loads((tmp_path / "build" / "annotated_records.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"suggested": 1})
            self.assertEqual(annotated[0]["annotated_text"], "§^一人(ひとり)で§^遊(あそ)ぶ")
            self.assertEqual(annotated[0]["notes"], "suggestion:manual-llm")

    def test_llm_review_retries_rate_limit_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:adv.trade": {
                            "id": "minecraft:adv.trade",
                            "namespace": "minecraft",
                            "key": "adv.trade",
                            "category": "compound_or_lexical_conflict",
                            "source_text": "村人と取引をする",
                            "current_text": "村人と取引をする",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^村人(むらびと)と§^取引(とりひき)をする",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^村(そん)§^人(じん)と§^取引(とりひき)をする",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )
            client = RetryThenSuccessClient()
            with mock.patch("rubi_gto.llm_review.time.sleep") as mocked_sleep:
                summary = llm_review(
                    tmp_path,
                    model="gpt-4.1-mini",
                    client=client,
                    max_rate_limit_retries=2,
                )

            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            self.assertEqual(client.calls, 2)
            self.assertEqual(summary["status_counts"], {"suggested": 1})
            self.assertEqual(suggestions["minecraft:adv.trade"]["annotated_text"], "§^村人(むらびと)と§^取引(とりひき)をする")
            mocked_sleep.assert_called_with(0.0)

    def test_llm_review_falls_back_on_arbitrary_client_error(self) -> None:
        class AlwaysFailClient:
            def create_structured_response(
                self,
                *,
                model: str,
                instructions: str,
                input_text: str,
                schema_name: str,
                schema: dict[str, object],
                reasoning_effort: str | None,
                max_output_tokens: int,
            ) -> dict[str, object]:
                raise RuntimeError("upstream exploded")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            _write_json(
                tmp_path / "review" / "generated" / "review_candidates.json",
                {
                    "candidate_count": 1,
                    "candidates": {
                        "minecraft:adv.trade": {
                            "id": "minecraft:adv.trade",
                            "namespace": "minecraft",
                            "key": "adv.trade",
                            "category": "compound_or_lexical_conflict",
                            "source_text": "村人と取引をする",
                            "current_text": "村人と取引をする",
                            "reason": "analyzer_conflict",
                            "options": [
                                {
                                    "source": "fugashi+unidic",
                                    "annotated_text": "§^村(そん)§^人(じん)と§^取引(とりひき)をする",
                                },
                                {
                                    "source": "sudachi-full",
                                    "annotated_text": "§^村人(むらびと)と§^取引(とりひき)をする",
                                },
                            ],
                            "source_origin": "test",
                        }
                    },
                },
            )

            summary = llm_review(tmp_path, model="gpt-4.1-mini", client=AlwaysFailClient())
            suggestions = json.loads((tmp_path / GENERATED_LLM_SUGGESTIONS_PATH).read_text(encoding="utf-8"))
            results = json.loads((tmp_path / GENERATED_LLM_REVIEW_RESULTS_PATH).read_text(encoding="utf-8"))

            self.assertEqual(summary["status_counts"], {"fallback_suggested": 1})
            self.assertEqual(
                suggestions["minecraft:adv.trade"]["annotated_text"],
                "§^村(そん)§^人(じん)と§^取引(とりひき)をする",
            )
            self.assertEqual(results["results"]["minecraft:adv.trade"]["option_choice"], "a")
            self.assertEqual(results["results"]["minecraft:adv.trade"]["status"], "fallback_suggested")
