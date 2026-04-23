# Rubi GTO

`rubi-gto` builds Rubi-compatible Japanese resource-pack `lang` files for GregTech Odyssey content.

The pipeline is intentionally conservative:

- it fetches upstream Japanese sources from manifest-defined repositories
- it normalizes them into a reviewable intermediate corpus
- it applies glossary terms and manual per-key overrides
- it validates Rubi syntax against `LANG_GUIDE.md`
- it only emits approved strings by default

## Directory layout

- `manifests/`: source manifests
- `review/glossary.json`: reusable term-level replacements
- `review/glossaries/*.json`: additional glossary shards, including curated technical terms
- `review/review_entries.json`: per-key approvals and manual overrides
- `review/suggestions.json`: optional pre-review suggestions, for example LLM-produced annotations
- `review/generated/review_candidates.json`: auto-generated review queue for analyzer conflicts or no-recommendation cases
- `review/generated/review_candidates_by_category.json`: the same auto-generated review queue grouped into conflict buckets
- `review/generated/llm_suggestions.json`: generated LLM suggestions that are applied before analyzer fallback but after manual overrides
- `review/generated/llm_review_results.json`: detailed per-candidate outcomes from the latest LLM review pass
- `review/generated/llm_review_report.json`: compact LLM review summary with status counts, A/B choice counts, and last-run outputs
- `build/`: generated records, reports, and resource-pack output

## Usage

Short command reference: [WORKFLOW.md](/home/mqxf/Desktop/Coding/Minecraft/rubi-gto/WORKFLOW.md)

Run the full pipeline:

```bash
python3 -m rubi_gto run --manifest manifests/gto_sources.json --workspace .
```

Run a smaller first pass using only vanilla Minecraft `1.20.1`:

```bash
python3 -m rubi_gto run --manifest manifests/vanilla_only.json --workspace .
```

`manifests/vanilla_only.json` includes generated entries by default so the first test emits a full vanilla pack without manual approvals.
It also includes pending entries by default, which keeps unreviewed vanilla strings in the output instead of dropping them.

Include generated but not yet approved strings in the output pack:

```bash
python3 -m rubi_gto run --manifest manifests/gto_sources.json --workspace . --include-generated
```

Force approved-only output even if a manifest enables generated entries by default:

```bash
python3 -m rubi_gto run --manifest manifests/vanilla_only.json --workspace . --approved-only
```

Force the build to exclude pending entries:

```bash
python3 -m rubi_gto run --manifest manifests/vanilla_only.json --workspace . --exclude-pending
```

Run individual stages:

```bash
python3 -m rubi_gto ingest --manifest manifests/gto_sources.json --workspace .
python3 -m rubi_gto annotate --workspace .
python3 -m rubi_gto report --workspace .
python3 -m rubi_gto build --manifest manifests/gto_sources.json --workspace .
```

Run the LLM suggestion pass on the remaining review queue:

```bash
cp .env.example .env
# fill in OPENAI_API_KEY in .env
python3 -m rubi_gto llm-review --workspace . --model gpt-5 --reasoning-effort high
python3 -m rubi_gto annotate --workspace .
python3 -m rubi_gto report --workspace .
```

`llm-review` automatically loads `OPENAI_API_KEY` and `OPENAI_BASE_URL` from `.env` in the workspace root if they are not already exported in the shell.

Limit the pass to one bucket or one record while tuning prompts:

```bash
python3 -m rubi_gto llm-review --workspace . --category reading_only_conflict --limit 20
python3 -m rubi_gto llm-review --workspace . --record-id minecraft:advancements.adventure.trade.description
```

Default LLM model: `gpt-4.1-mini`

Discover local upstream repos and emit a manifest you can run in one pass:

```bash
python3 -m rubi_gto discover-local --search-root .. --output manifests/gto_local_sources.json
python3 -m rubi_gto run --manifest manifests/gto_local_sources.json --workspace .
```

## Review workflow

1. Populate `review/glossary.json` with exact plain-text to Rubi replacements for repeated technical terms.
2. Optionally populate `review/suggestions.json` with manual machine-generated per-key suggestions.
3. Optionally run `llm-review` to write generated suggestions into `review/generated/llm_suggestions.json`.
4. Run `annotate` and inspect `review/generated/review_candidates.json`, `review/generated/review_candidates_by_category.json`, and `review/generated/review_report.json`.
5. Mark accepted generated strings or add manual fixes in `review/review_entries.json`.
6. Re-run `build` without `--include-generated` to produce an approved-only pack.

## Confidence and correctness

The pipeline can validate:

- Rubi syntax is well-formed
- stripping annotations reproduces the original Japanese text
- curated high-risk glossary terms stay correct through replacement

The pipeline cannot prove a reading is linguistically correct unless that reading comes from a reviewed glossary, reviewed override, or reviewed suggestion. For GTO’s hard technical vocabulary, the safe strategy is:

1. seed a curated technical glossary for high-value machine and chemistry terms
2. allow an LLM to propose per-key suggestions into `review/generated/llm_suggestions.json` or `review/suggestions.json`
3. keep human approval as the only path into the default final pack

## Notes

- GitHub sources are fetched from public repository archives through the GitHub API plus `codeload.github.com`.
- Vanilla Minecraft sources can be fetched directly from Mojang's official asset index for a specific game version.
- Manifest entries can also use `local_dir` sources for local testing.
- Generic JSON flattening is supported for corpus-building, but only `lang`-shaped data will naturally map to usable Minecraft translation namespaces.
