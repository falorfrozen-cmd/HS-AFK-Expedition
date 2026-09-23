# Phase 0 notes — measured behaviour

Own words only; no game script text (hub AGENTS.md, Legal).

## 2026-09-17 — P0-0 coexistence

- Loaded next to ForgePact (`BloodPactPlugin.dll`, research build) and YYToolkit.
  The Tracker producer was not installed in the test game folder.
- The `DropItem` script-table entry already pointed outside `Hero_Siege.exe`
  (ForgePact's research build holds it). A table-only hook would therefore have
  reported success and captured nothing, because the monster death path calls the
  function directly.
- Fix that shipped: resolve the function by name at runtime from its own
  prologue's name-string reference plus the `.pdata` unwind entry, and place the
  inline detour there. Result: `native (inline detour at resolved function,
  chained behind an existing detour)` — the function's first byte was already a
  jump (ForgePact detours it too) and Aurie chained the two. The script table is
  left to whoever owns it.

## 2026-09-17 — P0-1 capture (Act 3, ~2 minutes of play, another character than the one used for P0-0)

- 232 `DropItem` calls, 232 captured, 16 distinct packets, 0 capture errors,
  16 full snapshots (the cheap identity check spared the other 216 kills).
- `DropItem` takes **12** arguments on this build (the static reading of "10"
  was the caller's stack shape, not the arity): kinds observed
  `int, int, real, real, int, int, string, string, int, int, int, int`.
- Snapshot: 312–313 instance variables per monster, ~17 KB per packet file.
- Experience resolves through the anti-cheat wrapper on every monster kill:
  rank 1 → 282, rank 2 → 776, rank 3 → 1 199 (same zone). The 50 kills with no
  `nameKey` are **destructibles** (`Desert_Hay_01_obj`: hay bales) — they go
  through the very same drop routine with their own short `dList`
  `[1, 4, 6, 9, 10]` and no experience. Calibration must count them as
  "breakables", not kills.
- `dList` by rank in that zone: rank 1 = 10 categories, rank 2 = 16, rank 3 = 18
  (the profiles the wiki package calls P4 / P3 / P2). Confirms the list is a
  function of rank and zone, not of the monster's identity.
- Cost: no visible frame impact at ~2 kills/s with the identity cache.

## 2026-09-17 — deep capture (second session, 192 kills, Act 3)

- `DropItem` arguments, by observation over 35 packets: arg0 = rank
  (`enemyRarity`; 5 on a shadow goblin), arg1 = 0, arg2/3 = x/y, arg4 = 1,
  arg5 = the player's magic find at the kill (325, equal to the monster's
  `extraMagicFind`), arg6 = the monster's `dropTable` ds_list, arg7 = its
  `exclusiveDrops` ds_list (undefined on breakables), arg8–11 = 0.
- `dropTable` is the per-category chance table: entries like `[4, 4.0, 14, 1.0]`,
  `[6, 10.0]`, `[9, 3.0]`, `[11, 5.0]`, `[41, 0.0]` — first element the drop
  category (the numbering in `HeroSiege_Wiki_Data/16_dusme_profilleri`), second
  the chance. This is the table the wiki package could not decode statically.
- Protected values resolved per monster (30 names): `dCommonChance` 4 / 20 / 36
  for rank 1 / 2 / 3, `dCommonDropMult` 11 / 20 / 42, `dSlots` 1–5, real
  `max_hp` (2.1 M / 4.0 M / 5.3 M in that zone), `damage`, `killExperience`,
  `lootAmount`, `extraMagicFind`. The handles are a contiguous block per
  instance; a freed neighbour reads back as 0, a far-away id **crashes the game**
  (learned the hard way with 999999).
- `variable_get_hash(name)` equals the variable id compiled code uses
  (`dList` → 103176, `dropTable` → 103471). The ids `DropItem` reads from self
  resolve to `dCommonChance`, `dCommonDropMult`, `dList`, `dSatanicDropMult`,
  `dSlots`, `dropTable`, `enemyRarity`, `enemyType`.
- `asset_get_index` returns a typed asset reference on this runner, not a
  number; validate with `object_exists`, never with a kind check.

## 2026-09-17 — P0-2 replay mechanics (synthetic packet from a spawned orc)

- Anti-cheat API confirmed: `PC_InitNewVariableFastGMLWrapper(value)` returns a
  fresh handle, `PC_GetVariableGMLWrapper(handle)` reads it back,
  `PC_FreeVariableGMLWrapper(handle)` releases it.
- Running the drop routine on a **live** enemy instance with reconstructed
  arguments produced ground loot (30 calls → 1 item on a 4 % monster). Zone,
  arguments and the routine's early checks are therefore fine even in
  `Act_01_Pregame`.
- A ghost of an inert object type, carrying all 313 variables and real
  protected handles, produced **nothing** in 60 calls. Changing the ghost's
  type to the monster's object with `instance_change(obj, false)` (no Create
  event) before the call, and back before destroying it, fixed it: 200 calls →
  2 items + 1 coin pile, 0 failures, no crash. The drop routine keys on the
  instance's object type, not only on its variables.
- `instance_count` rises by one per replay within the frame because destroyed
  instances are still counted until the step ends; use per-object
  `instance_number` diffs (`afk census`) to see what a replay created.
- Ground loot objects: `Loot_Ground_obj` (items) and `Coin_obj` (gold), both
  children of `Pickup_Parent_obj`. These are what the spool interception will
  catch in Phase 1.

## 2026-09-17 — third session and first real-packet replay

- Third capture session: 901 kills / breaks in ~5 minutes of play, 79 packets,
  36 of them real deep packets with list contents (rank 1: 18 chance entries,
  rank 2: 24, rank 3: 26; a chest: `[[1,75],[3,3],[4,1,33,1],[6,3],[9,100],[10,8],[15,1],[58,true]]`).
- arg5 of `DropItem` is **not** the player's magic find constant: it varies per
  kill (0 on most kills, 175 / 325 / 450 / 500 on some), so it is a per-kill
  bonus the game computes at death. Packets carry it; the profile's packet
  weights carry its distribution.
- Replaying a **real** rank-2 orc packet 600× through the ghost: 276 items,
  24 coin piles and 2 loot pillars (the beam the game shows on rare drops),
  0 failures. About 0.46 items per call, against 0.01 for the placeholder
  packet of a spawned rank-1 orc — the replay follows the packet's chances.
- Replays do not create kill records: the extra kill lines in the session were
  the player's own (timestamps inside the play window).

## 2026-09-17 — P0-2 fidelity: live instance vs ghost, same monster, by code

Method: spawn an Orc Warrior, run the drop routine 4 000× on the live instance
(`afk replaylive`), take a packet from that same instance (`afk snapshot`) and
replay it 4 000× through the ghost (`afk replay`). Items are attributed by the
`CreateItemNew` hook (`ctx` field in the session file); `tools\compare_replay.py`
compares them. No player involvement.

| | live instance | ghost replay |
| --- | --- | --- |
| calls | 4 000 | 4 000 |
| items | 45 | 52 |
| rarity 1 / 2 / 3 / other | 27 / 13 / 4 / 1 | 35 / 14 / 2 / 1 |
| coin piles | 33 | 17 |
| item-type chi-square | 13.9, df 10, crit 18.3 → consistent at 95 % | |

Items: consistent in count, type and rarity. Coins looked lower on the ghost
path in this sample, so both were re-measured:

| cumulative | live instance | ghost replay |
| --- | --- | --- |
| calls | 9 000 | 14 000 |
| items | 105 (1.17 % per call) | 169 (1.21 % per call) |
| coin piles | 65 (0.72 %) | 93 (0.66 %) |
| rarity 1 / 2 / 3 / other | 67 / 29 / 6 / 1 | 95 / 37 / 8 / 2 |
| item-type chi-square | 18.9, df 11, crit 19.7 → consistent at 95 % | |

**Verdict: P0-2 passed.** The ghost reproduces the game's own drop behaviour for
the same monster within sampling noise, for items, rarity and gold. 10 000
replays run inside one frame without a crash (the command reply arrives a few
seconds later; `tools\dev_cycle.ps1` may time out waiting for it — read
`afk_ipc\out.txt`).

## 2026-09-17 — Phase 1: experience, gold, spool, the expedition executor

Experience (STATIC + MEASURED):

- The spawn alarm of the enemy parent object computes the kill's experience
  (`EnemyCalculateExperience`: zone, level gap, the player's experience
  stats) and stores it in the monster's protected `killExperience`. The
  death event reads that value back through the protected getter and hands
  the **number** to `EnemyGiveExperience(amount)`, which computes the
  counter hash and calls `ExperienceUpdate(amount, hash)`. The variable id
  the death event reads was confirmed at runtime (`afk varid` on the static
  slot → `killExperience`).
- The packet's captured `exp_reward` is the monster's base `experience`;
  `killExperience` is what the player actually receives (2 250 vs 776 on the
  rank-2 orc, 3 477 vs 1 199 on a rank-3 beast: the calibration character's
  experience bonus). Profiles use `killExperience` from the packet.
- The ghost replays this exactly: `EnemyGiveExperience(<killExperience>)`
  with self = the ghost typed as the monster, other = the player. The hook
  on `ExperienceUpdate` then sees `a0 = 2250` with the game's own hash, self =
  the monster object. 200 replays → 450 000 credited; an expedition of 1 180
  calls credited exactly the sum of its packets' values.
- Never call `ExperienceUpdate` yourself: it verifies the hash and reports a
  mismatch to the game's own cheat reporting (`ReportClient`), and passing an
  instance reference as the amount was credited as that number (~260 000 per
  call). Calling `EnemyGiveExperience` with **no** argument crashes the
  game. The slot-0 test character was mangled by those research calls before
  the path was understood (its level and experience no longer match its
  earlier saves); it is a throwaway.

Gold (MEASURED): coins are `Coin_obj` instances whose amount is the protected
`goldValue`. Destroying them credits nothing; the game credits gold when the
player picks a coin up (`GoldLogAdd(undefined, amount)` with self = the
player). The spool therefore teleports each new coin onto the player and lets
the game pick it up: 14 piles / 72 gold → the purse rose by exactly 72. A set
of already-handed-over coin ids stops the double counting an earlier build
had.

Spool: every item built during a replay is serialised from the game's own
item struct inside the `CreateItemNew` hook and the floor object returned by
`LootGroundCreate` is destroyed at once, so nothing lands on the ground. 3 000
replays of the orc packet: 489 items, 121 coin piles / 701 gold, 6.5 M
experience, 40 s at 5 calls per frame.

Executor: an expedition is a plan of (packet, count) pairs replayed a few
calls per frame (default 40, time-boxed at 10 ms) with a progress file
rewritten after every frame. It resumes from that file after a crash, an
abort or a quit, carries its counters across, pauses while there is no player
instance, and skips a packet that fails five times in a row (old packets
without protected values are refused up front). A parsed packet must be
anchored in a global variable while it is replayed across frames: the
runner's garbage collector freed a struct held only by C++ references and
the second frame of the first multi-frame run crashed at "build arguments".

Character stamp (MEASURED): `GetSelectedSlot(0)` with the player as self
answers the selected save slot as a ds_map handle (a typed reference on this
runner, not a number) with 127 keys: `name` (string), `class`, `level`,
`experience`, `herolevel`, `incarnation_exp`, `playtime`, the statistic
counters and the settings — the numbers are anti-cheat handles, read through
the wrapper. Without the argument, or without a player self, it answers a
bool. `afk who` and `afk level` use it; the profile records the stamp from
the capture's session_start and the claim compares name and class.

## 2026-09-18 — first run on the user's own character (Act 6)

- Calibration: 490 kills / breaks in 144 s of Act_06_01 with the level-100
  character, 68 packets → 196.7 kills/min, 431 097 exp/min.
- Plan 0.1 h → 1 180 kills + 45 breaks = 1 225 calls, preview 2 586 256 exp.
- Run on the live character: 1 225 calls in a few seconds, 369 items, 27 coin
  piles / 1 360 gold, 2 586 256 exp credited (preview and credit identical),
  purse 1 285 822 → 1 287 182 (+1 360, to the coin), 1 call failed.
- Vault ingest with the editor running: 369 deposited, 0 duplicate, gear on
  "AFK Farm / sgham_test1 · 2026-09-17 · Act 6 deneme", stackables in
  "AFK Materials".
- Character stamp correction: `GetSlot(i)` / `GetSelectedSlot(i)` take a
  **slot index**, so the stamp had been reading slot 0 while another character
  played. The live player's `name` (a `Player_obj` variable) matched against
  the slots' `name` keys identifies the right slot.

## 2026-09-18 — persisting the rewards (the user's hard quit lost them)

- Closing the game window right after the first real run lost both the
  expedition's gold and the user's own kill gold: the character file kept its
  map-entry timestamp and the account gold file (`hs2saves\shop.ini`,
  section `[gold]`, keys `gold` / `gold_hc`) kept the previous day's value.
  Experience for a level-100 / hero-level-300 character is discarded by the
  game anyway (`CalculateExpRequired(100)` = 535 338, the bar sits at the cap).
- Where things live: character level / experience / hero level in
  `herosiege<slot>.hss` (an INI, `[0]` section), account gold in `shop.ini`.
- `SaveLocalFile` (the game's local save: SaveStart → SaveSlot → SaveCommit →
  SaveStash → SaveLogin → SaveLocalSettings → … → SaveCharacter) writes the
  character file but **not** the gold file; called with the level-up shape
  `(0, 2)` it threw, with the phantom single argument it wrote the character
  file only. `SaveAccountData`, `ControllerOnlineLocalGoldSync`,
  `CurrencyUpdateJson`, `SaveStash` alone changed nothing on disk.
- What works: performing the Controller's **Room End** event
  (`event_perform(ev_other = 7, ev_room_end = 5)` with self = the
  `Controller_obj` instance). It runs ZoneStateSaveController, SaveLocalFile,
  ControllerOnlineLocalGoldSync and OnlineSave: +30 gold by replay → `shop.ini`
  and the character file rewritten, purse 1 285 105 after a relaunch. The
  game keeps running normally afterwards. The Game End event (7 / 3) also
  works but is not needed.
- Shipped: the plugin performs that save whenever an expedition stops
  (done, aborted, error) with anything credited, reports it in the progress
  file (`saved`), and `afk save` does it on demand; the command line asks for
  it once more after a run.
- Parser bug fixed on the way: a failed `>>` extraction leaves a std::string
  untouched, so `afk call X` had been passing X's own name back as a phantom
  argument (read as 0). That is why `GetSelectedSlot` "needed" an argument.

## 2026-09-18 — first real offline-clock cycle (start → quit → claim)

- Plan: 0.5 h of Act_06_01 for the level-100 character (5 900 kills + 225
  breaks). Armed, game closed, 22 minutes later the character re-entered a
  map and `claim` scaled the plan by 0.748: 4 415 kills + 168 breaks = 4 583
  calls, preview 9 679 062 exp.
- Result: 4 583 calls, 0 failed, 1 268 items into the Vault, 106 coin piles
  / 5 566 gold (purse 1 285 132 → 1 290 698, to the coin), 9 677 761 exp
  credited (one kill packet gave no experience: 4 414 exp calls for 4 415
  kills; the game discards it at the cap anyway). The plugin's automatic
  room-end save wrote the account gold file at the moment the expedition
  stopped; the purse read the new value in the next command.
- Pace was 40 calls/s this time (86 s for the run) against ~250/s in the
  earlier tests: the game window was in the background while the user chatted,
  and thousands of experience popups (`Combat_Text_obj`, one per call)
  accumulate. Acceptable; worth a look later (skip the popup, or cap per
  frame by time only).
- CLI fixes from this run: the reply reader now waits for the plugin's
  `---- done ----` marker (loading 68 packets took longer than one poll, so the
  claim was reported as "not started" while it was running); a claim whose
  progress file already exists is followed, never rescaled or rewritten, so a
  second `claim` after an interruption cannot change the counts the plugin
  holds.

## 2026-09-18 — fidelity against real play, same zone, same character

Method: the user played Act_06_01 with capture on (1 018 kills and breaks,
182 items produced inside the drop routine, +973 gold walked over, 109
packets); a plan with exactly those kills was replayed in the same map, once
credited and five more times with experience and gold off (statistics only).

| | live | replay ×6 |
| --- | --- | --- |
| items per 100 kills | 17.9 | 16.0 (runs: 15.7, 17.6, 16.7, 14.5, 16.4, 15.3) |
| gold per kill | 0.96 | 0.91 |
| rarity normal / magic / rare / satanic / unique | 48 / 35 / 10 / 6 / 1 % | 48 / 34 / 13 / 3 / 2 % (χ² 3.6, crit 9.5) |
| item class | | χ² 6.6, crit 15.5, consistent |
| rank 1 items per 100 | 1.3 | 1.2 |
| rank 2 items per 100 | 30.2 | 30.5 |
| rank 3 items per 100 (79 kills) | 134 | 117 (104–130) |
| breakables (27) | 5 items | 4 in every run |
| the one champion | 10 items | 6–8 |

Items for the 31 elites killed exactly once: live 49, replays 49 / 48 / 45 /
41 / 45. Live shows fewer empty elite kills (4 of 31) than the replays (7–11
of 31) and fewer 4+ multi-drops; totals agree. Set items and materials: none
in 1 018 real kills, none / one in the replays; keys 1 / 0; gems 2 / 3.

Reading: rarity, class, gold and the common ranks match; the live sample sits
about 10 % above the replay mean on items, driven by elite multi-drops, at
1.5 standard deviations of a 1 000-kill sample. Not a proven gap, not
excluded either. Two mechanical differences remain candidates: the ghost
drops at one fixed point next to the player (`LootGroundCreate` places loot
through `CreateLootInFreePos`), and the drop call's constant arguments 1 and
4 (captured as 0 and 1) may encode caller state. A larger paired sample
settles it: capture stays cheap, so the next calibrations should simply be
longer.

Changes made on the way: `extraMagicFind` (the drop call's 6th argument, a
per-monster champion bonus of 175–3 575) and `killExperience` are part of
the packet identity now, so bonus kills get their own packets and their
true frequency (109 packets from this session against 68 before); `claim`
and `run` refuse to replay outside a calibrated zone (`--anywhere`
overrides) after a town replay produced 8 % fewer items than the zone.

## 2026-09-18 — the two fidelity candidates, and the loot filter

- Constant drop-call arguments (1 and 4): captured as 0 and 1 in every one of
  the ~2 500 kills recorded so far, across zones, ranks and champions. Caller
  state would have shown variation; it did not. Excluded.
- Placement: `LootGroundCreate` → `CreateLootInFreePos` tries positions in a
  circle (`IsObtainablePosition`, `mp_grid_free`) and answers -4 when none
  is free. Irrelevant to the counts compared above: both the live and the
  replay item counts come from the `CreateItemNew` hook, which runs before
  placement. A record now carries `placed:false` when the floor object never
  came back, so a real placement loss becomes visible.
- What the drop routine reads that could still differ between a fight and an
  idle replay: 18 `GetBuff` / `GetBuffStack` queries (the player's temporary
  buffs at kill time). A calibration played under a shrine or potion buff
  bakes nothing of it into the packets, so the replay runs with the idle
  state. Not measured yet; the growing live baseline will show it.
- Loot filter: a floor item (`Loot_Ground_obj`) carries the verdict of the
  player's own in-game loot filter after its Create: `lootFilterVisible`,
  `lootFilterHighlight`, `skipLootFilter` (MEASURED on a live "Rapid Maul"
  that the filter hid). The spool now records `filter_visible` and
  `filter_highlight` per item (the item record is written from the
  `LootGroundCreate` hook once the floor object exists) and the Vault ingest
  skips hidden items unless `--keep-filtered`. The summary line reports
  `items_filtered` and `items_unplaced`. The filter file is
  `hs2saves\lootfilter_<slot>.txt` (base64 of the game's binary encoding).

## 2026-09-18 — passive calibration and the built-in proof

- `afk capture auto on` writes `auto_capture` into `afk\config.json`; the
  plugin then arms capture by itself whenever the hook is in and a player
  instance exists (checked every 5 s), so ordinary play keeps extending the
  live baseline. `afk.py verify --runs N` rebuilds the newest capture's
  profile, replays exactly its kills N times with experience and gold off
  (plans marked `stats_only`, spools not ingested) and prints live against
  replay per 100 kills, by rarity and by rank, plus the share the loot
  filter would hide. First run on the 1 018-kill Act 6 sample: 17.9 live vs
  16.3 replay items per 100 kills (runs 178 and 153), rarity 48/35/10/6/1 vs
  48/34/14/3/2, the filter would hide 63 % of the replay items.
- Loot filter test on the real character: 37 items, 26 hidden by the
  player's filter, 11 deposited.

## 2026-09-18 — a real defect: incomplete packets

- 20 of 317 monster packets lacked the protected values the drop routine
  reads (`dSlots`, `dCommonChance`, `dCommonDropMult`, `dSatanicDropMult`,
  `extraMagicFind`, `killExperience`, `lootAmount`). Cause: the capture only
  resolved handles within ±2048 of four anchor handles, but an enemy's
  handles sit up to 75 000 below and 600 000 above them. The ghost then got
  the raw handle ids as plain numbers: the wrapper read whatever those ids
  pointed to at replay time (wrong slots, wrong chances, silently) or threw
  once the ids were dead (`e_grave_ghoul_2` packet `df11056f0a92`: 9 of 9
  calls failed in a later session, 1 of 9 an hour earlier). Fix: the capture
  resolves the 34 names that are protected on every enemy by name (a live
  instance's own handle is always valid), the window stays as a catch-all;
  `LoadPacket` refuses a monster packet without the six drop-critical
  values; the profile builder counts such kills under a complete sibling
  packet (same monster, rank, room) or reports them as left out.
- The chest packet `27fdb67d71c7` (Chest_Drop_obj) was the same defect, not
  a mystery: its snapshot holds `dSlots`, `dCommonChance`, `dCommonDropMult`
  and `dSatanicDropMult` as raw handle ids (165 557–165 560) that the window
  never resolved, because breakables were assumed to have no protected
  values. One replay of it produced 4 items in one session, 0 in the next and
  6 650 in a third (the id had drifted onto a counter; the run took 25 s to
  build them all). The completeness rule now applies to breakables as well:
  any drop variable that looks like a handle in the snapshot must be
  resolved, or the packet is refused. The three statistics runs polluted by
  that chest were deleted; the credited runs before it took 4 items from it.

## 2026-09-18 — bosses, treasure goblins, popups

- Bosses and treasure goblins need no separate mechanism: they die through
  the same enemy death path (`Enemy_Parent_obj` Destroy → `DropItem`;
  goblins additionally `DropGold`, which the ground-loot creation already
  replays), so a boss or goblin killed with capture on is an ordinary packet
  and the profile replays it at the rate the calibration showed. What a
  short calibration cannot show is their rate, so `plan --extra
  PACKET=PER_HOUR` adds a chosen packet at a chosen rate (a "bounty"), and
  `afk.py packets --find <name>` lists what was captured. The death event's
  own goblin spawn roll (`Goblin_Orb_obj` / `Goblin_Shadow_obj` through
  `CreateInFreePos`) is not replayed; the goblin's kill is, when captured.
- Each replayed kill spawns a floating experience text (`Combat_Text_obj`);
  thousands alive dragged the pace from ~250 to ~40 calls per second in the
  first real claim. The replay now removes the ones each call created.

## 2026-09-18 — ForgePact integration

- Hook order (MEASURED): the AFK detour on `DropItem` sits outside
  ForgePact's, so a replayed call goes through ForgePact's drop-multiplier
  hook exactly like a real kill: `dropmult item 5` turned 50 items per 300
  replays of the same packet into 273. Capture sees one call per kill, so
  the profile's rates are not inflated by the multiplier. The same holds for
  its magic-find and Angelic-die hooks: whatever the panel has on at claim
  time applies to the replay.
- What is baked instead: kill experience (ForgePact's experience multiplier
  acts when the spawn alarm computes it), and everything that changes how
  fast the player kills (Monster Density, damage / attack speed / cast rate,
  movement, Monster Rarity shares, Special Content, enemy speed) through the
  measured kills per minute.
- Shipped: the plugin embeds the panel's `forgepact.json` in every
  session_start and expedition summary; the profile and plan keep a
  normalised summary; `claim` / `run` compare it with the panel's current
  file, print reward-side differences as information and refuse on
  rate-side differences unless `--forgepact-ignore`. The user's current
  panel: density x2, experience x4, magic find x50, movement x4, attack speed
  +25 %, cast rate +27 %, Monster Rarity 20/20, Chaos Tower x74, Angelic x100.

## 2026-09-18 — rewards that are not kills (rift, battlefield, chaos tower)

Static reading, before instrumenting:

- A rift / wormhole completion is `WormholeGiveReward`: two combat texts, a
  `LootExplosion(a, b)` call (two arguments), then the hashed
  `ExperienceUpdate` for the completion experience, season bookkeeping and a
  network message. It builds no items itself.
- `LootExplosion` calls `DropGold`, `LootGroundDrop` and `LootGroundInit`:
  it places gold and items through `LootGroundDrop`, not through the
  `LootGroundCreate` path the kill replay intercepts. `LootGroundDrop` is
  also what `CreateItemDrop` / `CreateItemDropInstance` use (items handed to
  the ground from an existing item struct).
- The Chaos Tower completion lives in a method of `Spawn_Next_obj` (quest
  and achievement updates, community quest, an API request,
  `SetSlotChaosTower`, `LootExplosion`, then one specific `LootGroundCreate`
  with a definition built from `CreateDefaultParams`).
- `DropBattleFragments` → `LootGroundCreate` (battle fragments are ordinary
  ground items). No Battlefield / Rift / Wormhole object event calls a drop
  routine directly; their rewards go through the scripts above.
- Cursed orbs, every chest type, treasure goblins, piles, Santa's sack and
  the Chaos Tower reward pickup all call `DropItem` themselves, so they are
  already captured as packets when the player triggers them.
- Shipped: pass-through observers on `LootExplosion`, `WormholeGiveReward`,
  `DropBattleFragments`, `CreateItemDrop`, `CreateItemDropInstance` and
  `LootGroundDrop` write an `event` line (script, self object, arguments)
  into the capture session and label the items built inside them
  (`ctx: live_event`), so the exact shape of each reward comes out of
  ordinary play. The replay side follows once a few real completions have
  been recorded.

## 2026-09-18 night — coverage map (STATIC call graph, old exe, direct calls)

Every route by which an item reaches the ground, from a scan of all `call`
targets in the executable (264 direct calls to the hand-out routines):

- `DropItem` has 19 callers: `Enemy_Parent_obj` Destroy (every monster), all
  chests (Chest_Drop, Abyss, Colossal, Dungeon, Ruby, Vanaheim), the **Cursed
  Orb** (`Cursed_Orb_obj` Destroy, set up with `LoadMonsterDropModifiers` in
  Create), the goblin orbs (`Goblin_Orb/Ore/Rune/Shadow/Treasure_obj`
  Destroy), destructibles and piles, Santa's sack, the Chaos Tower reward
  pickup (`Chaos_Tower_Reward_obj` collision) and a pirate talent. All of it
  is one hook: captured as a packet, replayed.
- Everything a kill can add sits inside `LoadDrops`, whose only caller is
  `DropItem`: `DropMonsterGold`, `DropExclusive`, `DropKeys`, `DropJewels`,
  `DropBossParts/Gems/Runes`, `DropAngelicKey`, `DropBifrostKey`,
  `DropCPL*`, `DropBlacksmithsMallet`, `DropBattleFragments`,
  `DropRiftItems`. Replayed with the kill.
- `EnemyGiveExperience` has one caller, `Enemy_Parent_obj` Destroy.
- **Summoning portals** (`Summoning_Portal_obj`, an enemy-like object with
  `ReturnEnemyStats`) call no hand-out routine at all; their yield is the
  summoned monsters, i.e. ordinary kills. **Chaos pillars** (`Chaos_Pillar_obj`)
  call `BuffAdd` and `CreateShrineEffect`, no drop routine; their yield is
  the waves, again ordinary kills.
- Outside the kill path, and therefore recorded as events but not replayed:
  `LootExplosion` (Chaos Tower completion via `Spawn_Next_obj`, wormhole via
  `WormholeFinish → WormholeGiveReward`), `LoadBossDeath` (nine extra
  `LootGroundCreate` in `Enemy_Child_Boss_obj` Destroy), a few monsters with
  one extra direct `LootGroundCreate` in their Destroy (shadow goblin,
  Commander Albert, Curacan Legion, Scorchwood, Valkyrie, rat), the uber
  charm (`Uber_Endrixia_obj`), shrines (`CreateShrineEffect`: seven
  `LootGroundCreate` and a `DropGold`), mining nodes, the slot machine, quest
  rewards and the vault. None of these is a per-kill rate.
- Data check: no capture session so far contains a cursed orb or a summoning
  portal; the rift session holds one shadow-goblin extra drop as an event.

## 2026-09-18 evening — game update, compact identity, the gap is gone

- The game updated (`exe-281751552-pe6aaa6779-111b8000`; the old one was
  `exe-281798144-pe6a9ed3ee-111c3000`). Every hook resolved by name on the
  new build without change. The build identity is now the PE link stamp and
  image size, not the file time (a re-copy of the old exe had made every
  packet "another build"); `afk.py migrate-build` re-labels existing data.
- Packet identity is now the drop routine's own inputs (`dList`,
  `dropTable`, `enemyType`, `dSlots`, `dCommonChance`, `dCommonDropMult`,
  `dSatanicDropMult`, `extraMagicFind`) instead of the affix list: with
  Monster Rarity on, nearly every elite had a unique affix combination and a
  full snapshot was taken on almost every kill (2 512 packets, 47 MB in one
  afternoon, and a likely cause of a crash during a mass kill). Experience
  varies per kill and now travels in the kill line (`kill_exp`); the profile
  uses the mean per packet and the plan hands it to the replay.
- Rift material: `DropRiftItems` runs inside a kill's `DropItem`
  (`DropItem → LoadDrops → DropRiftItems`, 2.9 calls per rift kill) and
  places Greater Unstable Dust and Dimensional Shards through
  `LootGroundCreate`, so rift materials were already part of every replayed
  rift kill. The observers now ignore anything reached inside a live kill;
  the 2 030 event packets they had written for it were deleted.
- Battlefield: 1 631 kills, 7 chests, 2 treasure goblins, no separate reward
  routine; everything is an ordinary packet. The rift completion itself
  produced no `WormholeGiveReward`; the Chaos Tower completion remains the
  only true event reward seen so far (`LootExplosion(x, y)` from
  `Spawn_Next_obj`), not yet replayed.
- Fidelity on the new build, same room the character stood in
  (`Unstable_Rift_03_03`, 2 139 live kills, two statistics replays): items
  per 100 kills live 100.3 vs replay 102.7 (2 177 / 2 216 against 2 146,
  inside the live 2-sd band); rarity 43/34/15/7/2 vs 45/33/14/7/2 %; rank 4
  (279 champions) 336.6 vs 339.1 per 100; rank 3 131.1 vs 138.5; rank 2
  33.3 vs 29.4; rank 1 1.0 vs 1.8. The earlier elite shortfall is gone: it
  was the incomplete-packet defect.

## 2026-09-17 — what does not trigger the drop routine

- Spawning a monster with ForgePact's `spawnname` and destroying it with
  `instance_destroy` does **not** call `DropItem` (hook counter stayed at 0).
  The Destroy event alone is not the death path; the drop is driven from the
  kill handling that runs when health reaches zero. Consequence for tests: a
  controlled kill needs the game's own damage path (or a later replay-side
  helper), not `instance_destroy`.
- No script named like a generic "damage/kill enemy" routine exists in the
  script table; damage is handled inside object events, so code-driven kills
  are not available yet. Calibration samples come from real play for now.
