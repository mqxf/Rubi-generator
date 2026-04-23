from __future__ import annotations

from pathlib import Path
from typing import Any

from .annotator import validate_annotation
from .io_utils import read_json, write_json
from .pipeline import (
    ANNOTATED_PATH,
    GENERATED_MANUAL_FIX_SUGGESTIONS_PATH,
    annotate,
    build,
    report,
    _read_records,
)
from .progress import NullProgress


GENERATED_REVIEW_PATH = Path("review/generated/review_candidates.json")
GENERATED_LLM_REVIEW_RESULTS_PATH = Path("review/generated/llm_review_results.json")
GENERATED_MANUAL_FIX_CANDIDATES_PATH = Path("review/generated/manual_fix_candidates.json")
GENERATED_MANUAL_FIX_OVERRIDES_PATH = Path("review/generated/manual_fix_overrides.json")

RESOLVED_LLM_STATUSES = {"suggested", "fallback_suggested"}


def _candidate_map(workspace: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(workspace / GENERATED_REVIEW_PATH, default={"candidates": {}})
    return dict(payload.get("candidates", {}))


def _llm_results_map(workspace: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(workspace / GENERATED_LLM_REVIEW_RESULTS_PATH, default={"results": {}})
    return dict(payload.get("results", {}))


def _option_text(candidate: dict[str, Any], source_name: str) -> str:
    for option in list(candidate.get("options", [])):
        if source_name in str(option.get("source", "")).lower():
            return str(option.get("annotated_text", ""))
    return ""


def export_manual_fix_candidates(workspace: Path, *, progress: NullProgress | None = None) -> dict[str, Any]:
    reporter = progress or NullProgress()
    reporter.stage("Manual Fix Export", "collect unresolved candidates")
    candidates = _candidate_map(workspace)
    llm_results = _llm_results_map(workspace)

    entries: dict[str, dict[str, Any]] = {}
    for record_id, candidate in sorted(candidates.items()):
        result = llm_results.get(record_id)
        status = str((result or {}).get("status", "unreviewed"))
        if status in RESOLVED_LLM_STATUSES:
            continue
        entries[record_id] = {
            "id": record_id,
            "key": candidate.get("key", ""),
            "original_text": candidate.get("source_text", ""),
            "fugashi_version": _option_text(candidate, "fugashi"),
            "sudachi_version": _option_text(candidate, "sudachi"),
            "current_text": candidate.get("current_text", ""),
            "category": candidate.get("category", ""),
            "llm_status": status,
            "llm_error": (result or {}).get("error"),
        }

    overrides = {record_id: "" for record_id in sorted(entries)}
    write_json(
        workspace / GENERATED_MANUAL_FIX_CANDIDATES_PATH,
        {
            "entry_count": len(entries),
            "entries": entries,
        },
    )
    write_json(workspace / GENERATED_MANUAL_FIX_OVERRIDES_PATH, overrides)
    reporter.done("Manual Fix Export", f"entries={len(entries)}")
    return {
        "entry_count": len(entries),
        "written_candidates_path": str(GENERATED_MANUAL_FIX_CANDIDATES_PATH),
        "written_overrides_path": str(GENERATED_MANUAL_FIX_OVERRIDES_PATH),
    }


def _record_map(workspace: Path) -> dict[str, Any]:
    annotated_path = workspace / ANNOTATED_PATH
    if not annotated_path.exists():
        return {}
    return {record.record_id: record for record in _read_records(annotated_path)}


def apply_manual_fix_overrides(
    workspace: Path,
    *,
    manifest_path: Path,
    fixes_path: Path | None = None,
    export_mode: str = "both",
    export_locale: str = "ja_rubi",
    include_generated: bool | None = True,
    include_pending: bool | None = True,
    progress: NullProgress | None = None,
) -> dict[str, Any]:
    reporter = progress or NullProgress()
    reporter.stage("Manual Fix Merge", "validate overrides")
    fixes_file = fixes_path or (workspace / GENERATED_MANUAL_FIX_OVERRIDES_PATH)
    if not fixes_file.exists():
        raise FileNotFoundError(f"manual fix overrides not found at {fixes_file}")

    fixes_payload = read_json(fixes_file, default={})
    records = _record_map(workspace)
    candidates = _candidate_map(workspace)
    merged = read_json(workspace / GENERATED_MANUAL_FIX_SUGGESTIONS_PATH, default={})
    applied = 0
    skipped_empty = 0
    invalid: list[dict[str, Any]] = []

    for record_id, raw_value in sorted(dict(fixes_payload).items()):
        value = str(raw_value or "").strip()
        if not value:
            skipped_empty += 1
            continue
        record = records.get(record_id)
        if record is None:
            invalid.append({"id": record_id, "error": "record_not_found"})
            continue
        issues = validate_annotation(record.source_text, value)
        if issues:
            invalid.append({"id": record_id, "error": "invalid_annotation", "issues": issues})
            continue
        candidate = candidates.get(record_id, {})
        merged[record_id] = {
            "annotated_text": value,
            "source": "manual-fix",
            "category": candidate.get("category", ""),
            "resolution_type": "manual_fix",
            "option_choice": "none",
        }
        applied += 1

    if invalid:
        raise ValueError(f"manual fixes contain invalid entries: {invalid}")

    write_json(workspace / GENERATED_MANUAL_FIX_SUGGESTIONS_PATH, merged)

    annotate_summary = annotate(workspace, progress=reporter)
    report_summary = report(workspace, progress=reporter)

    build_summaries: dict[str, Any] = {}
    if export_mode in {"overwrite", "both"}:
        build_summaries["overwrite"] = build(
            manifest_path,
            workspace,
            include_generated=include_generated,
            include_pending=include_pending,
            export_mode="overwrite",
            export_locale=export_locale,
            progress=reporter,
        )
    if export_mode in {"full-pack", "both"}:
        build_summaries["full-pack"] = build(
            manifest_path,
            workspace,
            include_generated=include_generated,
            include_pending=include_pending,
            export_mode="full-pack",
            export_locale=export_locale,
            progress=reporter,
        )

    reporter.done("Manual Fix Merge", f"applied={applied} rebuilt={len(build_summaries)}")
    return {
        "applied_count": applied,
        "skipped_empty_count": skipped_empty,
        "written_suggestions_path": str(GENERATED_MANUAL_FIX_SUGGESTIONS_PATH),
        "annotate": annotate_summary,
        "report": report_summary,
        "builds": build_summaries,
    }
