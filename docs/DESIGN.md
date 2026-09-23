# AFK FARM 0.5.0 — measured-kill product

The earlier combat-reconstruction project is archived outside the active tree.
The product uses empirical kills per region-second and native reward replay.

## Runtime boundary

The Aurie/YYToolkit DLL observes drop calls and retains one packet per canonical
source identity. The Python CLI derives profile rates and a single-zone plan.
The panel orchestrates existing commands through a worker and reads JSON files.
An elapsed-time clock survives panel/game closure. Claim scales packet counts with
largest-remainder allocation, executes native drops, saves through the game and
then imports spool items into the Item Editor Vault.

Starting the clock freezes an eligible saved profile and needs no live game or
character match. A different hero can be played or calibrated meanwhile. Claim
still verifies the recorded character, build, loadout and region before native
reward delivery. Selection in the UI never changes the frozen expedition owner.

`farm_context` schema 1 binds selected character, resolved level/loadout/difficulty,
active equipped GUIDs and definitions, selected talents/subtalents, attribute and
skill bindings, mercenary talents, incarnation/ether loadouts and ForgePact file
settings. Independent plans compare normalized inputs that exclude reward-only
ForgePact fields; they freeze their own reward policy separately. Recording itself
still requires an unchanged context. Canonical maps/structs are key-sorted. Inventory junk and volatile item
hashes are excluded. Inactive equipment sets are excluded; the selected set number
is included. This is a conservative compatibility stamp, not a combat calculation
or a proof that every transient game modifier is known.

During measurement the plugin emits a monotonic `farm_clock` sample about once per
second. Only samples in the requested room contribute to its rate. The interval
is assigned to the sampled room, so a room boundary can introduce approximately
one tick of error. Loading and transient player handles pause the same capture;
the first valid sample resumes without adding loading time. A changed resolved
stamp, non-loading invalid context or unexplained gap over five seconds invalidates
the session. Walking and in-region waiting count intentionally.
The panel requires normal Act rooms, ordinary monster rank packets, 60 seconds,
30 kills and >=95% capture coverage. These are quality gates, not confidence levels.

## UI and distribution

The five pages are Explore, Calibration, Loot, Modifiers and Settings. Original user-supplied
map screenshots provide the atlas background; local JSON supplies clickable room
coordinates and accessible select options. No generated landmarks imply new game
content. Character names/classes/levels are read from local HSS saves without edits.
Real equipped character rendering is not implemented; the optional portrait is a
PNG supplied by the user. Loot uses existing Item Editor names and icons, resolving
only unambiguous native display names or known catalog keys.

Python's ThreadingHTTPServer binds only to 127.0.0.1. The server validates Host,
Origin and a per-process action token; static paths must stay beneath web/.
Actions are allowlisted; no arbitrary command endpoint exists. CLI output is text,
not HTML. Single-worker serialization plus an OS IPC byte lock avoids command-file
overwrite. Fresh state carries process, request and build IDs. The bundle carries
CPython 3.13 plus stdlib and local SDK bindings; a small Win32 EXE opens the panel.
It does not include Aurie, YYToolkit, the game, saves or research captures.

## Known incompleteness

Normal kill replay does not reproduce event completion rewards, every boss
special-case, ForgePact kill-trigger RNG or Tracker kill counters. Transient buffs
are averaged through calibration. Changed builds require recapture, not relabeling.
Saved XP/gold and spool ingestion are separate stages, not one atomic transaction.
Saved rewards settle the clock despite Vault failure; ingestion is retryable on its
own. Crash-ambiguous replay remains blocked for manual reconciliation.

The 0.4.0 live check used a current Suh profile and real elapsed time. It established
native delivery, immediate character/account persistence, restart persistence and
Vault deduplication. Independent farm-rate validation remains pending; the first
reference contains only 334 seconds. See the release report for measured scope.

## Saving and recovery

The controller's Room End event alone was insufficient on the tested Suh character:
gold saved but XP stayed only in memory until a real room transition. The corrected
path also calls the SDK-named SaveLocalFile with argument 0 and the live player as
self/other. Both calls must succeed before reporting a completed save. A real
claim then matched +287476 XP and +84 gold on disk immediately and after relaunch.
Older room-end-only receipts do not qualify for automatic recovery or panel transfer.

Completed-save reconciliation requires the exact plan hash, complete native counts,
matching spool identifiers/sequence and gold/XP/item totals, no failure sidecar and
the corrected save receipt. It never invokes reward replay. This does not solve
atomicity between game saves and the item spool, especially during power loss.

## Quality and validation

Calibration quality describes complete one-minute windows, including idle ones.
The 600-second / 200-kill steady-sample recommendation and 25% variation / 20%
half-drift checks are engineering thresholds, not statistical confidence bounds.
Independent validation freezes a source digest and forecast before the second run,
requires a later separate recording under the same context, and checks a declared
20% kill/XP error target. Both samples need 600 seconds / 200 kills / 95% coverage
to pass. It does not establish rare-drop or event-reward parity.

## Independent reward settings

See [Independent rewards](INDEPENDENT_REWARDS.md) for modifier behavior, native MF
rounding, one-time recalibration and optional ForgePact compatibility.
