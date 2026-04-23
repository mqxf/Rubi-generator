from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import re
from typing import Any

from .annotator import KANJI_PATTERN, RUBI_PATTERN, strip_rubi


SKIP_POS = {"助詞", "助動詞", "補助記号", "記号", "空白"}
CONJUGATING_POS = {"動詞", "形容詞"}
WHITESPACE_SPLIT_RE = re.compile(r"(\s+)")
NUMERIC_TOKEN_RE = re.compile(r"(%(?:\d+\$)?s|\d+)$")
READING_ALIAS_TABLE = {
    "わたくし": "わたし",
    "わたくしたち": "わたしたち",
}


@dataclass(slots=True)
class MorphToken:
    surface: str
    reading: str
    lemma: str
    pos1: str
    pos2: str
    ctype: str
    cform: str


@dataclass(slots=True)
class AnnotationDecision:
    annotated_text: str
    status: str
    review_reason: str | None = None
    review_options: list[dict[str, str]] | None = None


class JamdictReadingResolver:
    def __init__(self) -> None:
        self.available = False
        self._jamdict: Any | None = None
        self._score_cache: dict[tuple[str, str], int] = {}
        try:
            jamdict = importlib.import_module("jamdict")
            self._jamdict = jamdict.Jamdict()
            self.available = bool(getattr(self._jamdict, "ready", True))
        except Exception:
            self._jamdict = None
            self.available = False

    def choose(self, surface: str, candidate_readings: list[str]) -> str | None:
        if not self.available:
            return None

        unique_candidates = list(dict.fromkeys(katakana_to_hiragana(reading) for reading in candidate_readings))
        if len(unique_candidates) < 2:
            return unique_candidates[0] if unique_candidates else None

        scores = {reading: self._score_reading(surface, reading) for reading in unique_candidates}
        best = max(scores.values())
        if best <= 0:
            return None
        winners = [reading for reading, score in scores.items() if score == best]
        if len(winners) != 1:
            return None
        return winners[0]

    def _score_reading(self, surface: str, reading: str) -> int:
        cache_key = (surface, reading)
        if cache_key in self._score_cache:
            return self._score_cache[cache_key]

        assert self._jamdict is not None
        normalized_reading = katakana_to_hiragana(reading)
        best_score = 0
        try:
            result = self._jamdict.lookup(surface)
        except Exception:
            self._score_cache[cache_key] = 0
            return 0

        for entry in getattr(result, "entries", []):
            matching_kanji = [form for form in getattr(entry, "kanji_forms", []) if form.text == surface]
            if not matching_kanji:
                continue
            kanji_score = max((self._priority_score(form.pri) for form in matching_kanji), default=0)
            for kana_form in getattr(entry, "kana_forms", []):
                kana_text = katakana_to_hiragana(kana_form.text)
                if kana_text != normalized_reading:
                    continue
                restrictions = list(getattr(kana_form, "restr", []) or [])
                if restrictions and surface not in restrictions:
                    continue
                score = kanji_score + self._priority_score(kana_form.pri) - self._info_penalty(kana_form.info)
                if score > best_score:
                    best_score = score

        self._score_cache[cache_key] = best_score
        return best_score

    @staticmethod
    def _priority_score(pri_tags: list[str]) -> int:
        score = 0
        for tag in pri_tags:
            if tag == "ichi1":
                score += 30
            elif tag == "ichi2":
                score += 20
            elif tag == "news1":
                score += 24
            elif tag == "news2":
                score += 16
            elif tag == "spec1":
                score += 22
            elif tag == "spec2":
                score += 14
            elif tag == "gai1":
                score += 18
            elif tag == "gai2":
                score += 10
            elif tag.startswith("nf") and len(tag) == 4 and tag[2:].isdigit():
                score += max(1, 20 - int(tag[2:]) // 2)
            else:
                score += 2
        return score

    @staticmethod
    def _info_penalty(info_tags: list[str]) -> int:
        penalty = 0
        for tag in info_tags:
            lowered = tag.lower()
            if "obsolete" in lowered or "out-dated" in lowered:
                penalty += 20
            elif "irregular" in lowered:
                penalty += 12
        return penalty


@dataclass(slots=True)
class RubiSpan:
    word: str
    reading: str
    start: int
    end: int


def katakana_to_hiragana(text: str) -> str:
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def contains_kanji(text: str) -> bool:
    return bool(KANJI_PATTERN.search(text))


def _trailing_non_kanji(surface: str) -> str:
    index = len(surface)
    while index > 0 and not contains_kanji(surface[index - 1]):
        index -= 1
    return surface[index:]


def _annotate_split(surface: str, reading: str, trailing: str) -> str | None:
    if not trailing or not surface.endswith(trailing):
        return None
    reading_suffix = katakana_to_hiragana(trailing)
    if not reading.endswith(reading_suffix):
        return None
    word = surface[: -len(trailing)]
    ruby = reading[: -len(reading_suffix)]
    if not word or not ruby or not contains_kanji(word):
        return None
    return f"§^{word}({ruby}){trailing}"


def annotate_token(token: MorphToken) -> str:
    surface = token.surface
    reading = katakana_to_hiragana(token.reading)
    if not contains_kanji(surface) or not reading or contains_kanji(reading):
        return surface
    if token.pos1 in SKIP_POS:
        return surface

    if token.pos1 == "動詞":
        if "一段" in token.ctype:
            if surface == token.lemma and surface.endswith("る") and len(surface) > 1:
                split = _annotate_split(surface, reading, "る")
                if split:
                    return split
            return f"§^{surface}({reading})"

        trailing = _trailing_non_kanji(surface)
        split = _annotate_split(surface, reading, trailing)
        if split:
            return split
        return f"§^{surface}({reading})"

    if token.pos1 == "形容詞":
        trailing = _trailing_non_kanji(surface)
        split = _annotate_split(surface, reading, trailing)
        if split:
            return split
        return f"§^{surface}({reading})"

    return f"§^{surface}({reading})"


def annotate_consensus_tokens(fugashi_tokens: list[MorphToken], sudachi_tokens: list[MorphToken]) -> str | None:
    if len(fugashi_tokens) != len(sudachi_tokens):
        return None

    output: list[str] = []
    for left, right in zip(fugashi_tokens, sudachi_tokens):
        if left.surface != right.surface:
            return None
        if contains_kanji(left.surface):
            if katakana_to_hiragana(left.reading) != katakana_to_hiragana(right.reading):
                output.append(left.surface)
                continue
            output.append(annotate_token(left))
        else:
            output.append(left.surface)
    return "".join(output)


def annotate_tokens(tokens: list[MorphToken]) -> str:
    return "".join(annotate_token(token) for token in tokens)


def _joined_surface(tokens: list[MorphToken]) -> str:
    return "".join(token.surface for token in tokens)


def _joined_reading(tokens: list[MorphToken]) -> str:
    return "".join(katakana_to_hiragana(token.reading) for token in tokens)


def _annotation_group_count(text: str) -> int:
    return len(RUBI_PATTERN.findall(text))


def _annotation_reading_sequence(text: str) -> str:
    return "".join(match.group(2).strip() for match in RUBI_PATTERN.finditer(text))


def _longest_annotated_word_length(text: str) -> int:
    lengths = [len(match.group(1).strip()) for match in RUBI_PATTERN.finditer(text)]
    return max(lengths, default=0)


def _annotation_spans(text: str) -> list[RubiSpan]:
    spans: list[RubiSpan] = []
    plain_pos = 0
    cursor = 0
    for match in RUBI_PATTERN.finditer(text):
        plain_pos += len(text[cursor:match.start()])
        word = match.group(1).strip()
        reading = match.group(2).strip()
        spans.append(RubiSpan(word=word, reading=reading, start=plain_pos, end=plain_pos + len(word)))
        plain_pos += len(word)
        cursor = match.end()
    return spans


def _render_annotated_text(plain_text: str, spans: list[RubiSpan]) -> str:
    if not spans:
        return plain_text

    parts: list[str] = []
    cursor = 0
    for span in spans:
        if span.start < cursor or plain_text[span.start:span.end] != span.word:
            raise ValueError("invalid rubi span layout")
        parts.append(plain_text[cursor:span.start])
        parts.append(f"§^{span.word}({span.reading})")
        cursor = span.end
    parts.append(plain_text[cursor:])
    return "".join(parts)


def _spans_share_boundaries(left_spans: list[RubiSpan], right_spans: list[RubiSpan]) -> bool:
    return len(left_spans) == len(right_spans) and all(
        left.word == right.word and left.start == right.start and left.end == right.end
        for left, right in zip(left_spans, right_spans)
    )


def _is_kana_text(text: str) -> bool:
    if not text:
        return False
    for char in text:
        code = ord(char)
        if 0x3040 <= code <= 0x309F:
            continue
        if 0x30A0 <= code <= 0x30FF:
            continue
        if char == "ー":
            continue
        return False
    return True


def _is_kana_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    if 0x3040 <= code <= 0x309F:
        return True
    if 0x30A0 <= code <= 0x30FF:
        return True
    return char == "ー"


def _normalize_reading_alias(reading: str) -> str:
    normalized = katakana_to_hiragana(reading)
    return READING_ALIAS_TABLE.get(normalized, normalized)


def _reading_compare_key(reading: str) -> str:
    return _devoice_kana(_normalize_reading_alias(reading))


def _canonicalize_alias_readings(text: str) -> str:
    plain_text = strip_rubi(text)
    spans = _annotation_spans(text)
    if not spans:
        return text

    changed = False
    rewritten: list[RubiSpan] = []
    for span in spans:
        normalized = _normalize_reading_alias(span.reading)
        if normalized != span.reading:
            changed = True
        rewritten.append(RubiSpan(word=span.word, reading=normalized, start=span.start, end=span.end))
    if not changed:
        return text
    return _render_annotated_text(plain_text, rewritten)


def _normalize_kana_prefix_groups(text: str) -> str:
    # If an annotation includes leading kana that also exist in the reading,
    # move those kana outside the rubi group. This preserves the plain text exactly
    # and reduces conflicts like "§^もう一度(もういちど)" vs "もう§^一度(いちど)".
    parts: list[str] = []
    cursor = 0
    for match in RUBI_PATTERN.finditer(text):
        parts.append(text[cursor:match.start()])
        original = match.group(0)
        word = match.group(1).strip()
        reading = match.group(2).strip()

        # Prefix trim only: strip kana that appear before the first kanji.
        # Suffix trimming is intentionally avoided; it can over-strip okurigana
        # in verbs (e.g. "投げつける") and create worse segmentation conflicts.
        prefix = ""
        while word and reading and _is_kana_char(word[0]) and contains_kanji(word):
            left = katakana_to_hiragana(word[0])
            right = katakana_to_hiragana(reading[0])
            if left != right:
                break
            prefix += word[0]
            word = word[1:]
            reading = reading[1:]

        if word and reading and contains_kanji(word) and not contains_kanji(reading):
            parts.append(f"{prefix}§^{word}({reading})")
        else:
            parts.append(original)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def _harmonize_equivalent_span_readings(
    plain_text: str,
    *,
    preferred_side: str,
    other_side: str,
) -> tuple[str, str]:
    if strip_rubi(preferred_side) != plain_text or strip_rubi(other_side) != plain_text:
        return preferred_side, other_side

    preferred_spans = _annotation_spans(preferred_side)
    other_spans = _annotation_spans(other_side)
    if not preferred_spans or not other_spans or not _spans_share_boundaries(preferred_spans, other_spans):
        return preferred_side, other_side

    replacements: dict[int, str] = {}
    for index, (preferred_span, other_span) in enumerate(zip(preferred_spans, other_spans)):
        if preferred_span.reading == other_span.reading:
            continue
        if _counter_preferred_reading(plain_text, preferred_span) is not None:
            continue
        if _reading_compare_key(preferred_span.reading) != _reading_compare_key(other_span.reading):
            continue
        replacements[index] = preferred_span.reading

    if not replacements:
        return preferred_side, other_side
    return preferred_side, _replace_span_readings(other_side, replacements)


def _trim_trailing_kana_to_match_other(
    plain_text: str,
    *,
    trim_side: str,
    reference_side: str,
) -> str | None:
    if strip_rubi(trim_side) != plain_text or strip_rubi(reference_side) != plain_text:
        return None

    trim_spans = _annotation_spans(trim_side)
    reference_spans = _annotation_spans(reference_side)
    if not trim_spans or not reference_spans:
        return None

    rewritten = list(trim_spans)
    changed = False
    for index, span in enumerate(trim_spans):
        for reference in reference_spans:
            if span.start != reference.start or span.end <= reference.end:
                continue
            suffix = plain_text[reference.end:span.end]
            if not suffix or contains_kanji(suffix) or not _is_kana_text(suffix):
                continue
            if span.word != reference.word + suffix:
                continue
            if _reading_compare_key(span.reading) != _reading_compare_key(reference.reading + suffix):
                continue
            rewritten[index] = RubiSpan(
                word=reference.word,
                reading=reference.reading,
                start=reference.start,
                end=reference.end,
            )
            changed = True
            break

    if not changed:
        return None
    rendered = _render_annotated_text(plain_text, rewritten)
    return rendered if strip_rubi(rendered) == plain_text else None


def _partition_combined_reading_for_split(
    plain_text: str,
    split_spans: list[RubiSpan],
    combined_reading: str,
) -> list[str] | None:
    if not split_spans:
        return None

    cursor = 0
    parts: list[str] = []
    for index, span in enumerate(split_spans):
        if index > 0:
            gap = plain_text[split_spans[index - 1].end:span.start]
            if gap:
                if contains_kanji(gap) or not _is_kana_text(gap):
                    return None
                gap_slice = combined_reading[cursor : cursor + len(gap)]
                if len(gap_slice) != len(gap) or _reading_compare_key(gap_slice) != katakana_to_hiragana(gap):
                    return None
                cursor += len(gap)

        span_slice = combined_reading[cursor : cursor + len(span.reading)]
        if len(span_slice) != len(span.reading):
            return None
        if _reading_compare_key(span_slice) != _reading_compare_key(span.reading):
            return None
        parts.append(span_slice)
        cursor += len(span.reading)

    if cursor != len(combined_reading):
        return None
    return parts


def _rewrite_combined_span_using_split_side(
    plain_text: str,
    *,
    split_side: str,
    combined_side: str,
) -> str | None:
    if strip_rubi(split_side) != plain_text or strip_rubi(combined_side) != plain_text:
        return None

    split_spans = _annotation_spans(split_side)
    combined_spans = _annotation_spans(combined_side)
    if not split_spans or not combined_spans:
        return None

    for combined_index, combined_span in enumerate(combined_spans):
        for start_index in range(len(split_spans)):
            if split_spans[start_index].start != combined_span.start:
                continue
            for end_index in range(start_index, len(split_spans)):
                candidate_spans = split_spans[start_index : end_index + 1]
                if candidate_spans[-1].end != combined_span.end:
                    continue
                if len(candidate_spans) < 2:
                    continue
                gaps = [
                    plain_text[candidate_spans[item_index - 1].end : candidate_spans[item_index].start]
                    for item_index in range(1, len(candidate_spans))
                ]
                has_kana_gap = any(gap and _is_kana_text(gap) for gap in gaps)
                has_non_kanji_span = any(
                    span.word == "的" or any(not contains_kanji(char) for char in span.word)
                    for span in candidate_spans
                )
                if not has_kana_gap and not has_non_kanji_span:
                    continue
                partition = _partition_combined_reading_for_split(plain_text, candidate_spans, combined_span.reading)
                if partition is None:
                    continue
                replacement = [
                    RubiSpan(word=span.word, reading=partition[idx], start=span.start, end=span.end)
                    for idx, span in enumerate(candidate_spans)
                ]
                rewritten_spans = combined_spans[:combined_index] + replacement + combined_spans[combined_index + 1 :]
                rewritten = _render_annotated_text(plain_text, rewritten_spans)
                if strip_rubi(rewritten) == plain_text:
                    return rewritten
    return None


def _normalize_pair_for_trivial_differences(
    plain_text: str,
    fugashi_annotated: str,
    sudachi_annotated: str,
) -> tuple[str, str]:
    fugashi = fugashi_annotated
    sudachi = sudachi_annotated
    previous: tuple[str, str] | None = None
    while previous != (fugashi, sudachi):
        previous = (fugashi, sudachi)
        fugashi = _canonicalize_alias_readings(_normalize_kana_prefix_groups(fugashi))
        sudachi = _canonicalize_alias_readings(_normalize_kana_prefix_groups(sudachi))
        fugashi, sudachi = _harmonize_equivalent_span_readings(
            plain_text,
            preferred_side=fugashi,
            other_side=sudachi,
        )
        rewritten = _trim_trailing_kana_to_match_other(plain_text, trim_side=fugashi, reference_side=sudachi)
        if rewritten:
            fugashi = rewritten
        rewritten = _trim_trailing_kana_to_match_other(plain_text, trim_side=sudachi, reference_side=fugashi)
        if rewritten:
            sudachi = rewritten
        rewritten = _rewrite_combined_span_using_split_side(plain_text, split_side=fugashi, combined_side=sudachi)
        if rewritten:
            sudachi = rewritten
        rewritten = _rewrite_combined_span_using_split_side(plain_text, split_side=sudachi, combined_side=fugashi)
        if rewritten:
            fugashi = rewritten
        fugashi, sudachi = _harmonize_equivalent_span_readings(
            plain_text,
            preferred_side=fugashi,
            other_side=sudachi,
        )
    return fugashi, sudachi


_DEVOICE_TABLE = {
    "が": "か",
    "ぎ": "き",
    "ぐ": "く",
    "げ": "け",
    "ご": "こ",
    "ざ": "さ",
    "じ": "し",
    "ず": "す",
    "ぜ": "せ",
    "ぞ": "そ",
    "だ": "た",
    "ぢ": "ち",
    "づ": "つ",
    "で": "て",
    "ど": "と",
    "ば": "は",
    "び": "ひ",
    "ぶ": "ふ",
    "べ": "へ",
    "ぼ": "ほ",
    "ぱ": "は",
    "ぴ": "ひ",
    "ぷ": "ふ",
    "ぺ": "へ",
    "ぽ": "ほ",
    "ガ": "カ",
    "ギ": "キ",
    "グ": "ク",
    "ゲ": "ケ",
    "ゴ": "コ",
    "ザ": "サ",
    "ジ": "シ",
    "ズ": "ス",
    "ゼ": "セ",
    "ゾ": "ソ",
    "ダ": "タ",
    "ヂ": "チ",
    "ヅ": "ツ",
    "デ": "テ",
    "ド": "ト",
    "バ": "ハ",
    "ビ": "ヒ",
    "ブ": "フ",
    "ベ": "ヘ",
    "ボ": "ホ",
    "パ": "ハ",
    "ピ": "ヒ",
    "プ": "フ",
    "ペ": "ヘ",
    "ポ": "ホ",
    "ヴ": "ウ",
}


def _devoice_kana(text: str) -> str:
    return "".join(_DEVOICE_TABLE.get(char, char) for char in text)


def _choose_fugashi_when_only_dakuten_diff(
    plain_text: str,
    fugashi_annotated: str,
    sudachi_annotated: str,
) -> str | None:
    if strip_rubi(fugashi_annotated) != plain_text or strip_rubi(sudachi_annotated) != plain_text:
        return None
    fugashi_spans = _annotation_spans(fugashi_annotated)
    sudachi_spans = _annotation_spans(sudachi_annotated)
    if not _spans_share_boundaries(fugashi_spans, sudachi_spans):
        return None

    saw_conflict = False
    for left, right in zip(fugashi_spans, sudachi_spans):
        if left.reading == right.reading:
            continue
        saw_conflict = True
        if _devoice_kana(left.reading) != right.reading:
            return None
    return fugashi_annotated if saw_conflict else None


def _choose_overlap_shift_candidate(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
) -> str | None:
    # Handle the common pattern:
    #   [A][BC] vs [AB][C]
    # without inventing readings. We just choose the left side deterministically
    # when both sides are "simple noun-ish" and fully annotated.
    if strip_rubi(left_annotated) != plain_text or strip_rubi(right_annotated) != plain_text:
        return None
    left_spans = _annotation_spans(left_annotated)
    right_spans = _annotation_spans(right_annotated)
    if len(left_spans) != 2 or len(right_spans) != 2:
        return None
    if any(not span.reading or contains_kanji(span.reading) for span in left_spans + right_spans):
        return None

    left_boundary = left_spans[0].end
    right_boundary = right_spans[0].end
    if left_boundary == right_boundary:
        return None
    if abs(left_boundary - right_boundary) != 1:
        return None
    # Avoid messing with verb/adjective stem logic.
    boundary_char = plain_text[min(left_boundary, right_boundary)]
    if not contains_kanji(boundary_char):
        return None
    return left_annotated


def _is_stem_span(shorter: RubiSpan, longer: RubiSpan, plain_text: str) -> bool:
    if shorter.start != longer.start or shorter.end >= longer.end:
        return False
    if not longer.word.startswith(shorter.word):
        return False
    suffix = longer.word[len(shorter.word) :]
    if not suffix or contains_kanji(suffix) or not _is_kana_text(suffix):
        return False
    if katakana_to_hiragana(longer.reading) != katakana_to_hiragana(shorter.reading) + katakana_to_hiragana(suffix):
        return False
    return plain_text[shorter.end : shorter.end + len(suffix)] == suffix


def _numeric_token_before(plain_text: str, start: int) -> str | None:
    match = NUMERIC_TOKEN_RE.search(plain_text[:start])
    if not match:
        return None
    return match.group(1)


def _literal_number_from_token(token: str) -> int | None:
    if not token or token.startswith("%"):
        return None
    try:
        return int(token)
    except ValueError:
        return None


def _counter_reading_for_number(word: str, number: int | None) -> str | None:
    stable = {
        "人": "にん",
        "行": "ぎょう",
        "個": "こ",
        "回": "かい",
        "枚": "まい",
        "台": "だい",
    }
    if word in stable:
        return stable[word]

    if number is None:
        return None

    if word == "匹":
        last_two = number % 100
        if last_two == 3:
            return "びき"
        last_digit = number % 10
        if last_digit in {1, 6, 8, 0}:
            return "ぴき"
        if last_digit == 3:
            return "びき"
        return "ひき"

    if word == "分":
        last_digit = number % 10
        if last_digit in {1, 3, 4, 6, 8, 0}:
            return "ぷん"
        return "ふん"

    if word == "本":
        last_digit = number % 10
        if last_digit in {1, 6, 8, 0}:
            return "ぽん"
        if last_digit == 3:
            return "ぼん"
        return "ほん"

    return None


def _is_supported_counter_word(word: str) -> bool:
    return word in {"人", "行", "個", "回", "枚", "台", "匹", "分", "本"}


def _counter_preferred_reading(plain_text: str, span: RubiSpan) -> str | None:
    token = _numeric_token_before(plain_text, span.start)
    if not token:
        return None
    return _counter_reading_for_number(span.word, _literal_number_from_token(token))


def _counter_conflict_context_profile(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
) -> list[tuple[int, RubiSpan, str, str, str | None]] | None:
    if strip_rubi(left_annotated) != plain_text or strip_rubi(right_annotated) != plain_text:
        return None

    left_spans = _annotation_spans(left_annotated)
    right_spans = _annotation_spans(right_annotated)
    if not _spans_share_boundaries(left_spans, right_spans):
        return None

    conflicts: list[tuple[int, RubiSpan, str, str, str]] = []
    for index, (left_span, right_span) in enumerate(zip(left_spans, right_spans)):
        if left_span.reading == right_span.reading:
            continue
        token = _numeric_token_before(plain_text, left_span.start)
        if not token or not _is_supported_counter_word(left_span.word):
            return None
        preferred = _counter_reading_for_number(left_span.word, _literal_number_from_token(token))
        conflicts.append((index, left_span, left_span.reading, right_span.reading, preferred))
    return conflicts


def _replace_span_readings(text: str, replacements: dict[int, str]) -> str:
    if not replacements:
        return text
    parts: list[str] = []
    cursor = 0
    match_index = 0
    for match in RUBI_PATTERN.finditer(text):
        parts.append(text[cursor:match.start()])
        word = match.group(1).strip()
        reading = replacements.get(match_index, match.group(2).strip())
        parts.append(f"§^{word}({reading})")
        cursor = match.end()
        match_index += 1
    parts.append(text[cursor:])
    return "".join(parts)


def _choose_counter_candidate(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
) -> str | None:
    conflicts = _counter_conflict_context_profile(plain_text, left_annotated, right_annotated)
    if not conflicts:
        return None

    replacements: dict[int, str] = {}
    for index, _span, left_reading, right_reading, preferred in conflicts:
        if preferred is None:
            return None
        if preferred not in {left_reading, right_reading}:
            return None
        replacements[index] = preferred

    base = left_annotated
    return _replace_span_readings(base, replacements)


def _choose_jamdict_reading_candidate(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
    resolver: JamdictReadingResolver | None,
) -> str | None:
    if resolver is None or not resolver.available:
        return None
    if strip_rubi(left_annotated) != plain_text or strip_rubi(right_annotated) != plain_text:
        return None

    left_spans = _annotation_spans(left_annotated)
    right_spans = _annotation_spans(right_annotated)
    if not _spans_share_boundaries(left_spans, right_spans):
        return None

    replacements: dict[int, str] = {}
    changed = False
    for index, (left_span, right_span) in enumerate(zip(left_spans, right_spans)):
        if left_span.reading == right_span.reading:
            continue
        chosen = resolver.choose(left_span.word, [left_span.reading, right_span.reading])
        if chosen is None:
            return None
        if chosen not in {left_span.reading, right_span.reading}:
            return None
        replacements[index] = chosen
        changed = True

    if not changed:
        return None
    return _replace_span_readings(left_annotated, replacements)


def extract_reading_conflicts(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
) -> list[dict[str, Any]] | None:
    if strip_rubi(left_annotated) != plain_text or strip_rubi(right_annotated) != plain_text:
        return None

    left_spans = _annotation_spans(left_annotated)
    right_spans = _annotation_spans(right_annotated)
    if not _spans_share_boundaries(left_spans, right_spans):
        return None

    conflicts: list[dict[str, Any]] = []
    for index, (left_span, right_span) in enumerate(zip(left_spans, right_spans)):
        if left_span.reading == right_span.reading:
            continue
        conflicts.append(
            {
                "index": index,
                "word": left_span.word,
                "start": left_span.start,
                "end": left_span.end,
                "left_reading": left_span.reading,
                "right_reading": right_span.reading,
            }
        )
    return conflicts


def reading_conflict_signature(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
) -> str | None:
    conflicts = extract_reading_conflicts(plain_text, left_annotated, right_annotated)
    if not conflicts:
        return None
    signature = [
        {
            "word": conflict["word"],
            "left_reading": conflict["left_reading"],
            "right_reading": conflict["right_reading"],
        }
        for conflict in conflicts
    ]
    return json.dumps(signature, ensure_ascii=False, separators=(",", ":"))


def apply_reading_conflict_choices(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
    choices: dict[int, str],
) -> str | None:
    conflicts = extract_reading_conflicts(plain_text, left_annotated, right_annotated)
    if not conflicts:
        return None

    conflict_map = {conflict["index"]: conflict for conflict in conflicts}
    if set(choices) != set(conflict_map):
        return None

    replacements: dict[int, str] = {}
    for index, choice in choices.items():
        conflict = conflict_map[index]
        if choice == "a":
            replacements[index] = conflict["left_reading"]
        elif choice == "b":
            replacements[index] = conflict["right_reading"]
        else:
            return None
    return _replace_span_readings(left_annotated, replacements)


def _choose_conjugation_candidate(
    plain_text: str,
    left_annotated: str,
    right_annotated: str,
) -> str | None:
    if strip_rubi(left_annotated) != plain_text or strip_rubi(right_annotated) != plain_text:
        return None

    left_spans = _annotation_spans(left_annotated)
    right_spans = _annotation_spans(right_annotated)
    if not left_spans or not right_spans:
        return None

    left_index = 0
    right_index = 0
    preferred: str | None = None
    saw_stem_conflict = False

    while left_index < len(left_spans) and right_index < len(right_spans):
        left_span = left_spans[left_index]
        right_span = right_spans[right_index]
        if left_span == right_span:
            left_index += 1
            right_index += 1
            continue
        if _is_stem_span(left_span, right_span, plain_text):
            if preferred not in (None, "left"):
                return None
            preferred = "left"
            saw_stem_conflict = True
            left_index += 1
            right_index += 1
            continue
        if _is_stem_span(right_span, left_span, plain_text):
            if preferred not in (None, "right"):
                return None
            preferred = "right"
            saw_stem_conflict = True
            left_index += 1
            right_index += 1
            continue
        return None

    if left_index != len(left_spans) or right_index != len(right_spans) or not saw_stem_conflict or preferred is None:
        return None
    return left_annotated if preferred == "left" else right_annotated


def _has_conjugating_token(tokens: list[MorphToken]) -> bool:
    return any(token.pos1 in CONJUGATING_POS for token in tokens)


def _all_tokens_have_kanji(tokens: list[MorphToken]) -> bool:
    return all(contains_kanji(token.surface) for token in tokens)


def _choose_same_reading_candidate(
    fugashi_tokens: list[MorphToken],
    sudachi_tokens: list[MorphToken],
    fugashi_annotated: str,
    sudachi_annotated: str,
) -> str | None:
    plain = _joined_surface(fugashi_tokens)
    if _joined_surface(sudachi_tokens) != plain:
        return None

    if _joined_reading(fugashi_tokens) != _joined_reading(sudachi_tokens):
        return None

    combined_tokens = list(fugashi_tokens) + list(sudachi_tokens)
    if not _has_conjugating_token(combined_tokens) and _all_tokens_have_kanji(fugashi_tokens) and _all_tokens_have_kanji(sudachi_tokens):
        reading = _joined_reading(fugashi_tokens)
        if reading and contains_kanji(plain):
            return f"§^{plain}({reading})"

    candidates = [candidate for candidate in (fugashi_annotated, sudachi_annotated) if candidate != plain]
    if not candidates:
        return None

    if _has_conjugating_token(combined_tokens):
        candidates.sort(key=lambda text: (-_annotation_group_count(text), len(text)))
    else:
        candidates.sort(key=lambda text: (_annotation_group_count(text), -len(text)))
    return candidates[0]


def _choose_equivalent_annotation_candidate(
    plain_text: str,
    fugashi_annotated: str,
    sudachi_annotated: str,
) -> str | None:
    candidates = [candidate for candidate in (fugashi_annotated, sudachi_annotated) if candidate != plain_text]
    if len(candidates) < 2:
        return None

    if strip_rubi(fugashi_annotated) != plain_text or strip_rubi(sudachi_annotated) != plain_text:
        return None

    fugashi_reading = _annotation_reading_sequence(fugashi_annotated)
    sudachi_reading = _annotation_reading_sequence(sudachi_annotated)
    if not fugashi_reading or fugashi_reading != sudachi_reading:
        return None

    candidates.sort(
        key=lambda text: (
            _annotation_group_count(text),
            -_longest_annotated_word_length(text),
            -len(text),
        )
    )
    return candidates[0]


def categorize_review_candidate(source_text: str, review_reason: str | None, options: list[dict[str, str]] | None) -> str:
    if review_reason == "no_recommendation":
        return "no_recommendation"
    if review_reason == "analyzer_error":
        return "analyzer_error"
    if "\n" in source_text:
        return "multiline_conflict"
    if not options or len(options) != 2:
        return "other"

    left_annotated = options[0].get("annotated_text", "")
    right_annotated = options[1].get("annotated_text", "")
    counter_conflicts = _counter_conflict_context_profile(source_text, left_annotated, right_annotated)
    if counter_conflicts:
        return "unresolved_counter_or_numeric_conflict"
    if _choose_conjugation_candidate(source_text, left_annotated, right_annotated):
        return "verb_stem_conflict"

    left_spans = _annotation_spans(left_annotated)
    right_spans = _annotation_spans(right_annotated)
    if left_spans and right_spans:
        if len(left_spans) == len(right_spans) and all(
            left.word == right.word and left.start == right.start and left.end == right.end
            for left, right in zip(left_spans, right_spans)
        ):
            return "reading_only_conflict"
        return "compound_or_lexical_conflict"

    plain = source_text.strip("！？…。、・「」『』（）() ")
    kanji_count = sum(1 for char in plain if contains_kanji(char))
    if 2 <= len(plain) <= 8 and kanji_count >= 2:
        return "short_fixed_phrase_conflict"
    return "other"


class ConsensusAnnotator:
    def __init__(self) -> None:
        self.available = False
        self._tagger: Any | None = None
        self._sudachi: Any | None = None
        self._jamdict_resolver = JamdictReadingResolver()
        try:
            fugashi = importlib.import_module("fugashi")
            sudachipy = importlib.import_module("sudachipy")
        except Exception:
            return

        try:
            self._tagger = fugashi.Tagger()
            self._sudachi = sudachipy.Dictionary(dict="full").create()
            self.available = True
        except Exception:
            self._tagger = None
            self._sudachi = None
            self.available = False

    def _tokenize_fugashi(self, text: str) -> list[MorphToken]:
        assert self._tagger is not None
        tokens: list[MorphToken] = []
        for word in self._tagger(text):
            feature = word.feature
            tokens.append(
                MorphToken(
                    surface=word.surface,
                    reading=getattr(feature, "kana", "") or "",
                    lemma=getattr(feature, "lemma", "") or word.surface,
                    pos1=getattr(feature, "pos1", "") or "",
                    pos2=getattr(feature, "pos2", "") or "",
                    ctype=getattr(feature, "cType", "") or "",
                    cform=getattr(feature, "cForm", "") or "",
                )
            )
        return tokens

    def _tokenize_sudachi(self, text: str) -> list[MorphToken]:
        assert self._sudachi is not None
        tokens: list[MorphToken] = []
        for morph in self._sudachi.tokenize(text):
            pos = morph.part_of_speech()
            tokens.append(
                MorphToken(
                    surface=morph.surface(),
                    reading=morph.reading_form(),
                    lemma=morph.dictionary_form(),
                    pos1=pos[0],
                    pos2=pos[1],
                    ctype=pos[4],
                    cform=pos[5],
                )
            )
        return tokens

    def annotate_text(self, text: str) -> str:
        return self.annotate_with_review(text).annotated_text

    def annotate_with_review(self, text: str) -> AnnotationDecision:
        if not self.available or not contains_kanji(text):
            return AnnotationDecision(text, "plain")

        cursor = 0
        parts: list[str] = []
        segment_statuses: list[AnnotationDecision] = []
        for match in RUBI_PATTERN.finditer(text):
            decision = self._annotate_plain_segment(text[cursor:match.start()])
            parts.append(decision.annotated_text)
            segment_statuses.append(decision)
            parts.append(match.group(0))
            cursor = match.end()
        decision = self._annotate_plain_segment(text[cursor:])
        parts.append(decision.annotated_text)
        segment_statuses.append(decision)

        review_options: list[dict[str, str]] = []
        review_reasons: list[str] = []
        final_status = "generated" if any(dec.status == "generated" for dec in segment_statuses) else "plain"
        for dec in segment_statuses:
            if dec.status == "review":
                final_status = "review"
                if dec.review_reason:
                    review_reasons.append(dec.review_reason)
                if dec.review_options:
                    review_options.extend(dec.review_options)

        return AnnotationDecision(
            annotated_text="".join(parts),
            status=final_status,
            review_reason=",".join(sorted(set(review_reasons))) if review_reasons else None,
            review_options=review_options or None,
        )

    def _annotate_plain_segment(self, text: str) -> AnnotationDecision:
        if not text:
            return AnnotationDecision(text, "plain")
        if any(char.isspace() for char in text):
            return self._annotate_preserving_whitespace(text)
        if not text or not contains_kanji(text):
            return AnnotationDecision(text, "plain")

        try:
            fugashi_tokens = self._tokenize_fugashi(text)
            sudachi_tokens = self._tokenize_sudachi(text)
        except Exception:
            return AnnotationDecision(text, "review", "analyzer_error", [])

        fugashi_annotated = annotate_tokens(fugashi_tokens)
        sudachi_annotated = annotate_tokens(sudachi_tokens)
        fugashi_annotated, sudachi_annotated = _normalize_pair_for_trivial_differences(
            text,
            fugashi_annotated,
            sudachi_annotated,
        )

        if fugashi_annotated == sudachi_annotated:
            if fugashi_annotated == text:
                return AnnotationDecision(text, "review", "no_recommendation", [])
            return AnnotationDecision(fugashi_annotated, "generated")

        if fugashi_annotated != text and sudachi_annotated == text:
            return AnnotationDecision(fugashi_annotated, "generated")

        if sudachi_annotated != text and fugashi_annotated == text:
            return AnnotationDecision(sudachi_annotated, "generated")

        counter_preferred = _choose_counter_candidate(text, fugashi_annotated, sudachi_annotated)
        if counter_preferred and counter_preferred != text:
            return AnnotationDecision(counter_preferred, "generated")

        conjugation_preferred = _choose_conjugation_candidate(text, fugashi_annotated, sudachi_annotated)
        if conjugation_preferred and conjugation_preferred != text:
            return AnnotationDecision(conjugation_preferred, "generated")

        dakuten_preferred = _choose_fugashi_when_only_dakuten_diff(text, fugashi_annotated, sudachi_annotated)
        if dakuten_preferred and dakuten_preferred != text:
            return AnnotationDecision(dakuten_preferred, "generated")

        overlap_shift_preferred = _choose_overlap_shift_candidate(text, fugashi_annotated, sudachi_annotated)
        if overlap_shift_preferred and overlap_shift_preferred != text:
            return AnnotationDecision(overlap_shift_preferred, "generated")

        jamdict_preferred = _choose_jamdict_reading_candidate(
            text,
            fugashi_annotated,
            sudachi_annotated,
            getattr(self, "_jamdict_resolver", None),
        )
        if jamdict_preferred and jamdict_preferred != text:
            return AnnotationDecision(jamdict_preferred, "generated")

        equivalent = _choose_equivalent_annotation_candidate(text, fugashi_annotated, sudachi_annotated)
        if equivalent and equivalent != text:
            return AnnotationDecision(equivalent, "generated")

        merged = _choose_same_reading_candidate(fugashi_tokens, sudachi_tokens, fugashi_annotated, sudachi_annotated)
        if merged and merged != text:
            return AnnotationDecision(merged, "generated")

        if fugashi_annotated == text and sudachi_annotated == text:
            return AnnotationDecision(text, "review", "no_recommendation", [])

        return AnnotationDecision(
            text,
            "review",
            "analyzer_conflict",
            [
                {"source": "fugashi+unidic", "annotated_text": fugashi_annotated},
                {"source": "sudachi-full", "annotated_text": sudachi_annotated},
            ],
        )

    def _annotate_preserving_whitespace(self, text: str) -> AnnotationDecision:
        parts: list[str] = []
        statuses: list[AnnotationDecision] = []
        for segment in WHITESPACE_SPLIT_RE.split(text):
            if segment == "":
                continue
            if segment.isspace():
                parts.append(segment)
                continue
            decision = self._annotate_plain_segment(segment)
            parts.append(decision.annotated_text)
            statuses.append(decision)

        review_options: list[dict[str, str]] = []
        review_reasons: list[str] = []
        final_status = "generated" if any(dec.status == "generated" for dec in statuses) else "plain"
        for dec in statuses:
            if dec.status == "review":
                final_status = "review"
                if dec.review_reason:
                    review_reasons.append(dec.review_reason)
                if dec.review_options:
                    review_options.extend(dec.review_options)

        return AnnotationDecision(
            annotated_text="".join(parts),
            status=final_status,
            review_reason=",".join(sorted(set(review_reasons))) if review_reasons else None,
            review_options=review_options or None,
        )
