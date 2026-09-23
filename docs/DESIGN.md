# AFK FARM 0.6.4 — measured-kill product

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
still verifies the recorded character, build and region before native reward
delivery. Since 0.6.3 a loadout, level or combat setting that changed after the
calibration no longer blocks a claim (the player's decision: a single-player
offline game, where the check only guarded the calibrated pace against a weaker
hero). The calibrated pace still sets the rewards; a new claim plan records the
context it is delivered with (`farm_context`, the calibration's kept as
`calibration_farm_context`) so the plugin's own check passes. A delivery that
already started stays bound to the context it began with. Selection in the UI never changes the frozen expedition owner.

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

## Delivery speed, pause and Vault transfer (0.6)

Measured before the change (2026-09-23, Suh, Act_02_05, plugin 0.5): a 2 h claim
of 102,006 calls delivered about 53 calls per second at the 10 ms frame budget,
stopped at 78.6% when the game window was closed, and its 4,547 Vault records took
16 minutes to ingest. The 0.5 preview claimed 42.5 s for the same claim.

Replay: the first replay of a packet prepares each restored variable's name and
converted value, the protected values and the call arguments; arrays and ds
markers stay per-call values, so every ghost still gets fresh ones. Asset and
object names are resolved once per session. `variable_instance_set` is called
through the runner routine YYToolkit resolves by name. The spool is one buffered
stream flushed after every replay frame and before every checkpoint; claims no
longer copy each item into the last calibration capture. A running checkpoint is
rewritten at most every 250 ms (pauses and endings at once). `progress.perf`
reports milliseconds per replay stage, so the next live claim shows where time goes.

Delivery speed maps to calls per frame and frame budget: Normal 40 / 10 ms, Fast
200 / 30 ms, Maximum 1000 / 80 ms. Estimates use `delivery-rate.json` (smoothed
from finished claims; defaults 50/80/100 calls per second).

Pause: `afk expedition abort` saves (character and account) and writes an
"aborted" checkpoint. Leaving the region while the expedition hero is still loaded
saves, writes a partial spool summary and a "paused" checkpoint with the save
receipt, and resumes by itself on return. A close request for the game window
(WM_CLOSE or SC_CLOSE) during delivery is held for one frame, the delivery is
stopped like Pause, and the same request is posted again; a repeated request passes
at once. `CanResume` accepts "aborted" and saved "paused" checkpoints for the same
plan. A crash, a close without the expedition hero, or any unsaved checkpoint
still requires review. None of this suspends the game's own loop.

Crash continuation (0.6.1): the spool is flushed every frame but a checkpoint is
written at most every 250 ms, so after a crash the spool usually runs ahead of the
checkpoint. Each checkpoint therefore records `spool_bytes`, the flushed spool size
at that moment. When the player accepts the recorded position, the panel verifies the
records up to that size, copies the rest to `spool/set-aside` and cuts the spool
there; the checkpoint's counters, the spool and the next sequence number then agree,
and `CanResume` continues a running checkpoint that carries `resume_accepted`.
The cut-off records belong to calls the checkpoint does not count, so they are
delivered again rather than transferred twice. XP or gold the game may have saved
for those calls (an autosave between checkpoint and crash) is the remaining
uncertainty, as is whether the delivered part was saved at all.

Vault transfer: `loot-filter.json` keeps back gear of unticked rarities and, if
chosen, keys and materials; records stay in the spool, so Transfer again adds them
later. Records go best rarity first in 500-record batches with `layout: defer`,
then one `finalize` request lays the expedition out once. The Item Editor stores
each batch in one SQLite transaction, one backup per batch. Older editors ignore
the new fields and lay out per batch.

Claim in background uses the verified automatic setup (`game_session`): minimized
launch, the game's own menu and travel routines, a Maximum-speed claim, and a
normal window close only when the action started the game and delivery finished
or paused safely.

## Filtered items: sale and Prospector break-down (0.6.2)

Static reading of the installed build (2026-09-23): a merchant sale pays
ceil(item info "9" × stack) and credits it with PickUpGoldCheck under a fresh
GetCounterHash; no player, merchant or difficulty input enters the price. The hash
is a pure read of a protected counter that PickUpGoldCheck advances on success; a
stale hash raises ReportClient(112), which sends to the server whenever the API
exchange is connected, even offline. So the plugin sells only when no report could
leave the game, takes the hash immediately before each credit, credits once per
frame, and confirms the credit through GetGoldAmount. The Prospector converts one
unit at a time with the recipe table the game builds at start; the plugin reads
that table at expedition start instead of carrying the numbers, and creates the
outputs through the call mining nodes use for stackable ground drops. Measured on
the real 2 h claim's records: of 64,123 hidden items, 61,733 were below Satanic
(3,544,084 gold at their sale value) and 2,390 were Satanic C/B/A tier
(38,154 Satanic Crystal Fragments). The live credit, the recipe read and one
created stack still need a game session (`afk convert probe`).
