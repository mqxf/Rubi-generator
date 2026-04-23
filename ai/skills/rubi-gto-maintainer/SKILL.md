---
name: rubi-gto-maintainer
description: Use when working inside the rubi-gto repository on furigana generation, source discovery, analyzer merge logic, LLM conflict review, or Minecraft instance scanning for GregTech Odyssey and related packs.
---

# Rubi GTO Maintainer

Use this skill when the task is about maintaining the `rubi-gto` pipeline itself rather than just running it.

## Read First

- `../../../LANG_GUIDE.md`
- `../../../AGENTS.md`
- `../../../WORKFLOW.md`
- `../../../docs/instance-content-summary.md`

## Main Work Areas

- Source discovery and ingest:
  `../../../rubi_gto/sources.py`
- Annotation merge logic:
  `../../../rubi_gto/japanese.py`
- LLM review:
  `../../../rubi_gto/llm_review.py`
- End-to-end orchestration:
  `../../../rubi_gto/pipeline.py`
- CLI commands:
  `../../../rubi_gto/cli.py`

## Working Rules

- Follow `LANG_GUIDE.md` exactly for Rubi formatting.
- Do not auto-approve uncertain readings.
- Preserve exact Japanese round-trip behavior.
- Prefer analyzer consensus, glossary replacements, JMdict, and explicit review artifacts before escalating to LLM suggestions.
- Track actual namespaces from `assets/*/...` paths. A repo, jar, or OpenLoader pack may serve multiple namespaces.
- Treat non-jar content as real translation sources:
  - `config/ftbquests/quests`
  - `config/openloader/resources/*`
  - `resourcepacks/*`
  - `assets/*/ae2guide/**`
  - Patchouli book locations

## GTO 1.20.1 Notes

- Quest SNBT is present under `config/ftbquests/quests`.
- GTO localizes quests by rewriting them to translation keys and then serving the localized text from `config/openloader/resources/quests/assets/gto/lang/<locale>.json`.
- OpenLoader packs in the instance may contain lang files for namespaces unrelated to the pack id.

## Default Validation

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/rubi.sh
```

## High-Value Commands

```bash
./scripts/rubi.sh discover-instance
./scripts/rubi.sh rerun-source manifests/gto_sources.json GregTech-Modern
./scripts/rubi.sh rerun-failed manifests/gto_sources.json
./scripts/rubi.sh conflicts
./scripts/rubi.sh llm --limit 20
```
