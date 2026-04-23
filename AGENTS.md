# AGENTS.md

## Project Goal

`rubi-gto` builds Rubi-compatible Japanese translation packs for Minecraft, GregTech Odyssey, and related content. The pipeline should prefer existing translations, preserve the original Japanese text exactly, and only add furigana syntax that complies with `LANG_GUIDE.md`.

## Read First

- `task.md`
- `LANG_GUIDE.md`
- `README.md`
- `WORKFLOW.md`
- `docs/instance-content-summary.md`

## Core Modules

- `rubi_gto/sources.py`
  Source discovery and ingest for GitHub archives, local repos, mod jars, OpenLoader packs, and instance scans.
- `rubi_gto/japanese.py`
  Analyzer merge logic, conflict categorization, and conservative automatic annotation.
- `rubi_gto/llm_review.py`
  LLM-assisted resolution for the remaining conflicts only.
- `rubi_gto/pipeline.py`
  End-to-end ingest, annotate, report, and build orchestration.
- `rubi_gto/cli.py`
  Command surface.

## Project Rules

- Do not invent readings when the pipeline has no support for them.
- Prefer analyzer consensus, curated glossary terms, JMdict decisions, and explicit review data before using the LLM.
- Keep `source_text` unchanged. Validation depends on stripping furigana back to the exact original Japanese.
- Follow `LANG_GUIDE.md` for stem-style verb and adjective annotation.
- Track actual asset namespaces, not just repo names or mod ids. A source may contain lang files for many namespaces.
- Treat quest and guide content as first-class sources even when they are outside `mods/`.

## GTO-Specific Content Layout

- FTB Quests data: `config/ftbquests/quests`
- GTO 1.20.1 quest localization output: `config/openloader/resources/quests/assets/gto/lang/<locale>.json`
- OpenLoader resource packs: `config/openloader/resources/*`
- GuideME pages: `assets/<namespace>/ae2guide/**`
- Patchouli content may be in `assets/<namespace>/patchouli_books/**` and `data/<namespace>/patchouli_books/<book>/book.json`

## Important Outputs

- Ingest report: `build/reports/source_report.json`
- Review queue: `review/generated/review_candidates.json`
- Grouped review queue: `review/generated/review_candidates_by_category.json`
- LLM suggestions: `review/generated/llm_suggestions.json`
- LLM review results: `review/generated/llm_review_results.json`
- Instance scan: `build/reports/instance_content_report.json`

## Useful Commands

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/rubi.sh
./scripts/rubi.sh discover-instance
./scripts/rubi.sh rerun-source manifests/gto_sources.json GregTech-Modern
./scripts/rubi.sh rerun-failed manifests/gto_sources.json
```

## Editing Guidance

- Use `apply_patch` for code edits.
- Keep docs concise and operational.
- If a source fails, preserve the per-source failure detail and do not collapse it into one generic error.
- When adding new discovery logic, include tests that prove both the discovery report and runnable manifest behavior.
