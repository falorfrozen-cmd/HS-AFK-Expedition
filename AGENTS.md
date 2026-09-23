# AFK FARM — agent guidance

The parent hub AGENTS.md applies. The user's current direction is a measured-kill
AFK product, not a full combat simulator. Do not restart combat research implicitly.
The previous source, SDK, studies and raw evidence are frozen outside the hub at
`../../AFK-Research-Archive/2026-09-21`; read its index when researching old results.
This working tree contains the product and its tests.

## Boundaries

- Product language is English only: UI, accessibility labels, messages, launcher
  dialogs and packaged user documentation. Keep player names and paths unchanged.

- Python 3.13 standard library only; no pip. Localhost web UI, no hosted service.
- Hook game routines by SDK names. Never call another plugin's symbols.
- Rewards come from native drop calls; never generate item structs in Python/JS.
- No decompiled game source in this tree. Archive private raw research outside the hub.
- Keep per-kill capture cheap. Do not reintroduce multi-megabyte research trace buffers.
- Player identity is version 2: save slot, name and class. Names alone are not unique.
- Farm context is build-bound; unknown values refuse use. Raw protected handles are
  not stat values. Equipment loadout is resolved first, then converted from one-based
  saved number to zero-based equipment set index. Inactive set items may be absent.
- A native direct-call hook is required. TableOnly is not working interception.
- Panel reads JSON state, not human CLI output. Long commands run in a worker;
  progress is polled every 500 ms. Raw output stays available in the log.
- IPC has one writer. Use Ipc.send's OS lock; never replace a pending command.
- Replay/save completion settles the clock even when Vault is unavailable. Retry
  ingestion only; never run saved rewards a second time to repair Vault transfer.
- Failure JSON takes precedence over progress. Unclean interruption is not automatic
  resume. Do not expose diagnostic --anywhere/--forgepact-ignore as normal UI fixes.
- Game saves are read-only from the panel. Native game saving remains in the engine.
- DLL backups belong outside mods/aurie (Aurie loads every DLL in that folder).

## Main files

- `tools/panel.py`, `web/`: local HTTP bridge and five UI pages.
- `tools/afk.py`: profiles, plans, elapsed clock, replay and result stages.
- `plugin/ModuleMain.cpp`, `plugin/FarmContext.inl`: native capture/replay and stamp.
- `plugin/TestSession.inl`, `tools/game_session.py`: fresh JSON / background setup.
- `launcher/Launcher.cpp`, `tools/build_release.py`: portable EXE distribution.
- `tools/calibration.py`, `tools/validate_farm.py`: descriptive quality and frozen
  independent comparisons. Never lower a frozen acceptance target after seeing data.
- `tools/recovery.py`: reconcile confirmed completed saves only. Partial or
  contradictory evidence refuses automatic recovery; never replay to repair it.
  The player may close such a claim as partial (keeps records, allows transfer)
  or, when the records end exactly at a running checkpoint's `spool_bytes`, accept
  that position to deliver the rest. Both are explicit, confirmed player actions.
- `tools/product_data.py`, `tools/item_labels.py`, `web/extras.js`: presentation,
  native rarity labels, loot journal, support matrix and optional user screenshot.

## Verification

Run Python unittest discovery and the two C++ smokes before/after engine changes.
Use temporary data for tests; do not fabricate usable profiles in live data.
Browser-check desktop and narrow layouts and the real states (empty, stale, offline).
Live startup, close and normal Act/town travel are authorized by the user for testing;
use game_session through the single IPC channel, without taking mouse/keyboard control.
Close test games normally afterward. Do not change gear/skills to simplify tests.
A successful teleport does not prove that an activity was cleared or unlocked.
Keep a report that distinguishes tested paths from untested real reward delivery.

Current design and limits: `docs/DESIGN.md`, `docs/UI_CONTRACT.md`, `README.md`.
Do not claim a complete DPS model, automatic crash recovery, exact drop parity, or
independent statistical validation from same-session verify results.

Independent rewards: read docs/INDEPENDENT_REWARDS.md before changing modifier,
XP baseline or ForgePact isolation behavior. Never convert old XP evidence by guessing
a config multiplier. Native MF query IDs differ from item and UI stat IDs.
