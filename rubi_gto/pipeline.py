from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import shutil
from typing import Any

from .annotator import apply_glossary, validate_annotation
from .io_utils import ensure_dir, read_json, write_json
from .japanese import ConsensusAnnotator, categorize_review_candidate
from .models import Record
from .sources import (
    ingest_sources,
    load_manifest,
    manifest_include_generated_default,
    manifest_include_pending_default,
)


INGESTED_PATH = Path("build/ingested_records.json")
ANNOTATED_PATH = Path("build/annotated_records.json")
SOURCE_REPORT_PATH = Path("build/reports/source_report.json")
REVIEW_REPORT_PATH = Path("build/reports/review_report.json")
RESOURCEPACK_PATH = Path("build/resourcepack")
GENERATED_REVIEW_PATH = Path("review/generated/review_candidates.json")
GENERATED_REVIEW_BY_CATEGORY_PATH = Path("review/generated/review_candidates_by_category.json")
GENERATED_REVIEW_REPORT_PATH = Path("review/generated/review_report.json")
GENERATED_LLM_SUGGESTIONS_PATH = Path("review/generated/llm_suggestions.json")
GENERATED_LLM_REVIEW_RESULTS_PATH = Path("review/generated/llm_review_results.json")


def _review_entry_map(workspace: Path) -> dict[str, dict[str, Any]]:
    return read_json(workspace / "review" / "review_entries.json", default={})


def _suggestion_entry_map(workspace: Path) -> dict[str, dict[str, Any]]:
    generated = read_json(workspace / GENERATED_LLM_SUGGESTIONS_PATH, default={})
    manual = read_json(workspace / "review" / "suggestions.json", default={})
    return {**generated, **manual}


def _glossary_terms(workspace: Path) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    legacy_payload = read_json(workspace / "review" / "glossary.json", default={"terms": []})
    terms.extend(list(legacy_payload.get("terms", [])))

    glossary_dir = workspace / "review" / "glossaries"
    if glossary_dir.exists():
        for path in sorted(glossary_dir.glob("*.json")):
            payload = read_json(path, default={"terms": []})
            terms.extend(list(payload.get("terms", [])))
    return terms


def _write_records(path: Path, records: list[Record]) -> None:
    write_json(path, [record.to_dict() for record in records])


def _read_records(path: Path) -> list[Record]:
    payload = read_json(path, default=[])
    return [Record.from_dict(item) for item in payload]


def ingest(manifest_path: Path, workspace: Path) -> dict[str, Any]:
    manifest, sources = load_manifest(manifest_path)
    records, errors = ingest_sources(sources)
    ingested_path = workspace / INGESTED_PATH
    preserved_previous_records = False
    if records or not errors or not ingested_path.exists():
        _write_records(ingested_path, records)
    else:
        preserved_previous_records = True
    report = {
        "manifest": str(manifest_path),
        "record_count": len(records),
        "source_count": len(sources),
        "errors": errors,
        "preserved_previous_records": preserved_previous_records,
        "pack": manifest.get("pack", {}),
    }
    write_json(workspace / SOURCE_REPORT_PATH, report)
    return report


def annotate(workspace: Path) -> dict[str, Any]:
    ingested = _read_records(workspace / INGESTED_PATH)
    glossary_terms = _glossary_terms(workspace)
    review_entries = _review_entry_map(workspace)
    suggestion_entries = _suggestion_entry_map(workspace)
    auto_annotator = ConsensusAnnotator()
    annotated_records: list[Record] = []
    generated_review_candidates: dict[str, dict[str, Any]] = {}
    generated_review_candidates_by_category: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    counts = defaultdict(int)

    for record in ingested:
        review_entry = review_entries.get(record.record_id, {})
        suggestion_entry = suggestion_entries.get(record.record_id, {})
        annotated_text = record.source_text
        notes = review_entry.get("notes")
        if review_entry.get("override_text"):
            annotated_text = review_entry["override_text"]
        elif suggestion_entry.get("annotated_text"):
            annotated_text = suggestion_entry["annotated_text"]
            if suggestion_entry.get("source"):
                notes = f"suggestion:{suggestion_entry['source']}"
        else:
            annotated_text, _ = apply_glossary(record.source_text, glossary_terms)
            decision = auto_annotator.annotate_with_review(annotated_text)
            annotated_text = decision.annotated_text
            if decision.status == "generated" and annotated_text != record.source_text and not notes and auto_annotator.available:
                notes = "auto:fugashi+unidic,sudachi-full"
            if decision.status == "review":
                review_category = categorize_review_candidate(record.source_text, decision.review_reason, decision.review_options)
                generated_review_candidates[record.record_id] = {
                    "id": record.record_id,
                    "namespace": record.namespace,
                    "key": record.key,
                    "source_text": record.source_text,
                    "current_text": annotated_text,
                    "reason": decision.review_reason,
                    "category": review_category,
                    "options": decision.review_options or [],
                    "source_origin": record.source_origin,
                    "source_id": record.source_id,
                }
                generated_review_candidates_by_category[review_category][record.record_id] = generated_review_candidates[
                    record.record_id
                ]

        issues = validate_annotation(record.source_text, annotated_text)
        approved = bool(review_entry.get("approved"))
        review_status = "approved" if approved else "generated"
        if issues:
            review_status = "pending" if not approved else "approved"
        elif record.record_id in generated_review_candidates and not approved:
            review_status = "pending"
        elif suggestion_entry.get("annotated_text") and not approved:
            review_status = "suggested"
        if annotated_text == record.source_text and not approved:
            review_status = "pending"

        annotated_records.append(
            Record(
                namespace=record.namespace,
                key=record.key,
                source_text=record.source_text,
                annotated_text=annotated_text,
                source_origin=record.source_origin,
                source_id=record.source_id,
                review_status=review_status,
                issues=issues,
                notes=notes,
            )
        )
        counts[review_status] += 1

    _write_records(workspace / ANNOTATED_PATH, annotated_records)
    write_json(
        workspace / GENERATED_REVIEW_PATH,
        {
            "candidate_count": len(generated_review_candidates),
            "candidates": generated_review_candidates,
        },
    )
    write_json(
        workspace / GENERATED_REVIEW_BY_CATEGORY_PATH,
        {
            "category_counts": {
                category: len(entries) for category, entries in sorted(generated_review_candidates_by_category.items())
            },
            "categories": {category: entries for category, entries in sorted(generated_review_candidates_by_category.items())},
        },
    )
    summary = {
        "record_count": len(annotated_records),
        "status_counts": dict(sorted(counts.items())),
    }
    return summary


def report(workspace: Path) -> dict[str, Any]:
    records = _read_records(workspace / ANNOTATED_PATH)
    review_candidates_payload = read_json(workspace / GENERATED_REVIEW_PATH, default={"candidate_count": 0, "candidates": {}})
    review_by_category_payload = read_json(
        workspace / GENERATED_REVIEW_BY_CATEGORY_PATH,
        default={"category_counts": {}, "categories": {}},
    )
    pending = [record.to_dict() for record in records if record.review_status == "pending"]
    generated = [record.to_dict() for record in records if record.review_status == "generated"]
    suggested = [record.to_dict() for record in records if record.review_status == "suggested"]
    issues = [record.to_dict() for record in records if record.issues]
    report_payload = {
        "record_count": len(records),
        "pending_count": len(pending),
        "generated_count": len(generated),
        "suggested_count": len(suggested),
        "issue_count": len(issues),
        "review_candidate_count": int(review_candidates_payload.get("candidate_count", 0)),
        "review_category_counts": dict(review_by_category_payload.get("category_counts", {})),
        "pending_records": pending,
        "generated_records": generated,
        "suggested_records": suggested,
        "issue_records": issues,
    }
    write_json(workspace / REVIEW_REPORT_PATH, report_payload)
    write_json(workspace / GENERATED_REVIEW_REPORT_PATH, report_payload)
    return report_payload


def _pack_meta(manifest_path: Path) -> dict[str, Any]:
    manifest, _ = load_manifest(manifest_path)
    pack = manifest.get("pack", {})
    return {
        "pack": {
            "pack_format": int(pack.get("pack_format", 34)),
            "description": pack.get("description", "Rubi GTO generated pack"),
        }
    }


def resolve_include_generated(manifest_path: Path, include_generated: bool | None) -> bool:
    if include_generated is not None:
        return include_generated
    manifest, _ = load_manifest(manifest_path)
    return manifest_include_generated_default(manifest)


def resolve_include_pending(manifest_path: Path, include_pending: bool | None) -> bool:
    if include_pending is not None:
        return include_pending
    manifest, _ = load_manifest(manifest_path)
    return manifest_include_pending_default(manifest)


def build(
    manifest_path: Path,
    workspace: Path,
    *,
    include_generated: bool | None = None,
    include_pending: bool | None = None,
) -> dict[str, Any]:
    annotated_path = workspace / ANNOTATED_PATH
    if not annotated_path.exists():
        raise FileNotFoundError(f"annotated records not found at {annotated_path}")
    records = _read_records(annotated_path)
    resolved_include_generated = resolve_include_generated(manifest_path, include_generated)
    resolved_include_pending = resolve_include_pending(manifest_path, include_pending)

    output_root = workspace / RESOURCEPACK_PATH
    if output_root.exists():
        shutil.rmtree(output_root)
    ensure_dir(output_root)
    write_json(output_root / "pack.mcmeta", _pack_meta(manifest_path))

    allowed_statuses = {"approved"}
    if resolved_include_generated:
        allowed_statuses.update({"generated", "suggested"})
    if resolved_include_pending:
        allowed_statuses.add("pending")

    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    for record in records:
        if record.review_status not in allowed_statuses:
            continue
        grouped[record.namespace][record.key] = record.annotated_text

    written_files: list[str] = []
    for namespace, mapping in grouped.items():
        path = output_root / "assets" / namespace / "lang" / "ja_jp.json"
        write_json(path, mapping)
        written_files.append(str(path.relative_to(workspace)))

    build_summary = {
        "namespace_count": len(grouped),
        "written_files": sorted(written_files),
        "include_generated": resolved_include_generated,
        "include_pending": resolved_include_pending,
    }
    write_json(workspace / "build" / "reports" / "build_report.json", build_summary)
    return build_summary


def run(
    manifest_path: Path,
    workspace: Path,
    *,
    include_generated: bool | None = None,
    include_pending: bool | None = None,
) -> dict[str, Any]:
    ingest_summary = ingest(manifest_path, workspace)
    annotate_summary = annotate(workspace)
    review_summary = report(workspace)
    build_summary = build(
        manifest_path,
        workspace,
        include_generated=include_generated,
        include_pending=include_pending,
    )
    return {
        "ingest": ingest_summary,
        "annotate": annotate_summary,
        "report": {
            "pending_count": review_summary["pending_count"],
            "issue_count": review_summary["issue_count"],
        },
        "build": build_summary,
    }
