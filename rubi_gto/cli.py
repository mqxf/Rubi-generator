from __future__ import annotations

import argparse
import json
from pathlib import Path

from .llm_review import llm_review
from .pipeline import annotate, build, ingest, report, run
from .sources import discover_local_sources


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rubi GTO pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("ingest", "build", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--workspace", type=Path, default=Path("."))
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace.resolve() if hasattr(args, "workspace") else None

    if args.command == "ingest":
        payload = ingest(args.manifest.resolve(), workspace)
    elif args.command == "annotate":
        payload = annotate(workspace)
    elif args.command == "report":
        payload = report(workspace)
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
        )
    elif args.command == "build":
        payload = build(
            args.manifest.resolve(),
            workspace,
            include_generated=args.include_generated,
            include_pending=args.include_pending,
        )
    elif args.command == "discover-local":
        payload = {
            "pack": {
                "description": args.pack_description,
                "pack_format": args.pack_format,
            },
            "sources": discover_local_sources(args.search_root.resolve()),
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        payload = run(
            args.manifest.resolve(),
            workspace,
            include_generated=args.include_generated,
            include_pending=args.include_pending,
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
