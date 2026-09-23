# AFK FARM panel contract — 0.7.0

The product interface is English only, including accessibility labels, server
messages and launcher dialogs. Number formatting uses en-US. Player names, game
region names, file paths and original CLI output are preserved.

During an active claim worker, matching native progress is shown as delivering,
paused or finishing. Incomplete-checkpoint recovery diagnostics are not presented
as an uncertain outcome while that worker is still completing the claim. Explicit
native failures and orphaned progress retain recovery warnings. No action is
enabled by this presentation distinction. Loot displays provisional generation
and filter counters; saved and transferred states still require their receipts.

## Local HTTP surface

`tools/panel.py` serves the packaged web assets at 127.0.0.1:8787 (or the next free
port through 8796). Reopening the EXE reuses an instance only when its version
matches. Older services keep their port; an update uses another available port.
The listener uses exclusive address ownership on Windows. `/api/state`
returns characters, profiles with eligibility reasons, fresh session state,
armed plan, progress, calibration quality, result stages, job, installation checks,
loot previews, recovery evidence, validation results and local portrait identifiers.
`/api/instance` identifies the application. `/api/action` accepts an allowlisted
JSON action and the `X-AFK-Token` from the page; Host and Origin are checked.

Actions: configure, install, launch, close, capture_start, capture_stop, start,
claim, claim_background, pause_delivery, cancel, ingest, restart_region,
validate_start, recover, portrait, save_modifiers, save_loot_filter, save_preferences, and since 0.7.0
wishlist_add, wishlist_remove, verify_special, worker_hire, worker_respec, worker_learn, worker_rename,
worker_start, worker_cancel, worker_collect, worker_transfer, worker_settle_partial (see 0.7.0 below).
`plan` is a compatibility alias for start, not a dry run. `claim` and
`claim_background` accept `speed` (normal, fast, max); a paused claim keeps the
speed written into its claim plan. `pause_delivery` is the only action that runs
beside another job: it sends `afk.py pause` (the plugin's saved abort) through the
single IPC writer lock and appends its output to the running claim's log.
Long actions return HTTP 202 and run in one worker. The browser polls JSON at
500 ms during recording or work, every 2 seconds while idle, and every 5 seconds
while hidden and idle. Polls cannot overlap. The countdown updates locally each
second. The panel renders raw CLI output as text in the log. It never interprets stdout
as game state. Empty values stay empty; no sample heroes or fabricated loot.

## Persistent data

All runtime data lives under `%LOCALAPPDATA%/Hero_Siege/afk`.

| File | Meaning |
| --- | --- |
| config.json | Game directory and optional passive capture flag |
| state.json | Schema 2: every armed expedition (one per hero) and the last settled claims |
| models/session-state-<request>.json | Request-owned current game state; removed after reading |
| reward-modifiers.json | Saved independent reward defaults for future expeditions |
| panel-calibration.json | Exact capture path, character and room chosen in the panel |
| sessions/capture_*.ndjson | session_start, farm_clock, kills, items, invalidation |
| profiles/*.json | Immutable-context profile plus latest room alias |
| plans/*.json | Single-region measured replay plan; panel_version marks product runs |
| sessions/<id>.progress.json | Native replay counters and state |
| sessions/<id>.failure.json | Authoritative failure, even if progress is stale |
| sessions/<id>.result.json | replay/save/ingest stages, rewards_saved and success |
| spool/<id>.ndjson | Native item structs and summary awaiting Vault |
| plugin-backups/ | Previous DLLs outside the Aurie loader directory |
| validations/*.reference.json | Forecast frozen before a separate recording |
| validations/*.result.json | Independent comparison with declared sample and error targets |
| portraits/<identity-hash>.png | User-selected screenshot, unchanged; never a rendered save |
| loot-filter.json | Vault transfer filter: gear rarities, keys/materials, game filter (schema 1) |
| preferences.json | Default delivery speed for new claims (schema 1) |
| delivery-rate.json | Smoothed calls per second per speed, learned from finished claims |
| sessions/<claim>.panel.json | Hero level and speed when the claim started, for the summary |

`farm_context` is `{schema:1,hash:<SHA256>,inputs:{...}}`. New panel profiles use
`profile_version:3`, `rate_basis:"farm-clock"` and identity version 2. A context
mismatch refuses recording and validation; claim requirements are described below. Start freezes the selected saved profile without contacting
the game; profile identity, eligibility and known build compatibility remain checked.
The current live hero's loadout has no bearing on another hero's offline timer.
Legacy profiles remain visible with a remeasure reason.
Only normal Act measurements are exposed. Context checks are once per second
while measuring and immediately before replay; they are not a universal simulation.

Each hero may have one armed expedition (0.7.0 roster, below). The hero picker remains usable during a timer;
an active expedition always displays its own frozen hero and region. Another hero
may be played or calibrated while it runs. Local actions do not acquire the monitor's
game-connection lock; an atomic job lock still prevents concurrent UI actions, and
the CLI reward lock still serializes clock/recovery operations across processes.

Calibration displays the current game's hero/location separately from a matching
selected hero's last recording. Unrelated past recordings do not supply the name,
counts or quality chart. An active recording displays its actual owner even when
the picker changes. Stale live state cannot report an active recording. Capture
statistics are cached by resolved path, region, modification time and size; file
changes invalidate the display cache. Saving and validation still read fresh evidence.
Past errors are labelled as a previous attempt, scoped to their action's page and
dismissible; their original log remains available.

Progress percent is calls_done/calls_total, clamped to 0–100, with zero when total
is unknown. A failure file overrides progress. A running/paused/unclean earlier
claim is never silently retried by the panel. `progress.resumable` (and
`recovery.status == "paused"`) marks an "aborted" or "paused" checkpoint for the
exact plan that carries the game's save receipt, no failed calls and no failure
file: Claim continues it with `afk.py claim`, which follows the existing claim plan
and never rescales it. Every other interrupted state still needs review.

The snapshot adds `delivery` (remaining calls and per-speed estimates from
`delivery-rate.json`), `preferences`, `loot_filter`, `repeat` (the last settled
expedition and a usable profile for it), `profile_live_matches` (profile id →
whether the loaded hero's pace inputs still match), `background` (whether Claim in
background can run, with the reason) and, per reward, `level_before`,
`level_now` and `delivery_seconds`. The browser estimates time left from the last
minute of live progress; notifications are a browser preference in localStorage. `rewards_saved` settles the clock;
`success` additionally reflects Vault status. Retry only ingest when Vault failed.
The Loot view excludes old developer results without panel_version.

Region loading pauses capture instead of splitting the recording. Unknown transient
player handles are loading states; a different resolved identity or changed loadout
invalidates the recording. Restart region visits the corresponding town and returns
to the original normal Act. It neither unlocks destinations nor automates combat.
The same-room travel helper alone does not reload a room.

Complete one-minute region-clock windows include idle windows. Quality labels use
descriptive variation and half-session drift; they are not confidence intervals.
Independent validation freezes the original source hash before capture and does
not replace its profile. A modified reference file, reused recording or changed
context refuses comparison; samples under 600 seconds / 200 kills / 95% coverage
cannot pass the declared 20% kill/XP error target.

Recover saved claim checks the exact plan hash, matching native checkpoint,
confirmed save callback, complete spool sequence and matching item/gold/XP totals.
It settles local bookkeeping without replay. A partial or contradictory claim stays
blocked. This is not an atomic transaction across game saves and the spool, or a
guarantee against power loss. GET /api/recovery-report?id=... exports the evidence.
Close as partial delivery (`settle_partial`, after a confirmation that states the
kept and abandoned call counts) is offered only for a claim that needs review. It
is refused while the live game reports a running replay or the checkpoint changed
in the last 30 seconds. It writes a result with state `partial`, `partial: true`,
`settled_by: player`, the original checkpoint state and the review reasons, credits
the delivered fraction of the hours and frees the armed clock. The checkpoint,
plan and spool stay untouched; no reward call is made. Such a result shows a
Partial badge and may be transferred to the Vault.
`recovery.inspect` reports `continuable` and `continue_blockers` for a claim that
needs review, and `records_after_checkpoint` when it can continue. Continue from
recorded position (`accept_position`, after a confirmation that states the counts)
requires a "running" checkpoint for the same plan, no failure sidecar, no failed or
skipped calls, and a spool whose records up to `spool_bytes` (the checkpoint's own
record of the spool size; older checkpoints must match the whole file) are complete:
contiguous sequence numbers, items and earlier "partial" markers only, as many items
as the checkpoint counts. It is refused while the game reports a running replay or
the checkpoint changed in the last 30 seconds. Bytes after `spool_bytes` are copied
to `spool/set-aside/<id>.after-checkpoint-<UTC>.ndjson` and cut from the spool; then
the checkpoint gets `resume_accepted: true`, `resume_accepted_by: player` and
`resume_basis`. The plugin's `CanResume` accepts a running checkpoint only with that
flag; the claim then continues like a saved pause and keeps its speed. An accepted
claim can still be closed as partial.
Reward operations share an OS lock. Snapshot recovery checks may be cached for two
seconds; actions always recheck the files. Vault retries never replay native rewards.

Loot previews include at most 500 items per expedition and the latest 20 product
results, with real item names/icons, rarity, search and filter visibility. Original
native spools remain intact. Favorites stay in browser localStorage. Rarity labels
refer to native itemInfoStruct[27], not unrelated rarity enums. Unknown tiers remain
numbered. Portrait uploads accept PNG screenshots up to 4 MB and 4096 pixels per
dimension. Portraits are indexed by character identity, not player name alone.

## Notifications and region comparison

`preferences.json` holds `delivery_speed` and `ready_notification` (default true).
`save_preferences` accepts either key. The panel's monitor keeps one Task Scheduler
task, `AFK FARM\Expedition ready`, in step with the armed expedition and the setting
(`tools/notify.py`, record `notify-ready.json`): created with `schtasks /XML` for the
local end time (StartWhenAvailable, least privilege, interactive token) running
`conhost --headless powershell.exe -File notify-ready.ps1`, which shows a toast under
Windows PowerShell's app id and deletes the task. It is deleted when the expedition
is claimed, cancelled or the setting is off; a refused request is shown and not
retried for the same expedition. The snapshot's `notification` reports `scheduled`
and `error`; the page's own "ready" notification is skipped while the task covers
the expedition.

The snapshot's `regions` lists, per hero with calibrations, one row per region (the
newest usable calibration, else the newest one with `usable: false`): calibration
`kills_per_min` and `xp_per_hour` (exp_per_min × 60), and from delivered claims
(saved, or closed as partial for their delivered share of the hours) `gold_per_hour`
divided by each claim's gold setting, `rarities_per_hour` for Unholy, Angelic, Heroic
and Satanic items at the Magic Find recorded (`magic_find`), `expeditions` and
`hours`. Vault labels are `<hero> · <region> · <time>`; a claim relabels its plan
with the credited time.

## Claim requirements

A claim needs the recorded hero (identity version 2), the same game build and the
recorded region. Gear, talents, levels and combat settings that changed after the
calibration do not block it (0.6.3; the note and the Start question say so since
0.6.4): the panel shows a note instead, and
`afk.py claim` writes the live `farm_context` into the new claim plan (keeping the
calibration's as `calibration_farm_context`) before the plugin starts. A delivery
with a checkpoint is refused when its context changed. Recording and validation
still require an unchanged context.

## Filtered items

`preferences.json` `filtered_items` is `convert` (default) or `keep`; the claim
passes it as `afk.py claim --filtered`, which stores it in a new claim plan as
`filtered_items` (a continued claim keeps its own). The plugin (0.6.2) reads it at
expedition start. `convert`: for an item whose floor object the loot filter hides,
below Satanic (itemInfoStruct "27" < 6) the sale value ceil(info "9" × max(1, def
"o")) is added to the frame's sale and the record is held; after the frame, one
`PickUpGoldCheck(GetCounterHash(), total, 1, …)` with the local player as self
credits it, and `GetGoldAmount` must rise by exactly that total. Selling requires
`onl` false, `Api_Exchange_Client_obj` absent or `apiExchangeConnected` false,
`Menu_Controller_obj` present and a pass-through `ReportClient` watch; a refusal, a
missing rise or any report writes the held records back as filtered items and stops
selling. Satanic and above equipment matching a unique recipe of
`global.prospectItem`/`global.prospectResult` (amounts through `PilipaliDecrypt`)
is broken down unit by unit with the game's `irandom`; fragments gather per
`type:id` and are created as full 999 stacks with `LootGroundCreate(x, y, type,
{o, b, j: 0, c: 0})`, the remainder when the delivery is done, each recorded with
`source: prospect` and `filter_visible: true`. Unmatched Satanic and above items
are kept. The checkpoint and the spool summary carry `conversion` (`sold_items`,
`sell_gold`, `prospected_items`, `kept_items`, `output_stacks`, `credit_failures`,
`recipes`, `note`, `pending`, `created`); a continued delivery restores it.
`afk convert probe [credit|make]` checks the recipe table, the safety state, a
1 gold credit or one fragment on the floor outside a delivery.

## User-facing limits

Do not offer --anywhere, --forgepact-ignore or migrate-build as ordinary fixes.
Guide users to the right character/region or a new measurement. One minute and
30 kills are minimum sample size, not “perfect calibration.” No full equipped
rendering, event completion parity, universal crash recovery or independent drop proof.

## Independent reward settings

See [Independent rewards](INDEPENDENT_REWARDS.md) for modifier behavior, native MF
rounding, one-time recalibration and optional ForgePact compatibility.


### Calibration versus an offline expedition

Calibration records the hero currently being played in Hero Siege. Selecting a
hero in AFK FARM selects a saved profile; it does not change the hero in the game.
Use **Explore → Start expedition** with a usable profile to start the timer while
the game is closed or another hero is loaded. The game is needed for the initial
recording and again to generate/claim rewards.

The calibration page links directly to **Open expedition planner**. Game-launch
actions are offered only with the game closed or on a confirmed main/character
selection menu; they do not replace a loaded character.

### Calibration save result

`calibration.outcome` is derived from recording JSON and saved profiles. It reports
status/title/message, the 60-second / 30-kill minimums and remaining amounts.
Only a usable profile matching the recording session, room and character yields
`saved` with a `profile_id`. Passing the live minimums yields `ready`, which is not
a saved profile or an accuracy guarantee. Validation is a separate outcome.

After capture stops, the panel persists `stopped_at`. A successful CLI process is
insufficient: `capture_stop` refreshes profiles and checks the saved-profile
postcondition. A skip or ineligible profile becomes a job error with a durable
`save_error`, and the raw recording stays untouched. An explicit
`save_profile: false` stops without invoking profile building, including if the
minimum is crossed between the button click and the stop command.

The main calibration card displays mm:ss, time/kill progress and the result.
Finishing is disabled below the minimum, with a distinct stop-without-profile
action. A successful profile links to its exact region/hero in Explore. Missing
profiles show the matching recording's reason there; failure never hides an older
usable profile. Raw CLI logs are retained verbatim and are not parsed for status.

### Map interaction

The map uses primary pointer capture on the pressed surface/region marker. Movement
of at least 4 CSS pixels starts panning and suppresses the resulting pointer click;
stationary clicks and keyboard activation still select regions. There is no inertia
or drag transition: movement follows the pointer directly. Bounds use the actual
stage and viewport dimensions, with no fixed pan-distance cap. Reversing direction
at an edge responds immediately.

Wheel deltas are normalized and zoom about the pointer; buttons and keyboard zoom
about the viewport center. Minimum zoom covers the viewport, maximum zoom is 240%
(or the cover scale if larger). The default scale is 115%. Resize reclamps the
camera. Blur, pointer cancellation and capture loss end the gesture.

Polling continues reading state during map input, but postpones full-page renders
until release / a 200 ms wheel-or-key quiet period. Explicit navigation cancels the
gesture. Focus is restored after a poll-driven render. Decorative map overlays do
not intercept input; only their controls do. Narrow layouts wrap the controls.

Verification: `node --test tests/test_panel_ui.cjs tests/test_map_ui.cjs`, followed
by real browser drag, wheel, click, keyboard and narrow-layout checks. No engine,
reward, calibration or game-save changes are needed for map navigation.


### UI presentation refresh (2026-09-24)

The five existing pages use a shared local obsidian/brass theme. Explore keeps the
interactive map and one expedition planner. Calibration separates the current
recording, its quality and saved profiles; the recording guide and independent
validation are optional disclosures. Loot has a single selected-expedition summary
and searchable collection. Delivery history opens by default while any displayed
result still needs transfer or review; saved, partial and transferred remain distinct.
Filter controls, comparison tables, help and setup diagnostics remain accessible in
labelled disclosures. Delivery failures, recovery decisions and confirmations are
not hidden by this simplification. A changed loadout notice still appears when known,
without suggesting that unchanged equipment is required to claim.

Open/closed disclosure choices and identified control focus survive polling and
rerenders within the page session. Navigation focuses the new page heading. Duration
presets and world switches expose their selected state to assistive technology.
Tables scroll within focusable, labelled regions on narrow screens; no data columns
are removed. The initial fetch waits for DOMContentLoaded so all deferred UI modules
are present before the first render. No API, reward rules or delivery logic changed.

Settings opens **Connection diagnostics** by itself while any setup check needs action
(its summary counts them); a section the player closed stays closed. The
notification card names the one-time Windows scheduled task it uses.

The isolated browser fixture accepts optional `presentation` fields in its temporary
control.json for UI-only empty, populated and interrupted states. These overrides
are not production API fields and do not enable native actions. See
[UI verification](UI_REFRESH_VERIFICATION.md) for tested paths and limitations.

## 0.7.0: hero roster, collection, Siege and workers

Everything below is served by `tools/panel.py`; the UI builds on these fields and
actions only. Product text stays English. Errors of an action arrive as
`job.error` (plain English, safe to show as text).

### Hero roster

`state.json` is schema 2: `expeditions` maps each armed expedition's id to its
record (`expedition_id`, `plan`, `started_at`, `hours`, `hero` = `slot:class:name`,
`mode`), one per hero; a 0.6 state with a single `armed` record reads as a one-hero
roster. `last_claims` maps each hero to its last settled claim (`last_claim` stays
the newest overall).

`/api/state?slot=N` (or `?expedition=ID`) chooses the **focus**: the top-level
`armed`, `plan`, `progress`, `recovery`, `delivery`, `background`, `notification` and
`repeat` describe the selected hero's expedition, or are empty when that hero has
none (the planner then applies). Without a query the live hero's expedition, else
the newest, is the focus (0.6 panels). `expeditions[]` always lists the whole
roster, oldest first: `expedition_id`, `hero`, `character`, `mode` (`farm` |
`siege`), `label`, `room`, `region`, `started_at`, `hours`, `ready_at`, `ready`,
`progress` {`state`, `percent`, `calls_done`, `calls_total`, `resumable`,
`reconciliation_required`, `pause`}, `recovery` {`status`, `recoverable`,
`resumable`}, `live_hero` (the hero loaded in the game) and, for a Siege, `siege`
(see below). `focus` echoes the query.

`start` refuses a hero that already has an armed expedition ("This hero's
expedition is already active..."); other heroes start their own. Every
expedition-bound action (`claim`, `claim_background`, `cancel`, `recover`,
`settle_partial`, `accept_position`, `pause_delivery`) takes an optional
`expedition` id; without it the server uses the hero in `slot`, else the live hero,
else the only one, and otherwise refuses ("Several heroes have an active
expedition. Choose which one."). Delivery is still one at a time: the plugin runs
one claim, for the hero loaded in the game. `repeat` is per hero and carries
`mode` and `siege_level`. `notification.tasks` lists every scheduled ready task
(`key`: `exp-<id>` or `worker-<id>`, `at`); `notification.scheduled` is the focus
expedition's.

### Collection, wishlist and share card

`web/collection.json` (tools/build_collection.py) lists the 916 collectible uniques
and set pieces. A delivered claim's record counts when its rarity (itemInfoStruct
"27") is Set or above and its native display name is a collectible's name.

- `/api/state` → `collection` {`total`, `found`, `wishlist`}; each reward gets
  `wishlist_hits` [{`key`, `name`, `rarity`, `icon`, `count`}] and `new_finds` (how
  many collectibles that claim found first).
- `GET /api/collection` → `total`, `found`, `by_rarity` {rarity: {`total`,
  `found`}}, `sets` [{`name`, `pieces` (keys), `found`}], `sets_complete`, `items`
  [{`key`, `name`, `rarity`, `set`, `tier`, `level`, `type`, `icon`, `found`,
  `count`, `first_at`, `first_hero`, `first_room`, `wished`}] (Unholy, Angelic,
  Heroic, Set, Satanic order).
- Actions `wishlist_add` {`key`} (a collectible key; up to 200) and
  `wishlist_remove` {`key`}. After a delivered claim, its wishlist drops produce one
  Windows notification (never twice for the same claim).
- `GET /api/share?id=<claim id>` → one delivered claim: `hero` {`name`,
  `class_name`, `level_before`, `level_now`}, `region`, `room`, `mode`, `siege`
  (a Siege's `siege_claim`), `hours`, `kills`, `exp`, `gold`, `items`,
  `visible_rarities`, `best` [{`name`, `rarity`, `group`, `icon`, `count`}],
  `partial`, `wishlist_hits`, `new_finds`, `new_finds_total`, `delivered_at`,
  `delivery_seconds`, `version`.
- `web/share.js`: `AfkShare.download(claimId)` draws the 1200×630 card and saves
  `AFK-FARM-<hero>-<date>.png`; `AfkShare.draw(summary, canvas)` only draws.

### Siege

`start` with `mode: "siege"` and `siege_level` (whole number 1-50) arms a Siege from
a usable profile (same hours limits). The plan keeps `siege` (the whole timeline,
drawn once from a stored seed; the UI must not show future waves). Rules:
`tools/siege.py` module note. Every 5 minutes a wave; level L's first wave demands
10 × 1.15^(L-1) kills per minute, +3% per wave; the gate (100) loses at most 50 per
wave the hero cannot keep up with and regains 5 on a wave cleared with 25% to spare;
kills equal farming while it holds; every 5th wave is elite, every 10th brings the
region's treasure goblins (when calibrated), every 25th a verified boss; +2% Magic
Find per level (up to ×100).

- Roster rows of a Siege carry `siege`: `level`, `waves_done`, `wave` (in progress,
  or null when over), `hp`, `gate_hp`, `fell`, `fell_at` (only once reached),
  `over`, `kills`, `held`, `last` (up to five past waves: `wave`, `kind`, `held`,
  `hp`), `next_special` {`wave`, `kind`}, `magic_find_bonus`. `ready` turns true
  when the siege is over (its report time); the Windows task fires then.
- A claim delivers the complete waves; its plan's `siege_claim` = `level`,
  `waves_fought`, `waves_held`, `fell`, `hp`, `elite_waves`, `treasure_waves`,
  `boss_waves`, `previous_best`, `record`. Settling it updates the hero's record.
- `GET /api/siege-forecast?profile=ID&level=L&hours=H` → `waves_total`,
  `waves_median`, `waves_low`, `waves_high` (10th-90th percentile), `fall_chance`,
  `kill_share` (kills relative to farming), `first_wave_demand`, `pace`,
  `magic_find_bonus`, `elite`/`treasure`/`boss` (whether such waves can come),
  `suggested_level` (the highest level whose median lasts the time), `best_waves`,
  `wave_minutes`, `max_level`.
- `/api/state` → `siege_records` for the focus hero: {room: {level: best wave}}.

### Special monsters (boss test)

Kills of special monsters (native rank outside 1-4: bosses, event monsters) no
longer close a calibration; they are left out of every plan until verified. A
kill without any rank still closes it. Profiles carry `special_kills`.

- `GET /api/specials` → `specials` [{`hash`, `monster_key`, `rank`, `room`,
  `region`, `kills`, `profiles`, `verified`, `verification`}].
- `verify_special` {`hash`} runs `afk.py special verify` (3 statistics runs of that
  packet through the game with XP and gold off and nothing sent to the Vault) with
  any offline hero standing in the packet's region. A clean run records it in
  `special-packets.json`; it then replays at its measured rate and in boss waves.

### Workers

Rules and numbers: `tools/workers.py` module note. `/api/state` → `workers`:
`crew` [{`id`, `name`, `type`, `level`, `xp_into_level`, `xp_for_next`, `points`,
`max_trip_hours`, `trip`}], `hire_price` (null when the crew is full, 3 workers),
`max_workers`, `pending_payments`. `trip` = `ore`, `ore_name`, `work_hours`,
`real_hours`, `started_at`, `ready_at`, `progress` (0-1), `ready`,
`credited_work_hours`, `planned` (a haul planned for delivery; it is never
re-rolled). `GET /api/workers` adds per worker `skills`, `time_factor`,
`respec_price`, `ores` (unlock level, `unlocked`, `digs_per_hour`), `stats`,
`history` (five latest), and the static `tree` (15 nodes: `id`, `branch`, `name`,
`max`, `requires` [node, rank] or null, `text`), `ores` and `find_names`.

Actions (all return errors as `job.error`):

| Action | Arguments | Needs the game |
| --- | --- | --- |
| `worker_hire` | `name` (optional) | an offline hero loaded; takes `hire_price` gold from that hero |
| `worker_respec` | `worker` | the same; takes `respec_price` gold |
| `worker_learn` | `worker`, `skill` | no |
| `worker_rename` | `worker`, `name` | no |
| `worker_start` | `worker`, `ore` (27-32), `hours` | no |
| `worker_cancel` | `worker` | no (nothing is mined) |
| `worker_collect` | `worker` (optional: every ready haul) | an offline hero loaded (any region) |
| `worker_transfer` | `delivery` | no; Item Editor must be running |
| `worker_settle_partial` | `worker` | no; closes a haul that stopped part way |

A payment runs through the plugin (`afk worker pay`): the gold must fall by exactly
the price and the game saves at once; a refused payment hires nobody. A haul is
delivered by the plugin (`afk worker deliver`) into `spool/worker_*.ndjson` and sent
to the Vault (materials go to AFK Materials) under
`AFK · Workers · <name> · <date>`; if Item Editor is closed the haul waits for
`worker_transfer`. Finished trips are also collected right after every claim while
the game is open. Worker trips schedule `worker-<id>` ready notifications.

| File | Meaning |
| --- | --- |
| workers.json | Crew (levels, skills, trips, stats, history) and payment records (schema 1) |
| models/worker-pay-<request>.json | The plugin's receipt of one payment |
| plans/worker_<id>_<time>.json | A planned haul (items, Gem Sense units, XP) |
| sessions/worker_<id>_<time>.result.json | The plugin's delivery result (`done`, `error` or `partial`), `ingest` |
| wishlist.json / wishlist-notified.json | Wishlist; claims already announced |
| collection-cache.json | Collectibles found per spool (display cache) |
| siege-records.json | Best wave per hero, region and level |
| special-packets.json | Verified special monster packets |
