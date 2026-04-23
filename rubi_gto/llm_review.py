from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Protocol
import urllib.error
import urllib.request

from .annotator import validate_annotation
from .io_utils import read_json, write_json
from .japanese import apply_reading_conflict_choices, extract_reading_conflicts, reading_conflict_signature
from .annotator import strip_rubi
from .progress import NullProgress


GENERATED_REVIEW_PATH = Path("review/generated/review_candidates.json")
GENERATED_LLM_SUGGESTIONS_PATH = Path("review/generated/llm_suggestions.json")
GENERATED_LLM_REVIEW_RESULTS_PATH = Path("review/generated/llm_review_results.json")
GENERATED_LLM_REVIEW_REPORT_PATH = Path("review/generated/llm_review_report.json")

LLM_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "resolution_type": {
            "type": "string",
            "enum": ["per_conflict", "pick_option", "merged_annotation", "abstain"],
        },
        "option_choice": {
            "type": "string",
            "enum": ["a", "b", "none"],
        },
        "conflict_choices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "choice": {"type": "string", "enum": ["a", "b"]},
                },
                "required": ["index", "choice"],
            },
        },
        "final_annotation": {"type": "string"},
    },
    "required": [
        "resolution_type",
        "option_choice",
        "conflict_choices",
        "final_annotation",
    ],
}

LLM_REVIEW_INSTRUCTIONS = """You resolve Japanese Minecraft lang strings into Rubi annotations.

Rules:
- Preserve the exact source text when Rubi markers are stripped.
- Use only `§^word(reading)` annotations.
- Readings must be kana only, never kanji.
- Preserve all whitespace, newlines, punctuation, placeholders, ASCII digits, and non-Japanese text exactly.
- Use one annotation per lexical unit. Adjacent kanji words stay separate unless the compound is a single fixed word.
- For verbs and i-adjectives, annotate only the stem and leave conjugating kana outside the annotation.
- For suru-verbs, annotate only the kanji noun and leave `する` / `した` / `して` outside.
- Prefer the provided analyzer options over inventing a brand-new transcript.
- For `reading_only_conflict`, prefer `per_conflict` and choose option `a` or `b` for each conflicting span.
- For `compound_or_lexical_conflict` and `multiline_conflict`, pick option `a`, pick option `b`, or return a `merged_annotation` only if you need a hybrid.
- If unsure, still choose the better option instead of abstaining.
- Return the smallest valid JSON object that matches the schema. Do not include explanations.
"""


@dataclass(slots=True)
class ReviewCandidate:
    record_id: str
    namespace: str
    key: str
    category: str
    source_text: str
    current_text: str
    options: list[dict[str, str]]
    source_origin: str
    source_id: str | None
    reason: str | None

    @property
    def option_a(self) -> dict[str, str]:
        return self.options[0] if len(self.options) >= 1 else {}

    @property
    def option_b(self) -> dict[str, str]:
        return self.options[1] if len(self.options) >= 2 else {}

    @classmethod
    def from_payload(cls, record_id: str, payload: dict[str, Any]) -> "ReviewCandidate":
        namespace = payload.get("namespace", "")
        key = payload.get("key", "")
        if not namespace or not key:
            namespace, _, key = record_id.partition(":")
        return cls(
            record_id=record_id,
            namespace=namespace,
            key=key,
            category=payload.get("category", "other"),
            source_text=payload.get("source_text", ""),
            current_text=payload.get("current_text", payload.get("source_text", "")),
            options=list(payload.get("options", [])),
            source_origin=payload.get("source_origin", ""),
            source_id=payload.get("source_id"),
            reason=payload.get("reason"),
        )


@dataclass(slots=True)
class ReviewCandidateGroup:
    representative: ReviewCandidate
    candidates: list[ReviewCandidate]
    conflict_signature: str | None


class StructuredResponseClient(Protocol):
    def create_structured_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        reasoning_effort: str | None,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        ...


class OpenAIResponsesHTTPClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def create_structured_response(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        reasoning_effort: str | None,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": input_text,
                        }
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if reasoning_effort and model.startswith("gpt-5"):
            payload["reasoning"] = {"effort": reasoning_effort}

        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise RuntimeError(_format_http_error(exc.code, detail, retry_after)) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI API request failed: {exc.reason}") from exc

        return _extract_structured_response(raw_response)


def _format_http_error(status_code: int, detail: str, retry_after: str | None) -> str:
    message = f"OpenAI API request failed with HTTP {status_code}: {detail}"
    if retry_after:
        message += f" retry_after={retry_after}"
    return message


def _retry_after_seconds(error_text: str) -> float | None:
    token = "retry_after="
    if token not in error_text:
        return None
    raw = error_text.split(token, 1)[1].split()[0].strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _is_rate_limit_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return "http 429" in lowered or "rate limit" in lowered


def _request_with_rate_limit_retry(
    client: StructuredResponseClient,
    *,
    model: str,
    instructions: str,
    input_text: str,
    schema_name: str,
    schema: dict[str, Any],
    reasoning_effort: str | None,
    max_output_tokens: int,
    max_rate_limit_retries: int,
    min_request_interval_seconds: float,
    progress: NullProgress | None = None,
) -> dict[str, Any]:
    attempt = 0
    while True:
        if min_request_interval_seconds > 0:
            time.sleep(min_request_interval_seconds)
        try:
            return client.create_structured_response(
                model=model,
                instructions=instructions,
                input_text=input_text,
                schema_name=schema_name,
                schema=schema,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            error_text = str(exc)
            if attempt >= max_rate_limit_retries or not _is_rate_limit_error(error_text):
                raise
            retry_after = _retry_after_seconds(error_text)
            wait_seconds = retry_after if retry_after is not None else min(60.0, 5.0 * (2**attempt))
            if progress:
                progress.note("RATE", f"rate limited, waiting {wait_seconds:.1f}s before retry")
            time.sleep(wait_seconds)
            attempt += 1


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> bool:
    if not path.exists():
        return False

    loaded = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_matching_quotes(value.strip())
        loaded = True
    return loaded


def _extract_structured_response(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("error"):
        error = payload["error"]
        raise RuntimeError(error.get("message", "OpenAI response contained an error"))

    status = payload.get("status")
    if status == "incomplete":
        incomplete = payload.get("incomplete_details", {})
        raise RuntimeError(f"OpenAI response was incomplete: {incomplete.get('reason', 'unknown')}")

    text_chunks: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                refusal = content.get("refusal") or content.get("text") or "model refusal"
                raise RuntimeError(str(refusal))
            if content.get("type") == "output_text" and content.get("text"):
                text_chunks.append(content["text"])

    if not text_chunks and isinstance(payload.get("output_text"), str):
        text_chunks.append(payload["output_text"])
    if not text_chunks:
        raise RuntimeError("OpenAI response did not contain structured text output")

    try:
        return json.loads("".join(text_chunks))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI response did not contain valid JSON: {exc}") from exc


def _load_review_candidates(workspace: Path) -> dict[str, ReviewCandidate]:
    payload = read_json(workspace / GENERATED_REVIEW_PATH, default={"candidates": {}})
    candidates = payload.get("candidates", {})
    return {
        record_id: ReviewCandidate.from_payload(record_id, data)
        for record_id, data in sorted(candidates.items())
    }


def _selected_candidates(
    candidates: dict[str, ReviewCandidate],
    *,
    categories: list[str] | None,
    record_ids: list[str] | None,
    limit: int | None,
) -> list[ReviewCandidate]:
    selected = list(candidates.values())
    if categories:
        allowed = set(categories)
        selected = [candidate for candidate in selected if candidate.category in allowed]
    if record_ids:
        allowed_ids = set(record_ids)
        selected = [candidate for candidate in selected if candidate.record_id in allowed_ids]
    if limit is not None:
        selected = selected[: max(0, limit)]
    return selected


def _candidate_conflict_signature(candidate: ReviewCandidate) -> str | None:
    if candidate.category != "reading_only_conflict":
        return None
    return reading_conflict_signature(
        candidate.source_text,
        candidate.option_a.get("annotated_text", ""),
        candidate.option_b.get("annotated_text", ""),
    )


def _group_selected_candidates(selected: list[ReviewCandidate]) -> list[ReviewCandidateGroup]:
    groups: list[ReviewCandidateGroup] = []
    grouped_indices: dict[tuple[str, str], int] = {}
    for candidate in selected:
        signature = _candidate_conflict_signature(candidate)
        if signature:
            key = (candidate.category, signature)
            existing_index = grouped_indices.get(key)
            if existing_index is not None:
                groups[existing_index].candidates.append(candidate)
                continue
            grouped_indices[key] = len(groups)
        groups.append(
            ReviewCandidateGroup(
                representative=candidate,
                candidates=[candidate],
                conflict_signature=signature,
            )
        )
    return groups


def _candidate_prompt_payload(candidate: ReviewCandidate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": candidate.record_id,
        "key": candidate.key,
        "category": candidate.category,
        "source_text": candidate.source_text,
        "current_text": candidate.current_text if candidate.current_text != candidate.source_text else "",
        "options": [
            {
                "label": "a",
                "annotated_text": candidate.option_a.get("annotated_text", ""),
            },
            {
                "label": "b",
                "annotated_text": candidate.option_b.get("annotated_text", ""),
            },
        ],
    }

    reading_conflicts = extract_reading_conflicts(
        candidate.source_text,
        candidate.option_a.get("annotated_text", ""),
        candidate.option_b.get("annotated_text", ""),
    )
    if reading_conflicts is not None:
        payload["reading_conflicts"] = [
            {
                "index": conflict["index"],
                "word": conflict["word"],
                "start": conflict["start"],
                "end": conflict["end"],
                "option_a_reading": conflict["left_reading"],
                "option_b_reading": conflict["right_reading"],
            }
            for conflict in reading_conflicts
        ]
    return payload


def _build_input_text(candidate: ReviewCandidate) -> str:
    payload = _candidate_prompt_payload(candidate)
    return "Return JSON only.\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _normalize_conflict_choices(raw_choices: list[dict[str, Any]]) -> dict[int, str] | None:
    normalized: dict[int, str] = {}
    for item in raw_choices:
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            return None
        choice = item.get("choice")
        if choice not in {"a", "b"}:
            return None
        if index in normalized:
            return None
        normalized[index] = choice
    return normalized


def _materialize_option_text(candidate: ReviewCandidate, option_text: str) -> str | None:
    if not option_text:
        return None
    if strip_rubi(option_text) == candidate.source_text:
        return option_text

    plain_segment = strip_rubi(option_text)
    if not plain_segment:
        return None
    if plain_segment not in candidate.current_text:
        return None
    merged = candidate.current_text.replace(plain_segment, option_text, 1)
    if strip_rubi(merged) != candidate.source_text:
        return None
    return merged


def _option_source(candidate: ReviewCandidate, label: str) -> str:
    option = candidate.option_a if label == "a" else candidate.option_b
    return str(option.get("source", "")).strip().lower()


def _fallback_choice_order(candidate: ReviewCandidate) -> list[str]:
    labels = ["a", "b"]
    non_sudachi = [label for label in labels if "sudachi" not in _option_source(candidate, label)]
    sudachi = [label for label in labels if "sudachi" in _option_source(candidate, label)]
    return non_sudachi + sudachi


def _fallback_annotation(candidate: ReviewCandidate) -> tuple[str | None, str | None]:
    options = {
        "a": _materialize_option_text(candidate, candidate.option_a.get("annotated_text", "")),
        "b": _materialize_option_text(candidate, candidate.option_b.get("annotated_text", "")),
    }
    valid = {label: text for label, text in options.items() if text and not validate_annotation(candidate.source_text, text)}
    if not valid:
        return None, None
    if len(valid) == 1:
        choice, annotation = next(iter(valid.items()))
        return annotation, choice
    for label in _fallback_choice_order(candidate):
        if label in valid:
            return valid[label], label
    choice, annotation = next(iter(sorted(valid.items())))
    return annotation, choice


def _resolve_candidate_output(candidate: ReviewCandidate, payload: dict[str, Any]) -> tuple[str | None, list[str]]:
    resolution_type = payload.get("resolution_type")
    option_choice = payload.get("option_choice")

    if resolution_type == "abstain":
        return None, []

    if resolution_type == "pick_option":
        if option_choice == "a":
            resolved = _materialize_option_text(candidate, candidate.option_a.get("annotated_text", ""))
            return resolved, [] if resolved else ["missing_option_a_annotation"]
        if option_choice == "b":
            resolved = _materialize_option_text(candidate, candidate.option_b.get("annotated_text", ""))
            return resolved, [] if resolved else ["missing_option_b_annotation"]
        return None, ["missing_option_choice"]

    if resolution_type == "per_conflict":
        if candidate.category != "reading_only_conflict":
            return None, ["per_conflict_not_supported_for_category"]
        choice_map = _normalize_conflict_choices(list(payload.get("conflict_choices", [])))
        if choice_map is None:
            return None, ["invalid_conflict_choices"]
        resolved = apply_reading_conflict_choices(
            candidate.source_text,
            candidate.option_a.get("annotated_text", ""),
            candidate.option_b.get("annotated_text", ""),
            choice_map,
        )
        if resolved is None:
            return None, ["unable_to_apply_conflict_choices"]
        return resolved, []

    if resolution_type == "merged_annotation":
        annotation = str(payload.get("final_annotation", "")).strip()
        if not annotation:
            return None, ["missing_final_annotation"]
        return annotation, []

    return None, ["unknown_resolution_type"]


def _apply_group_resolution(
    candidate: ReviewCandidate,
    resolution_payload: dict[str, Any],
    *,
    fallback_choice: str | None = None,
) -> tuple[str | None, list[str]]:
    if fallback_choice is not None:
        payload = dict(resolution_payload)
        payload["resolution_type"] = "pick_option"
        payload["option_choice"] = fallback_choice
        return _resolve_candidate_output(candidate, payload)
    return _resolve_candidate_output(candidate, resolution_payload)


def _result_entry(
    candidate: ReviewCandidate,
    *,
    status: str,
    model: str,
    resolution_payload: dict[str, Any] | None,
    annotated_text: str | None,
    representative_record_id: str | None = None,
    grouped_record_ids: list[str] | None = None,
    conflict_signature: str | None = None,
    error: str | None = None,
    validation_issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": candidate.record_id,
        "namespace": candidate.namespace,
        "key": candidate.key,
        "category": candidate.category,
        "status": status,
        "source_text": candidate.source_text,
        "current_text": candidate.current_text,
        "source_origin": candidate.source_origin,
        "model": model,
        "annotated_text": annotated_text or "",
        "resolution_type": (resolution_payload or {}).get("resolution_type", ""),
        "option_choice": (resolution_payload or {}).get("option_choice", "none"),
        "conflict_choices": list((resolution_payload or {}).get("conflict_choices", [])),
        "representative_record_id": representative_record_id or candidate.record_id,
        "grouped_record_ids": list(grouped_record_ids or [candidate.record_id]),
        "conflict_signature": conflict_signature or "",
        "validation_issues": list(validation_issues or []),
        "error": error,
    }


def _suggestion_entry(
    *,
    model: str,
    candidate: ReviewCandidate,
    payload: dict[str, Any],
    annotated_text: str,
) -> dict[str, Any]:
    return {
        "annotated_text": annotated_text,
        "source": f"llm:{model}",
        "category": candidate.category,
        "resolution_type": payload.get("resolution_type", ""),
        "option_choice": payload.get("option_choice", "none"),
    }


def _aggregate_status_counts(results: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = Counter(entry.get("status", "unknown") for entry in results.values())
    return dict(sorted(counts.items()))


def _llm_review_report_payload(results_payload: dict[str, Any]) -> dict[str, Any]:
    results = dict(results_payload.get("results", {}))
    resolution_type_counts = Counter()
    option_choice_counts = Counter()
    conflict_choice_counts = Counter()
    category_counts = Counter()
    category_status_counts: dict[str, Counter[str]] = {}

    for entry in results.values():
        resolution_type_counts[entry.get("resolution_type") or "none"] += 1
        option_choice_counts[entry.get("option_choice") or "none"] += 1
        for conflict_choice in entry.get("conflict_choices", []):
            choice = conflict_choice.get("choice") or "none"
            conflict_choice_counts[choice] += 1
        category = entry.get("category") or "other"
        category_counts[category] += 1
        category_status_counts.setdefault(category, Counter())[entry.get("status") or "unknown"] += 1

    last_run_record_ids = list(results_payload.get("last_run_record_ids", []))
    last_run_records = []
    for record_id in last_run_record_ids:
        entry = results.get(record_id)
        if not entry:
            continue
        last_run_records.append(
            {
                "id": entry.get("id", record_id),
                "key": entry.get("key", ""),
                "category": entry.get("category", ""),
                "status": entry.get("status", ""),
                "resolution_type": entry.get("resolution_type", ""),
                "option_choice": entry.get("option_choice", ""),
                "annotated_text": entry.get("annotated_text", ""),
                "error": entry.get("error"),
            }
        )

    return {
        "model": results_payload.get("model"),
        "reasoning_effort": results_payload.get("reasoning_effort"),
        "candidate_count": int(results_payload.get("candidate_count", 0)),
        "selected_candidate_count": int(results_payload.get("selected_candidate_count", 0)),
        "result_count": len(results),
        "aggregate_status_counts": dict(results_payload.get("aggregate_status_counts", {})),
        "aggregate_resolution_type_counts": dict(sorted(resolution_type_counts.items())),
        "aggregate_option_choice_counts": dict(sorted(option_choice_counts.items())),
        "aggregate_conflict_choice_counts": dict(sorted(conflict_choice_counts.items())),
        "aggregate_category_counts": dict(sorted(category_counts.items())),
        "aggregate_category_status_counts": {
            category: dict(sorted(counts.items())) for category, counts in sorted(category_status_counts.items())
        },
        "last_run_status_counts": dict(results_payload.get("last_run_status_counts", {})),
        "selected_group_count": int(results_payload.get("selected_group_count", 0)),
        "last_run_group_ids": list(results_payload.get("last_run_group_ids", [])),
        "last_run_record_ids": last_run_record_ids,
        "last_run_records": last_run_records,
    }


def llm_review(
    workspace: Path,
    *,
    model: str = "gpt-4.1-mini",
    reasoning_effort: str | None = None,
    categories: list[str] | None = None,
    record_ids: list[str] | None = None,
    limit: int | None = None,
    base_url: str | None = None,
    max_output_tokens: int = 8192,
    max_rate_limit_retries: int = 4,
    min_request_interval_seconds: float = 0.0,
    request_timeout_seconds: float = 20.0,
    client: StructuredResponseClient | None = None,
    progress: NullProgress | None = None,
) -> dict[str, Any]:
    candidates = _load_review_candidates(workspace)
    selected = _selected_candidates(candidates, categories=categories, record_ids=record_ids, limit=limit)
    groups = _group_selected_candidates(selected)
    env_loaded = _load_env_file(workspace / ".env")
    reporter = progress or NullProgress()
    reporter.stage("LLM Review", f"{len(selected)}/{len(candidates)} selected groups={len(groups)}")

    existing_suggestions = read_json(workspace / GENERATED_LLM_SUGGESTIONS_PATH, default={})
    existing_results_payload = read_json(workspace / GENERATED_LLM_REVIEW_RESULTS_PATH, default={"results": {}})
    merged_results = dict(existing_results_payload.get("results", {}))
    run_counts = Counter()
    resolved_client = client
    if selected and resolved_client is None:
        resolved_client = OpenAIResponsesHTTPClient(base_url=base_url, timeout=request_timeout_seconds)

    for index, group in enumerate(groups, start=1):
        candidate = group.representative
        group_record_ids = [item.record_id for item in group.candidates]
        reporter.item("LLM", index, len(groups), candidate.record_id, f"{candidate.category} x{len(group.candidates)}")
        prompt = _build_input_text(candidate)
        try:
            assert resolved_client is not None
            resolution = _request_with_rate_limit_retry(
                resolved_client,
                model=model,
                instructions=LLM_REVIEW_INSTRUCTIONS,
                input_text=prompt,
                schema_name="rubi_review_resolution",
                schema=LLM_REVIEW_SCHEMA,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
                max_rate_limit_retries=max_rate_limit_retries,
                min_request_interval_seconds=min_request_interval_seconds,
                progress=reporter,
            )
            group_fallback_annotation, group_fallback_choice = _fallback_annotation(candidate)
            fallback_payload = dict(resolution)
            fallback_payload["resolution_type"] = "pick_option"
            fallback_payload["option_choice"] = group_fallback_choice
            for member in group.candidates:
                annotated_text, resolution_issues = _apply_group_resolution(member, resolution)
                if resolution_issues:
                    fallback_annotation, _ = _apply_group_resolution(
                        member,
                        fallback_payload,
                        fallback_choice=group_fallback_choice,
                    ) if group_fallback_annotation else (None, [])
                    if fallback_annotation:
                        existing_suggestions[member.record_id] = _suggestion_entry(
                            model=model,
                            candidate=member,
                            payload=fallback_payload,
                            annotated_text=fallback_annotation,
                        )
                        merged_results[member.record_id] = _result_entry(
                            member,
                            status="fallback_suggested",
                            model=model,
                            resolution_payload=fallback_payload,
                            annotated_text=fallback_annotation,
                            representative_record_id=candidate.record_id,
                            grouped_record_ids=group_record_ids,
                            conflict_signature=group.conflict_signature,
                            error=",".join(resolution_issues),
                            validation_issues=[],
                        )
                        run_counts["fallback_suggested"] += 1
                        continue
                    existing_suggestions.pop(member.record_id, None)
                    merged_results[member.record_id] = _result_entry(
                        member,
                        status="error",
                        model=model,
                        resolution_payload=resolution,
                        annotated_text=annotated_text,
                        representative_record_id=candidate.record_id,
                        grouped_record_ids=group_record_ids,
                        conflict_signature=group.conflict_signature,
                        error=",".join(resolution_issues),
                        validation_issues=[],
                    )
                    run_counts["error"] += 1
                    continue

                if not annotated_text:
                    fallback_annotation, _ = _apply_group_resolution(
                        member,
                        fallback_payload,
                        fallback_choice=group_fallback_choice,
                    ) if group_fallback_annotation else (None, [])
                    if fallback_annotation:
                        existing_suggestions[member.record_id] = _suggestion_entry(
                            model=model,
                            candidate=member,
                            payload=fallback_payload,
                            annotated_text=fallback_annotation,
                        )
                        merged_results[member.record_id] = _result_entry(
                            member,
                            status="fallback_suggested",
                            model=model,
                            resolution_payload=fallback_payload,
                            annotated_text=fallback_annotation,
                            representative_record_id=candidate.record_id,
                            grouped_record_ids=group_record_ids,
                            conflict_signature=group.conflict_signature,
                        )
                        run_counts["fallback_suggested"] += 1
                        continue
                    existing_suggestions.pop(member.record_id, None)
                    merged_results[member.record_id] = _result_entry(
                        member,
                        status="abstained",
                        model=model,
                        resolution_payload=resolution,
                        annotated_text=None,
                        representative_record_id=candidate.record_id,
                        grouped_record_ids=group_record_ids,
                        conflict_signature=group.conflict_signature,
                    )
                    run_counts["abstained"] += 1
                    continue

                validation_issues = validate_annotation(member.source_text, annotated_text)
                if validation_issues:
                    fallback_annotation, _ = _apply_group_resolution(
                        member,
                        fallback_payload,
                        fallback_choice=group_fallback_choice,
                    ) if group_fallback_annotation else (None, [])
                    if fallback_annotation:
                        existing_suggestions[member.record_id] = _suggestion_entry(
                            model=model,
                            candidate=member,
                            payload=fallback_payload,
                            annotated_text=fallback_annotation,
                        )
                        merged_results[member.record_id] = _result_entry(
                            member,
                            status="fallback_suggested",
                            model=model,
                            resolution_payload=fallback_payload,
                            annotated_text=fallback_annotation,
                            representative_record_id=candidate.record_id,
                            grouped_record_ids=group_record_ids,
                            conflict_signature=group.conflict_signature,
                            error="invalid_annotation",
                            validation_issues=validation_issues,
                        )
                        run_counts["fallback_suggested"] += 1
                        continue
                    existing_suggestions.pop(member.record_id, None)
                    merged_results[member.record_id] = _result_entry(
                        member,
                        status="error",
                        model=model,
                        resolution_payload=resolution,
                        annotated_text=annotated_text,
                        representative_record_id=candidate.record_id,
                        grouped_record_ids=group_record_ids,
                        conflict_signature=group.conflict_signature,
                        error="invalid_annotation",
                        validation_issues=validation_issues,
                    )
                    run_counts["error"] += 1
                    continue

                existing_suggestions[member.record_id] = _suggestion_entry(
                    model=model,
                    candidate=member,
                    payload=resolution,
                    annotated_text=annotated_text,
                )
                merged_results[member.record_id] = _result_entry(
                    member,
                    status="suggested",
                    model=model,
                    resolution_payload=resolution,
                    annotated_text=annotated_text,
                    representative_record_id=candidate.record_id,
                    grouped_record_ids=group_record_ids,
                    conflict_signature=group.conflict_signature,
                )
                run_counts["suggested"] += 1
            reporter.meter("LLM", index, len(groups), detail=candidate.record_id, counts=run_counts)
        except Exception as exc:
            fallback_annotation, fallback_choice = _fallback_annotation(candidate)
            fallback_payload = {
                "resolution_type": "pick_option",
                "option_choice": fallback_choice,
                "conflict_choices": [],
                "final_annotation": "",
            }
            for member in group.candidates:
                resolved_fallback, _ = _apply_group_resolution(
                    member,
                    fallback_payload,
                    fallback_choice=fallback_choice,
                ) if fallback_annotation else (None, [])
                if resolved_fallback:
                    existing_suggestions[member.record_id] = _suggestion_entry(
                        model=model,
                        candidate=member,
                        payload=fallback_payload,
                        annotated_text=resolved_fallback,
                    )
                    merged_results[member.record_id] = _result_entry(
                        member,
                        status="fallback_suggested",
                        model=model,
                        resolution_payload=fallback_payload,
                        annotated_text=resolved_fallback,
                        representative_record_id=candidate.record_id,
                        grouped_record_ids=group_record_ids,
                        conflict_signature=group.conflict_signature,
                        error=str(exc),
                    )
                    run_counts["fallback_suggested"] += 1
                    continue
                existing_suggestions.pop(member.record_id, None)
                merged_results[member.record_id] = _result_entry(
                    member,
                    status="error",
                    model=model,
                    resolution_payload=None,
                    annotated_text=None,
                    representative_record_id=candidate.record_id,
                    grouped_record_ids=group_record_ids,
                    conflict_signature=group.conflict_signature,
                    error=str(exc),
                )
                run_counts["error"] += 1
            reporter.meter("LLM", index, len(groups), detail=candidate.record_id, counts=run_counts)

    write_json(workspace / GENERATED_LLM_SUGGESTIONS_PATH, existing_suggestions)
    results_payload = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "candidate_count": len(candidates),
        "selected_candidate_count": len(selected),
        "selected_group_count": len(groups),
        "aggregate_status_counts": _aggregate_status_counts(merged_results),
        "last_run_status_counts": dict(sorted(run_counts.items())),
        "last_run_group_ids": [group.representative.record_id for group in groups],
        "last_run_record_ids": [candidate.record_id for candidate in selected],
        "results": merged_results,
    }
    write_json(workspace / GENERATED_LLM_REVIEW_RESULTS_PATH, results_payload)
    write_json(workspace / GENERATED_LLM_REVIEW_REPORT_PATH, _llm_review_report_payload(results_payload))
    reporter.done("LLM Review", " ".join(f"{key}={value}" for key, value in sorted(run_counts.items())))
    return {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "candidate_count": len(candidates),
        "selected_candidate_count": len(selected),
        "selected_group_count": len(groups),
        "status_counts": dict(sorted(run_counts.items())),
        "env_loaded": env_loaded,
        "written_suggestions_path": str(GENERATED_LLM_SUGGESTIONS_PATH),
        "written_results_path": str(GENERATED_LLM_REVIEW_RESULTS_PATH),
        "written_report_path": str(GENERATED_LLM_REVIEW_REPORT_PATH),
        "max_rate_limit_retries": max_rate_limit_retries,
        "min_request_interval_seconds": min_request_interval_seconds,
        "request_timeout_seconds": request_timeout_seconds,
    }
