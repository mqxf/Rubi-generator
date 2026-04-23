# Copilot Instructions

This repository generates Rubi-compatible Japanese lang packs for Minecraft and GregTech Odyssey.

## Priorities

- Read `LANG_GUIDE.md` before changing annotation behavior.
- Prefer conservative behavior. Do not guess readings when the pipeline cannot support them.
- Preserve `source_text` exactly. Annotation must strip back to the original Japanese.
- Track real asset namespaces from file paths such as `assets/<namespace>/lang/*.json`.

## Important Content Locations

- Quests: `config/ftbquests/quests`
- GTO 1.20.1 quest lang output: `config/openloader/resources/quests/assets/gto/lang/<locale>.json`
- OpenLoader packs: `config/openloader/resources/*`
- GuideME pages: `assets/<namespace>/ae2guide/**`

## Key Files

- `rubi_gto/sources.py`
- `rubi_gto/japanese.py`
- `rubi_gto/llm_review.py`
- `rubi_gto/pipeline.py`
- `WORKFLOW.md`
- `docs/instance-content-summary.md`

## Validation

Run:

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/rubi.sh
```
