from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .annotator import validate_annotation
from .io_utils import read_json, write_json
from .llm_review import OpenAIResponsesHTTPClient, _load_env_file, _request_with_rate_limit_retry
from .manual_fixes import GENERATED_MANUAL_FIX_CANDIDATES_PATH, GENERATED_MANUAL_FIX_OVERRIDES_PATH
from .progress import NullProgress


GENERATED_MANUAL_FIX_LLM_REPORT_PATH = Path("review/generated/manual_fix_llm_report.json")

MANUAL_FIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "annotated_text": {"type": "string"},
    },
    "required": ["annotated_text"],
}

SELECTION_INSTRUCTIONS = """You are filling a manual override file for Japanese Minecraft Rubi annotations.

Return JSON only.

Rules:
- Output one full final annotation string in `annotated_text`.
- Preserve the exact original text when Rubi markers are stripped.
- Use only `§^word(reading)` annotations.
- Readings must be kana only.
- Preserve whitespace, punctuation, placeholders, formatting codes, and line breaks exactly.
- Prefer the provided analyzer outputs and current text over inventing a new segmentation.
- You may combine the provided options if needed, but keep the result conservative and valid.
"""

REPAIR_INSTRUCTIONS = """You are repairing a failed Japanese Minecraft Rubi annotation for manual review.

Return JSON only.

Rules:
- Output one full corrected annotation string in `annotated_text`.
- Preserve the exact original text when Rubi markers are stripped.
- Use only `§^word(reading)` annotations.
- Readings must be kana only.
- Preserve whitespace, punctuation, placeholders, formatting codes, and line breaks exactly.
- The prior attempt failed validation, so do not blindly copy one option if it is malformed.
- Use all provided context to produce the best valid repaired string.
"""


@dataclass(slots=True)
class ManualFixEntry:
    record_id: str
    key: str
    original_text: str
    fugashi_version: str
    sudachi_version: str
    current_text: str
    category: str
    llm_status: str
    llm_error: str

    @property
    def needs_repair_model(self) -> bool:
        return bool(self.llm_error.strip())


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


@dataclass(slots=True)
class ManualFixAttempt:
    model: str
    reasoning_effort: str | None
    max_output_tokens: int
    instructions: str
    phase: str


def _accept_manual_review_issues(issues: list[str]) -> bool:
    return bool(issues) and set(issues) <= {"unannotated_kanji"}


def _load_manual_fix_entries(workspace: Path) -> dict[str, ManualFixEntry]:
    payload = read_json(workspace / GENERATED_MANUAL_FIX_CANDIDATES_PATH, default={"entries": {}})
    entries = dict(payload.get("entries", {}))
    return {
        record_id: ManualFixEntry(
            record_id=record_id,
            key=str(data.get("key", "")),
            original_text=str(data.get("original_text", "")),
            fugashi_version=str(data.get("fugashi_version", "")),
            sudachi_version=str(data.get("sudachi_version", "")),
            current_text=str(data.get("current_text", "")),
            category=str(data.get("category", "")),
            llm_status=str(data.get("llm_status", "")),
            llm_error=str(data.get("llm_error", "") or ""),
        )
        for record_id, data in sorted(entries.items())
    }


def _candidate_prompt(entry: ManualFixEntry) -> str:
    payload = {
        "id": entry.record_id,
        "key": entry.key,
        "category": entry.category,
        "original_text": entry.original_text,
        "current_text": entry.current_text,
        "fugashi_version": entry.fugashi_version,
        "sudachi_version": entry.sudachi_version,
        "previous_llm_status": entry.llm_status,
        "previous_llm_error": entry.llm_error,
    }
    return str(payload)


def _attempts_for_entry(
    entry: ManualFixEntry,
    *,
    model: str,
    repair_model: str,
    repair_reasoning_effort: str | None,
    max_output_tokens: int,
    repair_max_output_tokens: int,
) -> list[ManualFixAttempt]:
    if entry.needs_repair_model:
        return [
            ManualFixAttempt(
                model=repair_model,
                reasoning_effort=repair_reasoning_effort,
                max_output_tokens=repair_max_output_tokens,
                instructions=REPAIR_INSTRUCTIONS,
                phase="repair",
            )
        ]
    return [
        ManualFixAttempt(
            model=model,
            reasoning_effort=None,
            max_output_tokens=max_output_tokens,
            instructions=SELECTION_INSTRUCTIONS,
            phase="selection",
        ),
        ManualFixAttempt(
            model=repair_model,
            reasoning_effort=repair_reasoning_effort,
            max_output_tokens=repair_max_output_tokens,
            instructions=REPAIR_INSTRUCTIONS,
            phase="repair_fallback",
        ),
    ]


def autofill_manual_fix_overrides(
    workspace: Path,
    *,
    model: str = "gpt-4.1",
    repair_model: str = "gpt-5",
    repair_reasoning_effort: str | None = "high",
    max_output_tokens: int = 32768,
    repair_max_output_tokens: int = 32768,
    overwrite_existing: bool = False,
    base_url: str | None = None,
    max_rate_limit_retries: int = 4,
    min_request_interval_seconds: float = 0.0,
    request_timeout_seconds: float = 20.0,
    client: StructuredResponseClient | None = None,
    progress: NullProgress | None = None,
) -> dict[str, Any]:
    reporter = progress or NullProgress()
    entries = _load_manual_fix_entries(workspace)
    overrides_path = workspace / GENERATED_MANUAL_FIX_OVERRIDES_PATH
    overrides = dict(read_json(overrides_path, default={}))
    env_loaded = _load_env_file(workspace / ".env")

    pending: list[ManualFixEntry] = []
    preserved_existing = 0
    for record_id, entry in entries.items():
        existing_value = str(overrides.get(record_id, "") or "").strip()
        if existing_value and not overwrite_existing:
            preserved_existing += 1
            continue
        pending.append(entry)

    reporter.stage("Manual Fix LLM", f"{len(pending)}/{len(entries)} pending")
    resolved_client = client
    if pending and resolved_client is None:
        resolved_client = OpenAIResponsesHTTPClient(base_url=base_url, timeout=request_timeout_seconds)

    report_entries: dict[str, dict[str, Any]] = {}
    status_counts = {
        "filled": 0,
        "filled_with_missing_kanji": 0,
        "skipped_invalid": 0,
        "error": 0,
        "preserved_existing": preserved_existing,
    }

    for index, entry in enumerate(pending, start=1):
        attempts = _attempts_for_entry(
            entry,
            model=model,
            repair_model=repair_model,
            repair_reasoning_effort=repair_reasoning_effort,
            max_output_tokens=max_output_tokens,
            repair_max_output_tokens=repair_max_output_tokens,
        )
        reporter.item("MANUAL", index, len(pending), entry.record_id, f"{entry.category} model={attempts[0].model}")
        prompt = _candidate_prompt(entry)
        attempt_reports: list[dict[str, Any]] = []
        final_report: dict[str, Any] | None = None
        for attempt in attempts:
            try:
                assert resolved_client is not None
                response = _request_with_rate_limit_retry(
                    resolved_client,
                    model=attempt.model,
                    instructions=attempt.instructions,
                    input_text=prompt,
                    schema_name="manual_fix_override",
                    schema=MANUAL_FIX_SCHEMA,
                    reasoning_effort=attempt.reasoning_effort,
                    max_output_tokens=attempt.max_output_tokens,
                    max_rate_limit_retries=max_rate_limit_retries,
                    min_request_interval_seconds=min_request_interval_seconds,
                    progress=reporter,
                )
                annotated_text = str(response.get("annotated_text", "")).strip()
                issues = validate_annotation(entry.original_text, annotated_text) if annotated_text else ["empty_annotation"]
                if issues:
                    if _accept_manual_review_issues(issues):
                        overrides[entry.record_id] = annotated_text
                        final_report = {
                            "status": "filled_with_missing_kanji",
                            "model": attempt.model,
                            "reasoning_effort": attempt.reasoning_effort,
                            "annotated_text": annotated_text,
                            "validation_issues": issues,
                            "previous_llm_error": entry.llm_error,
                            "attempts": attempt_reports,
                            "final_phase": attempt.phase,
                        }
                        status_counts["filled_with_missing_kanji"] += 1
                        break
                    attempt_reports.append(
                        {
                            "phase": attempt.phase,
                            "status": "skipped_invalid",
                            "model": attempt.model,
                            "reasoning_effort": attempt.reasoning_effort,
                            "annotated_text": annotated_text,
                            "validation_issues": issues,
                        }
                    )
                    continue

                overrides[entry.record_id] = annotated_text
                final_report = {
                    "status": "filled",
                    "model": attempt.model,
                    "reasoning_effort": attempt.reasoning_effort,
                    "annotated_text": annotated_text,
                    "validation_issues": [],
                    "previous_llm_error": entry.llm_error,
                    "attempts": attempt_reports,
                    "final_phase": attempt.phase,
                }
                status_counts["filled"] += 1
                break
            except Exception as exc:
                attempt_reports.append(
                    {
                        "phase": attempt.phase,
                        "status": "error",
                        "model": attempt.model,
                        "reasoning_effort": attempt.reasoning_effort,
                        "annotated_text": "",
                        "validation_issues": [],
                        "error": str(exc),
                    }
                )
                continue

        if final_report is not None:
            report_entries[entry.record_id] = final_report
        else:
            saw_invalid = any(item["status"] == "skipped_invalid" for item in attempt_reports)
            if saw_invalid:
                status_counts["skipped_invalid"] += 1
                last_invalid = next(item for item in reversed(attempt_reports) if item["status"] == "skipped_invalid")
                report_entries[entry.record_id] = {
                    "status": "skipped_invalid",
                    "model": last_invalid["model"],
                    "reasoning_effort": last_invalid["reasoning_effort"],
                    "annotated_text": last_invalid["annotated_text"],
                    "validation_issues": last_invalid["validation_issues"],
                    "previous_llm_error": entry.llm_error,
                    "attempts": attempt_reports,
                    "final_phase": last_invalid["phase"],
                }
            else:
                status_counts["error"] += 1
                last_error = attempt_reports[-1] if attempt_reports else {"model": "", "reasoning_effort": None, "error": ""}
                report_entries[entry.record_id] = {
                    "status": "error",
                    "model": last_error["model"],
                    "reasoning_effort": last_error["reasoning_effort"],
                    "annotated_text": "",
                    "validation_issues": [],
                    "previous_llm_error": entry.llm_error,
                    "error": last_error.get("error", ""),
                    "attempts": attempt_reports,
                    "final_phase": last_error.get("phase", ""),
                }
        reporter.meter("MANUAL", index, len(pending), detail=entry.record_id, counts=status_counts)

    write_json(overrides_path, overrides)
    write_json(
        workspace / GENERATED_MANUAL_FIX_LLM_REPORT_PATH,
        {
            "entry_count": len(entries),
            "pending_count": len(pending),
            "status_counts": status_counts,
            "model": model,
            "repair_model": repair_model,
            "repair_reasoning_effort": repair_reasoning_effort,
            "max_output_tokens": max_output_tokens,
            "repair_max_output_tokens": repair_max_output_tokens,
            "overwrite_existing": overwrite_existing,
            "results": report_entries,
        },
    )
    reporter.done("Manual Fix LLM", " ".join(f"{key}={value}" for key, value in sorted(status_counts.items())))
    return {
        "entry_count": len(entries),
        "pending_count": len(pending),
        "status_counts": status_counts,
        "env_loaded": env_loaded,
        "written_overrides_path": str(GENERATED_MANUAL_FIX_OVERRIDES_PATH),
        "written_report_path": str(GENERATED_MANUAL_FIX_LLM_REPORT_PATH),
        "model": model,
        "repair_model": repair_model,
        "repair_reasoning_effort": repair_reasoning_effort,
    }
