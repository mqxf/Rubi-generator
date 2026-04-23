import unittest

from rubi_gto.japanese import (
    ConsensusAnnotator,
    JamdictReadingResolver,
    MorphToken,
    annotate_consensus_tokens,
    annotate_token,
    categorize_review_candidate,
    katakana_to_hiragana,
)


class JapaneseTests(unittest.TestCase):
    def test_katakana_to_hiragana(self) -> None:
        self.assertEqual(katakana_to_hiragana("ボウケン"), "ぼうけん")

    def test_annotate_noun_token(self) -> None:
        token = MorphToken("冒険", "ボウケン", "冒険", "名詞", "普通名詞", "*", "*")
        self.assertEqual(annotate_token(token), "§^冒険(ぼうけん)")

    def test_annotate_godan_verb_token(self) -> None:
        token = MorphToken("遊ぶ", "アソブ", "遊ぶ", "動詞", "一般", "五段-バ行", "終止形-一般")
        self.assertEqual(annotate_token(token), "§^遊(あそ)ぶ")

    def test_annotate_ichidan_stem_token(self) -> None:
        token = MorphToken("食べ", "タベ", "食べる", "動詞", "一般", "下一段-バ行", "連用形-一般")
        self.assertEqual(annotate_token(token), "§^食べ(たべ)")

    def test_annotate_i_adjective_token(self) -> None:
        token = MorphToken("暗かっ", "クラカッ", "暗い", "形容詞", "一般", "形容詞", "連用形-促音便")
        self.assertEqual(annotate_token(token), "§^暗(くら)かっ")

    def test_consensus_adjacent_terms_stay_split(self) -> None:
        fugashi_tokens = [
            MorphToken("高電圧", "コウデンアツ", "高電圧", "名詞", "普通名詞", "*", "*"),
            MorphToken("機械", "キカイ", "機械", "名詞", "普通名詞", "*", "*"),
        ]
        sudachi_tokens = [
            MorphToken("高電圧", "コウデンアツ", "高電圧", "名詞", "普通名詞", "*", "*"),
            MorphToken("機械", "キカイ", "機械", "名詞", "普通名詞", "*", "*"),
        ]
        self.assertEqual(
            annotate_consensus_tokens(fugashi_tokens, sudachi_tokens),
            "§^高電圧(こうでんあつ)§^機械(きかい)",
        )

    def test_live_annotator_sample(self) -> None:
        annotator = ConsensusAnnotator()
        if not annotator.available:
            self.skipTest("Japanese analyzers are not installed")
        self.assertEqual(annotator.annotate_text("冒険の時間"), "§^冒険(ぼうけん)の§^時間(じかん)")

    def test_conflict_becomes_review(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [MorphToken("一人", "ヒトリ", "一人", "名詞", "普通名詞", "*", "*")]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [MorphToken("一人", "イチニン", "一人", "名詞", "普通名詞", "*", "*")]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("一人")
        self.assertEqual(decision.annotated_text, "一人")
        self.assertEqual(decision.status, "review")
        self.assertEqual(decision.review_reason, "analyzer_conflict")
        self.assertEqual(len(decision.review_options or []), 2)

    def test_same_reading_compound_prefers_longer_unit(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("無", "ム", "無", "接頭辞", "*", "*", "*"),
                    MorphToken("条件", "ジョウケン", "条件", "名詞", "普通名詞", "*", "*"),
                ]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [MorphToken("無条件", "ムジョウケン", "無条件", "名詞", "普通名詞", "*", "*")]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("無条件")
        self.assertEqual(decision.annotated_text, "§^無条件(むじょうけん)")
        self.assertEqual(decision.status, "generated")

    def test_one_side_missing_prefers_other(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [MorphToken("冒険", "ボウケン", "冒険", "名詞", "普通名詞", "*", "*")]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [MorphToken("冒険", "ボウケン", "冒険", "記号", "*", "*", "*")]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("冒険")
        self.assertEqual(decision.annotated_text, "§^冒険(ぼうけん)")
        self.assertEqual(decision.status, "generated")

    def test_verb_conflict_prefers_stem_style(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [MorphToken("投げつける", "ナゲツケル", "投げつける", "動詞", "一般", "下一段-カ行", "終止形-一般")]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("投げつけ", "ナゲツケ", "投げつける", "動詞", "一般", "下一段-カ行", "連用形-一般"),
                    MorphToken("る", "ル", "る", "助動詞", "*", "*", "*"),
                ]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("投げつける")
        self.assertEqual(decision.annotated_text, "§^投げつけ(なげつけ)る")
        self.assertEqual(decision.status, "generated")

    def test_counter_conflict_prefers_numeric_reading(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("2", "", "2", "名詞", "数詞", "*", "*"),
                    MorphToken("匹", "ピキ", "匹", "名詞", "普通名詞", "*", "*"),
                ]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("2", "", "2", "名詞", "数詞", "*", "*"),
                    MorphToken("匹", "ヒキ", "匹", "名詞", "普通名詞", "*", "*"),
                ]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("2匹")
        self.assertEqual(decision.annotated_text, "2§^匹(ひき)")
        self.assertEqual(decision.status, "generated")

    def test_placeholder_counter_prefers_default_people_counter(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True
                self._jamdict_resolver = JamdictReadingResolver()

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("%s", "", "%s", "記号", "*", "*", "*"),
                    MorphToken("人", "ジン", "人", "名詞", "普通名詞", "*", "*"),
                ]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("%s", "", "%s", "記号", "*", "*", "*"),
                    MorphToken("人", "ニン", "人", "名詞", "普通名詞", "*", "*"),
                ]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("%s人")
        self.assertEqual(decision.annotated_text, "%s§^人(にん)")
        self.assertEqual(decision.status, "generated")

    def test_jamdict_prefers_more_common_reading(self) -> None:
        class FakeResolver:
            available = True

            def choose(self, surface: str, candidate_readings: list[str]) -> str | None:
                if surface == "本当":
                    return "ほんとう"
                return None

        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True
                self._jamdict_resolver = FakeResolver()

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("本当", "ホント", "本当", "名詞", "普通名詞", "*", "*"),
                    MorphToken("に", "ニ", "に", "助詞", "*", "*", "*"),
                ]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("本当", "ホントウ", "本当", "名詞", "普通名詞", "*", "*"),
                    MorphToken("に", "ニ", "に", "助詞", "*", "*", "*"),
                ]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("本当に")
        self.assertEqual(decision.annotated_text, "§^本当(ほんとう)に")
        self.assertEqual(decision.status, "generated")

    def test_phrase_level_same_reading_prefers_longer_units(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("望遠", "ボウエン", "望遠", "名詞", "普通名詞", "*", "*"),
                    MorphToken("鏡", "キョウ", "鏡", "名詞", "普通名詞", "*", "*"),
                    MorphToken("を", "ヲ", "を", "助詞", "*", "*", "*"),
                    MorphToken("観察", "カンサツ", "観察", "名詞", "普通名詞", "*", "*"),
                    MorphToken("する", "スル", "する", "動詞", "一般", "サ行変格", "終止形-一般"),
                ]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [
                    MorphToken("望遠鏡", "ボウエンキョウ", "望遠鏡", "名詞", "普通名詞", "*", "*"),
                    MorphToken("を", "ヲ", "を", "助詞", "*", "*", "*"),
                    MorphToken("観察", "カンサツ", "観察", "名詞", "普通名詞", "*", "*"),
                    MorphToken("する", "スル", "する", "動詞", "一般", "サ行変格", "終止形-一般"),
                ]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("望遠鏡を観察する")
        self.assertEqual(decision.annotated_text, "§^望遠鏡(ぼうえんきょう)を§^観察(かんさつ)する")
        self.assertEqual(decision.status, "generated")

    def test_preserves_newlines(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                return [MorphToken(text, "セッテイ" if text == "設定" else "イドウ", text, "名詞", "普通名詞", "*", "*")]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                return [MorphToken(text, "セッテイ" if text == "設定" else "イドウ", text, "名詞", "普通名詞", "*", "*")]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("設定\n移動")
        self.assertEqual(decision.annotated_text, "§^設定(せってい)\n§^移動(いどう)")
        self.assertEqual(decision.status, "generated")

    def test_preserves_newlines_across_phrase_level_merge(self) -> None:
        class FakeAnnotator(ConsensusAnnotator):
            def __init__(self) -> None:
                self.available = True

            def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
                if text == "望遠鏡を観察する":
                    return [
                        MorphToken("望遠", "ボウエン", "望遠", "名詞", "普通名詞", "*", "*"),
                        MorphToken("鏡", "キョウ", "鏡", "名詞", "普通名詞", "*", "*"),
                        MorphToken("を", "ヲ", "を", "助詞", "*", "*", "*"),
                        MorphToken("観察", "カンサツ", "観察", "名詞", "普通名詞", "*", "*"),
                        MorphToken("する", "スル", "する", "動詞", "一般", "サ行変格", "終止形-一般"),
                    ]
                return [MorphToken("結果", "ケッカ", "結果", "名詞", "普通名詞", "*", "*")]

            def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
                if text == "望遠鏡を観察する":
                    return [
                        MorphToken("望遠鏡", "ボウエンキョウ", "望遠鏡", "名詞", "普通名詞", "*", "*"),
                        MorphToken("を", "ヲ", "を", "助詞", "*", "*", "*"),
                        MorphToken("観察", "カンサツ", "観察", "名詞", "普通名詞", "*", "*"),
                        MorphToken("する", "スル", "する", "動詞", "一般", "サ行変格", "終止形-一般"),
                    ]
                return [MorphToken("結果", "ケッカ", "結果", "名詞", "普通名詞", "*", "*")]

        annotator = FakeAnnotator()
        decision = annotator.annotate_with_review("望遠鏡を観察する\n結果")
        self.assertEqual(decision.annotated_text, "§^望遠鏡(ぼうえんきょう)を§^観察(かんさつ)する\n§^結果(けっか)")
        self.assertEqual(decision.status, "generated")

    def test_categorize_reading_only_conflict(self) -> None:
        category = categorize_review_candidate(
            "値が不足しています（角度は1つ必要です）",
            "analyzer_conflict",
            [
                {
                    "source": "fugashi+unidic",
                    "annotated_text": "§^値(あたい)が§^不足(ぶそく)しています（§^角度(かくど)は1つ§^必要(ひつよう)です）",
                },
                {
                    "source": "sudachi-full",
                    "annotated_text": "§^値(あたい)が§^不足(ふそく)しています（§^角度(かくど)は1つ§^必要(ひつよう)です）",
                },
            ],
        )
        self.assertEqual(category, "reading_only_conflict")

    def test_categorize_unresolved_counter_conflict(self) -> None:
        category = categorize_review_candidate(
            "%s匹",
            "analyzer_conflict",
            [
                {"source": "fugashi+unidic", "annotated_text": "%s§^匹(ぴき)"},
                {"source": "sudachi-full", "annotated_text": "%s§^匹(ひき)"},
            ],
        )
        self.assertEqual(category, "unresolved_counter_or_numeric_conflict")

    def test_categorize_compound_conflict(self) -> None:
        category = categorize_review_candidate(
            "村人と取引をする",
            "analyzer_conflict",
            [
                {"source": "fugashi+unidic", "annotated_text": "§^村(そん)§^人(じん)と§^取引(とりひき)をする"},
                {"source": "sudachi-full", "annotated_text": "§^村人(むらびと)と§^取引(とりひき)をする"},
            ],
        )
        self.assertEqual(category, "compound_or_lexical_conflict")
