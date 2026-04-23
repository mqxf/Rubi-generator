# Workflow

## First stage

Build the first pass from source files:

```bash
./scripts/rubi.sh first
```

Default manifest: `manifests/vanilla_only.json`

The main commands print live stage progress in the terminal.

Use another manifest:

```bash
./scripts/rubi.sh first manifests/gto_sources.json
```

Rerun only one source or a few sources:

```bash
./scripts/rubi.sh rerun-source manifests/gto_sources.json GregTech-Modern
./scripts/rubi.sh rerun-source build/reports/instance_sources.json openloader:gto_quests
```

Rerun only sources that failed in the last ingest:

```bash
./scripts/rubi.sh rerun-failed manifests/gto_sources.json
```

Failure details are written to:

- `build/reports/source_report.json`
- `failed_sources`
- `failed_source_ids`

## Get conflicts

Refresh annotated records and conflict reports:

```bash
./scripts/rubi.sh conflicts
```

Main files:

- `review/generated/review_candidates.json`
- `review/generated/review_candidates_by_category.json`
- `review/generated/review_report.json`

## LLM review

Run the LLM pass on remaining conflicts:

```bash
./scripts/rubi.sh llm
```

Default model: `gpt-4.1-mini`

Limit or target:

```bash
./scripts/rubi.sh llm --category reading_only_conflict --limit 20
./scripts/rubi.sh llm --record-id minecraft:advancements.husbandry.plant_seed.title
```

Main files:

- `review/generated/llm_suggestions.json`
- `review/generated/llm_review_results.json`
- `review/generated/llm_review_report.json`

## Merge outputs

Apply glossary + manual suggestions + LLM suggestions, then rebuild output:

```bash
./scripts/rubi.sh merge
```

Use another manifest:

```bash
./scripts/rubi.sh merge manifests/gto_sources.json
```

## Approved-only build

Build only reviewed entries:

```bash
./scripts/rubi.sh final
```

## Discover local GTO repos

Write a runnable local-source manifest:

```bash
./scripts/rubi.sh discover-local
```

Default output: `build/reports/gto_local_sources.json`

Optional vanilla-inclusive manifest:

```bash
python3 -m rubi_gto discover-local --search-root ../gto_repos --include-vanilla --output build/reports/gto_local_plus_vanilla.json
```

Optional extra upstream sources:

```bash
python3 -m rubi_gto discover-local --search-root ../gto_repos --append-manifest manifests/upstream_sources.example.json --output build/reports/gto_full_sources.json
```

Optional mods-folder archive source:

```bash
python3 -m rubi_gto discover-local --search-root ../gto_repos --mods-dir ../gto_repos/GregTech-Odyssey/mods --output build/reports/gto_all_sources.json
```

## Discover mods-folder archives

Write a runnable manifest from a `mods/` directory of `.jar` / `.zip` files:

```bash
./scripts/rubi.sh discover-mod-archives
```

Default output: `build/reports/gto_mod_archives.json`

## Discover packwiz coverage

Write a packwiz mod report with likely local translation repo matches:

```bash
./scripts/rubi.sh discover-packwiz
```

Default output: `build/reports/gto_packwiz_translation_report.json`

Use `upstream_lookup_candidates` in that report to find missing upstream repos, then add them through a small manifest like `manifests/upstream_sources.example.json`.

## Discover a Minecraft instance

Scan one instance folder for:

- mod jar lang files
- OpenLoader resource packs
- FTB Quests roots
- GuideME pages
- Patchouli books

Write both a content report and a runnable manifest:

```bash
./scripts/rubi.sh discover-instance
```

Defaults:

- instance root: `../gto_repos/GregTech-Odyssey`
- report: `build/reports/instance_content_report.json`
- manifest: `build/reports/instance_sources.json`
