# Odyssey Deploy Log

Records every deploy of the Odyssey Western Blot Imager app to the lab
Windows box. Each row identifies the exact source commit that produced
a given lab deploy — so rollback is:

```bash
git checkout lab-YYYY-MM-DD
scripts/deploy_to_desktop.sh
# then copy ~/Desktop/odyssey/ to the lab machine
```

## Convention

- **Tag every deployed commit** with `lab-YYYY-MM-DD` before running
  `scripts/deploy_to_desktop.sh`. Tag date = deploy date.
- **Never delete a `lab-*` tag**. Old ones are the safety net.
- **Append a row to this file** at the same time as the tag.
- On the Windows box the app lives at `C:\titronic\`. When installing
  a new deploy: rename current to `C:\titronic-backup-YYYY-MM-DD\`
  first, then copy the new deploy into `C:\titronic\`. Users can
  hand-rollback by renaming backup back over, without waiting on a
  fresh build.

## Which repo owns the SHAs

Deploys ship from **`github.com/vcjdeboer/odyssey-app-hap`** (spun out
2026-05-03 as the standalone production repo with a vendored `plr_v4`
slice). SHAs in the table below are from that repo — that's where the
`lab-*` tags live and where `scripts/deploy_to_desktop.sh` reads its
version stamp. The wider `titronic` monorepo mirrors these files but
has its own, different commit history for the same code (e.g., Phase
39 is `d037e9c` there vs `250be85` in odyssey-app-hap). When deploying,
work in `odyssey-app-hap`.

## Verifying what's actually in the lab

Windows box "Date modified" columns are the source of truth for what
was deployed and when. The Mac's `~/Desktop/odyssey/` folder can be a
rebuild that never reached the lab — don't trust its dates. Correlate
the Windows batch-file mtimes against `git log --format="%h %ci %s"`
to identify the deployed commit.

## Deploys

| Date       | Tag              | Commit    | Branch                       | Notes |
|------------|------------------|-----------|------------------------------|-------|
| 2026-05-01 | `lab-2026-05-01` | `250be85` | `022-odyssey-app-lab-fixes`  | Phase 39: B&W mode, crop region, live-preview tightened, TIFF diagnostic. **Current in-production version** (verified 2026-07-09 from lab box screenshots). Batch files written 2026-05-01 16:29-16:31; Phase 40 committed 7 min later never reached lab. Lab location: `C:\titronic\`. |
