#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$ROOT_DIR"
DEFAULT_MANIFEST="manifests/vanilla_only.json"
DEFAULT_MODEL="gpt-4.1-mini"

cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/rubi.sh first [manifest]
  ./scripts/rubi.sh run-instance [instance_root]
  ./scripts/rubi.sh run-gto-workflow [instance_root] [repo_root]
  ./scripts/rubi.sh rerun-source [manifest] <source_id...>
  ./scripts/rubi.sh rerun-failed [manifest]
  ./scripts/rubi.sh conflicts
  ./scripts/rubi.sh llm [llm-review args...]
  ./scripts/rubi.sh merge [manifest]
  ./scripts/rubi.sh final [manifest]
  ./scripts/rubi.sh discover-local [search_root] [output]
  ./scripts/rubi.sh discover-mod-archives [mods_dir] [output]
  ./scripts/rubi.sh discover-packwiz [pack_root] [search_root] [output]
  ./scripts/rubi.sh discover-instance [instance_root] [report_output] [manifest_output]
  ./scripts/rubi.sh discover-gto-workflow [instance_root] [repo_root] [manifest_output]

Defaults:
  manifest: manifests/vanilla_only.json
  llm model: gpt-4.1-mini
EOF
}

first_stage() {
  local manifest="${1:-$DEFAULT_MANIFEST}"
  python3 -m rubi_gto run --manifest "$manifest" --workspace "$WORKSPACE"
}

run_instance_stage() {
  local instance_root="${1:-../gto_repos/GregTech-Odyssey}"
  python3 -m rubi_gto run-instance --instance-root "$instance_root" --workspace "$WORKSPACE"
}

run_gto_workflow_stage() {
  local instance_root="${1:-../gto_repos/GregTech-Odyssey}"
  local repo_root="${2:-../gto_repos}"
  python3 -m rubi_gto run-gto-workflow --instance-root "$instance_root" --repo-root "$repo_root" --workspace "$WORKSPACE"
}

rerun_source_stage() {
  local manifest="$DEFAULT_MANIFEST"
  if [[ $# -gt 0 && -f "$1" ]]; then
    manifest="$1"
    shift
  fi
  if [[ $# -lt 1 ]]; then
    echo "rerun-source requires at least one source id" >&2
    exit 1
  fi
  local cmd=(python3 -m rubi_gto run --manifest "$manifest" --workspace "$WORKSPACE")
  while [[ $# -gt 0 ]]; do
    cmd+=(--source-id "$1")
    shift
  done
  "${cmd[@]}"
}

rerun_failed_stage() {
  local manifest="${1:-$DEFAULT_MANIFEST}"
  python3 -m rubi_gto run --manifest "$manifest" --workspace "$WORKSPACE" --failed-only
}

conflicts_stage() {
  python3 -m rubi_gto annotate --workspace "$WORKSPACE"
  python3 -m rubi_gto report --workspace "$WORKSPACE"
}

llm_stage() {
  if [[ $# -eq 0 ]]; then
    python3 -m rubi_gto llm-review --workspace "$WORKSPACE" --model "$DEFAULT_MODEL"
  else
    python3 -m rubi_gto llm-review --workspace "$WORKSPACE" "$@"
  fi
}

merge_stage() {
  local manifest="${1:-$DEFAULT_MANIFEST}"
  python3 -m rubi_gto annotate --workspace "$WORKSPACE"
  python3 -m rubi_gto report --workspace "$WORKSPACE"
  python3 -m rubi_gto build --manifest "$manifest" --workspace "$WORKSPACE"
}

final_stage() {
  local manifest="${1:-$DEFAULT_MANIFEST}"
  python3 -m rubi_gto build --manifest "$manifest" --workspace "$WORKSPACE" --approved-only --exclude-pending
}

discover_local_stage() {
  local search_root="${1:-../gto_repos}"
  local output="${2:-build/reports/gto_local_sources.json}"
  python3 -m rubi_gto discover-local --search-root "$search_root" --output "$output"
}

discover_mod_archives_stage() {
  local mods_dir="${1:-../gto_repos/GregTech-Odyssey/mods}"
  local output="${2:-build/reports/gto_mod_archives.json}"
  python3 -m rubi_gto discover-mod-archives --mods-dir "$mods_dir" --output "$output"
}

discover_packwiz_stage() {
  local pack_root="${1:-../gto_repos/GregTech-Odyssey}"
  local search_root="${2:-../gto_repos}"
  local output="${3:-build/reports/gto_packwiz_translation_report.json}"
  python3 -m rubi_gto discover-packwiz --pack-root "$pack_root" --search-root "$search_root" --output "$output"
}

discover_instance_stage() {
  local instance_root="${1:-../gto_repos/GregTech-Odyssey}"
  local report_output="${2:-build/reports/instance_content_report.json}"
  local manifest_output="${3:-build/reports/instance_sources.json}"
  python3 -m rubi_gto discover-instance \
    --instance-root "$instance_root" \
    --output "$report_output" \
    --manifest-output "$manifest_output"
}

discover_gto_workflow_stage() {
  local instance_root="${1:-../gto_repos/GregTech-Odyssey}"
  local repo_root="${2:-../gto_repos}"
  local manifest_output="${3:-build/reports/gto_workflow_sources.json}"
  python3 -m rubi_gto discover-gto-workflow \
    --instance-root "$instance_root" \
    --repo-root "$repo_root" \
    --manifest-output "$manifest_output"
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

command="$1"
shift

case "$command" in
  first)
    first_stage "$@"
    ;;
  run-instance)
    run_instance_stage "$@"
    ;;
  run-gto-workflow)
    run_gto_workflow_stage "$@"
    ;;
  rerun-source)
    rerun_source_stage "$@"
    ;;
  rerun-failed)
    rerun_failed_stage "$@"
    ;;
  conflicts)
    conflicts_stage
    ;;
  llm)
    llm_stage "$@"
    ;;
  merge)
    merge_stage "$@"
    ;;
  final)
    final_stage "$@"
    ;;
  discover-local)
    discover_local_stage "$@"
    ;;
  discover-mod-archives)
    discover_mod_archives_stage "$@"
    ;;
  discover-packwiz)
    discover_packwiz_stage "$@"
    ;;
  discover-instance)
    discover_instance_stage "$@"
    ;;
  discover-gto-workflow)
    discover_gto_workflow_stage "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
