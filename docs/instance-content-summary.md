# GTO Instance Content Summary

As of 2026-04-23, the live instance scan for `../gto_repos/GregTech-Odyssey` is captured in:

- `build/reports/instance_content_report.json`
- `build/reports/instance_sources.json`

## Current Findings

- `config/ftbquests/quests` exists and contains `55` SNBT files.
- That quest root has `data.snbt` and `chapter_groups.snbt`.
- It does not contain `lang/<locale>.snbt`, so this pack is using the older 1.20.1-style quest localization path.
- Quest text is rewritten to translation keys by `.github/localization/ftbquest_localization.py`.
- Localized quest strings are then supplied by an OpenLoader resource pack at `config/openloader/resources/quests/assets/gto/lang/<locale>.json`.
- OpenLoader resource packs also exist at:
  - `config/openloader/resources/quests`
  - `config/openloader/resources/resources`
- GuideME content exists outside mod jars at `config/openloader/resources/resources/assets/mae2/ae2guide/_zh_cn`.
- The current instance has no `ja_jp` lang sources inside:
  - `mods/`
  - `config/openloader/resources/`
  - `resourcepacks/`

Because of that, `build/reports/instance_sources.json` is currently empty. For actual Japanese generation, the repo-based manifests are still the active source of truth.

## Source Categories To Track

1. Mod jars and zips in `mods/`
   Look for `assets/*/lang/*.json`, `assets/*/ae2guide/**`, and `assets/*/patchouli_books/**`.
2. OpenLoader resource packs in `config/openloader/resources/`
   These can be directories or archives and may contain lang files for many namespaces that do not match the pack id.
3. Launcher `resourcepacks/`
   These can override lang files or guide content even when they are not mods.
4. FTB Quests data roots
   On GTO 1.20.1 this is `config/ftbquests/quests`.
5. External Patchouli books
   Track `patchouli_books/<book>/book.json` when present.

## Override Rules

- OpenLoader resource pack content overrides through normal resource-pack paths under `config/openloader/resources/<pack>/...`.
- GTO 1.20.1 quests override through `assets/<namespace>/lang/ja_jp.json`, not through localized quest SNBT.
- Newer FTB Quests versions may instead use `config/ftb_quests/quests/lang/<locale>.snbt`.
- GuideME loads pages from `assets/<namespace>/ae2guide/**` across all resource packs and namespaces.
- Patchouli books can override via `assets/<namespace>/patchouli_books/**`, while book declarations may live under `data/<namespace>/patchouli_books/<book>/book.json`.

## Useful Commands

Scan a whole instance:

```bash
./scripts/rubi.sh discover-instance
```

Write the raw report and runnable manifest explicitly:

```bash
python3 -m rubi_gto discover-instance \
  --instance-root ../gto_repos/GregTech-Odyssey \
  --output build/reports/instance_content_report.json \
  --manifest-output build/reports/instance_sources.json
```

Rerun only one source:

```bash
./scripts/rubi.sh rerun-source manifests/gto_sources.json GregTech-Modern
```

Rerun only previously failing sources:

```bash
./scripts/rubi.sh rerun-failed manifests/gto_sources.json
```

## Key Local References

- `task.md`
- `LANG_GUIDE.md`
- `WORKFLOW.md`
- `rubi_gto/sources.py`
- `rubi_gto/pipeline.py`
- `rubi_gto/cli.py`
- `../gto_repos/GregTech-Odyssey/.github/localization/ftbquest_localization.py`
- `../gto_repos/GregTech-Odyssey/config/openloader/resources/quests/pack.mcmeta`
- `../gto_repos/GregTech-Odyssey/config/openloader/resources/resources/pack.mcmeta`
- `../gto_repos/Applied-Energistics-2-gto/guidebook.md`

## External References

- OpenLoader: https://docs.darkhax.net/1.20.1/open-loader/
- Patchouli 1.20 resource-pack books: https://vazkiimods.github.io/Patchouli/docs/upgrading/upgrade-guide-120/
- Patchouli book JSON: https://vazkiimods.github.io/Patchouli/docs/reference/book-json/
- FTB Quests changelog: https://github.com/FTBTeam/FTB-Quests/blob/main/CHANGELOG.md
