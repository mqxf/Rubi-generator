from __future__ import annotations

import re
from typing import Iterable


RUBI_PATTERN = re.compile(r"§\^\s*(.+?)\s*\(\s*(.+?)\s*\)")
KANJI_PATTERN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF々ヶ]")


def contains_kanji(text: str) -> bool:
    return bool(KANJI_PATTERN.search(text))


def strip_rubi(text: str) -> str:
    return RUBI_PATTERN.sub(lambda match: match.group(1).strip(), text)


def apply_glossary(text: str, glossary_terms: Iterable[dict[str, str]]) -> tuple[str, bool]:
    ordered_terms = sorted(
        glossary_terms,
        key=lambda term: len(term["plain"]),
        reverse=True,
    )
    result = text
    changed = False
    for term in ordered_terms:
        plain = term["plain"]
        annotated = term["annotated"]
        cursor = 0
        segments: list[str] = []
        for match in RUBI_PATTERN.finditer(result):
            plain_segment = result[cursor:match.start()]
            replaced_segment = plain_segment.replace(plain, annotated)
            changed = changed or replaced_segment != plain_segment
            segments.append(replaced_segment)
            segments.append(match.group(0))
            cursor = match.end()
        tail = result[cursor:]
        replaced_tail = tail.replace(plain, annotated)
        changed = changed or replaced_tail != tail
        segments.append(replaced_tail)
        result = "".join(segments)
    return result, changed


def unannotated_kanji_segments(text: str) -> list[str]:
    outside_annotations = RUBI_PATTERN.sub("", text)
    return KANJI_PATTERN.findall(outside_annotations)


def validate_annotation(source_text: str, annotated_text: str) -> list[str]:
    issues: list[str] = []
    stripped = strip_rubi(annotated_text)
    if stripped != source_text:
        issues.append("plain_text_mismatch")
    leftover_sentinels = RUBI_PATTERN.sub("", annotated_text)
    if "§^" in leftover_sentinels:
        issues.append("malformed_rubi_syntax")
    for match in RUBI_PATTERN.finditer(annotated_text):
        word = match.group(1).strip()
        reading = match.group(2).strip()
        if not word:
            issues.append("empty_rubi_word")
        if not reading:
            issues.append("empty_rubi_reading")
        if contains_kanji(reading):
            issues.append("reading_contains_kanji")
    if contains_kanji(source_text) and unannotated_kanji_segments(annotated_text):
        issues.append("unannotated_kanji")
    return sorted(set(issues))
