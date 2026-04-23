from __future__ import annotations

import ast
from collections import defaultdict
import copy
from pathlib import Path
import shutil
from typing import Any

import json

from .annotator import apply_glossary, validate_annotation
from .io_utils import ensure_dir, read_json, read_text, write_json, write_text
from .japanese import ConsensusAnnotator, categorize_review_candidate
from .models import Record
from .progress import NullProgress
from .snbt import dump as dump_snbt
from .snbt import parse as parse_snbt
from .sources import (
    ingest_sources_with_report,
    load_manifest,
    manifest_include_generated_default,
    manifest_include_pending_default,
)


INGESTED_PATH = Path("build/ingested_records.json")
ANNOTATED_PATH = Path("build/annotated_records.json")
SOURCE_REPORT_PATH = Path("build/reports/source_report.json")
REVIEW_REPORT_PATH = Path("build/reports/review_report.json")
RESOURCEPACK_PATH = Path("build/resourcepack")
STAGED_INSTANCE_PATH = Path("build/staged")
GENERATED_REVIEW_PATH = Path("review/generated/review_candidates.json")
GENERATED_REVIEW_BY_CATEGORY_PATH = Path("review/generated/review_candidates_by_category.json")
GENERATED_REVIEW_REPORT_PATH = Path("review/generated/review_report.json")
GENERATED_LLM_SUGGESTIONS_PATH = Path("review/generated/llm_suggestions.json")
GENERATED_LLM_REVIEW_RESULTS_PATH = Path("review/generated/llm_review_results.json")
GENERATED_MANUAL_FIX_SUGGESTIONS_PATH = Path("review/generated/manual_fix_suggestions.json")


def _review_entry_map(workspace: Path) -> dict[str, dict[str, Any]]:
    return read_json(workspace / "review" / "review_entries.json", default={})


def _suggestion_entry_map(workspace: Path) -> dict[str, dict[str, Any]]:
    generated = read_json(workspace / GENERATED_LLM_SUGGESTIONS_PATH, default={})
    manual_fix_generated = read_json(workspace / GENERATED_MANUAL_FIX_SUGGESTIONS_PATH, default={})
    manual = read_json(workspace / "review" / "suggestions.json", default={})
    return {**generated, **manual_fix_generated, **manual}


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


def _selected_source_ids_from_previous_report(workspace: Path) -> list[str]:
    report = read_json(workspace / SOURCE_REPORT_PATH, default={})
    return list(report.get("failed_source_ids", []))


def ingest_with_progress(
    manifest_path: Path,
    workspace: Path,
    progress: NullProgress | None,
    *,
    source_ids: list[str] | None = None,
    failed_only: bool = False,
) -> dict[str, Any]:
    manifest, sources = load_manifest(manifest_path)
    reporter = progress or NullProgress()
    reporter.stage("Ingest", manifest_path.name)
    selected_ids: list[str] = list(source_ids or [])
    if failed_only:
        selected_ids.extend(_selected_source_ids_from_previous_report(workspace))
    selected_id_set = set(selected_ids)
    if source_ids or failed_only:
        sources = [source for source in sources if source.id in selected_id_set]
        reporter.note("FILTER", f"selected sources={len(sources)}")

    records, source_results = ingest_sources_with_report(sources, progress=reporter)
    errors = [
        {"source_id": str(item["source_id"]), "error": str(item["error"])}
        for item in source_results
        if item.get("status") == "error"
    ]
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
        "source_results": source_results,
        "failed_source_ids": [str(item["source_id"]) for item in source_results if item.get("status") == "error"],
        "failed_sources": [item for item in source_results if item.get("status") == "error"],
        "ok_source_ids": [str(item["source_id"]) for item in source_results if item.get("status") == "ok"],
        "selected_source_ids": sorted(selected_id_set),
        "failed_only": failed_only,
        "preserved_previous_records": preserved_previous_records,
        "pack": manifest.get("pack", {}),
    }
    write_json(workspace / SOURCE_REPORT_PATH, report)
    reporter.done("Ingest", f"records={len(records)} errors={len(errors)}")
    return report


def ingest(
    manifest_path: Path,
    workspace: Path,
    progress: NullProgress | None = None,
    *,
    source_ids: list[str] | None = None,
    failed_only: bool = False,
) -> dict[str, Any]:
    return ingest_with_progress(manifest_path, workspace, progress, source_ids=source_ids, failed_only=failed_only)


def annotate(workspace: Path, progress: NullProgress | None = None) -> dict[str, Any]:
    ingested = _read_records(workspace / INGESTED_PATH)
    glossary_terms = _glossary_terms(workspace)
    review_entries = _review_entry_map(workspace)
    suggestion_entries = _suggestion_entry_map(workspace)
    auto_annotator = ConsensusAnnotator()
    reporter = progress or NullProgress()
    reporter.stage("Annotate", f"{len(ingested)} records")
    annotated_records: list[Record] = []
    generated_review_candidates: dict[str, dict[str, Any]] = {}
    generated_review_candidates_by_category: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    counts = defaultdict(int)

    for index, record in enumerate(ingested, start=1):
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
                content_type=record.content_type,
                output_kind=record.output_kind,
                output_path=record.output_path,
                metadata=dict(record.metadata),
            )
        )
        counts[review_status] += 1
        reporter.meter("Annotate", index, len(ingested), detail=record.record_id, counts=counts)

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
    reporter.done(
        "Annotate",
        " ".join([f"records={len(annotated_records)}"] + [f"{key}={value}" for key, value in sorted(counts.items())]),
    )
    return summary


def report(workspace: Path, progress: NullProgress | None = None) -> dict[str, Any]:
    records = _read_records(workspace / ANNOTATED_PATH)
    reporter = progress or NullProgress()
    reporter.stage("Report", f"{len(records)} records")
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
    reporter.done(
        "Report",
        f"pending={report_payload['pending_count']} review={report_payload['review_candidate_count']} issues={report_payload['issue_count']}",
    )
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


def _build_target_layout(manifest_path: Path) -> str:
    manifest, _ = load_manifest(manifest_path)
    return str(manifest.get("build", {}).get("target_layout", "resourcepack"))


def _build_export_mode(manifest_path: Path) -> str:
    manifest, _ = load_manifest(manifest_path)
    return str(manifest.get("build", {}).get("export_mode", "overwrite"))


def _build_export_locale(manifest_path: Path) -> str:
    manifest, _ = load_manifest(manifest_path)
    return str(manifest.get("build", {}).get("export_locale", "ja_rubi"))


def _set_nested_value(payload: Any, path: list[str], value: str) -> None:
    current = payload
    for part in path[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    last = path[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def _write_pack_meta(path: Path, *, description: str, pack_format: int, pack_id: str | None = None) -> None:
    payload: dict[str, Any] = {
        "pack": {
            "description": description,
            "pack_format": pack_format,
        }
    }
    if pack_id:
        payload["id"] = pack_id
    write_json(path / "pack.mcmeta", payload)


def _write_pack_meta_with_language(
    path: Path,
    *,
    description: str,
    pack_format: int,
    locale: str,
    pack_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "pack": {
            "description": description,
            "pack_format": pack_format,
        },
        "language": {
            locale: {
                "name": locale,
                "region": "Generated",
                "bidirectional": False,
            }
        },
    }
    if pack_id:
        payload["id"] = pack_id
    write_json(path / "pack.mcmeta", payload)


def _replace_locale_in_output_path(path: str, locale: str) -> str:
    parts = list(Path(path).parts)
    if "lang" in parts:
        index = parts.index("lang")
        if index + 1 < len(parts):
            suffix = Path(parts[index + 1]).suffix
            parts[index + 1] = f"{locale}{suffix}"
            return Path(*parts).as_posix()
    return path


def _record_output_root(record: Record, *, export_mode: str) -> str:
    if export_mode == "full-pack":
        return str(record.metadata.get("full_pack_output_root") or "resourcepack")
    return str(record.metadata.get("output_root", "resourcepack"))


def _rewrite_legacy_ftbquests_payload(payload: Any, record: Record) -> None:
    current = payload
    for part in list(record.metadata.get("rewrite_path", [])):
        if not isinstance(current, dict):
            return
        if "." in part:
            list_name, object_id = part.split(".", 1)
            candidates = current.get(list_name, [])
            if not isinstance(candidates, list):
                return
            current = next(
                (
                    item
                    for item in candidates
                    if isinstance(item, dict) and str(item.get("id")) == object_id
                ),
                None,
            )
        else:
            current = current.get(part)
        if current is None:
            return
    _rewrite_legacy_ftbquests_value(current, record)


def _rewrite_legacy_ftbquests_value(node: Any, record: Record) -> bool:
    if not isinstance(node, dict):
        return False
    field_name = str(record.metadata["rewrite_field"])
    translation_key = str(record.metadata["translation_key"])
    value = node.get(field_name)
    if isinstance(value, str):
        node[field_name] = "{" + translation_key + "}"
        return True
    if not isinstance(value, list):
        return False
    target_list_index = record.metadata.get("rewrite_list_index")
    target_rich_index = record.metadata.get("rewrite_rich_index")
    translated_index = 0
    for item_index, item in enumerate(value):
        if not isinstance(item, str):
            continue
        if not item or (item.startswith("{") and item.endswith("}")):
            continue
        if translated_index != target_list_index:
            translated_index += 1
            continue
        if target_rich_index is None:
            node[field_name][item_index] = "{" + translation_key + "}"
            return True
        rich_payload = ast.literal_eval(item.replace("true", "True").replace("false", "False"))
        translated_rich_index = 0
        for rich_item_index, rich_item in enumerate(rich_payload):
            if rich_item == "":
                continue
            if translated_rich_index != target_rich_index:
                translated_rich_index += 1
                continue
            if isinstance(rich_item, str):
                rich_payload[rich_item_index] = {"translate": translation_key}
            else:
                rich_payload[rich_item_index].pop("text", None)
                rich_payload[rich_item_index]["translate"] = translation_key
            if rich_payload and rich_payload[0] != "":
                rich_payload = [""] + rich_payload
            node[field_name][item_index] = json.dumps(rich_payload, ensure_ascii=False)
            return True
    return False


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
    export_mode: str | None = None,
    export_locale: str | None = None,
    progress: NullProgress | None = None,
) -> dict[str, Any]:
    annotated_path = workspace / ANNOTATED_PATH
    if not annotated_path.exists():
        raise FileNotFoundError(f"annotated records not found at {annotated_path}")
    records = _read_records(annotated_path)
    reporter = progress or NullProgress()
    reporter.stage("Build", manifest_path.name)
    resolved_include_generated = resolve_include_generated(manifest_path, include_generated)
    resolved_include_pending = resolve_include_pending(manifest_path, include_pending)
    target_layout = _build_target_layout(manifest_path)
    resolved_export_mode = export_mode or _build_export_mode(manifest_path)
    resolved_export_locale = export_locale or _build_export_locale(manifest_path)
    manifest_payload, _ = load_manifest(manifest_path)
    source_portability = [
        {
            "source_id": source.get("id"),
            "type": source.get("type"),
            "portability": source.get("portability", "portable"),
            "overwrite_destination": source.get("output_root"),
            "full_pack_destination": (
                source.get("full_pack_output_root") or "resourcepack"
                if source.get("portability", "portable") == "portable"
                else None
            ),
        }
        for source in manifest_payload.get("sources", [])
    ]
    full_pack_blockers = [
        item
        for item in source_portability
        if resolved_export_mode == "full-pack" and item["portability"] != "portable"
    ]
    if full_pack_blockers:
        raise ValueError(
            "full-pack export is blocked by overwrite-only sources: "
            + ", ".join(f"{item['source_id']} ({item['type']})" for item in full_pack_blockers)
        )

    output_root = workspace / (
        RESOURCEPACK_PATH if resolved_export_mode == "full-pack" or target_layout != "instance" else STAGED_INSTANCE_PATH
    )
    instance_like = target_layout == "instance" and resolved_export_mode == "overwrite"
    if output_root.exists():
        shutil.rmtree(output_root)
    ensure_dir(output_root)

    allowed_statuses = {"approved"}
    if resolved_include_generated:
        allowed_statuses.update({"generated", "suggested"})
    if resolved_include_pending:
        allowed_statuses.add("pending")

    if instance_like:
        manifest_pack = _pack_meta(manifest_path)["pack"]
        resourcepack_root = output_root / "resourcepack"
        ensure_dir(resourcepack_root)
        _write_pack_meta(
            resourcepack_root,
            description=str(manifest_pack["description"]),
            pack_format=int(manifest_pack["pack_format"]),
        )
    else:
        manifest_pack = _pack_meta(manifest_path)["pack"]
        if resolved_export_mode == "full-pack":
            _write_pack_meta_with_language(
                output_root,
                description=str(manifest_pack["description"]),
                pack_format=int(manifest_pack["pack_format"]),
                locale=resolved_export_locale,
            )
        else:
            write_json(output_root / "pack.mcmeta", _pack_meta(manifest_path))

    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    grouped_lang_files: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    grouped_json: dict[tuple[str, str], dict[str, Any]] = {}
    grouped_snbt: dict[tuple[str, str], Any] = {}
    grouped_text: dict[tuple[str, str], str] = {}
    ordered_records = sorted(records, key=lambda record: int(record.metadata.get("merge_priority", 0)))
    for record in ordered_records:
        if record.review_status not in allowed_statuses:
            continue
        if not instance_like and record.content_type == "lang_json":
            grouped[record.namespace][record.key] = record.annotated_text
            continue
        output_path = record.output_path or f"assets/{record.namespace}/lang/ja_jp.json"
        output_root_key = _record_output_root(record, export_mode=resolved_export_mode)
        if resolved_export_mode == "full-pack" and record.content_type == "lang_json":
            output_path = _replace_locale_in_output_path(output_path, resolved_export_locale)
        if record.content_type == "lang_json":
            grouped_lang_files[(output_root_key, output_path)][record.key] = record.annotated_text
        elif record.content_type == "ftbquests_legacy_inline":
            lang_output_root = "resourcepack" if resolved_export_mode == "full-pack" else str(
                record.metadata.get("generated_lang_output_root", output_root_key)
            )
            lang_output_path = str(record.metadata.get("generated_lang_output_path", output_path))
            if resolved_export_mode == "full-pack":
                lang_output_path = _replace_locale_in_output_path(lang_output_path, resolved_export_locale)
            grouped_lang_files[(lang_output_root, lang_output_path)][str(record.metadata["translation_key"])] = record.annotated_text
            rewritten_root = str(record.metadata.get("rewritten_output_root", output_root_key))
            rewritten_path = str(record.metadata.get("rewritten_output_path", output_path))
            if resolved_export_mode == "full-pack":
                rewritten_root = str(record.metadata.get("full_pack_rewrite_root") or "resourcepack")
            key = (rewritten_root, rewritten_path)
            template = grouped_snbt.get(key)
            if template is None:
                source_file = Path(str(record.source_origin).split(":", 1)[0])
                template = parse_snbt(source_file.read_text(encoding="utf-8"))
                grouped_snbt[key] = template
            _rewrite_legacy_ftbquests_payload(template, record)
        elif record.content_type in {"patchouli_json", "json_strings"}:
            key = (output_root_key, output_path)
            if key not in grouped_json:
                grouped_json[key] = copy.deepcopy(record.metadata.get("template_payload", {}))
            json_path = list(record.metadata.get("json_path", []))
            if json_path:
                _set_nested_value(grouped_json[key], json_path, record.annotated_text)
        elif record.content_type == "ftbquests_locale_snbt":
            key = (
                output_root_key,
                _replace_locale_in_output_path(output_path, resolved_export_locale) if resolved_export_mode == "full-pack" else output_path,
            )
            if key not in grouped_snbt:
                grouped_snbt[key] = copy.deepcopy(record.metadata.get("template_payload", {}))
            json_path = list(record.metadata.get("json_path", []))
            if json_path:
                _set_nested_value(grouped_snbt[key], json_path, record.annotated_text)
        else:
            grouped_text[(output_root_key, output_path)] = record.annotated_text

    written_files: list[str] = []
    written_output_kinds: dict[str, int] = defaultdict(int)
    if not instance_like:
        for index, (namespace, mapping) in enumerate(sorted(grouped.items()), start=1):
            reporter.item("NAMESPACE", index, len(grouped), namespace, f"entries={len(mapping)}")
            locale = resolved_export_locale if resolved_export_mode == "full-pack" else "ja_jp"
            path = output_root / "assets" / namespace / "lang" / f"{locale}.json"
            write_json(path, mapping)
            written_files.append(str(path.relative_to(workspace)))
        total_extra_files = len(grouped_lang_files) + len(grouped_json) + len(grouped_snbt) + len(grouped_text)
        for (output_root_key, output_path), payload in sorted(grouped_lang_files.items()):
            reporter.item("FILE", len(written_files) + 1, total_extra_files, output_path, output_root_key)
            target_path = output_root / output_path if output_root_key == "resourcepack" else output_root / output_root_key / output_path
            write_json(target_path, payload)
            written_files.append(str(target_path.relative_to(workspace)))
        for (output_root_key, output_path), payload in sorted(grouped_json.items()):
            reporter.item("FILE", len(written_files) + 1, total_extra_files, output_path, output_root_key)
            target_path = output_root / output_path if output_root_key == "resourcepack" else output_root / output_root_key / output_path
            write_json(target_path, payload)
            written_files.append(str(target_path.relative_to(workspace)))
        for (output_root_key, output_path), payload in sorted(grouped_snbt.items()):
            reporter.item("FILE", len(written_files) + 1, total_extra_files, output_path, output_root_key)
            target_path = output_root / output_path if output_root_key == "resourcepack" else output_root / output_root_key / output_path
            write_text(target_path, dump_snbt(payload) + "\n")
            written_files.append(str(target_path.relative_to(workspace)))
        for (output_root_key, output_path), text in sorted(grouped_text.items()):
            reporter.item("FILE", len(written_files) + 1, total_extra_files, output_path, output_root_key)
            target_path = output_root / output_path if output_root_key == "resourcepack" else output_root / output_root_key / output_path
            write_text(target_path, text)
            written_files.append(str(target_path.relative_to(workspace)))
    else:
        openloader_roots: set[str] = set()
        total_files = len(grouped_lang_files) + len(grouped_json) + len(grouped_snbt) + len(grouped_text)
        write_index = 0
        for (output_root_key, output_path), mapping in sorted(grouped_lang_files.items()):
            write_index += 1
            reporter.item("FILE", write_index, total_files, output_path, output_root_key)
            target_path = output_root / output_root_key / output_path
            write_json(target_path, mapping)
            written_files.append(str(target_path.relative_to(workspace)))
            written_output_kinds[output_root_key] += 1
            if output_root_key.startswith("config/openloader/resources/"):
                openloader_roots.add(output_root_key)
        for (output_root_key, output_path), payload in sorted(grouped_json.items()):
            write_index += 1
            reporter.item("FILE", write_index, total_files, output_path, output_root_key)
            target_path = output_root / output_root_key / output_path
            write_json(target_path, payload)
            written_files.append(str(target_path.relative_to(workspace)))
            written_output_kinds[output_root_key] += 1
            if output_root_key.startswith("config/openloader/resources/"):
                openloader_roots.add(output_root_key)
        for (output_root_key, output_path), payload in sorted(grouped_snbt.items()):
            write_index += 1
            reporter.item("FILE", write_index, total_files, output_path, output_root_key)
            target_path = output_root / output_root_key / output_path
            write_text(target_path, dump_snbt(payload) + "\n")
            written_files.append(str(target_path.relative_to(workspace)))
            written_output_kinds[output_root_key] += 1
            if output_root_key.startswith("config/openloader/resources/"):
                openloader_roots.add(output_root_key)
        for (output_root_key, output_path), text in sorted(grouped_text.items()):
            write_index += 1
            reporter.item("FILE", write_index, total_files, output_path, output_root_key)
            target_path = output_root / output_root_key / output_path
            write_text(target_path, text)
            written_files.append(str(target_path.relative_to(workspace)))
            written_output_kinds[output_root_key] += 1
            if output_root_key.startswith("config/openloader/resources/"):
                openloader_roots.add(output_root_key)
        for openloader_root in sorted(openloader_roots):
            target_root = output_root / openloader_root
            if not (target_root / "pack.mcmeta").exists():
                if resolved_export_mode == "full-pack":
                    continue
                _write_pack_meta(
                    target_root,
                    description=f"Rubi GTO generated override for {Path(openloader_root).name}",
                    pack_format=int(manifest_pack["pack_format"]),
                    pack_id=Path(openloader_root).name,
                )

    build_summary = {
        "namespace_count": len(grouped) if not instance_like else len(
            {
                record.namespace
                for record in records
                if record.review_status in allowed_statuses
            }
        ),
        "written_files": sorted(written_files),
        "include_generated": resolved_include_generated,
        "include_pending": resolved_include_pending,
        "target_layout": target_layout,
        "export_mode": resolved_export_mode,
        "export_locale": resolved_export_locale,
        "written_output_kinds": dict(sorted(written_output_kinds.items())),
        "portability_report": source_portability,
        "full_pack_blockers": full_pack_blockers,
    }
    write_json(workspace / "build" / "reports" / "build_report.json", build_summary)
    reporter.done("Build", f"namespaces={len(grouped)} files={len(written_files)}")
    return build_summary


def run(
    manifest_path: Path,
    workspace: Path,
    *,
    include_generated: bool | None = None,
    include_pending: bool | None = None,
    export_mode: str | None = None,
    export_locale: str | None = None,
    progress: NullProgress | None = None,
    source_ids: list[str] | None = None,
    failed_only: bool = False,
) -> dict[str, Any]:
    reporter = progress or NullProgress()
    reporter.stage("Run", manifest_path.name)
    ingest_summary = ingest(
        manifest_path,
        workspace,
        progress=reporter,
        source_ids=source_ids,
        failed_only=failed_only,
    )
    annotate_summary = annotate(workspace, progress=reporter)
    review_summary = report(workspace, progress=reporter)
    build_summary = build(
        manifest_path,
        workspace,
        include_generated=include_generated,
        include_pending=include_pending,
        export_mode=export_mode,
        export_locale=export_locale,
        progress=reporter,
    )
    summary = {
        "ingest": ingest_summary,
        "annotate": annotate_summary,
        "report": {
            "pending_count": review_summary["pending_count"],
            "issue_count": review_summary["issue_count"],
        },
        "build": build_summary,
    }
    reporter.done("Run", f"manifest={manifest_path.name}")
    return summary
