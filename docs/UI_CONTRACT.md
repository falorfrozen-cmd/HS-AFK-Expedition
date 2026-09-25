# AFK FARM panel contract — 0.9.0

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
Windows PowerShell's app id and deletes the task. Since 0.7.0 the toast is a reminder:
it stays on screen until the player closes it (over a full-screen game a plain toast
was only heard, never seen) and its **Open AFK FARM** button opens the panel's address
(`-Url`, a local `http://127.0.0.1:<port>/` only). It is deleted when the expedition
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
  (a Siege's `siege_claim`), `hours`, `kills`, `exp`, `gold` (everything the claim
  paid: `gold_drops`, the gold picked up, plus `gold_sales`, what the game paid for
  the filtered items it sold), `gold_drops`, `gold_sales`, `items`,
  `visible_rarities`, `best` [{`name`, `rarity`, `group`, `icon`, `count`}],
  `partial`, `wishlist_hits`, `new_finds`, `new_finds_total`, `delivered_at`,
  `delivery_seconds`, `version`.
- `web/share.js`: `AfkShare.download(claimId)` draws the 1200×630 card and saves
  `AFK-FARM-<hero>-<date>.png`; `AfkShare.draw(summary, canvas)` only draws.

### Siege

`start` with `mode: "siege"` and `siege_level` (whole number 1-50) arms a Siege from
a usable profile (same hours limits). A Siege lasts whole waves: `hours` is rounded
down to 5 minutes (0.3 h arms 15 minutes, 3 waves), so offer 5- or 15-minute steps
and label the Siege by its plan's `hours`. The plan keeps `siege` (the whole timeline,
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
  `waves_fought` is how many waves the gate faced (the record and "N waves");
  `waves_held` only counts waves whose demand the hero fully met, and can be 0 in a
  siege that held to the end.
- `GET /api/siege-forecast?profile=ID&level=L&hours=H` → `waves_total`,
  `waves_median`, `waves_low`, `waves_high` (10th-90th percentile), `fall_chance`,
  `kill_share` (kills relative to farming), `first_wave_demand`, `pace`,
  `magic_find_bonus`, `elite`/`treasure`/`boss` (whether such waves can come),
  `suggested_level` (the highest level whose median lasts the time with a
  `fall_chance` of at most 25%: the gate usually still stands), `best_waves`,
  `wave_minutes`, `max_level`.
- `/api/state` → `siege_records` for the focus hero: {room: {level: best wave}}.

### Special monsters (boss test)

Kills of special monsters (native rank outside 1-4: bosses, event monsters) no
longer close a calibration; they are left out of every plan until verified. A
kill without any rank still closes it. Profiles carry `special_kills`.
Chest openings (`Chest_Drop_obj`, `Abyss_Chest_obj`, `Dungeon_Chest_obj`, ...) are
recorded as breaks but never replay in an expedition or a Siege: a golden or crystal
chest costs a key the replay would not take, and the Abyss chest is a rare map event.
Profiles carry `chest_breaks` (how many were recorded) so the page can say so.

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
the price and the game saves at once; a refused payment hires nobody. Every request
has one receipt and the game never runs a request twice. `pending_payments` lists
recent `pending`, `unknown` and `refused` ones: `unknown` means the game did not
answer within 60 s. It is not a refusal, the game may still take the gold: the panel
completes the purchase (hire or respec) as soon as the receipt appears, and a new
payment first finishes the unanswered one under its own request id (charging at most
once) or is refused while the game stays silent. Show `unknown` as "waiting for the
game", never as "failed", and do not offer to buy again meanwhile. A haul is
delivered by the plugin (`afk worker deliver`) into `spool/worker_*.ndjson` and sent
to the Vault (materials go to AFK Materials) under
`AFK · Workers · <name> · <date>`; if Item Editor is closed the haul waits for
`worker_transfer`. Finished trips are also collected right after every claim while
the game is open. Worker trips schedule `worker-<id>` ready notifications.

| File | Meaning |
| --- | --- |
| workers.json | Crew (levels, skills, traits, tools, trips, stats, history), payment records, the camp and the Tavern's candidates (schema 2 since 0.8; a schema 1 file is migrated on load) |
| models/worker-pay-<request>.json | The plugin's receipt of one payment |
| plans/worker_<id>_<time>.json | A planned haul (items, Gem Sense units, XP) |
| sessions/worker_<id>_<time>.result.json | The plugin's delivery result (`done`, `error` or `partial`), `ingest` |
| wishlist.json / wishlist-notified.json | Wishlist; claims already announced |
| collection-cache.json | Collectibles found per spool (display cache) |
| siege-records.json | Best wave per hero, region and level |
| special-packets.json | Verified special monster packets |

## 0.8: the camp, traits and more worker types (engine; UI to be designed)

Rules and numbers: tools/camp.py and tools/traits.py module notes. The camp and
traits are AFK FARM's own game layer: they change the crew's numbers and the Siege
gate, never what the game creates. Stone, spoils and gem dust exist only in AFK
FARM; gold is always the game's, taken through the payment path above.

### Camp

`GET /api/workers` adds `camp`: `buildings` [{`key`, `name`, `text`, `level`,
`max_level` (5), `unlock_hq`, `next` {`to`, `cost` {`gold`, `stone`, `spoils`,
`dust`}, `hours`, `blockers` [text]}}], `queue` [{`building`, `name`, `to`,
`started_at`, `ready_at`, `progress`}], `sites`, `resources` {`stone`, `spoils`,
`dust`}, `resource_cap`, `stock`, `stock_cap`, `keys`, `key_cap`, `effects` (every
number the buildings set, e.g. `max_workers`, `team_size`, `candidates`, `types`,
`gate_hp`, `tool_tier`, `xp_bonus`, `hotspots`). It also adds `hotspots` (today's:
{`type`, `target`, `bonus`}), `candidates` {type: [{`slot`, `type_name`,
`traits`, `price`}]} for every type the camp allows, `types`, `trees` (every
worker type's skill tree), `traits` and `quirks` (the tables).
`/api/state` → `workers.camp` = `resources`, `resource_cap`, `hq`, `queue`.

Buildings: Headquarters (the camp level; no building may pass it; level 3 gives a
second building site), Barracks (crew size 3-7, team size 2-4), Tavern (worker
types, candidates, trait odds, retraining at 3, daily candidates at 5), Walls (the
Siege gate: 110-150 health, better repairs, at 5 at most 45 damage a wave),
Storehouse (resource cap, Jeweler's stock, key rack), Forge (tool tiers), Training
Grounds (worker XP, cheaper resets, apprentices, a free weekly reset at 5),
Jeweler's Bench (the Jeweler and its recipe tiers), Watchtower (daily hot spots).
A building finishes on its own when its time is up (lazily, on the next read).

| Action | Arguments | Needs the game |
| --- | --- | --- |
| `camp_build` | `building` | an offline hero loaded; takes `next.cost.gold`, sets the camp resources aside |
| `worker_hire` | `type` (default miner), `candidate` (slot, default 0), `name` (optional) | as before |
| `worker_tool` | `worker` | the next tool tier (up to the Forge level); gold and camp resources |
| `worker_retrain` | `worker`, `what` (`trait` or `quirk`) | Tavern 3; `retrain_price` gold |
| `worker_route` | `worker`, `route` (`vault`/`stock`) | no; `stock` opens with the Jeweler |

A payment's `purpose` is now `hire`, `respec`, `build`, `tool` or `retrain`. Camp
resources set aside for a `build` or `tool` payment are given back if the game
refuses it; an `unknown` payment keeps them until its receipt decides.

**Keys and materials from the Vault.** The key rack (Basic Key `12:0`, Crystal Key
`12:1`) and the Jeweler's stock (jewel recipe materials `14:0`-`14:23`, `14:44`)
are filled from the Item Editor's Vault, category AFK Materials, through the
editor's `POST /api/vault/afk-take` (Item Editor 2.16.1 or newer). From 0.9 the
stock takes every town good (see 0.9 below). The key rack
holds 25/60/120/250/500 keys by Storehouse level; the stock 500 to 25,000.

- `GET /api/camp/vault` asks the running editor: `editor` (bool), `stock`
  [{`key` ("type:id"), `name`, `count`, `goes_to` (`rack`/`stock`)}] (dungeon keys
  and other materials are left out), `keys`, `key_cap`, `key_room`, `stock_cap`,
  `stock_room`, `pending` (takes still being settled) and `error` (for example an
  editor older than 2.16.1).
- `camp_take` {`items` {"type:id": count}}: whole counts from 1 to 100,000 of rack
  keys and jewel materials. Refused before the editor is asked when the rack or the
  stock has no room for them. Each take has one receipt in workers.json
  (`vault_takes`, request id, `state` `pending`/`done`/`refused`/`unknown`); the
  editor carries a request out at most once. Anything but a clear `done` is
  settled by cancelling the request: the editor reports the take it made (it
  reaches the camp once, even past the cap) or makes sure it never takes anything.
  An `unknown` take (the editor went quiet) is settled the same way by the panel
  once the editor answers again. `/api/state` → `workers.pending_vault_takes`
  lists the last unsettled or refused ones (`request_id`, `items`, `state`,
  `error`).

### Traits

Every worker has `traits` [{`id`, `name`, `rarity` (common, rare, epic,
legendary, quirk), `text`, `quirk`, `target` (a Specialist's favourite)}], `tool`,
`tool_name`, `route`, `retrain_price`, `type_name`. Crew entries in `/api/state`
carry `traits`, `tool` and `type_name` too. A trip stores `mods` (its multipliers
from traits, the camp and the hot spot, frozen at the start): `speed`, `amount`,
`xp`, `rare`, `tool`, `hotspot`, `bonus_find`; trip views show them.

### Adventurers and goblin hunters

Rules: tools/worker_loot.py module note. They go to a **region** where the plugin
recorded world chests or loot goblins while the player played; every opened chest
or caught goblin is one recorded packet of that region replayed through the game's
drop routine (no experience for the hero; gold picked up; filtered items handled
like a claim; the Vault label `AFK · Workers · <name> · <date>`).

- `GET /api/workers` adds `regions` {`adventurer`|`goblin_hunter`: [{`room`,
  `name`, `recorded` {tier or goblin kind: packets}}]} (running game build only),
  `chests` (tiers: `key`, `name`, `unlock`, `key_id`), `goblins` (kinds: `key`,
  `name`, `unlock`) and `key_names`. Worker views add `chests` / `goblins` with
  `unlocked`.
- `worker_start` {`worker`, `region`, `hours`} sends one (a miner keeps `ore`). An
  adventurer takes every Basic Key and Crystal Key from the camp's key rack; the
  keys it did not use come back, and keys it finds go onto the rack. Wooden chests
  open at level 1, golden (a Basic Key) at 3, crystal (a Crystal Key) at 8;
  goblins: treasure 1, rune 6, shadow 12, orb 18, ore 24 (once recorded).
- The trip view has `region`, `target_name`, `keys` instead of the ore fields.
- `worker_collect` {`worker`} needs an offline hero **standing in the trip's
  region** (the replay reads the live room); the error says where. It runs
  `afk.py worker-replay <plan>`; a stopped delivery continues on the next
  collect, a delivered one is never replayed. The log reports chests (and locked
  ones), keys found, goblins caught and fled, and the camp's spoils.

### The Jeweler

Rules: tools/worker_jeweler.py module note. The Jeweler works the game's own jewel
recipes (the craft cube's table, result types 37-41: tier 1-4 jewels and tier 5
gems) from the camp's material stock; every jewel is made by the game at delivery
(`worker deliver` with `crafts` [{`recipe`, `count`}]). It does not use or change
the hero's own Jewelcrafting level.

- The plugin reads the recipes from the running game (`afk worker recipes <id>`):
  action `worker_recipes` reads them again; a jeweler's `worker_start` reads them
  when none are kept for this game build. `GET /api/workers` adds `recipes`
  [{`index`, `result_type`, `name`, `tier`, `level`, `output` {`type` 15, `id`,
  `amount`}, `inputs` [{`type`, `id`, `amount`}], `affordable` (crafts the stock
  pays for), `bench_ok`}], `material_names` and `jewel_names`.
- `worker_start` {`worker`, `recipe` (its `index`), `hours`}: the materials of every
  planned craft (6 an hour, skills and traits) leave the stock at once; a
  cancelled session gives them all back, an early collect the unused ones.
- Tiers: the Jeweler's Bench level and a worker level (1, 8, 16, 24, 32).
- The trip view has `recipe`, `target_name` (the jewel), `planned`.
- Collecting needs any offline hero (like a miner); the jewels go to the Vault.
  The camp gets gem dust (one per material used, five per jewel).
- Miners: `worker_route` {`worker`, `route`: `stock`} sends their Gem Sense
  materials to the stock instead of the game (the plugin rolls them with the
  Prospector's own recipe and dice but does not make them; its result says
  `routed: true`, and only such a result fills the stock).

### Team trips

Rules: tools/teams.py module note. `team_start` {`members` [{`worker`, and
`ore`, `region` or `recipe` as for `worker_start`}], `hours`} sends 2 to
`team_size` idle workers at once (all start, or none: a refusal gives back the
keys and materials already taken). Each still works its own target and is
collected on its own. Every member gets +10% experience; Team Player auras reach
teammates; a Lone Wolf works 10% slower in a team (and 15% faster alone). Crews
with a synergy (`synergies`: key, name, types, text, bonus by type): Goblin
Patrol (miner + goblin hunter), Treasure Trail (adventurer + goblin hunter),
On-site Cutting (miner + jeweler), Deep Vein (adventurer + miner), Full Caravan
(all four). `GET /api/workers` adds `teams` (out now: `id`, `members`, `types`,
`hours`, `started_at`, `synergies`, `synergy_names`, `out`, `names`),
`team_size` and `synergies`; a member's trip has `team`.

### Siege and the Walls

Siege plans and `/api/siege-forecast` use the gate the Walls give; the forecast adds
`gate_hp`, and the plan's `siege` carries `gate_hp` and `gate` {`hp`, `repair`,
`max_damage`}.

## 0.9: the town (engine; UI to be designed)

Rules and numbers are in the module notes of:
- `tools/town_panel.py` (the panel side);
- `defense.py`, `battle.py`, `bestiary.py` and `fortifications.py` (sieges);
- `town.py` (fortifications in workers.json);
- `trade.py` (towns and wagons), `merchants.py` and `economy.py` (prices);
- `goods.py` (the goods list).

All of it is AFK FARM's own layer, except where it touches the game:
- **Items.** Every item is still made by the game. A siege's kills are replays of
  recorded kill packets in the siege's region, and goods leave for the Vault as
  stacks the plugin makes with the game's ground-drop routine.
- **Gold.** Gold enters and leaves the town's coffer only through receipts.

Product text stays English. Numbers are whole gold, whole units, UTC ISO times.

### The coffer, the stock and the town's goods

The town keeps its own gold, the **coffer**. Fortifications, merchants and wagons pay
from it and earn into it, so the town runs while the game is closed. Moving gold
between the coffer and the game takes an offline hero in the game:
- **Deposit.** The hero's gold goes into the coffer through the purchase path,
  as payment purpose `deposit`, with the same receipts as every camp payment.
- **Payout.** Coffer gold goes to the hero through `afk worker credit`, plugin
  0.9.0-town. Receipt: `models/worker-credit-<request>.json`. The records are in
  workers.json `credits`, with `state` `pending`, `unknown`, `paid`, `refused` or
  `review`.
  - The amount leaves the coffer when the payout is asked for.
  - A refusal puts it back.
  - An `unknown` payout is settled when its receipt arrives, never paid twice.
  - `review`: the game refused the payout, yet the hero's gold rose (a take-back
    that failed). The amount stays out of the coffer; tell the player to check the
    hero's gold.

The camp's **stock** (`camp.stock`, "type:id" → units, capped by the Storehouse) now
holds every town good, not only the Jeweler's materials. Basic and Crystal Keys stay
on the **key rack** (`camp.keys`). The goods list (`goods.py`, 226 kinds) covers:
ores, jewelcrafting materials, dusts, rare consumables (Satanic Crystal, Destiny
Shard, dice, Prophet's Wisdom…), keys (Angelic, Ruby, Chaos, Bifröst, dungeon keys),
fragments and shards, tarot cards, runes, gems, jewels and orbs. Each good has an
AFK FARM value in gold: the anchor its prices move around.
- **In from the Vault:** `camp_take` takes any town good from the Vault's AFK
  Materials (Item Editor 2.16.1). Up to 32 kinds per take.
- **Out to the Vault:** `stock_send` sends goods to the Vault, made by the game.

`GET /api/town`:
- `limits`: `walls`, `hq`, `workshop`, `watchtower`, `tower_slots`, `tower_max`,
  `hero_posts`, `sites`.
- `walls` {side: {`hp`, `max`, `armor`, `plating`, `next_plating` (see *plan*)}}.
  The sides are `north`, `east`, `south` and `west`.
- `keep` {`hp`, `max`, `dps`}.
- `towers` [{`id`, `kind`, `name`, `text`, `level`, `place`, `priority`, `perk`,
  `perks` (level 5+), `dps`, `dtype`, `reach`, `targets`, `air`, `ground`,
  `next` (*plan*)}].
- `tower_kinds` [{`key`, `name`, `text`, `dtype`, `reach`, `targets`, `air`,
  `ground`, `dps`, `perks`, `build` (*plan* of a new one at the keep)}].
- `queue` [{`target`, `to`, `started_at`, `ready_at`, `done_in_seconds`}].
- `siege` (the pointer: `id`, `room`, `level`, `started_at`, `hours`, `heroes`,
  `settled`) and `history` (the last 10 sieges, see below).
- `slain` (monsters the town ever killed), `coffer`, `stone`, `key_rack`.
- `stock` [{`key`, `name`, `category`, `count`, `value`}].
- `pending_credits`, `goods` (the whole list: `key`, `name`, `category`, `value`)
  and `category_names`.

A *plan* is {`to`, `cost` {`gold`, `stone`, `spoils`, `dust`}, `hours`,
`materials` [{`key`, `name`, `count`}], `blockers` [text]}. `to` is null at the
highest level. It can start when `blockers` is empty.

`/api/state` → `town` {`coffer`, `siege` (null or {`id`, `region`, `level`,
`waves_done`, `waves_total`, `wave`, `next_wave_at`, `over`, `outcome`, `walls`,
`keep`}), `town_shares` (finished sieges whose town share waits), `wagons_home`,
`merchants`, `building`}.

**Pages never write.** A page sees the town settled in memory: finished builds,
wagons that reached their town, a siege that ended. Actions and the monitor
(between actions) persist it.

### Fortifications

The **walls** are the camp's Walls building, level 0-5. It sets:
- each side's health (1,000 to 40,000) and armor (5-25% of wall damage blocked);
- the masons, who mend 2-6% of each wall between siege waves;
- the tower slots: 1, 2, 4, 6, 8 or 10.

Each side can be **plated** up to the Walls level, with Copper, Iron, Gold, Jade
and then Tarethium Ore. Each plating level gives that side +10% health and +2%
armor. Out of a siege a wall heals 5% an hour, or at once for stone
(10 health per stone).

The **keep** is set by Headquarters:
- health 2,000 to 78,000;
- guns of 2 to 21 health per second against whatever gets inside;
- hero posts: 0, 1, 1, 2, 3.

**Towers** stand at a side's wall, or at the keep (every side, but 25 farther
back). The **Siege Workshop** allows two tower levels per workshop level and a
second build site at level 4. The eight kinds:

| Kind | Damage | Role |
| --- | --- | --- |
| Ballista | physical, 1 target, reach 60, hits flyers | the toughest monster in reach |
| Mortar | physical, 6 targets, reach 15-75, ground only | packs far out; nothing close to the wall |
| Fire Brazier | fire, 5 targets, reach 25 | packs at the wall; stops regeneration |
| Frost Spire | cold, 3 targets, reach 40, slows 30% | buys time for the others |
| Storm Coil | lightning, 4 targets, reach 45, hits flyers | packs and flyers |
| Plague Totem | poison, 4 targets, reach 35 | stops regeneration |
| Sky Harpoon | physical, flyers only, reach 60 | flyers |
| Arcane Obelisk | holy (no monster resists it), strips shields 3× as fast | the rarest monster in reach |

How levels work:
- Levels 1-10 each multiply damage by 1.75.
- At level 5 a tower chooses one of two specialisations (`perks`), once.
- Costs: gold 100k × 1.8^(level-1), plus stone, spoils and dust. Materials come
  from the stock, by tier band (levels 1-3, 4-6, 7-8, 9-10): ore and
  jewelcrafting materials of the tower's element, then Satanic Crystals and
  Destiny Shard Fragments.

Health and damage use the siege region's unit, the health of one of its Common
monsters.

| Action | Arguments | Notes |
| --- | --- | --- |
| `fort_build` | `kind`, `place` (a side or `keep`) | a new tower from the coffer, camp resources and stock; real time |
| `fort_upgrade` | `tower` | the tower's next level |
| `fort_plating` | `side` | that wall's next plating |
| `fort_arrange` | `tower`, and any of `place`, `priority` (`first`, `strongest`, `weakest`, `flying`, `elite`), `perk` | moving is refused during a siege; a perk only at level 5+, once |
| `wall_repair` | `stone` | out of a siege, most hurt wall first |

All of these are local actions and need no game.

### Sieges

A siege brings the monsters of a region on the town. The region is any Act room
the **bestiary** knows: a monster the player killed there while AFK FARM recorded,
in the running game build.
- **Waves.** A wave comes every 5 minutes, from 1 side (levels 1-7), 2 (8-19),
  3 (20-34) or 4 (35+). Special waves:
  - every 5th: elite (Ancient and Legion only);
  - every 7th: special. These are the region's own special-content monsters, if
    it recorded any: *Abyssal Incursion* (the Abyss chest's pack), *Unholy Siege*
    (the Summoning Portal's), *Chaos Pillars*.
  - every 10th: loot goblins that try to slip past and escape;
  - every 25th and the last one: a Warlord with its host, from all four sides.
- **Events.** Some normal waves bring one: Blood Moon (monsters faster and
  harder), Thick Fog (towers reach less), Rally (heroes 25% harder), Supply Cart
  (masons mend double), Bounty (destroy a marked group for 40 spoils).
- **Ranks.** The game's own ranks are Common, Champion, Ancient and Legion. Above
  Legion come AFK FARM's tiers: Ascended (from level 15, 2 drops), Primordial
  (from level 30, 3 drops) and the Warlord (5 drops). Each drop replays the
  Legion's own packet again.
- **Monsters and affixes.** Each group is one recorded monster with its real
  name, rank, speed, range, immunities and elite affixes (the game's affix names).
  The town's model gives each affix an effect, for example Stoneskin resists
  physical, Fire Enchanted explodes at the wall, Vampiric heals while hitting,
  Extra Fast runs. A Fallen Angel Legion is the very Fallen Angel packet, so its
  replays can drop Angelic Keys.
- **Flyers.** Wasps, imps, spirits and other floating monsters fly over the walls
  (by name, AFK FARM's layer).

The fight (`battle.py`, one-second steps):
- Towers and heroes shoot; walls absorb; a wall at zero is breached; the keep's
  fall ends the siege.
- Masons and the siege's stone budget mend the walls between waves.
- A stationed **hero** fights with its measured pace from its own calibration in
  that region (health cleared per second = kills per minute × each rank's
  health), with a class role (damage type, reach, targets, hitting flyers, how
  much wall damage a melee hero blocks). A roaming hero goes where the pressure
  is.

**Rewards:**
- **A hero's kills** are that hero's claim: items, XP and gold, like an
  expedition, claimed as that hero standing in the region after the siege ends.
  They replay packets of its own calibration; a monster it met under another
  packet maps onto its own packets of that monster and rank.
- **Everything else** is the town's share, collected by any offline hero standing
  in the region, with no XP (`defense_collect`).
- **Magic Find:** +2% per level on the heroes' claims.
- **Spoils:** 1 per 20 kills (plus bounties) go to the camp when the siege ends.
  So do the stone budget left over and the walls' damage.

`GET /api/defense`:
- `siege`: the running or last siege (see below), or null.
- `history` [{`id`, `room`, `region`, `level`, `outcome` (`held`, `fell` or
  `retreated`), `waves`, `waves_total`, `kills`, `spoils`, `stone_back`,
  `ended_at`, `record` {`held`, `waves`: new records}, `town_collected`}].
- `records` {room: {`held` (highest level held to the end), `waves` {level: most
  waves}}}.
- `regions` [{`room`, `name`, `species`, `entries`, `ranks` {"1".."4": packets},
  `packets`, `special` (origins: `abyss`, `unholy`, `pillar`), `goblins`,
  `calibrated` [{`slot`, `name`, `profile`}] (heroes who can be stationed
  there)}].
- `limits`, `levels` {`max` 60, `min_hours`, `max_hours`, `wave_minutes`},
  `rank_names`, `tiers`, `events`, `affixes` {id: {`name`, effects}}, `towers`.

A siege view:
- identity and clock: `id`, `room`, `region`, `level`, `started_at`, `ends_at`,
  `waves_total`, `waves_done`, `wave` (the one coming), `next_wave_at`, `over`,
  `outcome`;
- defences: `walls` {side: {`hp`, `max`}}, `keep`, `stone_left`, `stone_budget`;
- `totals` {`kills`, `leaked`, `spoils`, `stone_used`, `breaches`};
- `by_defender` [{`id`, `name`, `drops`}];
- `heroes` [{`slot`, `name`, `class_name`, `stance`, `dps`, `expedition_id`}];
- `last`: the last 6 waves, each {`wave`, `kind`, `sides`, `event`,
  `bounty_done`, `kills`, `leaked`, `breached`, `fell`, `spoils`, `highlights`
  [{`t`, `side`, `text`}], `groups` [{`name`, `entry`, `tier`, `rank`, `side`,
  `affixes` (names), `flags`, `count`, `killed`, `leaked`, `boss`}], `walls`,
  `keep`, `stone_used`};
- `next`: what the Watchtower scouts of the coming wave. Nothing at level 0;
  from 1 the `kind` and `sides`; from 2 the `event`; from 3 the `groups`.
- `mf_bonus`, `town_collected`.

Only waves that are over are ever shown.

`GET /api/bestiary?room=Act_03_03` → `entries` [{`key`, `name`, `names`, `rank`,
`rank_name`, `origin`, `speed`, `ranged`, `immune`, `flyer`, `affixes` [{`id`,
`name`, `seen`}], `recorded`, `slain`}].

`GET /api/defense-forecast?room=…&hours=2&heroes=1:roam,2:north&stone=500` →
{`suggested`: level}, the highest level this town usually holds for 12 waves. With
`&level=N` → {`level`, `waves`, `fall_chance`, `waves_median`, `waves_low`}.
Simulated; it takes a few seconds, so ask for it on demand, never on a timer.

| Action | Arguments | Needs the game |
| --- | --- | --- |
| `defense_start` | `room`, `level` (1-60), `hours` (0.25-8), `heroes` [{`slot`, `stance` (`roam` or a side)}], `stone` (the budget, 0 or more) | no; each hero needs a usable calibration in that region and must be free |
| `defense_repair` | `stone` | no; between waves, from the camp's stone; re-draws only the waves still to come |
| `defense_retreat` | – | no; ends the siege after the waves fought |
| `defense_collect` | `siege` (optional id) | any offline hero standing in the siege's region |

**Stationed heroes.** Each one is armed as an expedition with `mode: 'defense'` in
`state.json`, one per hero. The roster row gets `defense` {`id`, `region`,
`level`, `waves_done`, `waves_total`, `wave`, `over`, `outcome`}, and `ready` when
the siege is over.
- **Claiming.** The normal `claim` action pays the hero's share. It is refused
  while the siege runs. A hero with nothing to claim is simply freed.
- **Cancelling.** Refused while the siege needs the hero.

### Trade with the other towns

The **Trading Post** (camp building, Headquarters 2) sends wagons. By its level:

| Level | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| Wagons | 1 | 2 | 2 | 3 | 4 |
| Units carried each way | 500 | 1,000 | 2,000 | 3,500 | 6,000 |
| Speed | ×1 | ×1 | ×1.1 | ×1.2 | ×1.3 |

Towns further away open at higher levels.

**The ten towns.** Ironhold, Emberfall, Saltmarsh, Duskhaven, Frostmere, Sanctum
of Dawn, Cinderpit, Goldcrest, Mirewatch and Skyreach. Each town:
- is 1-8 hours away;
- has a development level (1-5, grows with the gold traded there): deeper markets
  and more categories;
- has a taste: it makes some categories cheaply and needs others;
- has a standing with you (0-5, from the gold traded): its tariff falls from 12%
  to 0%;
- may have news that moves a category's price for the day (Festival, Cave-in,
  Caravan arrived, Key shortage, Crystal glut, Rune fair).

**How prices move** (economy.py):
- Each unit bought raises the next one's price; each unit sold lowers it.
- Prices recover with an 18-hour half-life.
- A round trip at one town always loses its margin and its tariff.

**What a wagon does.** It leaves with cargo from the stock (rack keys stay) and a
purse from the coffer. At the town it sells the cargo, then buys the orders with
the purse and the takings. Wagons trade at arrival, in arrival order. Home again,
`trade_unload`:
- bought and unsold goods go into the stock (Basic and Crystal Keys onto the
  rack), past the cap, since they are here;
- the gold left goes into the coffer.

`GET /api/trade`:
- `wagons` {`count`, `capacity`, `speed`, `post`}.
- `towns` [{`key`, `name`, `kind`, `text`, `hours`, `post`, `reachable`,
  `development`, `standing`, `tariff`, `traded`, `event`, `market` [{`key`,
  `name`, `category`, `ask`, `bid`, `stock`, `can_buy`, `can_sell`}]}].
  - `ask` is the price of the next unit bought; `bid` is the price of the next
    unit sold.
- `runs` [{`id`, `town`, `town_name`, `cargo`, `orders`, `purse`, `left_at`,
  `arrives_at`, `returns_at`, `state` (`travelling` or `arrived`), `home`,
  `result` {`earned`, `spent`, `bought`, `sold`, `sold_gold`, `unsold`,
  `gold_back`}}].
- `coffer`, `history`.

| Action | Arguments | Needs the game |
| --- | --- | --- |
| `trade_send` | `town`, `cargo` {"type:id": n}, `orders` {"type:id": n}, `purse` (gold) | no |
| `trade_unload` | `run` | no; once, when the wagon is home |

### Travelling merchants

The **Market Square** (camp building, Headquarters 2) brings merchants. The day
has four six-hour watches. In each watch a merchant may arrive: 40% at level 1,
up to 80% at level 5.
- **Stays and stalls.** A merchant stays a few hours; there are 1-3 stalls.
- **Wares by level.** Higher levels bring rarer merchants and dearer wares.

The merchants:
- Wandering Gem Cutter, Old Prospector, Royal Quartermaster (buys only, above
  market);
- Keymaster Brann (Angelic, Chaos and dungeon keys), Rune Scholar, Goblin Peddler
  (cheap, and gone fast);
- Fortune Teller (tarot), Satanic Occultist (Satanic Crystals, Destiny Shards,
  dice), Jewelers' Guild Envoy (jewels and orbs).

The game's own vendors sell none of these goods. Buying from a merchant raises its
next price; selling to it lowers its bid. Who comes is drawn from the camp's
founding time and the watch, so every page agrees.

`GET /api/market`:
- `level`, `stalls`, `chance`.
- `merchants` [{`id`, `merchant`, `name`, `text`, `arrives_at`, `leaves_at`,
  `offers` [{`key`, `name`, `category`, `side` (`sell`: it sells to you; `buy`: it
  buys from you), `left`, `unit` (the next unit's gold), `value`}]}].
- `history`, `next_watch` (a merchant is expected in the watch starting then),
  `known` (every merchant and the Market level it needs), `coffer`.

| Action | Arguments | Needs the game |
| --- | --- | --- |
| `market_buy` | `visit`, `good`, `count` | no; from the coffer into the stock (the rack for rack keys) |
| `market_sell` | `visit`, `good`, `count` | no; from the stock into the coffer |

### Coffer and shipments

| Action | Arguments | Needs the game |
| --- | --- | --- |
| `coffer_deposit` | `amount` | an offline hero loaded (purchase path, receipt) |
| `coffer_collect` | `amount` (up to the coffer, at most 500,000,000) | an offline hero loaded (`afk worker credit`, receipt) |
| `stock_send` | `items` {"type:id": n} | an offline hero loaded |

For `stock_send`, the game makes each stack with its ground-drop routine (a
`worker_town_` delivery), then the Vault takes them. A shipment that made nothing
gives the goods back. One that stopped part way keeps what it made, for review,
and is never made twice.

### Files (0.9)

| File | Meaning |
| --- | --- |
| workers.json `town` | Towers, plating, wall and keep health, the fortification queue, the siege pointer, the last sieges, monsters slain, a shipment under way |
| workers.json `trade` | Markets' stocks, towns' standing and prosperity, wagons, the coffer, history |
| workers.json `market` | Trades with the merchants in town, history |
| workers.json `credits` | Coffer payouts and their receipts |
| plans/defense_<id>.json | A siege's record: frozen fortifications, heroes, roster, seed, waves, repairs and retreat |
| plans/defense_<id>_h<slot>.json | A stationed hero's expedition plan (`mode: 'defense'`) |
| plans/worker_defense_<id>.json | The town's share as a replay plan |
| plans/worker_town_<time>_<id>.json | A shipment of goods to the Vault |
| models/worker-credit-<request>.json | The plugin's receipt of one payout |
| defense-records.json | Highest level held and most waves per region and level |
