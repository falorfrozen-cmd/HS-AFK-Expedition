# 0.1.1 — audit corrections (2026-09-18)

Historical baseline. The 0.3.0 product adds the farm-context stamp, room clock
and separate rewards_saved settlement described in [UI_CONTRACT.md](UI_CONTRACT.md).
The wall-time basis below is legacy and not accepted by the current panel.

This document supersedes the older Phase 1 assumptions about character identity,
multi-zone execution and automatic crash recovery. No dependency was added;
the CLI remains Python 3.13 standard library only.

## Character and calibration

- The plugin reads the selected local save from `global.slot[1]`, validates its
  map against the live name, and emits `identity_version: 2`. It never searches
  for the first matching name. Unlocked slots beyond index 15 are supported.
- CLI and plugin both require name, class and selected slot. Unknown identities
  and legacy stamps are rejected. Old stamps cannot be automatically migrated:
  the old code could have written the wrong slot. Recalibrate with this DLL.
- Profiles are saved as `<room>--<context hash>.json`; `<room>.json` is the latest
  selection for compatibility. `--zone <room>--<hash>` selects a stored context.
  The hash includes character, build, ForgePact settings and measurement basis.
- Incompatible contexts are rejected before planning. Replay currently requires
  one zone; a context shim for multi-zone rewards has not been verified.
  `--anywhere` remains an explicit diagnostic override for that one zone and
  is passed through to the plugin. It does not override identity or build checks.
- Capture sessions rotate on character changes. Settings are sampled once per
  second; when a change is detected the preceding session is marked
  `context_invalid` and cannot be used to calibrate a mixed rate.
- Missing, old or incomplete packets are excluded without assigning their kills
  to another monster/chest. Rates use only usable event counts. Coverage below
  95% requires recalibration. This is a data-quality guard, not proof that rare
  drops were sampled sufficiently.
- Wall time is the sum of first-to-last event spans of consecutive room visits,
  with one second added per visit. It includes gaps inside a visit but excludes
  observed visits to other rooms. Travel without events remains unmeasured.

## Counts and outcomes

- Scaling uses largest-remainder allocation to preserve the rounded total.
  Calls, kills, breaks, XP, per-zone counts and extras are recalculated from the
  resulting packet list. Learned item/gold estimates only use matching context.
- `sessions/<id>.result.json` contains `success` and `stages` for `replay`,
  `save`, and `ingest`. The plugin-owned progress file remains separate.
  Poll progress every 500 ms; display CLI output as-is.
- Replay failure, skipped calls, save failure, incomplete transfer or abort
  returns a nonzero CLI exit code. Ingest code 4 means unreadable/skipped records.
  A claim stays armed until its requested stages succeed.
- Retrying a completed run does not replay. It retries save/ingest, using the
  Vault's existing `(expedition_id, seq)` deduplication. `--no-ingest` explicitly
  completes without an Item Editor transfer.
- Verify uses unique IDs and an in-memory profile override; it no longer deletes
  previous verification spools or temporarily overwrites a normal profile.

## Controlled resume and limits

- Progress version 2 stores filter, unplaced, ground removal, gold, XP and
  diagnostic counters together. A controlled abort restores them all.
- The checkpoint binds to the exact plan file SHA-256. Changing a plan under an
  existing expedition ID is rejected. Do not reformat an in-progress plan.
- A failed native drop stops the run immediately and reports `error`; remaining
  monsters are not silently skipped and reported as successful work.
- Replay pauses if the character or required room changes. Return to the
  original character before aborting so rewards are saved to the right save.
- Windows progress replacement uses a temporary file and `MoveFileExW` with
  replace/write-through. Transient sharing/access locks retry for at most 250 ms.
  A persistent failure stops execution, saves available rewards and writes
  `sessions/<id>.failure.json`. Readers must prioritize this over stale progress.
  `checkpoint_write_retries` exposes the diagnostic retry count.
- **Only a clean, saved `aborted` checkpoint automatically resumes.** A process
  crash, electricity loss, corrupt checkpoint, legacy checkpoint or replay
  error requires manual reconciliation. The game save and spool are not a
  shared atomic transaction. This release prevents blind replay after an
  uncertain interruption; it does not implement automatic loss-free recovery.

## Reusable verification

Run from this module:

```powershell
py -3 -B -m unittest discover -s tests -p test_afk.py -v
cmd /c tests\build_and_run.bat
cmd /c plugin_build\build.bat
```

The Python tests use temporary files and fake IPC, and never modify game saves
or a real Vault. C++ runtime tests exercise the same slot/checkpoint helper used
by the DLL. The live evidence (`verification/fix-audit-20260918/`) is kept outside
the public repository.
The live harness uses fixed IDs and refuses to overwrite them; select new IDs
for a new campaign rather than deleting an old completed expedition.
