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
  ./scripts/rubi.sh conflicts
  ./scripts/rubi.sh llm [llm-review args...]
  ./scripts/rubi.sh merge [manifest]
  ./scripts/rubi.sh final [manifest]

Defaults:
  manifest: manifests/vanilla_only.json
  llm model: gpt-4.1-mini
EOF
}

first_stage() {
  local manifest="${1:-$DEFAULT_MANIFEST}"
  python3 -m rubi_gto run --manifest "$manifest" --workspace "$WORKSPACE"
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
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
