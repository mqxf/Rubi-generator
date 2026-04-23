# Manual Fixes

If the real run leaves unresolved entries:

1. Run the normal workflow:
```bash
./scripts/run_real_gto.sh
```

2. Open:
```text
review/generated/manual_fix_candidates.json
review/generated/manual_fix_overrides.json
```

3. Use `manual_fix_candidates.json` as reference.

4. Fill `manual_fix_overrides.json` with your final fixed annotations.
Leave entries as `""` if you want to skip them.

5. Merge the fixes and rebuild:
```bash
python3 -m rubi_gto merge-manual-fixes \
  --manifest build/reports/gto_workflow_runtime_manifest.json \
  --workspace .
```

Outputs:

- merged pack: `build/resourcepack`
- staged overrides: `build/staged`
