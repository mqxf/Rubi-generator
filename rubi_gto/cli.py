from __future__ import annotations

import argparse
import json
from pathlib import Path

from .llm_review import llm_review
from .pipeline import annotate, build, ingest, report, run
from .progress import ConsoleProgress
from .sources import (
    build_instance_manifest,
    build_instance_content_report,
    build_local_manifest,
    build_mod_archive_manifest,
    build_packwiz_translation_report,
    discover_mod_archives,
    load_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rubi GTO pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("ingest", "build", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--workspace", type=Path, default=Path("."))
        if name in ("ingest", "run"):
            command.add_argument("--source-id", dest="source_ids", action="append")
            command.add_argument("--failed-only", action="store_true")
        if name in ("build", "run"):
            include_group = command.add_mutually_exclusive_group()
            include_group.add_argument("--include-generated", dest="include_generated", action="store_true", default=None)
            include_group.add_argument("--approved-only", dest="include_generated", action="store_false")
            pending_group = command.add_mutually_exclusive_group()
            pending_group.add_argument("--include-pending", dest="include_pending", action="store_true", default=None)
            pending_group.add_argument("--exclude-pending", dest="include_pending", action="store_false")

    for name in ("annotate", "report"):
        command = subparsers.add_parser(name)
        command.add_argument("--workspace", type=Path, default=Path("."))

    llm_review_command = subparsers.add_parser("llm-review")
    llm_review_command.add_argument("--workspace", type=Path, default=Path("."))
    llm_review_command.add_argument("--model", default="gpt-4.1-mini")
    llm_review_command.add_argument("--reasoning-effort")
    llm_review_command.add_argument("--max-output-tokens", type=int, default=256)
    llm_review_command.add_argument("--limit", type=int)
    llm_review_command.add_argument("--base-url")
    llm_review_command.add_argument("--category", dest="categories", action="append")
    llm_review_command.add_argument("--record-id", dest="record_ids", action="append")

    discover = subparsers.add_parser("discover-local")
    discover.add_argument("--search-root", type=Path, required=True)
    discover.add_argument("--output", type=Path)
    discover.add_argument("--pack-description", default="Rubi GTO generated Japanese pack")
    discover.add_argument("--pack-format", type=int, default=34)
    discover.add_argument("--include-vanilla", action="store_true")
    discover.add_argument("--minecraft-version", default="1.20.1")
    discover.add_argument("--locale", default="ja_jp")
    discover.add_argument("--append-manifest", type=Path)
    discover.add_argument("--mods-dir", type=Path, action="append")

    discover_archives = subparsers.add_parser("discover-mod-archives")
    discover_archives.add_argument("--mods-dir", type=Path, required=True)
    discover_archives.add_argument("--output", type=Path)
    discover_archives.add_argument("--pack-description", default="Rubi GTO generated Japanese pack")
    discover_archives.add_argument("--pack-format", type=int, default=34)
    discover_archives.add_argument("--include-vanilla", action="store_true")
    discover_archives.add_argument("--minecraft-version", default="1.20.1")
    discover_archives.add_argument("--locale", default="ja_jp")
    discover_archives.add_argument("--source-id", default="mods-folder")

    discover_packwiz = subparsers.add_parser("discover-packwiz")
    discover_packwiz.add_argument("--pack-root", type=Path, required=True)
    discover_packwiz.add_argument("--search-root", type=Path, required=True)
    discover_packwiz.add_argument("--output", type=Path)

    discover_instance = subparsers.add_parser("discover-instance")
    discover_instance.add_argument("--instance-root", type=Path, required=True)
    discover_instance.add_argument("--output", type=Path)
    discover_instance.add_argument("--manifest-output", type=Path)
    discover_instance.add_argument("--pack-description", default="Rubi GTO generated Japanese pack")
    discover_instance.add_argument("--pack-format", type=int, default=34)
    discover_instance.add_argument("--include-vanilla", action="store_true")
    discover_instance.add_argument("--minecraft-version", default="1.20.1")
    discover_instance.add_argument("--locale", default="ja_jp")

    run_instance = subparsers.add_parser("run-instance")
    run_instance.add_argument("--instance-root", type=Path, required=True)
    run_instance.add_argument("--workspace", type=Path, default=Path("."))
    run_instance.add_argument("--include-vanilla", action="store_true")
    run_instance.add_argument("--minecraft-version", default="1.20.1")
    run_instance.add_argument("--locale", default="ja_jp")
    run_instance.add_argument("--source-id", dest="source_ids", action="append")
    run_instance.add_argument("--failed-only", action="store_true")
    include_group = run_instance.add_mutually_exclusive_group()
    include_group.add_argument("--include-generated", dest="include_generated", action="store_true", default=None)
    include_group.add_argument("--approved-only", dest="include_generated", action="store_false")
    pending_group = run_instance.add_mutually_exclusive_group()
    pending_group.add_argument("--include-pending", dest="include_pending", action="store_true", default=None)
    pending_group.add_argument("--exclude-pending", dest="include_pending", action="store_false")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace.resolve() if hasattr(args, "workspace") else None
    progress = ConsoleProgress()

    if args.command == "ingest":
        payload = ingest(
            args.manifest.resolve(),
            workspace,
            progress=progress,
            source_ids=args.source_ids,
            failed_only=args.failed_only,
        )
    elif args.command == "annotate":
        payload = annotate(workspace, progress=progress)
    elif args.command == "report":
        payload = report(workspace, progress=progress)
    elif args.command == "llm-review":
        payload = llm_review(
            workspace,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            categories=args.categories,
            record_ids=args.record_ids,
            limit=args.limit,
            base_url=args.base_url,
            max_output_tokens=args.max_output_tokens,
            progress=progress,
        )
    elif args.command == "build":
        payload = build(
            args.manifest.resolve(),
            workspace,
            include_generated=args.include_generated,
            include_pending=args.include_pending,
            progress=progress,
        )
    elif args.command == "discover-local":
        extra_sources = None
        if args.append_manifest:
            raw_extra_manifest, _ = load_manifest(args.append_manifest.resolve())
            extra_sources = list(raw_extra_manifest.get("sources", []))
        if args.mods_dir:
            extra_sources = list(extra_sources or [])
            for mods_dir in args.mods_dir:
                extra_sources.extend(discover_mod_archives(mods_dir.resolve()).get("sources", []))
        payload = build_local_manifest(
            args.search_root.resolve(),
            pack_description=args.pack_description,
            pack_format=args.pack_format,
            include_vanilla=args.include_vanilla,
            minecraft_version=args.minecraft_version,
            locale=args.locale,
            extra_sources=extra_sources,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "discover-mod-archives":
        payload = build_mod_archive_manifest(
            args.mods_dir.resolve(),
            pack_description=args.pack_description,
            pack_format=args.pack_format,
            include_vanilla=args.include_vanilla,
            minecraft_version=args.minecraft_version,
            locale=args.locale,
            source_id=args.source_id,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "discover-packwiz":
        payload = build_packwiz_translation_report(args.pack_root.resolve(), args.search_root.resolve())
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "discover-instance":
        payload = build_instance_content_report(
            args.instance_root.resolve(),
            pack_description=args.pack_description,
            pack_format=args.pack_format,
            include_vanilla=args.include_vanilla,
            minecraft_version=args.minecraft_version,
            locale=args.locale,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.manifest_output:
            args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_output.write_text(
                json.dumps(payload["manifest"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    elif args.command == "run-instance":
        runtime_manifest = build_instance_manifest(
            args.instance_root.resolve(),
            pack_description="Rubi GTO generated Japanese pack",
            pack_format=34,
            include_vanilla=args.include_vanilla,
            minecraft_version=args.minecraft_version,
            locale=args.locale,
        )
        runtime_manifest_path = workspace / "build" / "reports" / "instance_runtime_manifest.json"
        runtime_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_manifest_path.write_text(
            json.dumps(runtime_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payload = run(
            runtime_manifest_path,
            workspace,
            include_generated=args.include_generated,
            include_pending=args.include_pending,
            progress=progress,
            source_ids=args.source_ids,
            failed_only=args.failed_only,
        )
    else:
        payload = run(
            args.manifest.resolve(),
            workspace,
            include_generated=args.include_generated,
            include_pending=args.include_pending,
            progress=progress,
            source_ids=args.source_ids,
            failed_only=args.failed_only,
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
