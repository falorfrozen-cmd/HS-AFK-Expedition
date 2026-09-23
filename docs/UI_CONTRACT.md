# AFK FARM panel contract — 0.6.1

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
validate_start, recover, portrait, save_modifiers, save_loot_filter, save_preferences.
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
| state.json | Armed timer and last settled claim |
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
mismatch refuses claim. Start freezes the selected saved profile without contacting
the game; profile identity, eligibility and known build compatibility remain checked.
The current live hero's loadout has no bearing on another hero's offline timer.
Legacy profiles remain visible with a remeasure reason.
Only normal Act measurements are exposed. Context checks are once per second
while measuring and immediately before replay; they are not a universal simulation.

Only one expedition may be armed. The hero picker remains usable during that timer;
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
