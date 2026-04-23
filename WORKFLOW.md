# Workflow

## First stage

Build the first pass from source files:

```bash
./scripts/rubi.sh first
```

Default manifest: `manifests/vanilla_only.json`

Use another manifest:

```bash
./scripts/rubi.sh first manifests/gto_sources.json
```

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
