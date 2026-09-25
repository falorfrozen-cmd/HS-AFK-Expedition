# AFK FARM 0.7.0 — measured-kill product

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

## 0.7.0: roster, collection, Siege, special monsters and workers

**Hero roster.** `state.json` schema 2 keeps one armed expedition per hero under
`expeditions`; claims, cancels and recoveries name the expedition (`--expedition`)
or find it through the hero. The clock, plan, checkpoint and spool of every
expedition were already keyed by its id, so nothing else changed: delivery is still
one claim at a time, for the hero loaded in the game.

**Collection and wishlist.** Read-only over delivered spools: a Set-or-better record
whose native display name is a collectible's (web/collection.json, built from the
Item Editor catalog; names are unique there) counts. Per-spool results are cached by
size and time. A claim's wishlist drops show one Windows toast. The share card is
drawn in the browser from `/api/share`; nothing is uploaded.

**Siege.** A challenge layer over the measured pace (tools/siege.py): waves demand
a growing kill rate; the hero's rate is its calibrated pace times the mean of five
one-minute factors of its own calibration, damped by the calibration's length (full
weight from 30 minutes). Waves it cannot keep up with damage a gate. Kills equal
farming while the gate holds; special waves replay the calibration's own elite,
goblin or verified boss packets; the level adds Magic Find. The timeline is drawn
once from a stored seed, so claiming early or late never re-rolls it. It is not a
combat model: say so wherever it is presented.

**Special monsters.** Packets whose native rank is outside 1-4 no longer close a
calibration; `afk.replayable` leaves them out of every plan until `afk special
verify` replayed one cleanly in statistics runs (XP and gold off, items kept out of
the Vault). Verified packets replay at their measured rate and in boss waves.

**Workers.** A designed game layer (tools/workers.py), not a measurement: levels,
skills and haul sizes are its own rules. What the game does: the plugin
(`afk worker pay`) takes a hire or reset price from the loaded offline hero through
the purchase path the game's merchant uses (`PickUpGoldCheck` with a fresh
`GetCounterHash` and a negative amount), requires the gold to fall by exactly the
price with no anti-cheat report and no server link, saves at once and gives the gold
back if the save fails; one receipt per request. `afk worker deliver` creates every
stack of a planned haul with `LootGroundCreate` (the call a mining node uses) into
the delivery's own spool, and rolls the Gem Sense share per unit with the
Prospector's ore recipe and the game's `irandom` (one roll per output, first hit
wins, as the Prospector does). Only mining ores (27-32), jewel materials (0-23),
Satanic Crystal (58), its fragment (60) and Destiny Shard Fragment (66) may be
delivered. A haul is planned once from the trip's seed and never re-rolled; a
delivery that stopped part way is never made again and can be closed as partial.

Live check (2026-09-24, verified build, ForgePact and the Tracker producer loaded):
`afk worker pay` took 1 gold (the gold fell by exactly 1, the game saved the
character and account, no anti-cheat report; the same request again was refused);
`afk worker deliver` made 50 Copper Ore and 3 Satanic Crystal Fragments and turned
30 Copper Ore through the Prospector's recipe into 12 jewelcrafting materials (40%;
about 41% expected), 5 stacks recorded in the delivery's spool and nothing given to
the hero; the same delivery again was refused. The research commands
`call`/`gvar`/`gvars` no longer resolve numbers as protected handles: a gold amount
in that range crashed the game in the anti-cheat module.

Panel check (2026-09-24, same build, two heroes): the 1-gold payment above was still
missing after a game restart; Suh's 30-minute Siege (Act 2-5, level 34) and Sgham's
15-minute expedition (Act 4-3) ran at the same time; a claim of Suh's Siege with
Sgham loaded was refused; each claim delivered with its own hero (the Siege: 6 waves,
gate at 20 of 100, a new record, 3,864 items, 808,164 gold plus 1,911,459 from sold
items - the in-game gold matched to the unit - a wishlist drop and 31 new collection
entries); hiring the first miner took exactly 250,000 gold and saved; its 1-hour
Copper trip was collected right after a claim (179 ore to the Vault, level 3).
Not yet live: a boss verify (no boss packet captured).

What that check and the review of the pull request changed:
- A payment whose reply timed out was recorded as refused although the game could
  still run it; a retry under a new request could then pay twice. It is now
  `unknown` until its receipt appears; the purchase is completed then, once, and a
  new payment first finishes the unanswered one under its own request id.
- The suggested Siege level must also keep the gate standing (fall chance at most
  25%): level 35 lasted the 6 waves but fell on the last one in every run.
- A Siege lasts whole waves (the duration rounds down to 5 minutes); a duration
  between two waves left the last planned wave out of reach.
- The collection cache is shared by the panel's request threads; it is now written
  under one lock with a temporary file per writer.
- The share card counted waves fully held ("0 WAVES" after 6) and only the dropped
  gold; it shows the waves fought and all the gold the claim paid.
- Ready notifications stay on screen until closed: over a full-screen game a plain
  toast was only heard (Windows had it at 11:24:33; nothing was seen).
- Claim output prints XP as a whole number.
- Chest openings no longer scale into plans: calibrations record them as breaks,
  and a 5.6-minute Act 3-3 calibration with 6 Abyss-chest and 6 world-chest calls
  made plans open about 11 Abyss chests an hour (a rare map event) and golden and
  crystal chests without spending keys. Chests are left out of expeditions and
  Siege waves; the Siege's break rate leaves them out too. Siege treasure waves now
  also count orb and ore goblins (all five loot goblins).

## 0.8: the camp, traits and three more worker types

**The camp is AFK FARM's own layer.** Buildings (tools/camp.py), camp resources
(stone, spoils, gem dust) and traits (tools/traits.py) change the crew's numbers
and the Siege gate; none of them is an item and none reaches the game. Gold is the
game's, taken through the same payment path as hiring (one receipt per request,
never charged twice); camp resources set aside for a building or a tool come back
if the game refuses the gold. Buildings take real time, one site at a time (two
from Headquarters 3), and finish on their own. A trip freezes its multipliers
(traits, tool, Headquarters bonus, hot spot, team) when it starts, so a building
finished later never re-rolls a haul.

**Adventurers and goblin hunters replay what was recorded.** A world chest's or a
loot goblin's loot is not a rule of ours: every opened chest or caught goblin is a
packet the plugin recorded in that region (current game build, protected values
present), replayed through the game's drop routine exactly like an expedition's
kills, with experience off (the hero did not kill it) and gold picked up. How many
chests or goblins a trip meets, which tier opens and who escapes are AFK FARM's
rules; world chests keep the game's key costs (a golden chest takes a Basic Key, a
crystal chest a Crystal Key) from the camp's key rack. A replay reads the live
room (item level, heroic chance and zone tables come from it), so a haul is
collected with an offline hero standing in the trip's region, through `afk.py
worker-replay` (the claim machinery: resumable, never twice). A game update ends
the replayability of old packets; such a trip is cancelled (keys come back).
Real chest objects are never created (the game's spawn check reports them).

**Keys and jeweler materials from the Vault.** The rack and the stock are filled
from the Vault's AFK Materials by the Item Editor itself (`POST
/api/vault/afk-take`, 2.16.1): AFK FARM never writes the Vault database. The
editor carries a request id out at most once (checked inside its write
transaction) and a cancelled id never takes. The panel keeps one receipt per
request (`vault_takes`) and settles anything but a clear "done" by cancelling:
the editor then reports the take it made, which reaches the camp once (even past
the Storehouse cap, since it already left the Vault), or makes sure that request
never takes anything. As with a gold payment, a lost answer can neither double
the keys nor lose them. The rack's caps (25-500) were raised for this: an
8-hour adventurer trip uses about 8 Basic and 3 Crystal Keys.

**The Jeweler uses the game's recipes.** The plugin reads the craft cube's table
(`worker recipes`: global.craftComboList / craftComboResult, result types 37-41,
amounts through PilipaliDecrypt) and, at delivery, reads the recipe again and
creates only its jewel or gem (15:78-96) with the ground-drop routine. Materials
come from the camp's stock, which miners fill by routing their Gem Sense share
there: the plugin rolls that share with the Prospector's recipe and dice but does
not make it (`route_prospect`, result `routed: true`; only such a result counts).
The hero's own Jewelcrafting level is neither needed nor changed.

**Teams** are several workers leaving together (tools/teams.py): each keeps its
own target and delivery; synergies and auras are multipliers, nothing more.

Chest openings were also taken out of expedition and Siege plans (0.7.x): they
had scaled like breakables and opened rare Abyss chests and locked chests
without keys.
