# Town UI: the brief for ChatGPT (AFK FARM 0.9)

Build the town's pages in AFK FARM's panel. The engine is finished and tested, and
it was checked in the game on 2026-09-25: sieges, the coffer, shipments to the
Vault and the Vault takes all worked. Your job is the UI only: design it, build it
and test it.

## 0. Start here

1. Work on the branch the owner gives you (`chatgpt/town-ui`). Commit there, and do
   not push.
2. Read these, in order:
   - `AGENTS.md`;
   - this file;
   - `docs/UI_CONTRACT.md`, section "0.9: the town". It lists every field and action;
     use its names exactly.
   - the same file's "0.8" → "Camp" section, for the buildings;
   - `docs/PLAYER_GUIDE.md`, "The town (0.9)", for the words players already read.
3. Run the fixture: `py -3 -B tests/serve_panel_fixture.py`, then open
   http://127.0.0.1:9567. It seeds a whole town (see §9). Never run
   `tools/panel.py` against real data for UI work.
4. Keep every suite green:
   - `node --test tests/test_panel_ui.cjs tests/test_map_ui.cjs tests/test_share_ui.cjs`
   - `py -3 -B -m unittest discover -s tests` (391 tests; 2 are skipped)

## 1. Hard rules

- **English UI text only.**
- **No new dependencies.** No frameworks, build steps, CDNs or web fonts. Use plain
  JS and CSS like `web/`. You may add files under `web/`, for example `web/town.js`,
  loaded the way `extras.js` is.
- **No engine or API changes** (`tools/*.py`, the fixture). The contract is fixed. If
  a field you need is missing, write it in the report (§11); do not change Python.
- **Never show a siege's future.**
  - Show only waves that are over (`siege.last`) and what the Watchtower scouts
    (`siege.next`, as deep as its level allows).
  - Never extrapolate an outcome. `ends_at` is the planned end, not a promise.
- **`/api/defense-forecast` runs on a click only.** It simulates sieges for up to a
  few seconds, so never call it on a timer or when a page opens.
- **Money states are sacred** (§7). Never offer "retry" on a payout or a shipment:
  the engine settles them itself, exactly once.
- **Never display `path` fields.** They are local file paths.
- **Keep 0.6.5's accessibility.**
  - Every control is reachable by keyboard and has a visible focus.
  - Icon buttons have labels.
  - Nothing is told by colour alone.
  - `prefers-reduced-motion` is respected.
  - The page works at narrow widths.
- **Items and gold are the game's.** Where rewards are mentioned, say the kills are
  replayed through the game. Never promise particular items.

## 2. The feel

This is Riftbreaker-style base defense on top of Hero Siege, played semi-idle:
- the player builds and upgrades a fortified town between runs;
- they send the monsters they really killed (recorded kill packets) against it;
- they come back to results and loot.

What that asks of the UI:
- **Glanceable.** The first screen answers these questions: Is a siege on? How are
  the walls? What is ready: town shares to collect, wagons home, merchants in town,
  finished builds?
- **A base, not a spreadsheet.** A top-down sketch of the town is the centre of the
  Overview: the keep, four walls and the tower slots. Tables come second.
- **Battle reports, not animation.** The engine resolves each wave in one-second
  steps on the server. The live view is a war room:
  - a clock to the next wave;
  - wall and keep bars;
  - a feed of finished waves, each with its monsters, ranks and affixes, what broke
    through, and who killed what.
- **Readable threat.** Rank, tier, affixes, flags (flying, burrowing, splitting…)
  and immunities are the game's own language. Show them as compact chips with a
  legend, so the player sees *why* a wave hurt and which tower answers it. For
  example:
  - Sky Harpoon for flyers;
  - Fire Brazier or Plague Totem against regeneration;
  - Arcane Obelisk against shields.
- **Every disabled button says why.** Show the plan's `blockers`, that the game is
  closed, or that a siege is running.
- **Small rewards for attention.** Show:
  - new records (`history[].record`);
  - the Magic Find bonus of the siege level;
  - bounty groups destroyed;
  - a Warlord falling.

## 3. Where it goes in the panel

- **One new rail entry, Town,** between Loot and Modifiers, with the tabs
  **Overview · Siege · Bestiary · Trade · Market**.
  - Use a real tablist (arrow keys, `aria-selected`).
  - Remember the tab in `localStorage`, like the panel's other view state.
- **Rail badges** come from `/api/state` → `town`, which is already polled every
  2 s. Show a small count or dot for:
  - a running siege (`siege` not null and not `over`);
  - town shares waiting (`town_shares`);
  - wagons home (`wagons_home`);
  - merchants in town (`merchants`);
  - builds under way (`building`).
- **The camp's buildings (0.8) have no page yet.** Put the town's buildings on the
  Overview (§5.1). The Workers page from the 0.7 handover is separate work.
- **The roster (0.7 handover)** gets a *Defending* badge for a stationed hero (§5.2).

## 4. Data and refresh

| Source | Used for | When |
| --- | --- | --- |
| `/api/state` → `town`, `server_time`, `game_running`, `live`, `job` | badges, headers, clock offset, "needs the game" | already polled (2 s; 0.5 s during a job) |
| `/api/town` | Overview | on open, then every 5 s while visible |
| `/api/workers` → `camp` | buildings, camp resources, camp queue, stock and rack caps | with `/api/town` |
| `/api/camp/vault` | the "Take from Vault" dialog | when that dialog opens |
| `/api/defense` | Siege tab | on open, then every 5 s while visible |
| `/api/bestiary?room=…` | Bestiary | when the region changes |
| `/api/defense-forecast?…` | Suggest / Check a level | on a click only |
| `/api/trade` | Trade | on open, then every 10 s while visible |
| `/api/market` | Market | on open, then every 10 s while visible |

- **Pages never write.** Polling is always safe.
- **Countdowns.** Compute them locally each second from the ISO times. Correct for
  the difference between the server clock (`server_time`) and the browser clock.
  Refetch the tab's route when a countdown reaches zero.
- **Actions.**
  - `action(name, {...})` in `web/app.js` posts to `/api/action`. Everything becomes
    the panel's `job`.
  - Show `job.output` while it runs and `job.error` word for word when it fails,
    using the `jobBanner()` pattern. Add the town actions to its page map.
  - Refetch the tab's route when the job ends.
- **An HTTP 400 response** carries `{error}`: a plain English sentence for the
  player. Show it as a toast, unchanged.

## 5. The pages

### 5.1 Overview

```
┌─ TOWN ──────────────────────────────────────────── Coffer 2,450,000 gold ─┐
│ Stone 7,200 · Spoils 1,275 · Dust 180 │ Stock 1,500 / 1,500 │ Key rack 0 / 60 │
├───────────────────────────────┬───────────────────────────────────────────┤
│             NORTH             │  Ballista · t1 · level 4                   │
│        [Ballista 4]  [+]      │  physical · reach 60 · 1 target · air+ground│
│  W ┌─────────────────────┐ E  │  Priority [strongest ▾]   Place [north ▾]    │
│  E │        KEEP         │ A  │  Upgrade to 5 · 6.5 h                        │
│  S │   12,500 / 12,500   │ S  │    367,416 gold · 1,000 stone · 150 spoils   │
│  T │  [Frost Spire 3]    │ T  │    Iron Ore 40 (have 12) ⚠                   │
│    └─────────────────────┘    │    ✖ missing 28 Iron Ore     [Upgrade]      │
│             SOUTH             │  Specialisation at level 5                  │
├───────────────────────────────┴───────────────────────────────────────────┤
│ Walls   N ████████ 7,200/7,200 · armor 11% · plating 2/3 [Plate →3]         │
│ Queue   Ballista t2 → 4 · 2 h 19 m   ·   North wall plating → 3 · 5 h        │
│ Coffer  [Deposit] [Collect]      Stock  [Take from Vault] [Send to Vault]   │
│ Buildings  Headquarters 3/5 · Walls 3/5 · Siege Workshop 3/5 · …            │
│ Last sieges  L5 The Desert · held · 12/12 waves · 501 kills · share waiting │
└────────────────────────────────────────────────────────────────────────────┘
```

**The sketch** (SVG or CSS; no library):
- The keep sits in the centre, with four wall segments around it.
- **Tower slots.** There are `limits.tower_slots` slots in all; towers and new
  towers in the queue both take one.
  - A tower's `place` is a side or `keep`. Draw keep towers inside the keep.
  - An empty slot shows "+", which opens Build.
- **Walls.**
  - Each segment shows its `hp`/`max`, `armor` (a fraction, shown as a %) and
    `plating` out of `limits.walls`.
  - A wall with `max` 0 (Walls level 0) is drawn dashed: "No walls yet. Build Walls
    under Buildings."
- **During a siege**, draw the siege's own health from `/api/defense` → `siege.walls`
  and `siege.keep`. Moving a tower is locked then; the engine refuses it anyway.

**Tower drawer** (click a tower):
- **Stats:** `name`, `text`, `level`, `dps`, `dtype`, `reach`, `targets`, `air` and
  `ground`.
- **Priority:** a select → `fort_arrange {tower, priority}`. The values are
  `first`, `strongest`, `weakest`, `flying` and `elite`.
- **Place:** a select → `fort_arrange {tower, place}`. It is disabled during a siege.
- **Specialisation:** at level 5+ with `perk` null, show the kind's two `perks`.
  The choice is made once, for good, so confirm it in a dialog →
  `fort_arrange {tower, perk}`.
- **Upgrade:** show the `next` plan (§5.1.1) → `fort_upgrade {tower}`.

**Build** (the "+" slot or a button):
1. Pick a kind from `tower_kinds`. Show each as a card: name, text, damage type,
   reach, targets, air/ground, base dps, and the two level-5 perks.
2. Pick the place: a side or the keep. Explain the keep: "covers every side, from
   25 farther back".
3. Show the kind's `build` plan → `fort_build {kind, place}`.

**Walls**
- **Plate:** each side's `next_plating` plan → `fort_plating {side}`.
- **Repair:** out of a siege → `wall_repair {stone}`.
  - Offer "Repair all": stone = ⌈missing health × `repair_stone_per_hp`⌉, at most
    the camp's `stone`.
  - Out of a siege, walls also heal 5% an hour by themselves. Say so.

**Keep:** `hp`/`max`, `dps`, and hero posts (`limits.hero_posts`).

**Queue** (`queue` [{`target`, `to`, `ready_at`, `done_in_seconds`}]):
- Label each target:
  - `new:<kind>:<place>` → "New Ballista at the north wall";
  - `tower:<id>` → "<tower name> → level N";
  - `plating:<side>` → "North wall plating → N".
- Each shows a countdown. The camp's own queue (buildings) sits beside it.

**Coffer**
- Show `coffer`.
- **Deposit** → `coffer_deposit {amount}`.
- **Collect** → `coffer_collect {amount}`. The amount is at most the coffer and at
  most 500,000,000.
- Both need the game (§6).
- `pending_credits` are shown with the care in §7.

**Stock**
- `stock` grouped by `category`, using `category_names` for the headings.
- Each row shows `count` and `value`, the AFK FARM value of one unit.
- The key rack (`key_rack`) is shown apart.
- **Take from Vault** (a dialog):
  - It lists `/api/camp/vault` → `stock`, with a count to take for each (up to its
    `count`).
  - Respect `key_room` for rack keys and `stock_room` for the rest.
  - If `editor` is false or `error` is set, say "Open the Item Editor (2.16.1 or
    newer)".
  - Show `pending` takes as "being settled".
  - → `camp_take {items}`, at most 32 kinds.
- **Send to Vault:**
  - Pick goods and counts from the stock → `stock_send {items}`.
  - The game makes each stack, then the Vault takes them in.
- **A shipment under way:** `/api/town` → `sending` {`items`, `stage`, `at`}.
  - Show it as a card.
  - `waiting`: "Waiting for the game. Send again to finish it; nothing is made
    twice." Offer `stock_send` with no items.
  - `stopped`: "Stopped part way." Offer **Close as partial** →
    `stock_close_partial`: what was made goes to the Vault, the rest back into the
    stock.
  - `done`: "Made." Offer `stock_send` with no items, which takes it to the Vault.

**Buildings** (from `/api/workers` → `camp.buildings`)
- Show the town's buildings first: Headquarters, Walls, Storehouse, Watchtower,
  Siege Workshop, Market Square and Trading Post. The rest go in a disclosure.
- Each shows `level` of `max_level`, `text`, and `next` {`to`, `cost`, `hours`,
  `blockers`} → `camp_build {building}`.
- A building's gold comes from the hero in the game (the purchase path), so it needs
  the game (§6). Its stone, spoils and dust come from the camp.
- Show the camp queue with countdowns. `unlock_hq` explains why a building is
  locked.

**Last sieges:** `history` rows, each with its outcome and record badges.

#### 5.1.1 Plans

Every build, upgrade, plating and building shows its *plan*:
- **`to`:** the level it reaches. `to` null means it is already at the highest level.
- **`cost`:** gold from the coffer (for buildings, from the hero); stone, spoils and
  dust from the camp.
- **`hours`:** the build time.
- **`materials`** [{`key`, `name`, `count`}]: from the stock. Show "have / need"
  using the stock.
- **`blockers`:** plain sentences. An empty list means it can start.

### 5.2 Siege

The tab has three states: the **planner** (no siege), the **live** view (a siege is
running) and the **after** view (`over`). The watch and the waiting shares sit at the
side in every state.

**Planner**
- **Region:** from `/api/defense` → `regions`. For each region show:
  - `name`;
  - monsters (`species`, `entries`) and packets per rank (`ranks` 1-4 via
    `rank_names`);
  - special content (`special`: abyss / unholy / pillar) and goblins;
  - which heroes can be stationed there (`calibrated`).
  - A link opens that region in the Bestiary.
- **Level:** 1 to `levels.max`.
  - **Suggest** → `/api/defense-forecast?room=&hours=&heroes=&stone=` →
    `{suggested}`.
  - **Check level N** adds `&level=N` → {`waves`, `fall_chance`, `waves_median`,
    `waves_low`}.
  - Show a spinner: the simulation takes a few seconds. Say it is a simulation of
    this town, not a promise.
- **Hours:** from `levels.min_hours` to `levels.max_hours`. Waves = hours × 60 ÷
  `levels.wave_minutes`. Offer 15-minute steps.
- **Heroes:** up to `limits.hero_posts`.
  - A hero is offered only if it has a calibration there (`calibrated`) and is free,
    meaning it has no row in `/api/state` → `expeditions`.
  - Each gets a stance: `roam` (goes where the pressure is) or a side.
- **Stone budget:** 0 up to the camp's stone. The masons use it between waves; what
  is left comes back.
- **Records:** `records[room]` shows the highest level held and the most waves per
  level.
- **Start** → `defense_start {room, level, hours, heroes: [{slot, stance}], stone}`.
- Explain the rewards before starting:
  - A stationed hero's kills are that hero's claim (items, XP and gold), claimed in
    the region.
  - The rest is the town's share (no XP).
  - The siege level gives +2% Magic Find per level.

**Live** (`siege` with `over` false)
- **Header:**
  - `region` and `level`;
  - "Wave `waves_done` of `waves_total`";
  - the countdown to `next_wave_at`;
  - "planned end" from `ends_at`.
- **Defences:** `walls` and `keep` bars, and `stone_left` of `stone_budget`.
- **Totals and leaderboard:**
  - `totals`: kills, leaked, spoils, stone used, breaches.
  - `by_defender`: towers and heroes by drops.
  - `heroes`: name, class, stance, dps.
- **The feed:** `last`, the last 6 finished waves, newest first. Each wave shows:
  - `kind`: normal, elite, `special:<origin>`, goblin, warlord or final. Give
    special kinds a banner: Abyssal Incursion, Unholy Siege or Chaos Pillars.
  - `sides`;
  - `event`, from `/api/defense` → `events` (name and text);
  - `bounty_done`;
  - kills and leaked, `breached` sides and `fell`;
  - `highlights` [{`t`, `side`, `text`}] as a small log.
- **Groups** in a wave: `name`, rank chip (`rank_names`), `tier` badge (Ascended,
  Primordial, Warlord), `affixes` (game names), `flags` chips, count/killed/leaked
  and `boss`.
- **The legend** for chips comes from `/api/defense`:
  - `affixes` {id: {`name`, effects}};
  - `tiers`;
  - flags: flying, burrowing, teleporting, thief, summoner, splitting, exploding,
    enraged, vampiric, regenerating, frozen, shielded, juggernaut.
- **Scouting:** `next` is what the Watchtower shows of the coming wave.
  - Level 0: nothing.
  - Level 1: `kind` and `sides`.
  - Level 2: `event`.
  - Level 3: `groups`.
  - Say what a higher Watchtower would reveal.
- **Actions:**
  - **Repair** → `defense_repair {stone}`, from the camp's stone, between waves.
  - **Retreat** → `defense_retreat`, after a confirm dialog. The siege ends after the
    waves already fought.

**After** (`over` true)
- A banner with the outcome: `held`, `fell` or `retreated`.
- Totals, and new records (`history[0].record`).
- Magic Find: `mf_bonus` is a multiplier (1.12 = +12%).
- **Collect the town's share** → `defense_collect {siege}`, unless
  `town_collected`.
  - It needs any offline hero *standing in the siege's region* (§6).
  - No XP; the town's share is replayed through the game.
- **Each stationed hero** claims its own share from the roster with the normal
  claim, standing in the region.
  - The roster row shows `defense` {`region`, `level`, `waves_done`/`waves_total`,
    `over`, `outcome`} and a *Defending* badge while the siege runs.
  - Cancelling is refused while the siege needs the hero.

**Watch** (`/api/defense` → `watch`)
- **Keep watch:** a toggle, with region, level, hours per siege and stone per siege
  → `defense_watch {room, level, hours, stone}`.
- **Stop:** `defense_watch {off: true}`. A siege under way runs to its end.
- **While on:** show `since`, `started` (how many sieges) and `paused`.
  - `paused` is a plain reason; show it word for word. For example "8 town shares
    wait to be collected", "the town has no towers", or "the keep fell in the last
    siege: lower the level or strengthen the town, then keep watch again".
- Starting a siege by hand is refused while a watch siege runs.

**Waiting shares** (`waiting`)
- A list of finished sieges whose town share waits, each with **Collect**.
- **Collect all here** → `defense_collect {all: true}`: every share of the region the
  hero stands in, oldest first.

### 5.3 Bestiary

- **Region:** pick one from `/api/defense` → `regions`, then load
  `/api/bestiary?room=…`.
- **Entries** are grouped by `rank_name` (Common, Champion, Ancient, Legion). Each
  shows:
  - `name` (`names` are the variants);
  - an `origin` badge (ordinary, abyss, unholy, pillar, special);
  - `speed`, `ranged`, immunities (`immune`) and `flyer`;
  - `affixes` with how often each was `seen`;
  - `recorded` and `slain` (the town's kills).
- **Use it to teach.** "4 flyers here: a Sky Harpoon or Storm Coil helps." Only give
  hints that follow from the data shown: flyers, regeneration, shields,
  immunities.

### 5.4 Trade

- **Towns:** a map or list of the ten `towns`. Each shows:
  - `kind` and `text`;
  - `hours` away;
  - `post` (the Trading Post level needed) and `reachable`;
  - `development` 1-5 and `standing` 0-5;
  - `tariff` (a fraction, shown as a %);
  - `traded` (gold so far);
  - `event`, the day's news.
- **A town's market:** `market` [{`name`, `category`, `ask`, `bid`, `stock`,
  `can_buy`, `can_sell`}], with a category filter and search.
  - `ask` is the next unit you buy; `bid` is the next unit you sell.
  - Say plainly that prices move with every unit.
- **Wagon planner:**
  - **Town:** a reachable one.
  - **Cargo:** from the stock; rack keys stay home.
  - **Orders:** goods to buy there.
  - **Purse:** gold from the coffer.
  - **Limit:** `wagons.capacity` units each way.
  - **Estimate:** use `bid`/`ask` × count and label it an estimate.
  - → `trade_send {town, cargo, orders, purse}`.
- **Wagons:** `runs`, with countdowns to `arrives_at` and `returns_at`.
  - A wagon whose state is `arrived` has traded.
  - When `home` is true → **Unload** → `trade_unload {run}`.
  - Show `result`: earned, spent, bought, sold, unsold and gold back.
- **History:** `history`.
- `wagons` {`count`, `capacity`, `speed`, `post`} explains the Trading Post's level.

### 5.5 Market Square

- **Header:** `level`, `stalls`, and the chance a merchant comes each watch
  (`chance`). `next_watch` {`expected`, `watch_starts`} shows when the next one is
  expected.
- **Merchants in town:** `merchants`. Each shows `name`, `text`, and a countdown to
  `leaves_at`.
  - **Sells to you** (`side` `sell`): `name`, `unit` (the next unit's price),
    `left`, and `value` for comparison. **Buy** with a count →
    `market_buy {visit: id, good: key, count}`, paid from the coffer. Goods go into
    the stock; rack keys go onto the rack.
  - **Buys from you** (`side` `buy`): **Sell** with a count, at most what the stock
    holds → `market_sell {visit, good, count}`, paid into the coffer.
- **`known`:** every merchant and the Market level it needs, so the player knows
  what to build for.
- **Frame it as the game's missing market.** The game's own vendors sell none of
  these goods.

## 6. Actions and what they need

| Action | Arguments | Needs |
| --- | --- | --- |
| `fort_build` | `kind`, `place` | local |
| `fort_upgrade` | `tower` | local |
| `fort_plating` | `side` | local |
| `fort_arrange` | `tower` + `place` / `priority` / `perk` | local; no moving during a siege; a perk once, level 5+ |
| `wall_repair` | `stone` | local; out of a siege |
| `defense_start` | `room`, `level`, `hours`, `heroes`, `stone` | local; heroes free and calibrated there |
| `defense_repair` | `stone` | local; during a siege |
| `defense_retreat` | – | local |
| `defense_watch` | `room`, `level`, `hours`, `stone`, or `off: true` | local; needs towers |
| `defense_collect` | `siege` or `all: true` | **the game**: an offline hero standing in the siege's region |
| `claim` (the roster's) | as today | **the game**: the stationed hero, in the region |
| `trade_send` | `town`, `cargo`, `orders`, `purse` | local |
| `trade_unload` | `run` | local; once the wagon is home |
| `market_buy` / `market_sell` | `visit`, `good`, `count` | local |
| `coffer_deposit` | `amount` | **the game**: an offline hero loaded |
| `coffer_collect` | `amount` | **the game**: an offline hero loaded |
| `stock_send` | `items` (optional while a shipment is under way) | **the game**: an offline hero loaded |
| `stock_close_partial` | – | **the game**; only a stopped shipment |
| `camp_build` | `building` | **the game**: an offline hero loaded (the gold is the hero's) |
| `camp_take` | `items` | the Item Editor 2.16.1+, running (not the game) |

"Needs the game":
- `/api/state` → `game_running` false: disable the button and say "Open Hero Siege
  with an offline hero loaded".
- **Region-bound actions:** when `live.room` differs from the siege's `room`, say
  where to stand: "Stand in <region> with any offline hero".
- The engine checks all of this again and answers in `job.error`. The UI only guides.

## 7. States that need care

- **`pending_credits`** (Overview → Coffer):
  - `pending` / `unknown`: "Waiting for the game". Never show it as failed, and
    do not invite another payout meanwhile. It settles by itself exactly once.
  - `refused`: the gold went back to the coffer; show the `error`.
  - `review`: "The game refused this payout, but the hero's gold rose. Check the
    hero's gold." It is never paid twice, and there is no button for it.
- **`sending`:** see §5.1 Stock. No "retry"; sending again only finishes it.
- **The camp's Vault takes** (`/api/camp/vault` → `pending`) settle themselves. Show
  them as "being settled".
- **A siege that is over but not collected** stays in `waiting` until collected. The
  history never drops it.
- **Blockers** are sentences. Show them as they are, under the button they block.
- **Empty states** need friendly text and the next step:
  - no walls;
  - no towers;
  - no regions yet: "Record monsters with the game running: the bestiary fills from
    your own kills";
  - no merchants;
  - no wagons.

## 8. Copy (English)

- Short labels; the numbers carry the story.
- **Gold:** "2,450,000 gold". **Times:** "2 h 19 m", "in 3:12". **Health:**
  "7,200 / 7,200".
- **Siege words:** Wave, Held, Fell, Retreated, Breached, Leaked, Keep, Walls,
  Stone, Spoils, Town share, Stationed, Roam.
- **Say what the player can do next,** not what went wrong inside. For example:
  - "Stand in The Desert with any offline hero to collect."
  - "Waiting for the game."
- **Never promise drops.** Say "Kills are replayed through the game when you
  collect."

## 9. The fixture

- **What it seeds:**
  - Walls 3 with plating, five towers, and a building queue;
  - a coffer of 2,450,000 and a stocked camp;
  - a bestiary in Act 3-3 ("The Desert");
  - a level 6 siege under way (7 of 24 waves done) and a finished level 5 siege
    whose town share waits;
  - a wagon on the road and one home;
  - two merchants in town.
- **Simulated:** payouts, shipments to the Vault and the town-share delivery.
- **`control.json`** (its path is printed at start):
  - `gold`: the in-game hero's gold.
  - `live`: the game's state. `null` means the game is closed.
  - `presentation.<key>` overrides one top-level key of `/api/state` for a test,
    for example `presentation.expeditions` to show a stationed hero's roster row.
- **To see a real stationed hero:**
  1. Cancel Suh's farm expedition (Explore).
  2. Retreat the running siege.
  3. Start a new siege in Act 3-3 with Suh roaming. Suh is calibrated there.
- **Stopping and resetting:** create the `stop` file whose path is printed at start.
  Restarting the fixture resets everything.

## 10. Tests

- **Where:** extend `tests/test_panel_ui.cjs`, or add `tests/test_town_ui.cjs` in the
  same style (the `vm` context and fixture data). Cover at least:
  - only `last` waves and the allowed scouting depth render; nothing after the clock;
  - the forecast is fetched only on a click;
  - `pending_credits` `unknown` never shows as failed and never offers a payout
    retry; `review` shows the check-your-gold text;
  - game-needed buttons are disabled with the reason when `game_running` is false;
  - a plan with blockers disables its button and lists them; a maxed one (`to` null)
    says so;
  - an over siege shows its outcome and "Collect the town's share"; a collected one
    does not;
  - a paused watch shows its reason; waiting shares offer "Collect all here";
  - `sending` shows the right action for each stage;
  - a wagon home offers Unload; a travelling one shows its countdown;
  - buy and sell counts are bounded by `left`, the stock and the coffer;
  - no `path` value ever reaches the page.
- **Also:** keyboard walk-throughs of the tabs and dialogs, and a narrow-width check.
- **Python:** the suite must still pass (391, with 2 skipped). You do not change
  Python.

## 11. Hand back

1. Commit on your branch. Do not push.
2. Write `docs/TOWN_UI_VERIFICATION.md`:
   - what you built;
   - which tests you added and their results;
   - the fixture walk-through, state by state;
   - known gaps;
   - anything the contract did not give you.
3. The owner and Claude review it: UI text English only, no new dependencies, the API
   unchanged. Then they run all three suites and merge.

## 12. Suggested order

Each step ends with the suites green:
1. The Town rail entry, tabs, badges and polling; the Overview read-only (sketch,
   walls, keep, towers, queue, coffer, stock, buildings).
2. Fortification actions (build, upgrade, plating, arrange, repair) with plans and
   blockers.
3. The Siege live and after views, waiting shares and collect.
4. The Siege planner, the forecast and the watch.
5. The Bestiary.
6. Trade.
7. The Market Square.
8. The coffer and the stock (deposit, collect, take, send), with the §7 states.
9. Polish (keyboard, narrow screens, empty states), tests and the report.

## Appendix: sample payloads (from the fixture, trimmed)

### `/api/state` → `town`

```json
{
 "coffer": 2450000,
 "siege": {
  "id": "defense_fixture_live",
  "region": "The Desert",
  "level": 6,
  "waves_done": 7,
  "waves_total": 24,
  "wave": 8,
  "next_wave_at": "2026-09-25T17:37:07Z",
  "over": false,
  "outcome": null,
  "walls": {
   "north": {"hp": 7200.0, "max": 7200.0},
   "east": {"hp": 6600.0, "max": 6600.0},
   "south": {"hp": 6000.0, "max": 6000.0},
   "west": {"hp": 6000.0, "max": 6000.0}
  },
  "keep": {"hp": 12500.0, "max": 12500.0}
 },
 "town_shares": 1,
 "wagons_home": 1,
 "merchants": 2,
 "building": 1
}
```

### `/api/town`

Lists trimmed to one or a few entries. `sending` is null here; its shape is in §5.1.

```json
{
 "limits": {"walls": 3, "hq": 3, "workshop": 3, "watchtower": 3, "tower_slots": 6, "tower_max": 6, "hero_posts": 1, "sites": 1},
 "walls": {
  "north": {
   "hp": 7200.0,
   "max": 7200.0,
   "armor": 0.19,
   "plating": 2,
   "next_plating": {
    "to": 3,
    "cost": {"gold": 1000000, "stone": 970},
    "hours": 4.0,
    "blockers": ["the Siege Workshop is busy", "the town is under siege", "missing 120 Gold Ore"],
    "materials": [{"key": "14:29", "name": "Gold Ore", "count": 120}]
   }
  }
 },
 "keep": {"hp": 12500.0, "max": 12500.0, "dps": 6.5},
 "towers": [
  {
   "id": "t1",
   "kind": "ballista",
   "name": "Ballista",
   "text": "Heavy bolts at the toughest monster in reach. Hits flyers.",
   "level": 4,
   "place": "north",
   "priority": "strongest",
   "perk": null,
   "perks": [],
   "dps": 10.719,
   "dtype": "physical",
   "reach": 60.0,
   "targets": 1,
   "air": true,
   "ground": true,
   "next": {
    "to": 5,
    "cost": {"gold": 1050000, "stone": 790, "spoils": 260, "dust": 0},
    "hours": 2.53,
    "blockers": ["the Siege Workshop is busy", "the town is under siege", "missing 10 Iron Opal"],
    "materials": [{"key": "14:6", "name": "Iron Opal", "count": 40}]
   }
  }
 ],
 "tower_kinds": [
  {
   "key": "ballista",
   "name": "Ballista",
   "text": "Heavy bolts at the toughest monster in reach. Hits flyers.",
   "dtype": "physical",
   "reach": 60,
   "targets": 1,
   "air": true,
   "ground": true,
   "dps": 2.0,
   "perks": [{"key": "piercing", "name": "Piercing Bolts", "text": "Ignores half of any physical resistance.", "pierce": 0.5}],
   "build": {
    "to": 1,
    "cost": {"gold": 100000, "stone": 120, "spoils": 0, "dust": 0},
    "hours": 0.5,
    "blockers": ["the Siege Workshop is busy", "the town is under siege"],
    "materials": [{"key": "14:28", "name": "Iron Ore", "count": 30}]
   }
  }
 ],
 "queue": [
  {
   "target": "tower:t2",
   "to": 4,
   "started_at": "2026-09-25T16:34:07Z",
   "ready_at": "2026-09-25T19:54:07Z",
   "request": "fixture",
   "done_in_seconds": 8393
  }
 ],
 "history": [
  {
   "id": "defense_fixture_done",
   "room": "Act_03_03",
   "region": "The Desert",
   "level": 5,
   "outcome": "held",
   "waves": 12,
   "waves_total": 12,
   "kills": 501,
   "spoils": 25,
   "stone_back": 800,
   "ended_at": "2026-09-25T12:34:07Z",
   "record": {"held": true, "waves": true},
   "town_collected": false,
   "watch": false
  }
 ],
 "slain": 501,
 "repair_stone_per_hp": 0.1,
 "coffer": 2450000,
 "stock": [
  {"key": "12:33", "name": "Chaos Key", "category": "key", "count": 3, "value": 15000},
  {"key": "14:0", "name": "Bloodstone", "category": "material", "count": 45, "value": 400},
  {"key": "14:12", "name": "Cardinal Ruby", "category": "material", "count": 12, "value": 4500}
 ],
 "key_rack": {},
 "stone": 7200,
 "pending_credits": [],
 "goods": [{"key": "14:27", "name": "Copper Ore", "category": "ore", "value": 60}, {"key": "14:28", "name": "Iron Ore", "category": "ore", "value": 130}],
 "category_names": {
  "ore": "Ores",
  "material": "Jewelcrafting materials",
  "dust": "Dusts",
  "relic": "Rare consumables",
  "key": "Keys",
  "shard": "Fragments and shards",
  "tarot": "Tarot cards",
  "rune": "Runes",
  "gem": "Gems",
  "jewel": "Jewels",
  "orb": "Orbs"
 },
 "sending": null
}
```

### `/api/defense`

One finished wave of the six in `last`, two affixes of the table.

```json
{
 "siege": {
  "id": "defense_fixture_live",
  "room": "Act_03_03",
  "region": "The Desert",
  "level": 6,
  "started_at": "2026-09-25T16:57:07Z",
  "ends_at": "2026-09-25T18:57:07Z",
  "waves_total": 24,
  "waves_done": 7,
  "wave": 8,
  "next_wave_at": "2026-09-25T17:37:07Z",
  "over": false,
  "outcome": null,
  "walls": {
   "north": {"hp": 7200.0, "max": 7200.0},
   "east": {"hp": 6600.0, "max": 6600.0},
   "south": {"hp": 6000.0, "max": 6000.0},
   "west": {"hp": 6000.0, "max": 6000.0}
  },
  "keep": {"hp": 12500.0, "max": 12500.0},
  "stone_left": 800,
  "stone_budget": 800,
  "totals": {"kills": 359, "leaked": 0, "spoils": 17, "stone_used": 0, "breaches": 0},
  "by_defender": [{"id": "t4", "name": "Storm Coil", "drops": 285}, {"id": "t1", "name": "Ballista", "drops": 74}],
  "heroes": [],
  "mf_bonus": 1.12,
  "town_collected": false,
  "last": [
   {
    "wave": 6,
    "kind": "normal",
    "sides": ["east"],
    "event": "rally",
    "bounty_done": false,
    "kills": 53,
    "leaked": 0,
    "breached": [],
    "fell": false,
    "spoils": 0,
    "highlights": [],
    "groups": [
     {
      "id": "w6g0",
      "name": "Orc Warrior",
      "entry": "e_orc_warrior_1#1",
      "tier": "normal",
      "rank": 1,
      "side": "east",
      "affixes": [],
      "flags": [],
      "count": 21,
      "killed": 21,
      "leaked": 0,
      "boss": false
     },
     {
      "id": "w6g1",
      "name": "Boulderer",
      "entry": "e_orc_hunter_2#2",
      "tier": "normal",
      "rank": 2,
      "side": "east",
      "affixes": ["Champion", "Multishot"],
      "flags": [],
      "count": 9,
      "killed": 9,
      "leaked": 0,
      "boss": false
     },
     {
      "id": "w6g2",
      "name": "Sand Wasp",
      "entry": "e_sand_wasp_1#1",
      "tier": "normal",
      "rank": 1,
      "side": "east",
      "affixes": [],
      "flags": ["flying"],
      "count": 17,
      "killed": 17,
      "leaked": 0,
      "boss": false
     }
    ],
    "walls": {"north": 7200.0, "east": 6600.0, "south": 6000.0, "west": 6000.0},
    "keep": 12500.0,
    "stone_used": 0
   }
  ],
  "next": {
   "wave": 8,
   "kind": "normal",
   "sides": ["south"],
   "event": null,
   "groups": [{"name": "Orc Hunter", "tier": "normal", "rank": 1, "side": "south", "count": 20, "affixes": [], "flags": [], "boss": false}]
  }
 },
 "watch": null,
 "waiting": [
  {
   "id": "defense_fixture_done",
   "room": "Act_03_03",
   "region": "The Desert",
   "level": 5,
   "outcome": "held",
   "kills": 501,
   "ended_at": "2026-09-25T12:34:07Z"
  }
 ],
 "history": [
  {
   "id": "defense_fixture_done",
   "room": "Act_03_03",
   "region": "The Desert",
   "level": 5,
   "outcome": "held",
   "waves": 12,
   "waves_total": 12,
   "kills": 501,
   "spoils": 25,
   "stone_back": 800,
   "ended_at": "2026-09-25T12:34:07Z",
   "record": {"held": true, "waves": true},
   "town_collected": false,
   "watch": false
  }
 ],
 "records": {"Act_03_03": {"held": 5, "waves": {"5": 12}}},
 "regions": [
  {
   "room": "Act_03_03",
   "species": 9,
   "entries": 12,
   "ranks": {"1": 4, "2": 3, "3": 2, "4": 3},
   "packets": 12,
   "special": ["abyss"],
   "name": "Mos'Arathim Desert",
   "goblins": ["rune", "treasure"],
   "calibrated": [{"slot": 2, "name": "Suh", "profile": "qa"}]
  }
 ],
 "levels": {"max": 60, "min_hours": 0.25, "max_hours": 8.0, "wave_minutes": 5},
 "rank_names": {"1": "Common", "2": "Champion", "3": "Ancient", "4": "Legion"},
 "tiers": [{"key": "ascended", "name": "Ascended", "hp": 2.5, "wall": 1.6, "extra_affixes": 1, "drops": 2, "speed": 1.05, "level": 15}],
 "events": [{"key": "blood_moon", "name": "Blood Moon", "text": "Monsters run 25% faster and hit the walls 15% harder this wave.", "weight": 3}],
 "affixes": {
  "11": {"name": "Cold Enchanted", "resist": {"cold": 0.75}, "flags": ["frozen"]},
  "21": {"name": "Fallen Angel", "hp": 1.3, "resist": {"holy": 0.5}, "flags": ["regenerating"]}
 },
 "towers": [
  {
   "key": "ballista",
   "name": "Ballista",
   "dtype": "physical",
   "dps": 2.0,
   "reach": 60,
   "min_reach": 0,
   "targets": 1,
   "air": true,
   "ground": true,
   "priority": "strongest",
   "text": "Heavy bolts at the toughest monster in reach. Hits flyers.",
   "perks": [
    {"key": "piercing", "name": "Piercing Bolts", "text": "Ignores half of any physical resistance.", "pierce": 0.5},
    {"key": "twin", "name": "Twin Bolts", "text": "Hits two monsters at once.", "targets": 2}
   ]
  }
 ]
}
```

### `/api/defense-forecast`

`?room=Act_03_03&hours=1&stone=0` gives `suggest`; adding `&level=6` gives `check_level`.

```json
{"suggest": {"suggested": 23}, "check_level": {"level": 6, "waves": 12, "fall_chance": 0.0, "waves_median": 12, "waves_low": 12}}
```

### `/api/bestiary?room=Act_03_03`

```json
{
 "room": "Act_03_03",
 "name": "Mos'Arathim Desert",
 "entries": [
  {
   "key": "e_orc_hunter_2#2",
   "name": "Boulderer",
   "names": ["Boulderer"],
   "rank": 2,
   "rank_name": "Champion",
   "origin": "ordinary",
   "speed": 2.8,
   "ranged": true,
   "immune": [],
   "flyer": false,
   "affixes": [{"id": 0, "name": "Champion", "seen": 1}, {"id": 16, "name": "Multishot", "seen": 1}],
   "recorded": 1,
   "slain": 34
  }
 ]
}
```

### `/api/trade`

One town of ten, two goods of its market.

```json
{
 "wagons": {"count": 2, "capacity": 1000, "speed": 1.0, "post": 2},
 "towns": [
  {
   "key": "ironhold",
   "name": "Ironhold",
   "kind": "Mining town",
   "text": "Dwarf-built shafts under the mountain. Ore is cheap here; cut stones and keys are not.",
   "hours": 1.5,
   "post": 1,
   "reachable": true,
   "development": 2,
   "standing": 1,
   "tariff": 0.09,
   "traded": 454410,
   "event": null,
   "market": [
    {"key": "12:0", "name": "Basic Key", "category": "key", "ask": 1266, "bid": 935, "stock": 384, "can_buy": 345, "can_sell": 1152},
    {"key": "12:1", "name": "Crystal Key", "category": "key", "ask": 5061, "bid": 3740, "stock": 118, "can_buy": 106, "can_sell": 354}
   ]
  }
 ],
 "runs": [
  {
   "id": "wagon_fixture1",
   "town": "emberfall",
   "cargo": {"14:27": 400},
   "orders": {"14:0": 20},
   "purse": 150000,
   "from_coffer": 150000,
   "payment": null,
   "left_at": "2026-09-25T16:34:07Z",
   "arrives_at": "2026-09-25T19:04:07Z",
   "returns_at": "2026-09-25T21:34:07Z",
   "state": "travelling",
   "result": null,
   "town_name": "Emberfall",
   "home": false
  }
 ],
 "coffer": 2450000,
 "history": []
}
```

### `/api/market`

```json
{
 "level": 2,
 "stalls": 2,
 "chance": 0.5,
 "merchants": [
  {
   "id": "keymaster@82886",
   "merchant": "keymaster",
   "name": "Keymaster Brann",
   "text": "A ring of keys to every door, the Angelic Realm's included, for a price.",
   "arrives_at": "2026-09-25T16:34:07Z",
   "leaves_at": "2026-09-25T20:34:07Z",
   "offers": [
    {"key": "12:8", "name": "Angelic Key", "category": "key", "side": "sell", "left": 3, "unit": 88161, "value": 60000},
    {"key": "12:33", "name": "Chaos Key", "category": "key", "side": "sell", "left": 8, "unit": 21421, "value": 15000}
   ]
  }
 ],
 "history": [],
 "next_watch": {"expected": true, "watch_starts": "2026-09-25T18:00:00Z"},
 "known": [
  {
   "key": "gem_cutter",
   "name": "Wandering Gem Cutter",
   "text": "Cuts stones by the roadside. Sells gems and jewelcrafting stones; buys ore.",
   "market": 1
  }
 ],
 "coffer": 2450000
}
```

### `/api/workers` → `camp` (the town's buildings)

```json
{
 "camp": {
  "buildings": [
   {
    "key": "walls",
    "name": "Walls",
    "text": "The town's walls (health, armor, masons) and tower slots; your heroes' Siege gate.",
    "level": 3,
    "max_level": 5,
    "unlock_hq": 1,
    "next": {
     "key": "walls",
     "to": 4,
     "cost": {"gold": 7000000, "stone": 4500, "spoils": 1000, "dust": 150},
     "hours": 14.0,
     "blockers": ["needs Headquarters level 4"]
    }
   },
   {
    "key": "workshop",
    "name": "Siege Workshop",
    "text": "Builds and raises towers: each level allows two more tower levels; a second site at level 4.",
    "level": 3,
    "max_level": 5,
    "unlock_hq": 1,
    "next": {
     "key": "workshop",
     "to": 4,
     "cost": {"gold": 6000000, "stone": 4000, "spoils": 1200, "dust": 150},
     "hours": 14.0,
     "blockers": ["needs Headquarters level 4"]
    }
   }
  ],
  "queue": [
   {
    "building": "barracks",
    "to": 2,
    "started_at": "2026-09-25T17:14:07Z",
    "ready_at": "2026-09-25T18:14:07Z",
    "request": "fixture",
    "name": "Barracks",
    "progress": 0.3356
   }
  ],
  "resources": {"stone": 7200, "spoils": 1275, "dust": 180},
  "resource_cap": 3000,
  "stock_cap": 1500,
  "key_cap": 60,
  "sites": 2
 }
}
```

### `/api/camp/vault`

```json
{
 "editor": true,
 "stock": [
  {"key": "12:0", "name": "Basic Key", "count": 156, "goes_to": "rack"},
  {"key": "12:1", "name": "Crystal Key", "count": 27, "goes_to": "rack"},
  {"key": "12:33", "name": "Chaos Key", "count": 52, "goes_to": "stock"},
  {"key": "14:5", "name": "Lesser Impstone", "count": 40, "goes_to": "stock"}
 ],
 "keys": {},
 "key_cap": 60,
 "key_room": 60,
 "stock_cap": 1500,
 "stock_room": 0,
 "pending": []
}
```
